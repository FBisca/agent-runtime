"""The stall clock: the idle budget, the resume grace, the words a stalled member is given and the one
reading of the store's clock that turns progress into an age.

The readers that report a stall run inside `Store.read` and `Store.track_progress`, which are over a
ceiling and stay at the store's own path; they import these names back from here."""

from typing import Any

STALL_NEXT = (
    "no progress for 15 minutes: collect it, message it, ask the user, or stop it; "
    "the runtime never stops it or marks it complete"
)
STALL_MS = 15 * 60 * 1000
RESUME_GRACE_MS = 60 * 1000
NOT_STARTED_NEXT = (
    "resume has not started: collect the worker's screen or stop it if no longer needed; "
    "the runtime never resends the task"
)


class Stall:
    def __getattr__(self, name) -> Any:
        """The store behind this mixin: the schema, the transactions and the other families are the
        Store's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def now_ms(self):
        return int(self.clock() * 1000)
