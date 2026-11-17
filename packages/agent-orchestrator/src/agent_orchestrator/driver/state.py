"""Where a run's files are, and the locks and marks it keeps them with.

A run's state directory, its ledger, its run directory and the attempts a unit accumulates are decided
by the plan's layout and the repository's config, not by the caller, and every one of them is reached
through this module. The same file keeps the lock a Lead command and a drive share, the marks a crashed
run is read by, and the wake a parked drive leaves, so a path or a lock is decided once.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines.
"""

import fcntl
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .. import events, timeutil
from .. import ledger as ledgers
from .. import plan as yaml_plan
from ..plan import PlanError
from ..timeutil import iso as now
from .archive import crash_line
from .routing import repo_key, repo_root, require_yaml


def _engine():
    """The package this module is split from, asked when the run's lock is located.

    A test answers the git seam by patching `driver.run`; a name bound here at
    import time would answer the real process and ignore it.
    """
    return sys.modules[__name__.rpartition(".")[0]]


def legacy_imports(data):
    """What a ledger written before `events.jsonl` knew about each unit, as `import` events: its route, failed
    rounds, escalations (events sent to a decider plus pauses for the user), decider calls and the runtime's usage per
    assignment. The new driver writes them once, when it takes over such a run dir."""
    units = {}
    for task in data["tasks"].values():
        units.setdefault(task["packet"].get("unit") or task["packet"]["task_id"], []).append(task)
    rows = []
    for unit, tasks in units.items():
        ids = {
            a.get("assignment_id")
            for t in tasks
            for a in (t, t.get("review") or {}, t.get("decision") or {})
        } - {None}
        usage = data.get("usage", {})
        rows.append(
            {
                "unit": unit,
                "route": {
                    key: tasks[0]["packet"].get(key)
                    for key in ("size", "difficulty", "class", "model")
                },
                "review_rounds": sum(
                    (t.get("report") or {}).get("review_rounds", 0) for t in tasks
                ),
                "escalations": sum(1 for t in tasks if t.get("event"))
                + sum(len(t.get("attention") or []) for t in tasks),
                "decider_calls": sum(1 for t in tasks if t.get("decision")),
                "assignments": {
                    i: usage[i] for i in sorted(i for i in ids if i is not None) if i in usage
                },
            }
        )
    return rows


def state_layout(state_dir, plan_path, repo, slug):
    """(ledger path, run directory) of a plan. A YAML plan's are under `<state>/<repo-key>/`, so two repositories
    that run the same slug have two ledgers."""
    keyed = Path(state_dir).expanduser() / repo_key(repo)
    return keyed / (slug + ".json"), keyed / slug


def plan_home(args):
    """(slug, ledger path, run directory) of the plan a Lead command names."""
    plan_path = Path(args.plan).resolve(strict=True)
    slug = plan_slug(plan_path)
    if not slug:
        raise Refused("plan has no Slug")
    repo = repo_root(plan_path, getattr(args, "repo", None))
    return (slug, *state_layout(args.state_dir, plan_path, repo, slug))


def enter_plan_state(data, state, at=None):
    """The plan's state in the ledger, with its history: one entry per change, oldest first."""
    plan = data.setdefault("plan", {"state": None, "history": []})
    if plan["state"] != state:
        plan["state"] = state
        plan["history"].append({"state": state, "at": at or now()})


def split_status(text):
    """(state, reason) of a ticket Status: `IN PROGRESS · decision pending: why` is IN PROGRESS and its reason."""
    text = " ".join((text or "TO-DO").split())
    if text.lower().startswith("needs you:"):
        return "needs you", text.split(":", 1)[1].strip() or None
    head, _, reason = text.partition(" · ")
    return re.sub(r"\s+\d{4}-\d{2}-\d{2}$", "", head), reason.strip() or None


def read_ledger_file(path):
    """A ledger read without its lock: every writer replaces the file whole, so a reader sees one version."""
    data = ledgers.snapshot(path)
    if data is None:
        # The one refusal this read has: a file that is not there, named so the caller can act on it.
        raise ValueError("cannot read the ledger %s: no such file" % path)
    return data


def plan_slug(plan_path):
    """The slug a YAML plan names, read without validating it: a plan mid-edit still names its slug."""
    path = Path(plan_path).resolve(strict=True)
    require_yaml(path)
    data = yaml_plan.read_yaml(path)[0]
    slug = data.get("slug") if isinstance(data, dict) else None
    return slug if isinstance(slug, str) else None


def write_atomic(path, text):
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".orchestrate-", dir=path.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


