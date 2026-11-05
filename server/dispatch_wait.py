"""One bounded wait: durable membership/messages, IPC hints, internal reconciliation.

Lost hints recover on a 0.5s recheck interval. Each hc call in an observation
(`hc ls`, plus `hc get` per silent live member) has a 0.2s budget; SQLite retains its finite 5s busy timeout. No host/model polling is needed.
Queued nonterminal any-events require an observation on each call, including
zero-timeout replay: zero skips waiting for events, not the bounded status probe.
"""
import time

from agent_runtime import ERROR_NEXT, UNREPORTED_NEXT
from dispatch_store import STALL_NEXT
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


NEEDS_PERSON_STATES = ("unreported", "error")
FAILURE_STATES = ("exited", "finished", "interrupted", "replaced")


def persist_failures(store, observations):
    """D2: commit observed failures before the read, so the result carries the one persisted message.

    Also records hc turns for the stall clock (D14); no extra hc call is made.
    """
    store.track_progress(observations)
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
            elif runtime in NEEDS_PERSON_STATES:
                assignment["next"] = UNREPORTED_NEXT if runtime == "unreported" else ERROR_NEXT
    ended = {a["assignment_id"] for a in batch["assignments"] if turn_ended(a, observations)}
    for a in batch["assignments"]:
        # Idle is reported only after this call saw the runtime state (failure wins), and never over an
        # attention that already carries its own next step (unreported/error/ended turn).
        if (a["assignment_id"] not in observations or a.get("runtime_state") in NEEDS_PERSON_STATES
                or a["assignment_id"] in ended or a["state"] != "working"):
            a["_stalled"] = False
        elif a.get("_stalled"):
            a["attention"], a["next"] = "stalled", STALL_NEXT
    batch["unfinished"] = [a["assignment_id"] for a in batch["assignments"] if a["state"] != "complete"]
    states = {a["state"] for a in batch["assignments"]}
    # D24: the turn ended (done/idle) with no accepted complete/blocked/decision report. A report that
    # committed before this snapshot already made the state complete/blocked/decision, so it wins (`ended` above).
    # Internal metadata consumed by Store.read in the final receipt transaction.
    batch["_attention"] = sum(
        a["state"] in ("blocked", "decision") or
        (a["state"] not in ("complete", "failed") and
         (a.get("runtime_state") in NEEDS_PERSON_STATES or a["assignment_id"] in ended or a.get("_stalled")))
        for a in batch["assignments"])
    for a in batch["assignments"]:
        a.pop("turn", None)
        a.pop("mode", None)  # internal dispatch marker, not part of the result
    if not batch["assignments"]:
        return "messages" if batch["messages"] else "empty"
    if "failed" in states:
        return "failed"
    stuck = any(a.get("runtime_state") in NEEDS_PERSON_STATES and a["state"] != "complete" for a in batch["assignments"])
    if ended or stuck or any(a.get("_stalled") for a in batch["assignments"]) or states.intersection(("blocked", "decision")):
        return "attention"
    if not batch["unfinished"]:
        return "completed"
    if target == "any" and batch["messages"]:
        return "messages"
    # D2: wait(all) returns the first undelivered report instead of waiting for every assignment.
    # A report already named by a receipt is delivered: it stays readable, but it does not wake the wait.
    if target == "all" and batch.get("_undelivered"):
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
