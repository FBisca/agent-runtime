"""The callers, workers and teams a session is: who may act for a team, which worker it holds, and what
stops a worker holding one.

This is the authorization seam: every verb that writes passes through `authorize`, and the rules it
asks are the terminal-ownership rule in `dispatch_ownership.py` and the assignment on the worker."""

from typing import Any

from . import dispatch_events
from .dispatch_errors import StoreError
from .dispatch_ownership import alive_verdicts, owning_team, terminal_owned

ACTOR_SQL = (
    "SELECT a.id,a.state,w.id AS worker,t.id AS team FROM assignments a "
    "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team "
    "WHERE t.scope=? AND t.lead=? AND ((w.address=? AND w.active=1 AND a.current=1) "
    "OR (a.id=? AND a.state='pending' AND w.name=?)) ORDER BY a.rowid DESC LIMIT 1"
)


class Callers:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def terminal_liveness(self, scope, lead):
        """The process evidence for a terminal's team rows, gathered outside the write transaction.

        No `ps` runs while the store holds its write lock: the readers that need the evidence ask
        for it here and hand the verdict to the rule that decides."""
        with self.reading() as db:
            rows = db.execute(
                "SELECT generation FROM teams WHERE scope=? AND lead=?", (scope, lead)
            ).fetchall()
        return alive_verdicts((row["generation"] for row in rows), self.alive)

    def assignment_liveness(self, assignment, scope):
        """The process evidence for the terminal an assignment belongs to."""
        with self.reading() as db:
            row = db.execute(
                "SELECT t.lead FROM assignments a JOIN teams t ON t.id=a.team WHERE a.id=?",
                (assignment,),
            ).fetchone()
        return self.terminal_liveness(scope, row["lead"]) if row else {}

    def worker_address(self, team, name):
        with self.transaction() as db:
            row = db.execute(
                "SELECT address FROM workers WHERE team=? AND name=?", (team, name)
            ).fetchone()
            return row[0] if row else None

    def pin(self, pin):
        """Commit a deferred first-contact pin on its own (receipt-free reads skip the inbox transaction)."""
        with self.transaction() as db:
            self._pin(db, *pin)

    def _pin(self, db, scope, address, generation, team):
        pinned = db.execute(
            "SELECT team FROM callers WHERE scope=? AND address=? AND generation=?",
            (scope, address, generation),
        ).fetchone()
        if pinned and pinned[0] != team:
            raise StoreError("stale caller session cannot adopt another team generation")
        db.execute(
            "INSERT OR IGNORE INTO callers VALUES (?,?,?,?)", (scope, address, generation, team)
        )

    def authorize(self, db, actor, pending=False, liveness=None):
        terminal = actor["parent"] or actor["address"]
        owner = owning_team(
            db, actor["scope"], terminal, own_team=actor["team_id"], liveness=liveness
        )
        if not owner or owner["id"] != actor["team_id"]:
            raise StoreError(
                "stale team generation: "
                + terminal_owned(terminal, owner["generation"] if owner else "none")
            )
        if actor["worker_id"] and not pending:
            row = db.execute(
                "SELECT 1 FROM workers w JOIN assignments a ON a.worker=w.id "
                "WHERE w.id=? AND w.address=? AND w.active=1 AND a.id=? AND a.current=1",
                (actor["worker_id"], actor["address"], actor["assignment_id"]),
            ).fetchone()
            if not row:
                raise StoreError("stale worker assignment")

    def has_worker(self, team, target):
        with self.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM workers WHERE team=? AND active=1 AND (name=? OR id=?)",
                    (team, target, target),
                ).fetchone()
                is not None
            )

    def team_worker(self, team, target):
        """True when the worker belongs to this team, active or not: the ownership scope of a host."""
        with self.transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM workers WHERE team=? AND (name=? OR id=?) LIMIT 1",
                    (team, target, target),
                ).fetchone()
                is not None
            )

    def stopped(self, team, name, ack=None):
        with self.transaction() as db:
            if ack:
                self.acknowledge(db, *ack)
            worker = db.execute(
                "SELECT id FROM workers WHERE team=? AND name=?", (team, name)
            ).fetchone()
            if not worker:
                raise StoreError("worker is not registered in this team")
            db.execute("UPDATE workers SET active=0 WHERE id=?", (worker[0],))
            for row in db.execute(
                "SELECT id FROM assignments WHERE worker=? AND state!='complete'", (worker[0],)
            ).fetchall():
                self._release(db, row[0])
            db.execute(
                "UPDATE assignments SET state='failed' WHERE worker=? AND state!='complete'",
                (worker[0],),
            )
        dispatch_events.notify(self.directory)

    def owned_team(self, scope, lead, generation):
        """The team this caller leads, or None; never creates one."""
        with self.transaction() as db:
            row = db.execute(
                "SELECT id FROM teams WHERE scope=? AND lead=? AND generation=?",
                (scope, lead, generation),
            ).fetchone()
            return row[0] if row else None
