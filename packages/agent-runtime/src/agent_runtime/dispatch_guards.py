"""The guards a dispatch call runs around its handler: the reports a lead has not read, the work its
team has not finished, and the store a restricted host cannot do without."""

import sqlite3

from . import agent_runtime as lib
from . import dispatch_store as dispatch
from . import host_policy
from .tool_call import ToolError

WAIT_NUDGE = "call wait(target=all) to receive their reports"


# The verbs that change the team: a lead that has not read its reports must not use them.
MUTATING = ("spawn", "message", "resume", "stop")


def undelivered_reports(store, team, lead):
    """Lead-addressed messages no receipt has delivered yet: the count the lead hooks block on.

    The rule is the store's (`dispatch_store.undelivered_count`), the one the Stop guard counts too, so a
    host that reads the guard's reason sees the number the server refuses on.
    """
    with store.transaction() as db:
        return dispatch.undelivered_count(db, team, lead)


def refuse_unread(ctx, scope, generation, caller, name):
    """Refuse a mutating verb while the caller's own team has reports it has not read; a failed count never refuses.

    The orchestrator's dispatch loop calls through `agent-runtime call --lead` and reads its reports at the
    end of every loop, so a report landing between that page and its next spawn is its routine race, not a
    nudge it should answer. A nudge-reachable session (the MCP tool surface) has no such path: the CLI's
    ancestry gate refuses an LLM-run `agent-runtime call`, and its live-lead gate refuses another generation
    of a terminal whose lead still holds reports.
    """
    if lib.FORCED_LEAD:
        return
    try:
        if ctx.actor["worker_id"]:
            team, lead = ctx.store.owned_team(scope, caller, generation), caller
        else:
            team, lead = ctx.actor["team_id"], ctx.actor["recipient"]
        pending = undelivered_reports(ctx.store, team, lead) if team else 0
    except (dispatch.StoreError, sqlite3.Error):
        return
    if pending:
        raise ToolError(
            "refusing %s: %d undelivered report%s; call wait(all) to receive %s, then retry"
            % (name, pending, "" if pending == 1 else "s", "it" if pending == 1 else "them")
        )


def lead_unfinished(name, arguments, ctx, scope, generation, caller):
    """Unfinished count over the caller's own team, or 0; a failed count never fails the call."""
    try:
        if not ctx.actor["worker_id"]:
            return ctx.store.unfinished_count(ctx.actor["team_id"])
        team = ctx.store.owned_team(
            scope, caller, generation
        )  # a worker with children leads its own team
        if not team or (name == "message" and not ctx.store.has_worker(team, arguments.get("to"))):
            return 0
        return ctx.store.unfinished_count(team)
    except (dispatch.StoreError, sqlite3.Error):
        return 0


def require_dispatch(env):
    """A restricted host needs the durable store: without it there is no identity, no attribution
    and no scope to check."""
    host = host_policy.host_of(env)
    if host_policy.restricted(host):
        raise ToolError(
            "the %s host needs the durable dispatch store: start the MCP server with "
            "AGENT_RUNTIME_DISPATCH=1" % host
        )
