"""What an approval of a plan covers, and the snapshot a driver reads it back from.

The digest inputs use eight-byte big-endian length prefixes: the canonical plan bytes - `safe_dump`
sorted, with the approval and its amendments dropped - plus every file the approval covers, each
named. The snapshot holds those same bytes, so a driver, an amendment and `verify` can compare the
plan in the tree against the contract that was approved.
"""

import hashlib
import os
import posixpath
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from . import ledger as ledgers
from . import runner
from .plan_discovery import repo_config
from .plan_layout import PlanLayout
from .plan_locks import state_paths

DEFAULT_STATE = "~/.local/state/orchestrate"


# The folder's subdirectories whose every file the approval digests.
FOLDER_PAYLOADS = ("acceptance", "negatives")


# What a person or `plan amend` writes about the contract, not the contract: neither enters the digest.
UNDIGESTED = ("approval", "amendments")


def canonical(data):
    return yaml.safe_dump(
        {k: v for k, v in data.items() if k not in UNDIGESTED}, sort_keys=True
    ).encode("utf-8")


def payloads(directory):
    return (
        {
            p.relative_to(directory).as_posix(): p.read_bytes()
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        }
        if directory.exists()
        else {}
    )


def folder_files(path, data):
    """{folder-relative name: bytes} of a folder plan's acceptance/ and negatives/ and of each negative the plan
    names: what approval digests, snapshots and later compares. Interpreter caches are not the plan's."""
    folder = Path(path).parent
    names = set()
    for sub in FOLDER_PAYLOADS:
        base = folder / sub
        if base.is_dir():
            names.update(
                p.relative_to(folder).as_posix()
                for p in base.rglob("*")
                if p.is_file() and "__pycache__" not in p.relative_to(base).parts
            )
    for scenario in data.get("scenarios", []) if isinstance(data, dict) else []:
        named = scenario.get("evidence", {}).get("negative") if isinstance(scenario, dict) else None
        if isinstance(named, str) and (folder / named).is_file():
            names.add(posixpath.normpath(named))
    return {n: (folder / n).read_bytes() for n in sorted(names)}


def approved_files(path, data):
    """The files an approval of `path` covers, by the name the digest and the snapshot use."""
    layout = PlanLayout(path)
    return folder_files(path, data) if layout.is_folder else payloads(layout.oracle_dir)


def snapshot_files(snapshot, path):
    """The files of an approval snapshot directory: `oracle/` for a legacy plan, the folder-relative names for a
    folder plan (everything beside its plan.yaml)."""
    if not PlanLayout(path).is_folder:
        return payloads(snapshot / "oracle")
    return {n: b for n, b in payloads(snapshot).items() if n != "plan.yaml"}


def file_path(path, name):
    """Where a snapshot file name lives in the authored plan, for a message."""
    layout = PlanLayout(path)
    return layout.folder / name if layout.is_folder else layout.oracle_dir / name


def destination(model, item, repo):
    """The repo-relative directory a folder plan's acceptance files are placed in for `item`:
    `<package>/<acceptance.dir>/<domain>`. A plan with no domain (it discards) has none."""
    parts = [item.package or ".", repo_config(repo).acceptance_dir] + (
        [model.domain] if model.domain else []
    )
    return posixpath.normpath(posixpath.join(*parts))


def runner_inputs(path, model, item, files, repo):
    """(oracles, place, negative) the runner takes for an evidence item: test file bytes by name, the directory they
    are placed in, and the negative patch as (name, bytes). One derivation for the baseline, the runs and the
    mutation checks, whichever layout the plan has."""
    negative = (item.negative, files[item.negative]) if item.negative else None
    if not PlanLayout(path).is_folder:
        return {name: files[name] for name in item.oracle}, item.place, negative
    return (
        {name: files["acceptance/" + name] for name in item.acceptance},
        destination(model, item, repo) if item.acceptance else None,
        negative,
    )


def placed_files(path, model, item, repo):
    """Repo-relative path of each test file an evidence item places."""
    if not PlanLayout(path).is_folder:
        return [
            ((item.place or "").rstrip("/") + "/" + name.removesuffix(".oracle")).lstrip("/")
            for name in item.oracle
        ]
    return [posixpath.join(destination(model, item, repo), name) for name in item.acceptance]


def digest(repo_identity, base, plan_bytes, files):
    parts = [repo_identity.encode(), base.encode(), plan_bytes]
    for name, content in sorted(files.items()):
        parts.extend((name.encode(), content))
    h = hashlib.sha256()
    for part in parts:
        h.update(len(part).to_bytes(8, "big"))
        h.update(part)
    return h.hexdigest()


