"""One bounded wait: durable membership/messages, IPC hints, internal reconciliation.

Lost hints recover on a 0.5s recheck interval. Each hc observation has a 0.2s
budget; SQLite retains its finite 5s busy timeout. No host/model polling is needed.
"""
import time

from dispatch_events import Listener

RECONCILE_INTERVAL = 0.5
OBSERVE_TIMEOUT = 0.2


def reason(batch, target, observations):
    for assignment in batch["assignments"]:
        runtime = observations.get(assignment["assignment_id"])
        if runtime and assignment["state"] != "complete":
            assignment["runtime_state"] = runtime
            if runtime in ("exited", "finished", "interrupted", "replaced"):
                assignment["state"] = "failed"
            elif runtime == "blocked":
                assignment["state"] = "blocked"
    batch["unfinished"] = [a["assignment_id"] for a in batch["assignments"] if a["state"] != "complete"]
    states = {a["state"] for a in batch["assignments"]}
    if not batch["assignments"]:
        return "empty"
    if "failed" in states:
        return "failed"
    if states.intersection(("blocked", "decision")):
        return "attention"
    if not batch["unfinished"]:
        return "completed"
    if target == "any" and batch["messages"]:
        return "messages"
    return None


def wait(store, team, recipient, target, timeout, receipt, ctx, observe):
    deadline = time.monotonic() + timeout
    members = store.snapshot(team, recipient, target, receipt)
    assignment_ids = [m["assignment_id"] for m in members]
    observations, reconcile_at = {}, 0
    def ready(batch):
        return reason(batch, target, observations) or ("timeout" if time.monotonic() >= deadline else None)
    # Register before re-reading: a publication during registration cannot be lost.
    with Listener(store.directory) as listener:
        ctx.wake = listener.wake
        try:
            while True:
                if ctx.cancelled.is_set():
                    raise InterruptedError("cancelled")
                batch = store.read(team, recipient, target, assignment_ids=assignment_ids, ready=ready)
                if batch["reason"] is not None:
                    return batch
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    continue
                now = time.monotonic()
                if now >= reconcile_at:
                    observations = observe(members, min(OBSERVE_TIMEOUT, remaining))
                    reconcile_at = time.monotonic() + RECONCILE_INTERVAL
                    continue  # Recheck commits that raced the hc observation.
                listener.wait(min(remaining, reconcile_at - now))
        finally:
            ctx.wake = None
