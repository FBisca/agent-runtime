"""The agent-runtime CLI tests' shared paths and markers; pytest does not collect this module."""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "server" / "agent_runtime.py"
BIN = SCRIPT

# A generation whose process is gone: no process carries that pid, so the row is provably dead.
DEAD_GENERATION = "999999999:Wed Nov  4 10:00:00 2026"
