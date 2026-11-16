"""The receipt: what a page handed over, and the acknowledgement it authorizes on the next call.

A receipt names exactly the returned ids, so passing it on acknowledges that page and nothing else."""

import json
from typing import Any

from .dispatch_errors import StoreError


class Receipts:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def receipt_owner(self, receipt):
        with self.transaction() as db:
            row = db.execute(
                "SELECT team,recipient FROM receipts WHERE id=?", (receipt,)
            ).fetchone()
            if not row:
                raise StoreError("invalid receipt")
            return row["team"], row["recipient"], receipt

    def acknowledge(self, db, team, recipient, receipt):
        if receipt is None:
            return
        row = db.execute("SELECT * FROM receipts WHERE id=?", (receipt,)).fetchone()
        if not row or (row["team"], row["recipient"]) != (team, recipient):
            raise StoreError("invalid or foreign receipt")
        for message_id in json.loads(row["ids"]):
            db.execute("UPDATE messages SET acked=1 WHERE id=?", (message_id,))
