"""The rhythm: when a turn may end, and what each harness can be asked to do between turns.

One predicate decides a lead's Stop refusal and one decides a worker's — its own assignment, while it
is unreported — and one table says what a harness can do, so the Claude and Codex Stop hooks and the
Pi stop dialect refuse the same state with the same reason. Hooks read the store and format the answer
for their host's dialect; they never decide it.
"""

from typing import NamedTuple

from . import dispatch_store as dispatch


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


def worker_assignment(db, who):
    """The id of the caller's own current assignment while it is unreported, else None.

    A session is a worker, not a lead, when no team row owns its terminal; the assignment then belongs
    to the team its terminal is bound to. A report ends the obligation: complete, blocked and decision
    all leave the working state, so only a pending or working assignment keeps the turn open. A team
    whose session is dead, or whose terminal a newer team now owns, owes nothing: the caller is a new
    session at a reused terminal, not the worker that took the assignment. Accepts any read-only
    connection: the hook opens the snapshot, the runtime decides what it means."""
    scope, terminal, _generation = who
    row = db.execute(
        "SELECT a.id, a.state, a.team, t.lead, t.generation FROM assignments a "
        "JOIN workers w ON w.id=a.worker JOIN teams t ON t.id=a.team "
        "WHERE w.address=? AND w.active=1 AND a.current=1 AND t.scope=? "
        "ORDER BY a.id LIMIT 1",
        (terminal, scope),
    ).fetchone()
    if not row or row["state"] not in ("pending", "working"):
        return None
    if not dispatch.generation_alive(row["generation"]):
        return None
    owner = dispatch.owning_team(db, scope, row["lead"], own_team=row["team"])
    return row["id"] if owner is not None and owner["id"] == row["team"] else None


WORKER_REASON = (
    "agent-runtime: assignment %s is unreported — call report complete or report blocked "
    "before ending the turn"
)


def worker_refusal(assignment):
    """The worker's stop verdict: the reason its own assignment keeps its turn open, or None when it
    may end. `assignment` is the id of the caller's current assignment while it is still pending or
    working (`worker_assignment` returns it); a report is the way out, because a report is also how a
    worker ends an assignment."""
    return WORKER_REASON % assignment if assignment else None
