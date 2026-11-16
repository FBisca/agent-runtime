"""The read path `wait` uses: the snapshot a wait freezes, the unfinished count it reports, and the
team-state digest that decides whether anything changed since the last wait.

The queries are literals, so the reader sees the whole rule; the digest is pure and accepts a
read-only connection without a Store, so the hooks can ask the same question the CLI does."""

import hashlib
import json
from typing import Any

SNAPSHOT_SQL = (
    "SELECT a.id AS assignment_id,d.turn,d.mode,w.name,w.address,t.lead FROM assignments a "
    "LEFT JOIN dispatch_turns d ON d.assignment=a.id "
    "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team "
    "WHERE a.team=? AND (a.current=1 OR a.id=? OR (? IN ('all','any') AND EXISTS "
    "(SELECT 1 FROM messages m WHERE m.assignment=a.id AND m.acked=0))) "
    "AND (? IN ('all','any') OR w.name=? OR a.id=?) "
    # D26: all/any cover unfinished work of active workers plus results not yet acknowledged.
    "AND (? NOT IN ('all','any') OR (w.active=1 AND a.state NOT IN ('complete','failed')) "
    "OR EXISTS (SELECT 1 FROM messages m WHERE m.assignment=a.id AND m.acked=0)) ORDER BY a.rowid"
)


# The liveness rule, written where it runs: the query is a literal, so the reader sees the whole rule.
# A team holds work its lead is still serving when it has an unfinished assignment of an active worker or a
# result that is not acknowledged yet. This is the rule the published lead-conflict requirement states
# ("that team has unfinished assignments"), so a superseded turn of an active worker still keeps the
# terminal. The CLI's own live-lead check reads the snapshot's "all" branch instead, which asks about the
# current turn of every worker: it is looser, so a call it lets through is refused here, with the message
# that names the terminal, the owner and the way out. The two readers answer different questions on
# purpose; the store's answer is the one that decides the terminal, and the CLI's check should read this
# rule (it lives in agent_runtime.py, outside this ticket's paths).
READ_MEMBERS_SQL = (
    "SELECT a.id AS assignment_id,a.worker AS worker_id,CASE WHEN a.state='complete' THEN 'complete' "
    "WHEN a.current=0 OR w.active=0 THEN 'failed' ELSE a.state END AS state,w.name,d.turn,d.mode FROM assignments a LEFT JOIN dispatch_turns d ON d.assignment=a.id "
    "JOIN workers w ON w.id=a.worker WHERE a.team=? AND {selection} ORDER BY a.rowid"
)
READ_MESSAGES_SQL = "SELECT body FROM messages WHERE team=? AND recipient=? AND acked=0"


READ_NAMED_CLAUSE = " AND assignment IN (SELECT id FROM assignments WHERE worker IN (SELECT id FROM workers WHERE team=? AND name=?) OR id=?)"


READ_PINNED_CLAUSE = " AND (assignment IN ({ids}) OR assignment IS NULL)"


def wait_fingerprint(db, team):
    """Pure team-state digest; accepts a read-only sqlite connection without a Store.

    One SELECT captures assignment states/dispatch turns and the lead inbox's
    message high-water mark in one SQLite snapshot. IDs and current flags include pending
    registrations and assignment replacement; acknowledgements never change it.
    """
    # "Stall reported and unchanged" (R128) is part of the digest so a worker's recovery reads as new work;
    # raw progress times and turns stay out, or every turn would flip "seen". Older stores lack stall_progress.
    has_stall = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stall_progress'"
    ).fetchone()
    rows = db.execute(
        "SELECT a.id,a.state,a.current,d.turn,d.mode,"
        "(SELECT MAX(seq) FROM messages WHERE team=?1 "
        "AND recipient=(SELECT lead FROM teams WHERE id=?1)),"
        + (
            "COALESCE(sp.stall_reported_for IS NOT NULL AND sp.stall_reported_for=sp.last_progress_ms,0)"
            if has_stall
            else "0"
        )
        + " FROM teams t LEFT JOIN assignments a ON a.team=t.id "
        "LEFT JOIN dispatch_turns d ON d.assignment=a.id "
        + ("LEFT JOIN stall_progress sp ON sp.assignment=a.id " if has_stall else "")
        + "WHERE t.id=?1 ORDER BY a.id",
        (team,),
    )
    payload = json.dumps([tuple(row) for row in rows], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def last_wait_state(db, team):
    """Return (reason, attention, stored fingerprint, current fingerprint), or None.

    Both reads share one read transaction, including on mode=ro connections.
    Preserve a caller's existing transaction; never initialize an older store.
    """
    own_transaction = not db.in_transaction
    if own_transaction:
        db.execute("BEGIN")
    try:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='last_wait'"
        ).fetchone():
            return None
        row = db.execute(
            "SELECT reason,attention,fingerprint FROM last_wait WHERE team=?", (team,)
        ).fetchone()
        return (*row, wait_fingerprint(db, team)) if row else None
    finally:
        if own_transaction:
            db.rollback()


class Reads:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def snapshot(self, team, recipient, target, receipt=None, ack=None):
        """Acknowledge the previous result and freeze membership before listening."""
        with self.transaction() as db:
            self.acknowledge(db, *(ack or (team, recipient, receipt)))
            return [
                dict(row)
                for row in db.execute(
                    SNAPSHOT_SQL, (team, target, target, target, target, target, target)
                )
            ]

    def unfinished_count(self, team):
        """Unfinished members of wait(all)'s snapshot, read without acknowledging anything."""
        with self.transaction() as db:
            return db.execute(
                "SELECT COUNT(*) FROM assignments WHERE state!='complete' AND id IN "
                "(SELECT assignment_id FROM (%s))" % SNAPSHOT_SQL,
                (team, "all", "all", "all", "all", "all", "all"),
            ).fetchone()[0]
