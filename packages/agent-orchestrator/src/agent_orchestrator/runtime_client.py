"""The runtime's public surface, as the orchestrator reaches it.

The orchestrator never imports the runtime's modules and never reads its SQLite store: it calls the
runtime's CLI (`agent-runtime call <tool> --lead <id> --json <arguments>`) and reads the JSON answer.
This module is that seam: the protocol the driver depends on and the one implementation that speaks
to the CLI.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Protocol

DEFAULT_TIMEOUT_SECONDS = 120


class RuntimeError(RuntimeError):  # noqa: A001 - the name the driver already catches
    """The runtime refused a call or answered with something that is not JSON."""


class RuntimeClient(Protocol):
    """One dispatch tool call against the runtime, over its public CLI."""

    def call(
        self, tool: str, arguments: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        """The tool's JSON answer; raises RuntimeError when the runtime refuses or fails."""
        raise NotImplementedError(
            "call(%r, %r, timeout=%r) is not implemented" % (tool, arguments, timeout)
        )


class CliRuntimeClient:
    """The runtime client that speaks to the `agent-runtime` CLI.

    The CLI is the boundary: the orchestrator dispatches, resumes, collects and cancels through it,
    and never through a private module or a database.
    """

    def __init__(self, lead: str, command: str = "agent-runtime") -> None:
        self.lead = lead
        self.command = command

    def call(
        self, tool: str, arguments: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        argv = [self.command, "call", tool, "--lead", self.lead, "--json", json.dumps(arguments)]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            raise RuntimeError(
                "agent-runtime %s failed (exit %d): %s"
                % (tool, result.returncode, (result.stderr.strip() or result.stdout.strip())[:500])
            )
        try:
            answer = json.loads(result.stdout)
        except ValueError as error:
            raise RuntimeError(
                "agent-runtime %s answered with something that is not JSON (%s)" % (tool, error)
            ) from None
        if not isinstance(answer, dict):
            raise RuntimeError(
                "agent-runtime %s answered with something that is not a JSON object" % tool
            )
        return answer
