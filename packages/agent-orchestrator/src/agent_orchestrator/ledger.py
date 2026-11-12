"""The run's ledger: one lock, one deadline, one marker, for every process that writes it.

The driver, the Lead's commands and the evidence runner all update `<ledger>.json` under `<ledger>.json.lock`. A writer
that finds the lock held waits with jittered backoff under one monotonic deadline that counts the waits and the sleeps
alike, so a long wait is bounded once and not once per layer. A reader that only looks takes `snapshot`, which needs no
lock because every writer replaces the file whole; a write that follows such a read passes the snapshot back to
`locked`, which refuses it when the keys it decided on have moved. Standard library only."""

import fcntl
import json
import os
import random
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 1
KIND = "agent-orchestrator/ledger"

DEADLINE = 60.0  # seconds a writer waits for a held ledger, sleeps included
BACKOFF_FIRST = 0.2  # the first sleep; each sleep doubles, up to BACKOFF_CAP
BACKOFF_CAP = 5.0


class LedgerError(ValueError):
    """The ledger cannot be used as it is: a symlink, not JSON, not a ledger of this orchestrator."""


class LedgerStale(LedgerError):
    """A write that rests on a snapshot the ledger has moved past."""


class LedgerBusy(Exception):
    """The ledger stayed locked past the deadline.

    Not a `ValueError` on purpose: the driver's step handlers catch `ValueError` and go on, and a ledger that stays busy
    must reach the crash guard instead of being booked as one gate's failure."""


def describe(seconds):
    return "%gs" % seconds


def acquire(lock, wait, clock=time.monotonic, sleep=time.sleep, jitter=random.uniform):
    """Take the exclusive lock on the open file `lock`, or raise `LedgerBusy` once `wait` seconds have passed.

    The deadline is read once, on the monotonic clock, before the first attempt. A sleep is cut to what is left of it, so
    the wait never runs past it."""
    deadline = clock() + wait
    pause = BACKOFF_FIRST
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            left = deadline - clock()
            if left <= 0:
                raise LedgerBusy("ledger busy >%s" % describe(wait)) from None
            # Half to all of the step: two writers that met once do not meet again in step.
            sleep(min(jitter(pause / 2, pause), left))
            pause = min(pause * 2, BACKOFF_CAP)


def validate(data, path):
    if not isinstance(data, dict):
        raise LedgerError("the ledger %s is not an object" % path)
    if data.get("version") != VERSION or not isinstance(data.get("tasks"), dict):
        raise LedgerError("invalid ledger %s: it is not a version %d ledger" % (path, VERSION))
    if data.get("kind", KIND) != KIND:
        raise LedgerError(
            "the ledger %s is of kind %r, not %r: it belongs to another tool"
            % (path, data["kind"], KIND)
        )
    return data


def load(path):
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise LedgerError("cannot read the ledger %s: %s" % (path, error)) from None
    if not isinstance(data, dict):
        raise LedgerError("the ledger %s is not an object" % path)
    return data


def snapshot(path):
    """The ledger as one reader sees it, without its lock, or None when there is none yet. A snapshot can be stale the
    moment it is read: a write that rests on it says so to `locked`."""
    return load(path)


def new():
    return {"version": VERSION, "kind": KIND, "tasks": {}}


def stamp(data):
    """The marker, beside `version`, which stays what it was."""
    rest = {k: v for k, v in data.items() if k not in ("version", "kind")}
    data.clear()
    data.update({"version": data.get("version", VERSION), "kind": KIND}, **rest)
    return data


def write(path, data):
    """Replace the ledger whole and durably: a reader sees the old file or the new one, never a half."""
    fd, temporary = tempfile.mkstemp(prefix=".orchestrate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(
    path,
    *,
    wait=None,
    snapshot=None,
    keys=(),
    clock=time.monotonic,
    sleep=time.sleep,
    jitter=random.uniform,
):
    """The ledger's data, under its lock; what the block changes is written when it ends.

    `wait` is the deadline in seconds (`DEADLINE` when None). A ledger without the kind marker gets it on disk before the
    block runs, so nothing a worker can see is ever written ahead of it. `snapshot` is the data an earlier unlocked read
    returned: when any of `keys` differs from it now, the write is refused with `LedgerStale` and nothing is written."""
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise LedgerError("ledger must not be a symlink: %s" % path)
    lock_fd = os.open(
        path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(lock_fd, "w") as lock:
        acquire(lock, DEADLINE if wait is None else wait, clock, sleep, jitter)
        current = load(path)
        fresh = current is None
        data = new() if fresh else validate(current, path)
        if snapshot is not None:
            moved = [key for key in keys if snapshot.get(key) != data.get(key)]
            if moved:
                raise LedgerStale(
                    "the ledger changed since it was read (%s); read it again and retry"
                    % ", ".join(moved)
                )
        if not fresh and data.get("kind") != KIND:
            write(path, stamp(data))
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            write(path, data)
