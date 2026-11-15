"""The one reading of the current UTC timestamp: `now()` is the datetime, `iso()` its string.

Callers keep the type they always had, so a module re-exports the spelling it needs rather than
formatting its own: `now = timeutil.iso` where a record was an ISO string, `now = timeutil.now`
where it was a datetime.
"""

from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc)


def iso():
    return now().isoformat()
