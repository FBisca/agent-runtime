#!/usr/bin/env python3
"""SessionStart hook: inject a one-line agent-runtime pointer. Never fails the session."""

import importlib.util
import os
import sys
from pathlib import Path

RESOLVER = Path(__file__).resolve().parent.parent / "scripts/agent-runtime.py"


def load_resolver():
    spec = importlib.util.spec_from_file_location("agent_runtime", RESOLVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pointer_line(cli):
    info = load_resolver().resolve(os.environ, cli)
    line = ("agent-runtime: runtime=%s role=%s parent=%s · run ~/bin/agent-runtime contract before delegating"
            % (info["runtime"], info["role"], info.get("parent", "-")))
    if info["role"] == "worker" and info.get("parent"):
        line += ' · report: hc send %s "<result>" when done or stuck' % info["parent"]
    return line


def main(argv):
    try:
        cli = "codex" if "codex" in argv[1:] else "claude"
        sys.stdin.read()
        print(pointer_line(cli))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
