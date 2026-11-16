"""One sqlite connection per operation: a read-only one and a write one, both closed after the call.

Keeping them here rather than in the store's own body is what lets every family open the connection
it needs without a second copy of the pragma and the rollback rule."""

import sqlite3
from contextlib import contextmanager
from typing import Any


class Connections:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    @contextmanager
    def reading(self):
        """A read-only connection: the owner row and the work it holds are read outside the write
        transaction, so no process evidence runs while the store's write lock is held."""
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            # SQLite derives rollback-journal permissions from the private database.
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
