"""The assignments table: what an assignment is bound to, resumed in, released from, and what an
observed exit turns it into.

A dead assignment never keeps an unfinished task, and a resumed one never loses the turn it already
ran; both rules are written where they run, beside the writes that enforce them."""

import json
from typing import Any

from . import dispatch_events
from .dispatch_ids import uid

BIND_NEWER_ADDRESS_SQL = (
    "SELECT 1 FROM workers w JOIN teams t ON t.id=w.team "
    "JOIN assignments a ON a.worker=w.id WHERE w.address=? AND t.scope=? "
    "AND a.current=1 AND a.rowid>? LIMIT 1"
)
BIND_NEWER_ASSIGNMENT_SQL = "SELECT 1 FROM assignments WHERE worker=? AND rowid>? LIMIT 1"
BIND_GONE_SQL = (
    "SELECT a.id,a.team,a.worker,t.lead FROM assignments a JOIN workers w ON w.id=a.worker "
    "JOIN teams t ON t.id=a.team WHERE w.address=? AND w.id!=? AND t.scope=? "
    "AND a.state NOT IN ('complete','failed')"
)


class Assignments:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def bind(self, assignment, address, scope, ack=None, turn=None, mode="fresh"):
        liveness = self.assignment_liveness(assignment, scope)
        with self.transaction() as db:
            if ack:
                self.acknowledge(db, *ack)
            self._bind(db, assignment, address, scope, turn, mode, liveness=liveness)
        dispatch_events.notify(self.directory)

    def mark_resume(self, assignment, turn, mode):
        """Record dispatch before sending, including when a report races the send response."""
        with self.transaction() as db:
            db.execute(
                "INSERT INTO resume_marks VALUES (?,?,?,?)", (assignment, self.now_ms(), turn, mode)
            )

    @staticmethod
    def _release(db, assignment):
        """D9/D12: a dead assignment never keeps an unfinished task."""
        db.execute(
            "UPDATE tasks SET status='pending',owner=NULL,assignment=NULL WHERE assignment=? AND status!='complete'",
            (assignment,),
        )

    def started_by_other(self, assignment, address):
        """True when that address is the worker's bound terminal and another live assignment is its current binder.

        Every bind makes the binding assignment current, so a fast report of ours (current) is never "other"."""
        with self.transaction() as db:
            return bool(
                db.execute(
                    "SELECT 1 FROM assignments o JOIN workers w ON w.id=o.worker WHERE w.address=? AND o.current=1 "
                    "AND o.state!='failed' AND o.id!=? AND o.worker=(SELECT worker FROM assignments WHERE id=?)",
                    (address, assignment, assignment),
                ).fetchone()
            )

    def assignment_info(self, assignment):
        with self.transaction() as db:
            return self.assignment_view(db, assignment)

    @staticmethod
    def assignment_view(db, assignment):
        found = db.execute(
            "SELECT a.state,a.current,a.worker,w.name,w.active FROM assignments a "
            "JOIN workers w ON w.id=a.worker WHERE a.id=?",
            (assignment,),
        ).fetchone()
        view = {"assignment_id": assignment}
        if not found:
            return dict(
                view,
                state="unknown",
                reason="the assignment is no longer in the store: the operation cannot be resumed, "
                "so start the work again with a new operation_id",
            )
        view.update(worker_id=found["worker"], name=found["name"])
        state = found["state"]
        if state in ("complete", "failed", "pending"):
            return dict(view, state=state)
        if state in ("working", "blocked", "decision"):
            if not found["current"] or not found["active"]:
                return dict(
                    view, state="failed"
                )  # superseded or its terminal is gone, as read() says
            if state == "working":
                reported = db.execute(
                    'SELECT 1 FROM messages WHERE assignment=? AND body LIKE \'%"kind":"progress"%\'',
                    (assignment,),
                ).fetchone()
                state = "working" if reported else "bound"
            return dict(view, state=state)
        return dict(
            view,
            state="unknown",
            reason="the assignment is in state %r, which this runtime does not know: "
            "check it with status, or start again with a new operation_id" % state,
        )

    def assignment_state(self, assignment):
        with self.transaction() as db:
            row = db.execute("SELECT state FROM assignments WHERE id=?", (assignment,)).fetchone()
            return row[0] if row else None

    def fail_observed(self, failures):
        """D2: persist observed exit/interrupt/replacement once: failed state, one lead message, task released.

        failures maps assignment id to the observed runtime state. Assignments already complete/failed are
        skipped, so repeated waits and restarts add nothing.
        """
        wrote = False
        with self.transaction() as db:
            for assignment, runtime in failures.items():
                gone = db.execute(
                    "SELECT a.id,a.team,a.worker,a.current,t.lead FROM assignments a JOIN teams t ON t.id=a.team "
                    "WHERE a.id=? AND a.state NOT IN ('complete','failed')",
                    (assignment,),
                ).fetchone()
                if not gone:
                    continue
                wrote = True
                db.execute("UPDATE assignments SET state='failed' WHERE id=?", (gone["id"],))
                self._release(db, gone["id"])
                for old in db.execute(
                    "SELECT id FROM assignments WHERE worker=? AND id!=? AND state NOT IN ('complete','failed')",
                    (gone["worker"], gone["id"]),
                ).fetchall():  # superseded by a resume
                    db.execute("UPDATE assignments SET state='failed' WHERE id=?", (old[0],))
                    self._release(db, old[0])
                if not gone["current"]:
                    continue  # superseded by a resume: failed and released silently, one message per terminal exit
                envelope = {
                    "message_id": uid(),
                    "team_id": gone["team"],
                    "worker_id": gone["worker"],
                    "assignment_id": gone["id"],
                    "sender": gone["worker"],
                    "recipient": gone["lead"],
                    "kind": "failed",
                    "text": "worker terminal %s before completion" % runtime,
                }
                db.execute(
                    "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
                    (
                        envelope["message_id"],
                        gone["team"],
                        gone["lead"],
                        gone["id"],
                        json.dumps(envelope, separators=(",", ":")),
                    ),
                )
        if wrote:
            dispatch_events.notify(self.directory)
