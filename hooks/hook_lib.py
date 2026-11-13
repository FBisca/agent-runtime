"""Shared helpers for read-only, fail-open lead hooks (Stop guard, context reminders).

Hooks never write the store: it is opened through a `mode=ro` URI with a short busy timeout, and every
error (bad stdin, missing or locked store, no identity) means "allow, print nothing". Identity is
computed exactly like the MCP server's: scope from the hc socket/scope, lead from HC_TERMINAL_ID, and
the generation from the nearest Claude/Codex ancestor (agent_runtime.cli_generation).
"""

import fcntl
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))

from agent_runtime import agent_runtime as lib  # noqa: E402  (no side effects on import)
from agent_runtime import dispatch_store as dispatch  # noqa: E402
from agent_runtime import hc_tools  # noqa: E402

BUSY_MS = 200


def fail_open(main):
    """Run main(), swallowing every error: a hook must never block or crash its host."""
    try:
        main()
    except BaseException as error:  # noqa: BLE001 - includes SystemExit from helpers
        if isinstance(error, KeyboardInterrupt):
            raise
    return 0


def read_payload(stream=None):
    """The hook's stdin JSON object, or None when it is missing, unparseable or not an object."""
    try:
        data = json.load(stream or sys.stdin)
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


def identity(env=None):
    """(scope, lead, generation) or None when any part is unavailable."""
    env = os.environ if env is None else env
    lead = env.get("HC_TERMINAL_ID")
    if not lead:
        return None
    try:  # same function as the server, including its no-ancestor session-ID fallback
        scope, generation = hc_tools.dispatch_identity(env)
    except hc_tools.ToolError:
        return None
    return scope, lead, generation


def store_path(env=None):
    env = os.environ if env is None else env
    root = Path(env.get("XDG_STATE_HOME") or Path(env.get("HOME") or Path.home()) / ".local/state")
    return root / "agent-runtime" / "dispatch.sqlite3"


def open_readonly(env=None):
    """A read-only connection (never creates or changes a file); raises sqlite3.Error when unavailable."""
    path = store_path(env)
    db = sqlite3.connect(
        "file:%s?mode=ro"
        % path.as_posix().replace("%", "%25").replace("?", "%3f").replace("#", "%23"),
        uri=True,
        timeout=BUSY_MS / 1000,
        isolation_level=None,
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=%d" % BUSY_MS)
    return db


def led_team(db, who):
    """The team id when this caller leads the terminal's owning team, else None.

    The owner is the newest team row for (scope, lead) that is neither dead nor empty: a session that
    left nothing behind never hides the lead's own team, and a newer row that is alive or holds work
    still owns the terminal. The same rule the server reads."""
    scope, lead, generation = who
    owner = dispatch.owning_team(db, scope, lead, own_generation=generation)
    return owner["id"] if owner and owner["generation"] == generation else None


def undelivered(db, team, lead):
    """Lead-addressed messages not acknowledged and not named by any receipt (a delivered final read)."""
    return db.execute(
        "SELECT COUNT(*) FROM messages m WHERE m.team=? AND m.recipient=? AND m.acked=0 AND NOT EXISTS "
        "(SELECT 1 FROM receipts r, json_each(r.ids) j WHERE r.team=m.team AND r.recipient=m.recipient "
        "AND j.value=m.id)",
        (team, lead),
    ).fetchone()[0]


def working(db, team):
    """Current assignments of active workers that are pending or working, except those whose stall was
    already reported with no progress since (the lead was told; it must not be nagged about them)."""
    query = (
        "SELECT COUNT(*) FROM assignments a JOIN workers w ON w.id=a.worker %s "
        "WHERE a.team=? AND a.current=1 AND w.active=1 AND a.state IN ('pending','working')%s"
    )
    if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stall_progress'"
    ).fetchone():
        return db.execute(
            query
            % (
                "LEFT JOIN stall_progress sp ON sp.assignment=a.id",
                " AND NOT (sp.stall_reported_for IS NOT NULL AND sp.stall_reported_for=sp.last_progress_ms)",
            ),
            (team,),
        ).fetchone()[0]
    return db.execute(query % ("", ""), (team,)).fetchone()[0]


