"""The runtime's public surface, as the orchestrator reaches it.

The orchestrator never imports the runtime's modules and never reads its SQLite store: it calls the
runtime's CLI (`agent-runtime call <tool> --lead <id> --json <arguments>`) and reads the JSON answer.
This module is that seam: the protocol the driver depends on and the one implementation that speaks
to the CLI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

DEFAULT_TIMEOUT_SECONDS = 120


class RuntimeError(RuntimeError):  # noqa: A001 - the name the driver already catches
    """The runtime refused a call or answered with something that is not JSON."""


# What `agent-runtime call operation` answers for an operation id. The runtime knows nothing of the orchestrator's
# intents: these are its words for where a keyed spawn or resume stands.
OPERATION_ADOPTED = ("bound", "working", "blocked", "decision", "complete", "failed")
OPERATION_STATES = ("absent", "pending", "unknown", *OPERATION_ADOPTED)


def operation_state(answer):
    """The state a lookup answered, or `unknown` for an answer this driver does not know the meaning of: an unlisted
    state is no reason to send again."""
    state = answer.get("state") if isinstance(answer, dict) else None
    return state if state in OPERATION_STATES else "unknown"


def refuses_operation_ids(error):
    """True when a runtime's refusal of a keyed send says the runtime does not take operation ids (an older runtime,
    or one without dispatch): the send did not happen, and an unkeyed one must not take its place."""
    return "operation_id" in str(error)


class ProcessResult(Protocol):
    """What a finished process gives back: its exit code and its two streams."""

    returncode: int
    stdout: str
    stderr: str


class ProcessRunner(Protocol):
    """One process, run to the end. The seam is the process, not the answer: the tests answer it in process."""

    def __call__(self, argv: list[str], timeout: float | None = None) -> ProcessResult: ...


def runtime_process(argv, timeout=None):
    """One `agent-runtime` process. The tests answer it in process (a fake module), so a driver run starts no
    process per call: the seam is the process, not the answer."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


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

    def __init__(
        self,
        lead: str,
        command: str = "agent-runtime",
        process: ProcessRunner | None = None,
    ) -> None:
        self.lead = lead
        self.command = command
        self.process: ProcessRunner = process if process is not None else runtime_process

    def call(
        self, tool: str, arguments: dict[str, Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> dict[str, Any]:
        argv = [self.command, "call", tool, "--lead", self.lead, "--json", json.dumps(arguments)]
        result = self.process(argv, timeout=timeout)
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


# The hosts an agent runs in. A process with none of these is a person's terminal or CI.
HOST_MARKERS = (
    "HC_TERMINAL_ID",
    "HERDR_PANE_ID",
    "CLAUDECODE",
    "CODEX_THREAD_ID",
    "CODEX_TOOL_CALL_ID",
    "PI_CODING_AGENT",
)


def ask_the_lead(verb):
    """The way out, for each caller: a worker cannot approve or amend, and a Lead the runtime cannot verify (a plain
    agent session with no hc ancestry) runs it where no agent marker makes its identity doubtful."""
    return (
        "%s belongs to the Lead (the person in their own session): a worker: ask the Lead to run plan %s; "
        "a Lead runs plan %s from a terminal with no agent host, or from its hc Lead terminal"
        % ("approval" if verb == "approve" else "amendment", verb, verb)
    )


def approval_refusal(env=None, process=None, verb="approve"):
    """A reason `plan approve` must refuse this caller, or None when it may go on.

    The runtime CLI is always asked (`agent-runtime resolve --json`), because it can see worker ancestry that no
    env marker shows. A worker is refused marker or not; only a caller it verifies as the Lead passes for sure. A
    failed lookup or a caller it cannot place is refused only inside a host (a marker is present), so a plain
    terminal or CI without the runtime still approves. A marker is forgeable, so it never grants anything: it only
    makes an unverified answer a refusal."""
    env = os.environ if env is None else env
    in_host = any(env.get(name) for name in HOST_MARKERS)
    run = process if process is not None else runtime_process
    home = env.get("AGENT_RUNTIME_HOME")
    script = Path(home).expanduser() / "server" / "agent_runtime.py" if home else None
    argv = [sys.executable, str(script)] if script and script.is_file() else ["agent-runtime"]
    try:
        done = run([*argv, "resolve", "--json"], timeout=10)
        if done.returncode != 0:
            raise ValueError("exit %d" % done.returncode)
        answer = json.loads(done.stdout)
        if not isinstance(answer, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        if not in_host:
            return None
        return "the runtime lookup of the caller failed (%s), so the caller is unverified — %s" % (
            error,
            ask_the_lead(verb),
        )
    caller = answer.get("caller") if isinstance(answer.get("caller"), dict) else {}
    if answer.get("role") == "worker" or caller.get("kind") == "worker":
        return "the caller is a spawned worker (parent %s) — %s" % (
            answer.get("parent", "unknown"),
            ask_the_lead(verb),
        )
    if (caller.get("kind") == "lead" and caller.get("verified") is True) or not in_host:
        return None
    return "the runtime does not verify the caller as the Lead (it reports %s) — %s" % (
        caller.get("kind", answer.get("role", "unknown")),
        ask_the_lead(verb),
    )
