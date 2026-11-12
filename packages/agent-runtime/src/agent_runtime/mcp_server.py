#!/usr/bin/env python3
"""Stdio MCP server for the agent runtime: JSON-RPC 2.0, one JSON message per line.

stdout carries protocol JSON only; diagnostics go to stderr.
"""

import copy
import io
import json
import os
import sys
import threading

from . import (
    hc_tools,  # noqa: E402
    profiles,  # noqa: E402
)

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "agent-runtime", "version": "0.2.4"}


def schema(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required)}


def text(description):
    return {"type": "string", "description": description}


TOOLS = [
    (
        "spawn",
        "Start one agent per task and checkout; include the whole task. "
        "Returns promptly: worker_id, assignment_id; then wait(all). "
        "kind=lookup is rejected: use the CLI's subagent tool. "
        "kind=worker: implementation, long tasks, Codex. cli: built-in (claude, codex, pi) or profile; "
        "default claude; a pi lead names an authorized one.",
        schema(
            {
                "kind": {"type": "string", "enum": ["worker", "lookup"]},
                "cli": {"type": "string", "enum": list(profiles.BUILTINS)},
                "name": text("unique a-z 0-9 - _, max 32"),
                "task": text("task text"),
                "cwd": text("checkout directory"),
                "task_id": text("ready task to link"),
                "model": text("optional model"),
                "effort": {"type": "string", "enum": list(hc_tools.EFFORTS)},
                "agent": text("role: none by default; named roles need report"),
                "instructions_file": text("codex only: role text path"),
                "operation_id": text("idempotency key"),
            },
            ["kind", "name", "task", "cwd"],
        ),
    ),
    (
        "message",
        "Send text to a teammate or lead. Peer messages arrive in tool results. Never forces input past a permission dialog.",
        schema(
            {
                "to": text("teammate name/ID or lead"),
                "text": text("message"),
                "message_id": text("optional retry ID"),
            },
            ["to", "text"],
        ),
    ),
    (
        "resume",
        "Give a finished, idle or interrupted worker another assignment; returns promptly. A new spawn is not resumption. "
        "Pass task_id to keep the task. Pull-only: reports landing after this resume returns need a new wait(all).",
        schema(
            {
                "name": text("worker name"),
                "task": text("what to do next"),
                "task_id": text("task to move here"),
                "operation_id": text("idempotency key"),
            },
            ["name", "task"],
        ),
    ),
    (
        "wait",
        "Block until children report, need attention or time out (max 590s). target=all with workers returns as soon as a report is "
        "undelivered: reports, assignment states, unfinished (open work), receipt. "
        "Wait per work period; pass receipt and wait again for remaining reports. "
        "No workers left: use target=inbox for peer messages. "
        "Pull-only: reports landing after this wait returns need a new wait(all).",
        schema(
            {
                "target": text("any, all, worker name/ID, or inbox"),
                "timeout": {
                    "type": "integer",
                    "description": "seconds, default 170, max 590; dispatch allows 0",
                },
                "receipt": text("acknowledge previous batch"),
            },
            ["target"],
        ),
    ),
    (
        "status",
        "Diagnostic fallback, not the normal flow: {name, runtime, role, parent, state, turn, native, next[]}. "
        "state is working|idle|blocked|done|exited|unknown.",
        schema({"name": text("worker name or address")}, ["name"]),
    ),
    ("list", "Diagnostic: status records of the agents you started.", schema()),
    (
        "collect",
        "Diagnostic fallback: read a worker's screen after its last prompt when no report arrived. Never infer success.",
        schema({"name": text("worker name")}, ["name"]),
    ),
    (
        "stop",
        "End a worker you started, after its work is accepted.",
        schema({"name": text("worker name")}, ["name"]),
    ),
    (
        "report",
        "Worker only: send progress or result to your parent. kind=progress|complete|blocked|decision; kind and assignment_id are required under dispatch.",
        schema(
            {
                "text": text("result; max 65536 bytes"),
                "assignment_id": text("assigned ID"),
                "kind": {"type": "string", "enum": ["progress", "complete", "blocked", "decision"]},
                "message_id": text("optional retry ID"),
            },
            ["text"],
        ),
    ),
    (
        "task",
        "Shared team tasks. Claim needs finished dependencies; complete/blocked reports update the claim.",
        schema(
            {
                "action": {"type": "string", "enum": ["create", "list", "claim", "update"]},
                "task_id": text("task ID"),
                "subject": text("create text"),
                "cursor": text("list continuation"),
                "depends_on": {
                    "type": "array",
                    "maxItems": 32,
                    "items": {"type": "string", "maxLength": 64},
                    "description": "prerequisite task IDs",
                },
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "complete", "blocked"],
                },
            },
            ["action"],
        ),
    ),
]
HOST_RESULT_CHARS = (
    500000  # Claude per-tool text threshold; canonical payloads stay under 192 KiB (D22)
)
for _, _, input_schema in TOOLS:
    input_schema["properties"].setdefault("receipt", text("acknowledge prior batch"))
    input_schema["properties"].setdefault("assignment_id", text("required on first call"))
TOOL_NAMES = [name for name, _, _ in TOOLS]


def log(message):
    print("agent-runtime: %s" % message, file=sys.stderr, flush=True)


def tool_result(payload, is_error=False):
    body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    return {"content": [{"type": "text", "text": body}], "isError": is_error}