def unfinished(db, team):
    """Same count as Store.unfinished_count (wait(all)'s snapshot), read-only."""
    return db.execute(
        "SELECT COUNT(*) FROM assignments WHERE state!='complete' AND id IN "
        "(SELECT assignment_id FROM (%s))" % dispatch.SNAPSHOT_SQL,
        (team, "all", "all", "all", "all", "all", "all"),
    ).fetchone()[0]


def needs(db, team, lead):
    """dict(undelivered, working, unfinished, seen, attention) for one team."""
    last = dispatch.last_wait_state(db, team)
    seen = bool(last and last[0] != "timeout" and last[2] == last[3])
    attention = last[1] if (last is not None and seen) else 0
    return {
        "undelivered": undelivered(db, team, lead),
        "working": working(db, team),
        "unfinished": unfinished(db, team),
        "seen": seen,
        "attention": attention,
    }


def lead_check(env=None, who=None):
    """(identity, needs) where needs is None when the caller leads no team; (None, None) without identity.
    Pass `who` to reuse an identity already computed."""
    who = who or identity(env)
    if not who:
        return None, None
    db = open_readonly(env)
    try:
        db.execute("BEGIN")  # one read snapshot for the team lookup and every count
        try:
            team = led_team(db, who)
            return who, needs(db, team, who[1]) if team else None
        finally:
            db.rollback()
    finally:
        db.close()


def lead_needs(env=None):
    """needs() for the caller's own team, or None when it leads none (or anything is unavailable)."""
    return lead_check(env)[1]


# --- Stop-guard block budget (the only file hooks write; never the dispatch store) ---------------------

BUDGET = 3  # blocks per user work period; -1 marks "spent and already noted"


def state_root(env=None):
    return store_path(env).parent.parent


def budget_file(env, who):
    key = "\0".join([str(state_root(env)), *who])
    return (
        state_root(env)
        / "agent-runtime"
        / "stop-guard"
        / hashlib.sha256(key.encode()).hexdigest()[:32]
    )


def update_budget(env, who, change, create=True):
    """Atomically (flock) apply change(remaining) -> new remaining to the 0600 budget file; returns
    (old, new). A missing or unreadable file reads as a full budget."""
    path = budget_file(env, who)
    if not create and not path.exists():
        return BUDGET, BUDGET  # nothing recorded means a full budget already
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            old = int(os.read(fd, 32).decode().strip())
        except ValueError:
            old = BUDGET
        new = change(old)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, str(new).encode())
        return old, new
    finally:
        os.close(fd)  # also releases the lock


def spend_budget(env, who):
    """True when a block may be issued (one unit spent); False when spent. Returns (allowed, first_time)."""
    old, _ = update_budget(env, who, lambda n: n - 1 if n > 0 else -1)
    return old > 0, old == 0


STALE_SECONDS = 7 * 86400


def prune_budgets(env, now=None):
    """Best-effort removal of budget files untouched for a week (dead sessions)."""
    folder = state_root(env) / "agent-runtime" / "stop-guard"
    cutoff = (now or time.time()) - STALE_SECONDS
    try:
        for path in folder.iterdir():
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass
    except OSError:
        pass


def reset_budget(env, who):
    prune_budgets(env)
    update_budget(env, who, lambda _n: BUDGET, create=False)  # never creates state for non-leads


TERMINAL_STATUSES = ("completed", "failed", "killed", "stopped", "cancelled")


def running_wait_task(payload):
    """True when background_tasks holds a not-yet-finished MCP task for the agent-runtime server's `wait`.
    Plugin servers are named `plugin:<plugin>:<server>`; a manually added one is just `<server>`. Status
    values are undocumented, so anything but a terminal status counts as running. Other shapes: no exemption
    (the `wait` target is not visible here, so a wait of any target exempts)."""
    tasks = payload.get("background_tasks")
    if not isinstance(tasks, list):
        return False
    for task in tasks:
        if (
            not isinstance(task, dict)
            or task.get("type") != "MCP task"
            or task.get("status") in TERMINAL_STATUSES
        ):
            continue
        server, tool = task.get("server"), task.get("tool")
        if (
            isinstance(server, str)
            and isinstance(tool, str)
            and server.rsplit(":", 1)[-1] == "agent-runtime"
            and tool.rsplit("__", 1)[-1] == "wait"
        ):
            return True
    return False
