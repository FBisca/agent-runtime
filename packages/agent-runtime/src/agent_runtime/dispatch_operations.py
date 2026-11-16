"""The operation ledger: what an operation_id already started, so a retry replays an assignment instead
of starting a second one.

The keys live in a side table the first keyed call creates, so a store whose lead never uses an
operation_id keeps exactly the tables it had and `user_version` stays 2."""

from typing import Any

from .dispatch_limits import HC_RESTARTED


class Operations:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    # Operation keys live in a side table that the first keyed call creates: a store whose lead never uses an
    # operation_id keeps exactly the tables it had, older servers neither read nor write it, and user_version
    # stays 2. The owner is never NULL, so the primary key really is unique per owner.
    OPERATIONS = (
        "CREATE TABLE IF NOT EXISTS operations (team TEXT NOT NULL, owner TEXT NOT NULL, "
        "operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL, assignment_id TEXT NOT NULL, "
        "PRIMARY KEY(owner,operation_id))"
    )

    @staticmethod
    def previous_operation(db, owner, operation_id):
        """A prior socket's key, including legacy owners whose instance was never recorded.

        Keep the terminal prefix to distinguish lost provenance from an unused key: absent
        would invite a resend. Bound parameters and substr avoid SQL wildcard path matching.
        """
        terminal, marker, _ = owner.rpartition("\nsocket:")
        if not marker:
            return False
        prefix = terminal + "\nsocket:"
        return bool(
            db.execute(
                "SELECT 1 FROM operations WHERE operation_id=? AND owner<>? "
                "AND (owner=? OR substr(owner,1,?)=?) LIMIT 1",
                (operation_id, owner, terminal, len(prefix), prefix),
            ).fetchone()
        )

    def operation(self, owner, operation_id):
        """What this owner's operation_id stands for, read-only: {state, assignment_id, worker_id, name} with state
        absent, pending, bound, working, blocked, decision, complete, failed or unknown (then `reason` says what
        to do). Reads never acknowledge or change anything."""
        with self.transaction() as db:
            used = None
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations'"
            ).fetchone():
                used = db.execute(
                    "SELECT assignment_id FROM operations WHERE owner=? AND operation_id=?",
                    (owner, operation_id),
                ).fetchone()
                if not used and self.previous_operation(db, owner, operation_id):
                    return {"state": "unknown", "assignment_id": None, "reason": HC_RESTARTED}
            if not used:
                return {"state": "absent", "assignment_id": None}
            return self.assignment_view(db, used["assignment_id"])
