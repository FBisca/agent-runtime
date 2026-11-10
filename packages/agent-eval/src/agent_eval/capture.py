"""Freeze a run into a case: `<home>/cases/<slug>/`.

A case holds verbatim transcripts and code, so it lives outside every git working tree, directories
0700 and files 0600, and an existing case is never touched. Everything that can refuse does so
before the first byte is written; the case is staged beside its final place and renamed into it.
"""

import json
import os
import shutil
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_eval import claude, gitpatch, orchestrate
from agent_eval.errors import CaptureError
from agent_eval.sessions import Aux, Transcript, assignment_id
from agent_eval.store import (
    check_slug,
    home,
    inside_git_tree,
    iso,
    make_private_dirs,
    write_file,
    write_gzipped,
)

SCHEMA = "case/1"
RETENTION_DAYS = 30  # Claude deletes a session's files after this many days
WARN_AFTER_DAYS = 25


@dataclass
class Entry:
    """One transcript to copy, with where it goes in the case."""

    id: str
    role: str
    parent: str | None
    harness: str
    transcript: Transcript
    cwd: str | None = None
    assignment_id: str | None = None


def plan(
    cdir: Path, refs: Sequence[str], excludes: Sequence[str], run: orchestrate.Run | None = None
) -> tuple[list[Entry], list[str], list[str], str | None]:
    """-> (entries, excluded ids, missing ids, root id). Reads only; writes nothing.

    The root is the oldest session of the first `--session` chain, captured or not: it names the
    default case, and is None when only a run was named. Sessions of later `--session` values that no
    link joins to it have role `session`. The run's workers join as role `worker`, each once: a worker
    the chain already holds stays what the chain made it.
    """
    all_jobs = claude.jobs(cdir)
    named = [claude.resolve(cdir, ref) for ref in refs]
    order: list[str] = []
    links: dict[str, claude.Edge | None] = {}
    for sid in named:
        for member, edge in claude.chain(all_jobs, sid):
            if member not in links:
                order.append(member)
                links[member] = edge
    root = order[0] if order else None
    workers = [w for w in orchestrate.workers(run) if w.id not in links] if run else []
    candidates = order + [w.id for w in workers]
    excluded: list[str] = []
    for ref in excludes:
        matches = [sid for sid in candidates if sid.startswith(ref)]
        if len(matches) != 1:
            raise CaptureError(
                f"--exclude {ref!r} does not name exactly one session of the chain or the run "
                f"({', '.join(sid[:8] for sid in candidates)})",
                2,
            )
        if matches[0] not in excluded:
            excluded.append(matches[0])
    entries: list[Entry] = []
    missing: list[str] = []
    for sid in order:
        if sid in excluded:
            continue
        found = claude.transcript(cdir, sid)
        if found is None:
            if sid in named:
                raise CaptureError(f"session {sid} has no transcript under {cdir / 'projects'}")
            missing.append(sid)  # a job state naming a session whose file is already deleted
            continue
        edge = links[sid]
        role = edge.kind if edge else ("root" if sid == root else "session")
        entries.append(Entry(sid, role, edge.parent if edge else None, "claude", found))
        entries.extend(subagent_entries(found, sid))
    for worker in workers:
        if worker.id in excluded:
            continue
        found = worker.transcript
        if worker.harness == "claude":  # tool results and subagents sit beside the main file
            found = claude.transcript(cdir, worker.id) or found
        entries.append(
            Entry(
                worker.id,
                "worker",
                None,
                worker.harness,
                found,
                worker.cwd,
                assignment_id(worker.prompt),
            )
        )
        if worker.harness == "claude":
            entries.extend(subagent_entries(found, worker.id))
    if not entries:
        raise CaptureError("nothing to capture: every session of the chain is excluded or missing")
    return entries, excluded, missing, root


def subagent_entries(main: Transcript, parent: str) -> list[Entry]:
    return [Entry(sub.id, "subagent", parent, "claude", sub) for sub in claude.subagents(main)]


def staged_path(entry: Entry) -> str:
    name = entry.id if entry.role != "subagent" else f"{entry.parent}/{entry.id}"
    return f"transcripts/{name}.jsonl.gz"


