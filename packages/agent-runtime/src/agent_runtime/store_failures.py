"""What a store failure leaves behind, and how a pending assignment is settled after a step failed.

A handler whose external effect already happened cannot undo it: it fails the assignment instead and
says what happened, so the caller knows whether a retry is safe.
"""

import sqlite3

from . import dispatch_store as dispatch
from .tool_call import ToolError

# What a store failure leaves behind, by tool, when it escapes the handler before the tool's external effect:
# every pending change was a transaction the store already rolled back, so retrying the call is safe.
STORE_DOWN = {
    "spawn": "nothing was started",
    "resume": "nothing was sent",
    "message": "the message was not delivered",
    "report": "the report was not recorded",
    "stop": "nothing was stopped",
    "task": "the task was not changed",
}


def store_down(name, error):
    return ToolError("store failure (%s): %s; retry the %s" % (error, STORE_DOWN[name], name))


def settle(store, assignment, **kwargs):
    """Fail an assignment after its launch or send went wrong; returns the store error that stopped it, never raises.

    The caller is already reporting a failure of its own: a second one must not replace it."""
    try:
        store.fail(assignment, **kwargs)
    except (sqlite3.Error, dispatch.StoreError) as error:
        return error
    return None


def row_state(raw):
    status = raw.get("status")
    return status.get("state") if isinstance(status, dict) else status
