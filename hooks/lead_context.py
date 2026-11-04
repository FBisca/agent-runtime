#!/usr/bin/env python3
"""Claude SessionStart (compact/clear) and UserPromptSubmit hook: remind a lead of unseen runtime work."""

import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONTEXT_SOURCES = ("compact", "clear")


def reminder(payload, hook_lib, env=None):
    """The one context line, or None."""
    event = payload.get("hook_event_name")
    if event == "SessionStart" and payload.get("source") not in CONTEXT_SOURCES:
        return None
    if event not in ("SessionStart", "UserPromptSubmit"):
        return None
    found = hook_lib.lead_needs(env)
    if not found:
        return None
    if found["undelivered"] > 0 or (found["working"] > 0 and not found["seen"]):
        return "agent-runtime: %d unfinished, %d undelivered — call wait(all)" % (found["unfinished"],
                                                                                found["undelivered"])
    if found["seen"] and found["attention"] > 0:
        return "agent-runtime: %d workers need you — check status" % found["attention"]
    return None


def main():
    import hook_lib  # inside the fail-open path: an import error must not fail the host

    payload = hook_lib.read_payload()
    line = reminder(payload, hook_lib) if payload else None
    if line:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": payload["hook_event_name"],
                                                 "additionalContext": line}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail open: any error, including an import error, prints nothing
        pass
    sys.exit(0)