class Refused(PlanError):
    """A command of the Lead that cannot be honoured; the command exits 2 and writes nothing. It names the reason and
    the way out: `str()` carries both, and `reason` and `way_out` stay apart for a caller that answers in JSON."""

    def __init__(self, reason, way_out=None):
        super().__init__("%s; %s" % (reason, way_out) if way_out else reason)
        self.reason, self.way_out = reason, way_out


class JsonRefusal(PlanError):
    """A refusal an agent Lead reads: one JSON object `{error, reason, way_out}` on stderr, exit 2."""

    def __init__(self, refused):
        super().__init__(str(refused))
        self.output = {
            "error": "decision refused",
            "reason": refused.reason,
            "way_out": refused.way_out or "fix the reason above and run decide again",
        }


def run_dir_of(args):
    """The run directory of the plan a Lead command names."""
    return _engine().plan_home(args)[2]


def numbered(directory, stem, suffix):
    """The existing write-once files <stem><suffix>, <stem>.2<suffix>, ... in order."""
    found = []
    for index in range(1, 1000):
        path = directory / (stem + ("" if index == 1 else ".%d" % index) + suffix)
        if not path.exists():
            break
        found.append(path)
    return found


def take_wake(run_dir):
    """The oldest wake `await` has not printed (and mark it printed), or None. Concurrent awaits take turns."""
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(run_dir / "wakes.cursor", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+") as cursor:
        fcntl.flock(cursor, fcntl.LOCK_EX)
        raw = cursor.read().strip()
        seen = int(raw) if raw.isdigit() else 0
        wakes = events.read(run_dir / "wakes.jsonl")
        if seen >= len(wakes):
            return None
        cursor.seek(0)
        cursor.truncate()
        cursor.write(str(seen + 1))
        cursor.flush()
        os.fsync(cursor.fileno())
        return wakes[seen]["text"]


DRIVER_MARKS = ("start", "finish", "crash")


def last_mark(run_dir):
    """The newest of the driver's `start`, `finish` and `crash` events, as its row, or None before any."""
    marks = [row for row in events.read(run_dir / "events.jsonl") if row["kind"] in DRIVER_MARKS]
    return marks[-1] if marks else None


def run_finished(run_dir):
    """True when the newest of the driver's `start`, `finish` and `crash` events is `finish`."""
    mark = last_mark(run_dir)
    return bool(mark) and mark["kind"] == "finish"


def driver_gone(run_dir, lock_path):
    """What a run whose driver is not running left behind, as the line `await` prints; None when a driver may still
    come or still be there. The run lock is the proof of life: `flock` dies with the process, so no lock held and a
    `start` with no `finish` or `crash` after it is a driver that was killed. A run that never logged a `start` has no
    driver yet, and a `start` without a pid was written by a version that cannot be judged this way."""
    mark = last_mark(run_dir)
    if mark is None or mark["kind"] == "finish" or lock_path is None or lock_held(lock_path):
        return None
    if mark["kind"] == "crash":
        return crash_line(run_dir, mark.get("text") or "crashed " + str(mark.get("reason")))
    return "driver gone without a crash log" if mark.get("pid") else None


def lock_held(path):
    """True when some process holds the flock on `path`; a lock that is not there is not held."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    with os.fdopen(fd) as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def await_lock_path(args):
    """The run lock of the plan `await` names, or None when it cannot be located (the check is then not made)."""
    try:
        plan_path = Path(args.plan).resolve(strict=True)
        slug = plan_slug(plan_path)
        return (
            run_lock_path(repo_root(plan_path, getattr(args, "repo", None)), slug) if slug else None
        )
    except (ValueError, OSError):
        return None


def run_lock_path(repo, slug):
    """`<git common dir>/agent-orchestrator/<slug>.lock`: it lives in the repository, not the state dir, because two
    drivers of one plan collide on its worktrees, branches and plan file whatever `--state-dir` each names."""
    common = Path(
        _engine().run(
            ["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"]
        )
    )
    return common / "agent-orchestrator" / (slug + ".lock")


@contextmanager
def run_lock(repo, slug, plan_path):
    """Hold the plan's lock for the whole run: one driver per plan and repository.

    It lives in the git common dir, not the state dir: two drivers of one plan collide on its worktrees, branches
    and plan file whatever `--state-dir` each names. `flock` dies with the process, so a crash leaves no stale lock.
    """
    path = run_lock_path(repo, slug)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = lock.read().strip() or "another driver"
            raise PlanError(
                "plan '%s' is already being driven by %s in %s; follow it with `await %s`, "
                "or stop that driver before starting another" % (slug, holder, repo, plan_path)
            ) from None
        lock.seek(0)
        lock.truncate()
        lock.write(
            "pid %d since %s\n" % (os.getpid(), timeutil.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
        lock.flush()
        yield
