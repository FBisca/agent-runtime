#!/usr/bin/env python3
"""The stdio MCP server's entry point.

The runtime lives in the `agent-runtime` member (`packages/agent-runtime/src/agent_runtime`).
This file keeps the documented path working: the Claude plugin's `.mcp.json` runs it, and the
installer's `agent-runtime-mcp` shim execs it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))

from agent_runtime.mcp_server import serve  # noqa: E402

if __name__ == "__main__":
    sys.exit(serve())
