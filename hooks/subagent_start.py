#!/usr/bin/env python3
"""SubagentStart hook: tell subagents not to use the delegation tools or raw hc."""

import json
import sys

CONTEXT = ("Subagents never use the agent-runtime spawn/message/resume/stop/report tools, wait, any call with "
           "assignment_id or receipt, or raw hc; only task list and status/list/collect without assignment_id "
           "are allowed. Report through your return value.")


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
