"""One bounded wait: durable membership/messages, IPC hints, internal reconciliation.

Lost hints recover on a 0.5s recheck interval. Each hc observation has a 0.2s
budget; SQLite retains its finite 5s busy timeout. No host/model polling is needed.
Queued nonterminal any-events require an observation on each call, including
zero-timeout replay: zero skips waiting for events, not the bounded status probe.
"""
import time

from dispatch_events import Listener

RECONCILE_INTERVAL = 0.5
OBSERVE_TIMEOUT = 0.2


def turn_ended(assignment, observations):
    """Working assignment whose terminal is done/idle after at least one turn finished since dispatch.

    The turn counter counts finished turns, so a just-started CLI is idle at its recorded turn (usually 0)
    and is not ended. Recorded turn defaults to 0; a resume of a mid-turn worker (mode working) must also
    outlast the turn that was already running. No observed turn fails safe to no attention.
    """
    if assignment["state"] != "working" or observations.get(assignment["assignment_id"]) not in ("done", "idle"):
        return False
    seen = observations.get((assignment["assignment_id"], "turn"))
    recorded = assignment.get("turn") or 0
    return seen is not None and seen > recorded + (assignment.get("mode") == "working")


FAILURE_STATES = ("exited", "finished", "interrupted", "replaced")


def persist_failures(store, observations):
    """D2: commit observed failures before the read, so the result carries the one persisted message."""
    failures = {k: v for k, v in observations.items() if isinstance(k, str) and v in FAILURE_STATES}
    if failures:
        store.fail_observed(failures)


def reason(batch, target, observations):
    for assignment in batch["assignments"]:
        runtime = observations.get(assignment["assignment_id"])
        if runtime and assignment["state"] != "complete":
            assignment["runtime_state"] = runtime
            if runtime in FAILURE_STATES:
                assignment["state"] = "failed"
            elif runtime == "blocked":
                assignment["state"] = "blocked"
    batch["unfinished"] = [a["assignment_id"] for a in batch["assignments"] if a["state"] != "complete"]
    states = {a["state"] for a in batch["assignments"]}
    # D24: the turn ended (hc done/idle) with no accepted complete/blocked/decision report. A report that
    # committed before this snapshot already made the state complete/blocked/decision, so it wins.
    ended = any(turn_ended(a, observations) for a in batch["assignments"])
    for a in batch["assignments"]:
        a.pop("turn", None)
        a.pop("mode", None)  # internal dispatch marker, not part of the result
    if not batch["assignments"]:
        return "messages" if batch["messages"] else "empty"
    if "failed" in states:
        return "failed"
    if ended or states.intersection(("blocked", "decision")):
        return "attention"
    if not batch["unfinished"]:
        return "completed"
    if target == "any" and batch["messages"]:
        return "messages"
    return None


def wait(store, team, recipient, target, timeout, receipt, ctx, observe, inbox=False):
    deadline = time.monotonic() + timeout
    members = store.snapshot(team, recipient, "inbox" if inbox else target, receipt, ack=getattr(ctx, "ack", None))
    if inbox:
        members = []  # Inbox delivery never waits on teammates' assignment completion.
    assignment_ids = [m["assignment_id"] for m in members]
    observations, reconcile_at = {}, 0
    observed, needs_observation = False, False
    def ready(batch):
        nonlocal needs_observation
        candidate = ("messages" if batch["messages"] else None) if inbox else reason(batch, target, observations)
        needs_observation = not inbox and candidate == "messages" and (not observed or time.monotonic() >= reconcile_at)
        if needs_observation:
            return None  # Do not issue a receipt before reconciling runtime failure.
        return candidate or ("timeout" if time.monotonic() >= deadline else None)
    # Register before re-reading: a publication during registration cannot be lost.
    with Listener(store.directory) as listener:
        ctx.wake = listener.wake
        try:
            while True:
                if ctx.cancelled.is_set():
                    raise InterruptedError("cancelled")
                batch = store.read(team, recipient, target, assignment_ids=assignment_ids, ready=ready, inbox=inbox)
                if batch["reason"] is not None:
                    return batch
                remaining = deadline - time.monotonic()
                if needs_observation:
                    budget = min(OBSERVE_TIMEOUT, remaining) if remaining > 0 else OBSERVE_TIMEOUT
                    observations = observe(members, budget)
                    persist_failures(store, observations)
                    observed = True
                    reconcile_at = time.monotonic() + RECONCILE_INTERVAL
                    continue  # Finalize a fresh atomic snapshot after the bounded probe.
                if remaining <= 0:
                    continue
                if inbox:
                    listener.wait(min(remaining, RECONCILE_INTERVAL))
                    continue
                now = time.monotonic()
                if now >= reconcile_at:
                    observations = observe(members, min(OBSERVE_TIMEOUT, remaining))
                    persist_failures(store, observations)
                    observed = True
                    reconcile_at = time.monotonic() + RECONCILE_INTERVAL
                    continue  # Recheck commits that raced the hc observation.
                listener.wait(min(remaining, reconcile_at - now))
        finally:
            ctx.wake = None
