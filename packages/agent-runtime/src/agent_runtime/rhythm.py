"""The lead's rhythm: when a turn may end, and what each harness can be asked to do between turns.

One predicate decides the Stop refusal and one table says what a harness can do, so the Claude and
Codex Stop hooks and the Pi stop dialect refuse the same state with the same reason. Hooks format the
answer for their host's dialect; they never decide it.
"""

from typing import NamedTuple


class Capabilities(NamedTuple):
    """What one harness contributes to the lead's rhythm.

    can_refuse_stop: the host acts on a refusal to end the turn.
    can_park_wait: the host can leave a wait running in the background, so a block is unnecessary.
    can_push_wake: the host can push a wake into a session that is sitting idle.
    request_cap: the extra provider requests one user work period may force (the Stop-guard budget).
    stop_dialect: how the host spells the refusal — `decision` (Claude, Codex) or `json` (Pi).
    """

    can_refuse_stop: bool
    can_park_wait: bool
    can_push_wake: bool
    request_cap: int
    stop_dialect: str


DECISION = "decision"  # Claude and Codex: {"decision": "block", "reason": ...}
JSON = "json"  # Pi: {"block": <bool>, "reason": <text>}, answered on every stop
REQUEST_CAP = 3  # extra provider requests a harness may be forced into per user work period

CAPABILITIES = {
    "claude": Capabilities(
        can_refuse_stop=True,
        can_park_wait=True,  # the Stop payload lists a backgrounded MCP wait (background_tasks)
        can_push_wake=False,
        request_cap=REQUEST_CAP,
        stop_dialect=DECISION,
    ),
    "codex": Capabilities(
        can_refuse_stop=True,
        can_park_wait=False,  # Codex payloads carry no background_tasks: no wait exemption there
        can_push_wake=False,
        request_cap=REQUEST_CAP,
        stop_dialect=DECISION,
    ),
    "pi": Capabilities(
        can_refuse_stop=True,
        can_park_wait=False,
        can_push_wake=True,  # the extension pushes a wake into an idle session instead of sleeping
        request_cap=REQUEST_CAP,
        stop_dialect=JSON,
    ),
}


def capabilities(cli):
    """The rhythm row for one harness. Every harness the runtime can resolve has one."""
    try:
        return CAPABILITIES[cli]
    except KeyError:
        raise KeyError("no rhythm capabilities for harness %r" % (cli,)) from None


REASON = "agent-runtime: %d unfinished, %d undelivered — call wait(all) before ending the turn"
BUDGET_SPENT = (
    "agent-runtime: stop guard budget spent; allowing the stop (resets on your next prompt)"
)


def stop_refusal(needs):
    """The lead's stop verdict: the reason its turn must stay open, or None when it may end.

    Undelivered messages always block; a working assignment blocks until the lead has seen a wait
    result (a timeout never counts as seen). Blocked, decision, failed and already-reported stalls
    never block. `needs` is `hook_lib.needs` (or None when the caller leads no team).
    """
    if not needs:
        return None
    if needs["undelivered"] > 0 or (needs["working"] > 0 and not needs["seen"]):
        return REASON % (needs["unfinished"], needs["undelivered"])
    return None


WORKER_REASON = (
    "agent-runtime: assignment %s is unreported — call report complete or report blocked "
    "before ending the turn"
)


def worker_refusal(assignment):
    """The worker's stop verdict: the reason its own assignment keeps its turn open, or None when it
    may end. `assignment` is the id of the caller's current assignment while it is still pending or
    working (hook_lib.worker_check reads it); a report is the way out, because a report is also how a
    worker ends an assignment."""
    return WORKER_REASON % assignment if assignment else None
