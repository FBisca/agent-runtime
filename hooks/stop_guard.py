#!/usr/bin/env python3
"""Claude, Codex and Pi Stop hook: refuse to end a lead's turn while it has undelivered messages or
unseen working assignments, at most three times per user work period.

The runtime decides (`agent_runtime.rhythm`); this entry only formats the answer for the host's
dialect: `{"decision": "block"}` for Claude and Codex, or the neutral `{"block", "reason"}` for Pi,
which answers on every Stop so the extension can read a decision without guessing. Fails open on
every error."""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def emit(dialect, block, reason=""):
    """Print one answer. Pi's dialect answers on every Stop; the Claude/Codex dialect only blocks, and
    spells a spent budget as one stderr note instead."""
    if dialect == "json":
        print(json.dumps({"block": block, "reason": reason}))
    elif block:
        print(json.dumps({"decision": "block", "reason": reason}))
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
    dialect = hook_lib.option(argv, "--dialect") or caps.stop_dialect
    who, found = hook_lib.lead_check()
    reason = rhythm.stop_refusal(found) if who else None
    if not reason:
        if who:
            hook_lib.reset_budget(os.environ, who)  # nothing to block on: a new period
        return emit(dialect, False)
    if caps.can_park_wait and hook_lib.running_wait_task(payload):
        return emit(
            dialect, False
        )  # a backgrounded wait(all) will deliver; no budget spent, none reset
    allowed, first_time = hook_lib.spend_budget(os.environ, who)
    if allowed:
        return emit(dialect, True, reason)
    emit(dialect, False, rhythm.BUDGET_SPENT if first_time else "")


if __name__ == "__main__":
    try:
        import hook_lib

        hook_lib.fail_open(main)
    except Exception:  # an import error also allows the stop
        pass
    sys.exit(0)