def capture(
    sessions: Sequence[str],
    case: str | None = None,
    excludes: Sequence[str] = (),
    repo: str | None = None,
    base: str | None = None,
    head: str | None = None,
    orchestrate_run: str | None = None,
    orchestrate_state_dir: str | None = None,
    now: float | None = None,
    warn: Callable[[str], None] = lambda message: None,
) -> Path:
    """Freeze the sessions, their chains and subagents, and a run's workers, into a new case."""
    if not sessions and orchestrate_run is None:
        raise CaptureError("--session or --orchestrate-run is required", 2)
    git_args = (repo, base, head)
    if any(x is not None for x in git_args) and not all(x is not None for x in git_args):
        raise CaptureError("--repo, --base and --head go together", 2)
    if case is not None:
        check_slug(case)
    root_dir = home().resolve()
    if inside_git_tree(root_dir):
        raise CaptureError(
            f"{root_dir} is inside a git working tree; cases hold transcripts and stay outside "
            "every repository: set AGENT_EVALS_HOME to a directory outside it"
        )
    run = orchestrate.load(orchestrate_run, orchestrate_state_dir) if orchestrate_run else None
    entries, excluded, missing, root_id = plan(claude.claude_dir(), sessions, excludes, run)
    slug = case or root_id or (run.slug if run else None)
    if slug is None:
        raise CaptureError("no case slug: name one with --case")
    check_slug(slug)
    final = root_dir / "cases" / slug
    if os.path.lexists(final):
        raise CaptureError(f"case {slug} already exists at {final}; cases are immutable")
    git_info: dict[str, str] | None = None
    patch: bytes | None = None
    if repo is not None and base is not None and head is not None:
        path, base_sha, head_sha = gitpatch.resolve(repo, base, head)
        git_info = {"repo": str(path), "base": base_sha, "head": head_sha}
        patch = gitpatch.diff(path, base_sha, head_sha)

    stamp = time.time() if now is None else now
    for entry in entries:
        if entry.harness != "claude":  # only Claude deletes a session's files on a schedule
            continue
        age = (stamp - entry.transcript.source.stat().st_mtime) / 86400
        if age > WARN_AFTER_DAYS:
            whole = int(age)
            warn(
                f"session {entry.id} is {whole} days old; {max(0, RETENTION_DAYS - whole)} days "
                f"left before Claude deletes it ({RETENTION_DAYS}-day retention)"
            )

    make_private_dirs(final.parent)
    staging = final.parent / f".tmp-{slug}-{os.getpid()}"
    try:
        records = [write_entry(staging, entry) for entry in entries]
        run_record = write_run(staging, run) if run else None
        if patch is not None:
            write_file(staging / "changes.patch", patch)
        manifest = {
            "schema": SCHEMA,
            "case": slug,
            "captured": iso(stamp),
            "sessions": records,
            "excluded": excluded,
            "missing": missing,
            "git": git_info,
            "orchestrate": run_record,
        }
        write_file(staging / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        try:
            os.rename(staging, final)
        except OSError:
            raise CaptureError(
                f"case {slug} already exists at {final}; cases are immutable"
            ) from None
    except OSError as error:
        raise CaptureError(f"cannot write the case: {error}") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return final


def write_run(staging: Path, run: orchestrate.Run) -> dict:
    """Copy the run directory, gzipped like a transcript, below `orchestrate/`."""
    files = []
    for source in run.files:
        rel = f"orchestrate/{source.relative_to(run.directory).as_posix()}.gz"
        try:
            digest, size = write_gzipped(source, staging / rel)
        except OSError as error:
            raise CaptureError(f"cannot read {source}: {error}") from error
        files.append({"file": rel, "sha256": digest, "bytes": size, "source": str(source)})
    return {
        "slug": run.slug,
        "source": str(run.directory),
        "window": {"start": iso(run.start), "end": iso(run.end)},
        "worktrees": sorted(run.worktrees),
        "files": files,
    }


def write_entry(staging: Path, entry: Entry) -> dict:
    source = entry.transcript.source
    rel = staged_path(entry)
    try:
        digest, size = write_gzipped(source, staging / rel)
        files = []
        for aux in entry.transcript.aux:
            aux_rel = aux_path(entry, aux)
            aux_digest, aux_size = write_gzipped(aux.source, staging / aux_rel)
            files.append(
                {
                    "kind": aux.kind,
                    "file": aux_rel,
                    "sha256": aux_digest,
                    "bytes": aux_size,
                    "source": str(aux.source),
                }
            )
    except OSError as error:
        raise CaptureError(f"cannot read {source} or its files: {error}") from error
    return {
        "id": entry.id,
        "harness": entry.harness,
        "role": entry.role,
        "parent": entry.parent,
        "file": rel,
        "sha256": digest,
        "bytes": size,
        "source": str(source),
        "modified": iso(source.stat().st_mtime),
        "cwd": entry.cwd,
        "assignment_id": entry.assignment_id,
        "files": files,
    }


def aux_path(entry: Entry, aux: Aux) -> str:
    """Where an aux file goes: a subagent's meta beside its transcript, the rest in a folder."""
    if aux.kind == "meta":
        return f"transcripts/{entry.parent}/{aux.name}.gz"
    folder = "rollouts" if aux.kind == "rollout" else "tool-results"
    return f"transcripts/{entry.id}/{folder}/{aux.name}.gz"