def call_tool(name, arguments, env, ctx):
    try:
        return hc_tools.call(name, arguments, env, ctx)
    except Exception as error:  # a tool failure must not kill the server
        log("%s failed: %s" % (name, error))
        return tool_result("%s failed: %s" % (name, error), True)


def rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def valid_id(request_id):
    """A JSON-RPC id is a string, a number or null; an array, object or boolean is not an id."""
    return request_id is None or (
        isinstance(request_id, (str, int, float)) and not isinstance(request_id, bool)
    )


def handle(message, env):
    """Reply dict for a request, None for notifications and stray responses."""
    if not isinstance(message, dict):
        return rpc_error(None, -32600, "invalid request")
    method, request_id = message.get("method"), message.get("id")
    if not valid_id(request_id):
        return rpc_error(None, -32600, "invalid request: id must be a string or a number")
    if not isinstance(method, str):
        if "result" in message or "error" in message:
            return None
        return rpc_error(request_id, -32600, "invalid request")
    if "id" not in message:
        return None
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return rpc_error(request_id, -32602, "params must be an object")
    try:
        return dispatch(method, request_id, params, env)
    except Exception as error:
        log("%s failed: %s" % (method, error))
        return rpc_error(request_id, -32603, "internal error")


def dispatch(method, request_id, params, env):
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        listed = copy.deepcopy(TOOLS)
        listed[0][2]["properties"]["cli"]["enum"] = profiles.cli_names(env)
        result = {
            "tools": [
                {
                    "name": n,
                    "description": d,
                    "inputSchema": s,
                    "_meta": {"anthropic/maxResultSizeChars": HOST_RESULT_CHARS},
                }
                for n, d, s in listed
            ]
        }
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            return rpc_error(request_id, -32602, "unknown tool: %s" % name)
        arguments = params.get("arguments")
        result = call_tool(
            name, arguments if isinstance(arguments, dict) else {}, env, hc_tools.Context()
        )
    else:
        return rpc_error(request_id, -32601, "method not found: %s" % method)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


MAX_CALLS = 64  # live tools/call threads of one server process
MAX_WAITS = 56  # of those, the waits: the other slots stay free for non-wait tools


def busy(request_id, reason):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": tool_result("server busy: %s; retry shortly" % reason, True),
    }


def start_call(message, env, reply, calls, session, live):
    """Run one tools/call on its own thread so waits never block ping or cancellation.

    `live` holds the threads in flight and the waits among them; a call over the cap is refused at
    once, never queued, because a queued wait would hold its caller's turn with no reply.
    """
    request_id = message["id"]
    params = message.get("params")
    is_wait = isinstance(params, dict) and params.get("name") == "wait"
    with live["lock"]:
        if len(live["threads"]) >= MAX_CALLS:
            refusal = "%d calls are in flight" % MAX_CALLS
        elif is_wait and live["waits"] >= MAX_WAITS:
            refusal = "%d waits are in flight" % MAX_WAITS
        else:
            refusal = None
            live["waits"] += is_wait
    if refusal:
        reply(busy(request_id, refusal))
        return
    ctx = hc_tools.Context(session)  # the session's state is shared by every call of this process
    calls[request_id] = ctx

    def work():
        try:
            outcome = hc_tools_dispatch(message, params, env, ctx)
        finally:
            calls.pop(request_id, None)
            with live["lock"]:
                live["threads"].discard(threading.current_thread())
                live["waits"] -= is_wait
        if outcome is not None and not ctx.cancelled.is_set():
            reply(outcome)

    thread = threading.Thread(target=work, daemon=True)
    with live["lock"]:
        live["threads"].add(thread)
    thread.start()


def hc_tools_dispatch(message, params, env, ctx):
    request_id = message["id"]
    if not isinstance(params, dict):
        return rpc_error(request_id, -32602, "params must be an object")
    name = params.get("name")
    if name not in TOOL_NAMES:
        return rpc_error(request_id, -32602, "unknown tool: %s" % name)
    arguments = params.get("arguments")
    try:
        result = call_tool(name, arguments if isinstance(arguments, dict) else {}, env, ctx)
    except Exception as error:
        log("%s failed: %s" % (name, error))
        return rpc_error(request_id, -32603, "internal error")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin=None, stdout=None, env=None):
    stdin = stdin or io.TextIOWrapper(
        sys.stdin.buffer, encoding="utf-8", errors="replace", newline=None
    )
    stdout = stdout or sys.stdout
    env = dict(os.environ if env is None else env)
    lock, calls = threading.Lock(), {}
    live = {"lock": threading.Lock(), "threads": set(), "waits": 0}
    session = {}  # what one server process shares: the session's identity is resolved once

    def reply(payload):
        with lock:
            stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
            stdout.flush()

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            reply(rpc_error(None, -32700, "parse error"))
            continue
        if (
            isinstance(message, dict)
            and message.get("method") == "notifications/cancelled"
            and "id" not in message
        ):
            params = message.get("params")
            asked = params.get("requestId") if isinstance(params, dict) else None
            ctx = calls.get(asked) if isinstance(asked, (str, int)) else None  # unhashable: no call
            if ctx:
                ctx.cancel()
            continue
        if (
            isinstance(message, dict)
            and message.get("method") == "tools/call"
            and "id" in message
            and valid_id(message["id"])
        ):
            start_call(message, env, reply, calls, session, live)
            continue
        outcome = handle(message, env)
        if outcome is not None:
            reply(outcome)
    for ctx in list(calls.values()):
        ctx.cancel()
    with live["lock"]:
        pending = list(live["threads"])
    for thread in pending:
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(serve())
