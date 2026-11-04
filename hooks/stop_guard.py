#!/usr/bin/env python3
"""Claude Stop hook: refuse to end a lead's turn while it has undelivered messages or unseen working
assignments, at most three times per user work period. Fails open on every error."""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    import hook_lib  # inside the fail-open path: an import error must not fail the host

    payload = hook_lib.read_payload()
    if not payload or payload.get("hook_event_name") not in (None, "Stop"):
        return
    who, found = hook_lib.lead_check()
    if not who:
        return
    blocking = bool(found) and (found["undelivered"] > 0 or (found["working"] > 0 and not found["seen"]))
    if not blocking:
        hook_lib.reset_budget(os.environ, who)  # nothing to block on: a new period
        return
    if hook_lib.running_wait_task(payload):
        return  # a backgrounded wait(all) will deliver; no budget spent, none reset
    allowed, first_time = hook_lib.spend_budget(os.environ, who)
    if allowed:
        print(json.dumps({"decision": "block", "reason": "agent-runtime: %d unfinished, %d undelivered — "
                          "call wait(all) before ending the turn" % (found["unfinished"], found["undelivered"])}))
    elif first_time:
        print("agent-runtime: stop guard budget spent; allowing the stop (resets on your next prompt)",
              file=sys.stderr)


if __name__ == "__main__":
    try:
        import hook_lib
        hook_lib.fail_open(main)
    except Exception:  # an import error also allows the stop
        pass
    sys.exit(0)
