"""The state dir holds only plans that are not yet archived.

A driver that archives a plan is still logging into its run dir and writing its retro when ARCHIVED is
recorded, and `await` reads that dir, so nothing deletes there. The next driver start or `plan approve`
sweeps instead: a ledger archived at least `_KEEP` ago goes with its lock, its run dir and the approval snapshots its
digests name (the ledger's own, and every `amended` event's). A snapshot of an approval no ledger names
(approved but not started, or superseded before any driver started) has no safe rule and stays.
"""

import fcntl
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import events
from .. import ledger as ledgers

_KEEP = timedelta(days=14)  # recent runs stay available for inspection and analysis


def _aged(data):
    """Whether the ledger's ARCHIVED transition is at least _KEEP old; one with no readable time is kept."""
    for entry in reversed((data.get("plan") or {}).get("history") or []):
        if entry.get("state") == "ARCHIVED":
            try:
                at = datetime.fromisoformat(entry["at"])
            except (KeyError, TypeError, ValueError):
                return False
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) - at >= _KEEP
    return False


def _driver_alive(run_dir):
    """Whether the newest `start` of the run names a process that still runs: it may still be finishing."""
    starts = [row for row in events.read(run_dir / "events.jsonl") if row["kind"] == "start"]
    pid = starts[-1].get("pid") if starts else None
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _digests(data, run_dir):
    found = {(data.get("approval") or {}).get("digest")}
    for row in events.read(run_dir / "events.jsonl"):
        if row["kind"] == "amended":
            found |= {row.get("prev_digest"), row.get("digest")}
    return {d for d in found if isinstance(d, str) and d and "/" not in d and d != ".."}


def _forget(state, ledger, run_dir, lock):
    """Delete one archived plan's state, under its ledger lock; skip it while a writer holds the lock."""
    with open(lock, "a+") as held:
        try:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        data = ledgers.snapshot(ledger) or {}
        if (data.get("plan") or {}).get("state") != "ARCHIVED" or not _aged(data) or _driver_alive(run_dir):
            return
        for digest in _digests(data, run_dir):
            shutil.rmtree(state / "approvals" / digest, ignore_errors=True)
        shutil.rmtree(run_dir, ignore_errors=True)
        ledger.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def sweep(state_dir):
    """Delete every plan's, archived at least _KEEP ago, ledger, lock, run dir and snapshots under `state_dir`."""
    state = Path(state_dir).expanduser()
    for ledger in sorted(state.glob("*/*.json")):
        if state / "approvals" == ledger.parent or ledger.is_symlink():
            continue
        try:
            _forget(state, ledger, ledger.with_suffix(""), ledger.with_name(ledger.name + ".lock"))
        except (OSError, ledgers.LedgerError):
            continue  # a plan that cannot be read is not known to be archived
