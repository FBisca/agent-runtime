"""Who the caller is, and the team it owns.

The runtime the caller's host supplies, the hc server instance whose scope the lead and its workers
share, the CLI session generation that keeps one team across an MCP restart, and the store that team
lives in.
"""

import os

from . import agent_runtime as lib
from . import dispatch_store as dispatch
from .tool_call import ToolError, gap

NATIVE_TOOLS = {
    "herdmates": "SendMessage and the Agent tool (named teammate), the CLI's own subagent tool",
    "native": "the CLI's own subagent tool (Claude Agent tool, Codex MultiAgent spawn tool)",
}


def me(env):
    """Caller identity, and its hc address (None outside hc)."""
    info = lib.resolve(env)
    return info, env.get("HC_TERMINAL_ID") if info["runtime"] == "hc" else None


def require_hc(env):
    info, caller = me(env)
    if info["runtime"] != "hc":
        raise gap("%s support for worker tools" % info["runtime"], NATIVE_TOOLS[info["runtime"]])
    return info, caller


def hc_server(env):
    """Runtime scope is the hc server, shared by a lead and its workers. HC_SCOPE is a per-starter
    token that differs between them, so it is only a fallback when no socket is visible."""
    socket = env.get("HC_TERMINAL_SOCKET")
    return os.path.realpath(socket) if socket else env.get("HC_SCOPE") or "default"


def dispatch_identity(env, ctx=None):
    """Scope plus CLI session generation; MCP process restarts retain the CLI ancestor.

    The generation is the nearest CLI process PID/start time alone, shared with hooks. Hosts
    without visible CLI ancestry must supply a session ID.

    A session's generation is resolved once and kept: the walk spends a `ps` per hop, and a loaded
    machine can push a hop past its budget. An identity that changes between two calls of one
    session forks the team, and the workers of the first team go stale.
    """
    cached = getattr(ctx, "identity", None)
    if cached:
        return cached  # computed once per call
    session = getattr(ctx, "session", None) if ctx is not None else None
    if session is not None and session.get("identity"):
        return session["identity"]  # the session's own identity, resolved when the session started
    scope = hc_server(env)
    generation = lib.cli_generation()
    if not generation:
        # A Pi lead is identified by its own process: an environment variable alone never stands in
        # for a session, and two hosts claiming the same session are ambiguous, not Claude.
        if lib.pi_marked(env):
            if len(lib.marked_harnesses(env)) > 1:
                raise ToolError(
                    "cannot establish the lead session: the environment claims more than "
                    "one host, so the lead identity is ambiguous"
                )
            raise ToolError(
                "cannot establish a Pi lead session: no Pi process runs above this "
                "server; start the MCP server from Pi"
            )
        # The Claude variable that is actually set is CLAUDE_CODE_SESSION_ID.
        generation = env.get("CODEX_THREAD_ID") or env.get("CLAUDE_CODE_SESSION_ID")
    if not generation:
        raise ToolError(
            "cannot establish lead session generation; host must supply a CLI session ID"
        )
    if session is not None:
        session["identity"] = (scope, generation)
    return scope, generation


def lead_store(env, ctx=None):
    if ctx is not None and getattr(ctx, "owned_team", None):
        return ctx.store, ctx.owned_team
    _, caller = require_hc(env)
    scope, generation = dispatch_identity(env, ctx)
    store = getattr(ctx, "store", None) or dispatch.Store(env)
    team = store.team(scope, caller, generation)
    if ctx is not None:
        ctx.owned_team = team
    return store, team
