#!/usr/bin/env python3
"""The runtime's CLI entry point.

The runtime lives in the `agent-runtime` member (`packages/agent-runtime/src/agent_runtime`).
This file keeps the documented path working for the plugin, the installer's shims, the hooks and
the orchestrator's `$AGENT_RUNTIME_HOME/server/agent_runtime.py` lookup, so nothing outside the
repository has to change.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))

from agent_runtime.agent_runtime import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
