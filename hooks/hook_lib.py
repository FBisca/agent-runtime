"""Shared helpers for read-only, fail-open lead hooks (Stop guard, context reminders).

Hooks never write the store: it is opened through a `mode=ro` URI with a short busy timeout, and every
error (bad stdin, missing or locked store, no identity) means "allow, print nothing". Identity is
computed exactly like the MCP server's: scope from the hc socket/scope, lead from HC_TERMINAL_ID, and
the generation from the nearest Claude/Codex ancestor (agent_runtime.cli_generation).
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import agent_runtime as lib  # noqa: E402  (no side effects on import)
import dispatch_store as dispatch  # noqa: E402
import hc_tools  # noqa: E402

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
    db = sqlite3.connect("file:%s?mode=ro" % path.as_posix().replace("%", "%25").replace("?", "%3f").replace("#", "%23"),
                         uri=True, timeout=BUSY_MS / 1000, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=%d" % BUSY_MS)
    return db


def led_team(db, who):
    """The team id when (scope, lead, generation) is exactly the latest team for (scope, lead), else None."""
    scope, lead, generation = who
    row = db.execute("SELECT id,generation FROM teams WHERE scope=? AND lead=? ORDER BY rowid DESC LIMIT 1",
                     (scope, lead)).fetchone()
    return row["id"] if row and row["generation"] == generation else None


def undelivered(db, team, lead):
    """Lead-addressed messages not acknowledged and not named by any receipt (a delivered final read)."""
    return db.execute(
        "SELECT COUNT(*) FROM messages m WHERE m.team=? AND m.recipient=? AND m.acked=0 AND NOT EXISTS "
        "(SELECT 1 FROM receipts r, json_each(r.ids) j WHERE r.team=m.team AND r.recipient=m.recipient "
        "AND j.value=m.id)", (team, lead)).fetchone()[0]


def working(db, team):
    """Current assignments of active workers that are pending or working."""
    return db.execute("SELECT COUNT(*) FROM assignments a JOIN workers w ON w.id=a.worker "
                      "WHERE a.team=? AND a.current=1 AND w.active=1 AND a.state IN ('pending','working')",
                      (team,)).fetchone()[0]


def unfinished(db, team):
    """Same count as Store.unfinished_count (wait(all)'s snapshot), read-only."""
    return db.execute("SELECT COUNT(*) FROM assignments WHERE state!='complete' AND id IN "
                      "(SELECT assignment_id FROM (%s))" % dispatch.SNAPSHOT_SQL,
                      (team, "all", "all", "all", "all", "all", "all")).fetchone()[0]


def needs(db, team, lead):
    """dict(undelivered, working, unfinished, seen, attention) for one team."""
    last = dispatch.last_wait_state(db, team)
    seen = bool(last and last[0] != "timeout" and last[2] == last[3])
    return {"undelivered": undelivered(db, team, lead), "working": working(db, team),
            "unfinished": unfinished(db, team), "seen": seen, "attention": last[1] if seen else 0}


def lead_needs(env=None):
    """needs() for the caller's own team, or None when it leads none (or anything is unavailable)."""
    who = identity(env)
    if not who:
        return None
    db = open_readonly(env)
    try:
        db.execute("BEGIN")  # one read snapshot for the team lookup and every count
        try:
            team = led_team(db, who)
            return needs(db, team, who[1]) if team else None
        finally:
            db.rollback()
    finally:
        db.close()
