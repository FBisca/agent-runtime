"""One tool call: the context that cancels it, the refusal it raises and the answer it returns.

Nothing here resolves a runtime or starts a process: this is the vocabulary every handler shares, so a
call's cancellation, its gap, its refusal, the timeouts it runs under and the bound on its answer are
spelled once.
"""

import json
import re
import threading

from . import dispatch_store as dispatch

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


SHORT_SECONDS = 30


WAIT_DEFAULT, WAIT_MAX = 170, 590


WAIT_DEFAULT, WAIT_MAX = 170, 590


class ToolError(Exception):
    """A tool failure whose text goes to the model."""


class DiagnosticUnavailable(Exception):
    """Dispatch cannot be consulted (store, session identity or hc role lookup failed), as opposed to a
    caller being refused. Diagnostics (status, collect, list) degrade to hc-only state on this alone."""


DIAGNOSTICS = ("status", "collect", "list")


class Context:
    """Per-call cancellation: kills the running subprocess when the client cancels.

    `session` is the state one server process shares between its calls: the session's identity is
    resolved once and kept, so a slow ancestry walk cannot change it between two calls.
    """

    def __init__(self, session=None):
        self.cancelled = threading.Event()
        self.proc = None
        self.wake = None
        self.session = session if session is not None else {}

    def cancel(self):
        self.cancelled.set()
        wake = self.wake
        if wake is not None:
            wake()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()


def dispatch_enabled(env):
    return env.get("AGENT_RUNTIME_DISPATCH") == "1"


def result(payload, is_error=False, structured=False):
    body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    response = {"content": [{"type": "text", "text": body}], "isError": is_error}
    if structured and isinstance(payload, dict):
        response["structuredContent"] = payload
    return response


def gap(what, command):
    return ToolError("gap: add %s → %s; ask the user" % (what, command))


def need(arguments, *names):
    values = []
    for name in names:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ToolError("%s is required" % name)
        values.append(value)
    return values


def worker_name(value):
    if not NAME_RE.match(value):
        raise ToolError(
            "invalid name %r: use 1-32 of a-z 0-9 - _ , not starting with - or _" % value
        )
    return value


def legacy_wait_seconds(arguments):
    value = arguments.get("timeout", WAIT_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ToolError("timeout must be a positive number of seconds")
    return int(min(value, WAIT_MAX))


def wait_seconds(arguments):
    value = arguments.get("timeout", WAIT_DEFAULT)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value < float("inf")
    ):
        raise ToolError("timeout must be a nonnegative finite number of seconds")
    return min(value, WAIT_MAX)


def bounded(out):
    """Dispatch mode: a text result whose JSON encoding exceeds half MAX_BASE_BYTES becomes an explicit
    partial structured result keeping the tail, where collect's
    result is (escaped size, not raw bytes, is what the canonical payload counts)."""
    if out.get("isError") or out.get("structuredContent") is not None:
        return out
    body = out["content"][0]["text"]
    if len(json.dumps(body)) <= dispatch.MAX_BASE_BYTES // 2:
        return out
    budget = (
        dispatch.MAX_BASE_BYTES // 2 - 1024
    )  # headroom for the partial/kept/original_bytes fields
    keep = body[
        -budget:
    ]  # escaped size is at least the char count; shrink from the head until it fits
    while len(json.dumps(keep)) > budget:
        keep = keep[max(1, (len(json.dumps(keep)) - budget) // 6) :]
    return result(
        {"result": keep, "partial": True, "kept": "tail", "original_bytes": len(body.encode())},
        structured=True,
    )