def replaced_text(text, name, new):
    """`text` with its top-level `name` set to `new` (appended when absent), every other byte kept: the
    author's comments survive."""
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    existing = next(((k, v) for k, v in node.value if k.value == name), None)
    if node.flow_style:
        entry = yaml.safe_dump({name: new}, default_flow_style=True, width=10000).strip()[1:-1]
        if existing:
            key, value = existing
            return text[: key.start_mark.index] + entry + text[value.end_mark.index :]
        end = node.end_mark.index - 1
        return text[:end] + (", " if node.value else "") + entry + text[end:]
    eol = "\r\n" if "\r\n" in text else "\n"
    block = yaml.safe_dump({name: new}, sort_keys=False).replace("\n", eol)
    if existing:
        key, value = existing
        start, end = key.start_mark.index, value.end_mark.index
        if text[end:].startswith(eol):
            end += len(eol)
    else:
        # An explicit YAML document terminator must stay after the new block.
        start = end = next(
            (
                e.start_mark.index
                for e in yaml.parse(text)
                if isinstance(e, yaml.DocumentEndEvent) and e.explicit
            ),
            len(text),
        )
    prefix = text[:start]
    return prefix + (eol if prefix and not prefix.endswith("\n") else "") + block + text[end:]


def approved_text(text, approval):
    return replaced_text(text, "approval", approval)


def evidence_key(path, model, item, files, repo):
    """What one check evidence item runs: the check, its oracle bytes (by hash), place, env names, timeout and
    negative. Two items with one key observe the same thing at one commit."""
    oracles, place, negative = runner_inputs(path, model, item, files, repo)
    return (
        item.check,
        tuple(sorted((name, runner.sha256(content)) for name, content in oracles.items())),
        place,
        tuple(item.env),
        item.timeout,
        (negative[0], runner.sha256(negative[1])) if negative else None,
    )


def checked_scenarios(model):
    return [s for s in model.scenarios if s.evidence is not None and s.evidence.check]


def git_email(repo):
    email = subprocess.run(
        ["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True, timeout=15
    ).stdout.strip()
    return email or os.environ.get("USER") or "unknown"


def publish(path, text, plan_bytes, files, sha, state_dir):
    """Write the approval snapshot of digest `sha`, then replace the plan file with `text`, in that order: a driver
    that reads the file in between finds the snapshot of the old approval and of the new one."""
    approvals = Path(state_dir).expanduser() / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    target = approvals / sha
    snap = target / "plan.yaml"
    if (
        not snap.is_file()
        or snap.read_bytes() != plan_bytes
        or snapshot_files(target, path) != files
    ):
        temporary = Path(tempfile.mkdtemp(prefix=".approval-", dir=approvals))
        try:
            (temporary / "plan.yaml").write_bytes(plan_bytes)
            for name, content in files.items():
                dest = (
                    temporary / name if PlanLayout(path).is_folder else temporary / "oracle" / name
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(content)
            if target.exists():
                shutil.rmtree(target)
            temporary.rename(target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    fd, temporary = tempfile.mkstemp(prefix=".approval-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        shutil.copymode(path, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def completed(repo, slug, state_dir):
    """The ticket IDs the plan's ledger marks COMPLETE."""
    ledger, _ = state_paths(repo, slug, state_dir)
    found = ledgers.snapshot(ledger)  # a read: it never waits for the driver
    statuses = (found or {}).get("ticket_status") or {}
    return {
        t for t, status in statuses.items() if str(status).strip().upper().startswith("COMPLETE")
    }


def frozen_problems(old, new, done):
    """What an edit may not do to the tickets in `done`: change or drop them, or change the `when` or `then` of a
    scenario they satisfy. A scenario's `evidence` may change."""
    before, after = ({t["id"]: t for t in d.get("tickets", [])} for d in (old, new))
    scenarios = (
        {s["id"]: s for s in d.get("scenarios", []) if isinstance(s, dict)} for d in (old, new)
    )
    old_scenarios, new_scenarios = scenarios
    problems = []
    for ticket in sorted(done & before.keys()):
        if ticket not in after:
            problems.append(f"{ticket} is COMPLETE and the edit drops it")
        elif after[ticket] != before[ticket]:
            problems.append(f"{ticket} is COMPLETE and the edit changes it")
        for sid in before[ticket]["satisfies"]:
            was, now = old_scenarios.get(sid), new_scenarios.get(sid)
            if was and (now is None or (was["when"], was["then"]) != (now["when"], now["then"])):
                problems.append(
                    f"{sid} is satisfied by COMPLETE {ticket} and the edit changes its when or then"
                )
    return problems
