"""A worker's report and the team's task actions: the durable store records them with the receipt they
came with, or hc carries the text to the parent when dispatch is off."""

import json

from .hc_boundary import hc
from .team_session import NATIVE_TOOLS, hc_server, me
from .tool_call import ToolError, dispatch_enabled, gap, need, result


def legacy_report(arguments, env, ctx):
    (text,) = need(arguments, "text")
    info, _ = me(env)
    if info["runtime"] != "hc":
        raise gap("%s support for report" % info["runtime"], NATIVE_TOOLS[info["runtime"]])
    if info["role"] != "worker" or not info.get("parent"):
        raise ToolError("report is for hc workers; a lead reads results with collect")
    hc(["send", info["parent"], text], env, ctx)
    return result("reported to %s" % info["parent"])


def report(arguments, env, ctx):
    if not dispatch_enabled(env):
        return legacy_report(arguments, env, ctx)
    text, assignment = need(arguments, "text", "assignment_id")
    info, caller = me(env)
    if info["runtime"] != "hc":
        raise gap("%s support for report" % info["runtime"], NATIVE_TOOLS[info["runtime"]])
    if info["role"] != "worker" or not info.get("parent"):
        raise ToolError("report is for hc workers with a registered assignment")
    scope = hc_server(env)
    if ctx.cancelled.is_set():
        raise ToolError("cancelled")
    try:
        raw = json.loads(hc(["get", caller, "--json"], env, ctx))
    except ValueError:
        raise ToolError("cannot verify report sender")
    if not isinstance(raw, dict) or raw.get("parent") != info["parent"]:
        raise ToolError("report sender changed during resolution")
    return result(
        ctx.store.publish(
            scope,
            caller,
            info["parent"],
            assignment,
            text,
            arguments.get("kind"),
            arguments.get("message_id"),
            caller_name=raw.get("name"),
            ack=ctx.ack,
            generation=ctx.generation,
        ),
        structured=True,
    )


def task(arguments, env, ctx):
    if not dispatch_enabled(env):
        raise ToolError("task requires AGENT_RUNTIME_DISPATCH=1")
    (action,) = need(arguments, "action")
    return result(ctx.store.task(ctx.actor, action, arguments, ack=ctx.ack), structured=True)
