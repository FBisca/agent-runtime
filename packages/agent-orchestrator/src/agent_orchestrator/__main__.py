"""`python -m agent_orchestrator` runs the CLI: the same commands the skill's script offered."""

from .driver import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
