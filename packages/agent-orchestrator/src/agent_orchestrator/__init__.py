"""The typed orchestration engine: Pydantic contracts over YAML policy.

The orchestrator drives workers through the runtime's public CLI and never touches the runtime's
store. Its public surface:

- `agent_orchestrator.driver.main` — the `agent-orchestrator` CLI (the same commands the skill used);
- `agent_orchestrator.contracts` — the versioned JSON handoff contracts;
- `agent_orchestrator.routes` — the typed route policy (JSON or YAML);
- `agent_orchestrator.runtime_client` — the seam to the runtime.
"""

__all__ = ["main"]


def main(*args, **kwargs):
    """The `agent-orchestrator` CLI entry point."""
    from .driver import main as _main

    return _main(*args, **kwargs)
