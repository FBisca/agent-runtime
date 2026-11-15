#!/usr/bin/env python3
"""Claude, Codex and Pi Stop hook: refuse to end a lead's turn while it has undelivered messages or
unseen working assignments, at most three times per user work period.

The runtime decides (`agent_runtime.rhythm`); this entry only formats the answer for the host's
dialect: `{"decision": "block"}` for Claude and Codex, or — when the host asks for it with
`--dialect json` — the neutral `{"block": <bool>, "reason": <text>}` on every Stop, so the Pi
extension can read a decision instead of inferring one from silence. Without an explicit dialect a
host keeps its block-or-silence shape. Fails open on every error."""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def emit(dialect, block, reason="", answered=False):
    """Print one answer. A block is always spelled: `decision` for Claude and Codex, `block`/`reason`
    for `json`. An allowed Stop is answered only when the host asked for the neutral dialect, so a
    host that reads stdout keeps the block-or-silence shape its own hook expects; otherwise a spent
    budget is the one line on stderr. Each dialect carries that note in one channel: the answer's
    `reason` for json, stderr for decision."""
    if block:
        shape = (
            {"block": True, "reason": reason}
            if dialect == "json"
            else {"decision": "block", "reason": reason}
        )
        print(json.dumps(shape))
    elif dialect == "json" and answered:
        print(json.dumps({"block": False, "reason": reason}))
    elif reason:
        print(reason, file=sys.stderr)


def main(argv=None):
    import hook_lib  # inside the fail-open path: an import error must not fail the host
    from agent_runtime import rhythm

    argv = list(sys.argv[1:] if argv is None else argv)
    payload = hook_lib.read_payload()
    if not payload or payload.get("hook_event_name") not in (None, "Stop"):
        return
    caps = rhythm.capabilities(hook_lib.harness_cli(argv))
    explicit = hook_lib.option(argv, "--dialect")
    dialect = explicit or caps.stop_dialect
    who, found = hook_lib.lead_check()
    lead = found is not None
    if not who:
        reason = None
    elif lead:
        reason = rhythm.stop_refusal(found)
    else:  # no team owns this terminal: the session is a worker, and its own assignment is the work
        reason = rhythm.worker_refusal(hook_lib.worker_check(who))
    if not reason:
        if who:
            hook_lib.reset_budget(os.environ, who)  # nothing to block on: a new period
        return emit(dialect, False, answered=bool(explicit))
    if lead and caps.can_park_wait and hook_lib.running_wait_task(payload):
        return emit(
            dialect, False, answered=bool(explicit)
        )  # only a lead's wait(all) delivers; a worker's way out is its report, not a parked wait
    allowed, first_time = hook_lib.spend_budget(os.environ, who)
    if allowed:
        return emit(dialect, True, reason)
    emit(dialect, False, rhythm.BUDGET_SPENT if first_time else "", answered=bool(explicit))


if __name__ == "__main__":
    try:
        import hook_lib

        hook_lib.fail_open(main)
    except Exception:  # an import error also allows the stop
        pass
    sys.exit(0)
