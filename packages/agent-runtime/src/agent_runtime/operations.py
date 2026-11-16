"""The operation_id books: the key a caller reuses, the socket owner it is bound to, the fingerprint a
replay has to match, and where a repeated spawn or resume already stands."""

import hashlib
import json
import os
import re
import sqlite3
import stat

from . import dispatch_store as dispatch
from .team_session import hc_server, require_hc
from .tool_call import ToolError, dispatch_enabled, result


def linked_task(arguments, env):
    task_id = arguments.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        raise ToolError("task_id must be a task ID")
    if task_id is not None and not dispatch_enabled(env):
        raise ToolError("task_id requires AGENT_RUNTIME_DISPATCH=1")
    return task_id


OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def operation_id(arguments, env, required=False):
    """The caller's idempotency key, or None. It lives in the durable store, so it needs dispatch."""
    key = arguments.get("operation_id")
    if key is None and not required:
        return None
    if not isinstance(key, str) or not OPERATION_ID.fullmatch(key):
        raise ToolError(
            "operation_id must be 1-128 characters of A-Z a-z 0-9 . _ : - starting with a letter or digit"
        )
    if not dispatch_enabled(env):
        raise ToolError("operation_id requires AGENT_RUNTIME_DISPATCH=1")
    return key


def operation_owner(env, caller):
    """The terminal on one bound hc socket, independent of orchestrator generations.

    Path and terminal addresses can both be reused after restart. Device/inode/ctime identify
    the socket incarnation; no session marker or path-only fallback can prove that identity.
    """
    socket = env.get("HC_TERMINAL_SOCKET")
    if not socket:
        raise OSError(
            "cannot identify the hc server instance; restore HC_TERMINAL_SOCKET and retry"
        )
    info = os.stat(socket)
    if not stat.S_ISSOCK(info.st_mode):
        raise OSError("cannot identify the hc server instance; restore its Unix socket and retry")
    return "%s\n%s\nsocket:%s:%s:%s" % (
        hc_server(env),
        caller,
        info.st_dev,
        info.st_ino,
        info.st_ctime_ns,
    )


def operation_fingerprint(verb, **fields):
    """Every argument that changes what a spawn or resume does, normalized; a replay must match it exactly."""
    body = json.dumps(dict(fields, verb=verb), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def replayed(store, identity, name, caller, address=None):
    """The answer to a repeated operation_id: the first call's assignment and where it stands now. Nothing is sent."""
    view = store.assignment_info(identity["assignment_id"])
    payload: dict = dict(
        identity,
        name=view.get("name") or name,
        address=address or store.worker_address(identity["team_id"], view.get("name") or name),
        parent=caller,
        state=view["state"],
        replayed=True,
    )
    if view["state"] == "failed":
        payload["reason"] = (
            "this operation_id already failed: start the work again with a new operation_id"
        )
    elif view.get("reason"):
        payload["reason"] = view["reason"]
    return result(payload, structured=True)


def operation(arguments, env, ctx):
    """Where a spawn or resume with this operation_id stands, for the lead that made it. Read-only: it never
    acknowledges a report, so it takes no receipt and returns no inbox."""
    key = operation_id(arguments, env, required=True)
    _, caller = require_hc(env)
    try:
        store = getattr(ctx, "store", None) or dispatch.Store(env)
        found = store.operation(operation_owner(env, caller), key)
    except (sqlite3.Error, OSError, dispatch.StoreError) as error:
        found = {
            "state": "unknown",
            "assignment_id": None,
            "reason": "the store could not be read (%s); restore access to the store and retry the lookup"
            % error,
        }
    return result(dict(found, operation_id=key), structured=True)
