"""What an approval of a plan covers, and the snapshot a driver reads it back from.

The digest inputs use eight-byte big-endian length prefixes: the repository's identity, the base, and
the canonical plan bytes - `safe_dump` sorted, with the approval and its amendments dropped. The plan
alone is the contract: no file beside it is digested. The snapshot holds those same bytes, so a driver,
an amendment and `verify` can compare the plan in the tree against the contract that was approved.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

from . import ledger as ledgers
from .plan_locks import ledger_path

DEFAULT_STATE = "~/.local/state/orchestrate"


# What a person or `plan amend` writes about the contract, not the contract: neither enters the digest.
UNDIGESTED = ("approval", "amendments")


def canonical(data):
    return yaml.safe_dump(
        {k: v for k, v in data.items() if k not in UNDIGESTED}, sort_keys=True
    ).encode("utf-8")


def digest(repo_identity, base, plan_bytes):
    parts = [repo_identity.encode(), base.encode(), plan_bytes]
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


def git_email(repo):
    email = subprocess.run(
        ["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True, timeout=15
    ).stdout.strip()
    return email or os.environ.get("USER") or "unknown"


def publish(path, text, plan_bytes, sha, state_dir):
    """Write the approval snapshot of digest `sha`, then replace the plan file with `text`, in that order: a driver
    that reads the file in between finds the snapshot of the old approval and of the new one."""
    approvals = Path(state_dir).expanduser() / "approvals"
    approvals.mkdir(parents=True, exist_ok=True)
    target = approvals / sha
    snap = target / "plan.yaml"
    if not snap.is_file() or snap.read_bytes() != plan_bytes:
        temporary = Path(tempfile.mkdtemp(prefix=".approval-", dir=approvals))
        try:
            (temporary / "plan.yaml").write_bytes(plan_bytes)
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
    ledger = ledger_path(repo, slug, state_dir)
    found = ledgers.snapshot(ledger)  # a read: it never waits for the driver
    statuses = (found or {}).get("ticket_status") or {}
    return {
        t for t, status in statuses.items() if str(status).strip().upper().startswith("COMPLETE")
    }


def frozen_problems(old, new, done):
    """What an edit may not do to the tickets in `done`: change or drop them, or change the `when` or `then` of a
    scenario they satisfy."""
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
