#!/usr/bin/env python3
"""SubagentStart hook: tell subagents and in-process teammates to stay read-only on runtime commands."""

import json
import sys

CONTEXT = ("Subagents and in-process teammates run only read-only hc commands (get, ls, read, wait, "
           "file read…) and never herdmates or the contract's spawn/message/stop verbs; report through "
           "your return value or SendMessage.")


def main():
    try:
        sys.stdin.read()
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                                 "additionalContext": CONTEXT}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
