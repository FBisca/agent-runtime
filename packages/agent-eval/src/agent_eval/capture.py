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

from agent_eval import claude, codex, gitpatch, orchestrate, pi
from agent_eval.errors import CaptureError
from agent_eval.sessions import Aux, Found, Transcript, assignment_id, head, text_of
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


@dataclass
class Named:
    """A `--session` ref resolved: the harness that holds it, and for Codex and Pi the session found."""

    id: str
    harness: str
    found: Found | None = None


def resolve(cdir: Path, ref: str) -> Named:
    """The one session `ref` (a full id, or a unique prefix) names, in Claude, Codex or Pi.

    A full id wins over longer ids it prefixes. Exit 2 when nothing matches, when several ids do,
    and when one id is held by two harnesses: which of them the run used cannot be guessed.
    """
    places = {
        "claude": cdir / "projects",
        "codex": codex.codex_dir() / "sessions",
        "pi": pi.pi_dir() / "sessions",
    }
    held: list[tuple[str, str, Path, Found | None]] = []
    for path in sorted((cdir / "projects").glob("*/*.jsonl")):
        if path.stem.startswith(ref):
            held.append(("claude", path.stem, path, None))
    for found in codex.scan(codex.codex_dir()) + pi.scan(pi.pi_dir()):
        if found.id.startswith(ref):
            held.append((found.harness, found.id, found.transcript.source, found))
    exact = [item for item in held if item[1] == ref]
    pool = exact or held
    ids = sorted({item[1] for item in pool})
    if not ids:
        looked = "; ".join(f"{name}: {place}" for name, place in places.items())
        raise CaptureError(f"session {ref!r} not found; looked in {looked}", 2)
    if len(ids) > 1:
        candidates = ", ".join(
            f"{item[0]} {item[1]}" for item in sorted(pool, key=lambda i: (i[1], i[0]))
        )
        raise CaptureError(f"session {ref!r} is ambiguous: {candidates}", 2)
    harnesses = sorted({item[0] for item in pool})
    if len(harnesses) > 1:
        where = " and ".join(f"{item[0]} {item[2]}" for item in pool)
        raise CaptureError(
            f"session {ids[0]} is found under two harnesses: {where}; name it from a "
            "home that holds only one",
            2,
        )
    same = [item for item in pool if item[0] != "claude"]
    if len(same) > 1:
        paths = " and ".join(str(item[2]) for item in same)
        raise CaptureError(f"session {ids[0]} is found in two {harnesses[0]} files: {paths}", 2)
    return Named(ids[0], harnesses[0], pool[0][3])


def own_fields(path: Path) -> tuple[str | None, str | None]:
    """The cwd and assignment id of a Claude file's own records: the first cwd any record carries,
    and the id its first user prompt names. Null for what the file does not say."""
    cwd, prompt = None, None
    for record in head(path):
        if cwd is None and isinstance(record.get("cwd"), str):
            cwd = record["cwd"]
        if prompt is None and record.get("type") == "user" and not record.get("isMeta"):
            message = record.get("message")
            prompt = (text_of(message.get("content")) if isinstance(message, dict) else "") or None
        if cwd is not None and prompt is not None:
            break
    return cwd, assignment_id(prompt or "")


def plan(
    cdir: Path, refs: Sequence[str], excludes: Sequence[str], run: orchestrate.Run | None = None
) -> tuple[list[Entry], list[str], list[str], str | None]:
    """-> (entries, excluded ids, missing ids, root id). Reads only; writes nothing.

    The root is the first `--session`'s session, or for a Claude one the oldest of its chain, captured
    or not: it names the default case, and is None when only a run was named. Other named sessions
    that no link joins to it have role `session`. The run's workers join as role `worker`, each once:
    a worker the naming already holds stays what the naming made it.
    """
    all_jobs = claude.jobs(cdir)
    named = [resolve(cdir, ref) for ref in refs]
    order: list[str] = []
    links: dict[str, claude.Edge | None] = {}
    outside: dict[str, Found] = {}  # Codex and Pi sessions: no chain, their own transcript in hand
    for one in named:
        if one.found is not None:
            if one.id not in links:
                order.append(one.id)
                links[one.id] = None
                outside[one.id] = one.found
            continue
        for member, edge in claude.chain(all_jobs, one.id):
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
        edge = links[sid]
        role = edge.kind if edge else ("root" if sid == root else "session")
        if sid in outside:
            one = outside[sid]
            entries.append(
                Entry(
                    sid, role, None, one.harness, one.transcript, one.cwd, assignment_id(one.prompt)
                )
            )
            continue
        found = claude.transcript(cdir, sid)
        if found is None:
            if any(n.id == sid for n in named):
                raise CaptureError(f"session {sid} has no transcript under {cdir / 'projects'}")
            missing.append(sid)  # a job state naming a session whose file is already deleted
            continue
        cwd, assignment = own_fields(found.source)
        entries.append(
            Entry(sid, role, edge.parent if edge else None, "claude", found, cwd, assignment)
        )
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
    entries = []
    for sub in claude.subagents(main):
        cwd, assignment = own_fields(sub.source)
        entries.append(Entry(sub.id, "subagent", parent, "claude", sub, cwd, assignment))
    return entries


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
