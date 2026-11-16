"""The read side: the runtime's status and children, the screen a collect keeps, and the usage figures
a wait attaches to the rows it returns. None of it acknowledges a report."""

import json

from . import agent_runtime as lib
from . import usage
from .hc_boundary import hc
from .team_session import require_hc
from .tool_call import ToolError, need, result, worker_name

COLLECT_LINES = 200


HISTORY_LINES = 1000


def attach_usage(batch, env):
    """Add each returned row's measured `usage`; a row whose worker has no measurement gets no key.

    Done once, on the batch the wait returns, outside the store's transaction and the listener. The
    store budgets a worst-case dict per row, so the attached response stays under its cap.
    """
    measured = {}

    def figures(worker):
        if worker not in measured:
            try:
                measured[worker] = usage.assignment_usage(env, worker)
            except Exception:  # measuring is garnish: it never fails a wait
                measured[worker] = {}
        return measured[worker]

    for row in batch.get("assignments") or ():
        found = figures(row.get("name")).get(row.get("assignment_id"))
        if found:
            row["usage"] = found


def collect(arguments, env, ctx):
    (name,) = need(arguments, "name")
    worker_name(name)
    require_hc(env)
    try:
        app = lib.hc_app(json.loads(hc(["get", name, "--json"], env, ctx)))
    except ValueError:
        app = None
    screen = hc(["read", name, "--lines", str(HISTORY_LINES)], env, ctx)
    block = lib.last_prompt_block(screen, app) if app else []
    lines = block or screen.splitlines()  # unknown CLI or no task prompt: the whole screen
    return result("\n".join(lines[-COLLECT_LINES:]))


def status(arguments, env, _ctx):
    (name,) = need(arguments, "name")
    return result(lib.status(lib.resolve(env)["runtime"], name, env))


def list_agents(_arguments, env, _ctx):
    runtime = lib.resolve(env)["runtime"]
    if runtime != "hc":
        raise ToolError("list is hc-only; use the runtime's own tools")
    return result(lib.list_children(runtime, env))
