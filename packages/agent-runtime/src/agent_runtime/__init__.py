"""The agent-runtime runtime: the local runtime that gives a lead its workers.

The package is standard-library-only in production. Its public surface is small and stable:

- `agent_runtime.agent_runtime.main` — the `agent-runtime` CLI (resolve, status, list, call);
- `agent_runtime.mcp_server.serve` — the stdio MCP server;
- `agent_runtime.profiles.launch` — the profile launcher the shims use.

Everything else is internal to the runtime. The orchestrator reaches the runtime through the
public CLI/API and never imports these modules.

The package imports lazily on purpose: the profile launcher runs under whatever `python3` the
host has, so importing one submodule must not drag in the rest.
"""

__all__ = ["main"]


def main(*args, **kwargs):
    """The `agent-runtime` CLI entry point."""
    from .agent_runtime import main as _main

    return _main(*args, **kwargs)
