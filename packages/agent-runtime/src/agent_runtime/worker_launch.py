"""The launch a spawn builds: the model, effort and role flags each CLI family takes, the role text a
backend with no role file of its own is handed, the launcher a configured profile runs behind, and the
cleanup a launcher that died during startup owes."""

import json
import os
import re
import time
from pathlib import Path

from . import agent_runtime as lib
from .hc_boundary import hc
from .tool_call import Context, ToolError

# Configured launches get at most one second to expose a diagnostic or terminal exit.
PROFILE_STARTUP_WINDOW_SECONDS = 3.0


def codex_instructions(agent="implementer"):
    path = os.path.join(os.path.expanduser("~"), ".codex", "agents", "%s.toml" % agent)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            import tomllib

            found = tomllib.loads(text)["developer_instructions"]
        except ImportError:  # Python < 3.11
            match = re.search(r'^developer_instructions\s*=\s*"""(.*?)"""', text, re.S | re.M)
            if not match:
                raise KeyError("developer_instructions")
            found = match.group(1)
        if not isinstance(found, str) or not found.strip():
            raise ValueError("developer_instructions must be a non-empty string")
        return found.strip()
    except (OSError, KeyError, ValueError) as error:
        raise ToolError("cannot read Codex %s instructions from %s: %s" % (agent, path, error))


# The CLIs that load a role file themselves: Claude reads ~/.claude/agents/<agent>.md on its own, so the
# runtime never injects the role's text for it and never takes an instructions_file for it.
ROLE_FILE_CLIS = ("claude",)


def role_path(agent, env):
    """Where a role's own file lives: the user's Claude roles, unless the operator moved them."""
    home = env.get("HOME") or os.path.expanduser("~")
    directory = env.get("AGENT_RUNTIME_ROLE_DIR") or os.path.join(home, ".claude", "agents")
    return os.path.join(directory, "%s.md" % agent)


def role_body(text):
    """The role's content, without the metadata its file carries for the CLI that loads it.

    Claude's role files open with a front matter block naming that CLI's tools, model and
    permissions. A backend that does not load the file itself has no use for the block, and the
    tools it names are not the tools that backend has."""
    stripped = text.strip()
    if stripped.startswith("---"):
        end = stripped.find("\n---", 3)
        if end != -1:
            return stripped[end + 4 :].strip()
    return stripped


def role_text(agent, env):
    """The role's content for a backend with no role file of its own.

    The user's Claude roles are the roles this machine has, so they are what such a backend reads:
    the same discipline Claude would have loaded, without the metadata Claude reads it with. A role
    file that is missing, empty or unreadable is a refusal: nothing is launched."""
    path = role_path(agent, env)
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise ToolError(
            "cannot read the %s role file %s: %s"
            % (agent, path, error.strerror or type(error).__name__)
        ) from None
    body = role_body(text)
    if not body:
        raise ToolError("the %s role file %s is empty" % (agent, path))
    return body


# The dispatch contract, for a worker whose role file does not carry it: how to report, and what a result is.
WORKER_PROTOCOL = (
    "Worker protocol: send your result with the agent-runtime report tool as "
    "report(kind, assignment_id), where kind is progress, complete, blocked or decision; a final "
    "message alone never reaches the parent. Return a compact block: STATUS: COMPLETE | BLOCKED | "
    "PARTIAL, then task id, files changed, checks (command and outcome), decisions, remaining work."
)


LEGACY_WORKER_PROTOCOL = WORKER_PROTOCOL.replace(
    "report(kind, assignment_id), where kind is progress, complete, blocked or decision",
    "report(text)",
)


# Effort levels every CLI accepts: claude --effort, codex model_reasoning_effort, pi --thinking.
# Pi knows off and minimal as well; the runtime never sends a level a CLI does not take.
EFFORTS = ("low", "medium", "high", "xhigh", "max")


AGENT_CODEX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


AGENT_CLAUDE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:-]{0,63}")


def spawn_model(arguments):
    model = arguments.get("model")
    if model is None:
        return []
    if not isinstance(model, str) or not model.strip():
        raise ToolError("model must be a model name")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in model):
        raise ToolError("model must not contain whitespace or control characters")
    if model.startswith("-"):
        raise ToolError("model must not start with '-'")
    return ["--model", model]


def spawn_effort(arguments, cli):
    """Per-task effort as a launch flag, so it never touches the CLI's saved default."""
    effort = arguments.get("effort")
    if effort is None:
        return []
    if effort not in EFFORTS:
        raise ToolError("effort must be one of %s" % ", ".join(EFFORTS))
    if cli == "claude":
        return ["--effort", effort]
    if cli == "pi":
        return ["--thinking", effort]
    return ["-c", 'model_reasoning_effort="%s"' % effort]


NO_AGENT = "none"  # the reserved agent name: no role file, no role text


def spawn_agent(arguments, cli):
    """The role the worker runs under: a name, or None for no role at all."""
    agent = arguments.get("agent")
    if agent is None:
        return None
    pattern = AGENT_CLAUDE if cli == "claude" else AGENT_CODEX
    if not isinstance(agent, str):
        raise ToolError("agent must match %s" % pattern.pattern)
    if agent == NO_AGENT:
        return None
    # Codex reads ~/.codex/agents/<agent>.toml, so it never takes ':' (path-safe names only).
    if cli == "codex" and ":" in agent:
        raise ToolError("Codex agents cannot contain ':' (Claude plugin agents only)")
    if not pattern.fullmatch(agent):
        raise ToolError("agent must match %s" % pattern.pattern)
    return agent


def spawn_instructions(arguments, env):
    """Role text from the file the caller names; None when there is none."""
    path = arguments.get("instructions_file")
    if path is None:
        return None
    if not isinstance(path, str) or not path.strip():
        raise ToolError("instructions_file must be a path")
    home = env.get("HOME") or os.path.expanduser("~")
    expanded = home + path[1:] if path.startswith("~/") else path
    if not os.path.isabs(expanded):
        raise ToolError("instructions_file must be an absolute or ~/ path: %s" % path)
    if not os.path.isfile(expanded):
        raise ToolError("instructions_file is not a file: %s" % path)
    try:
        with open(expanded, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        raise ToolError(
            "cannot read instructions_file %s: %s" % (path, error.strerror or type(error).__name__)
        )
    if not text.strip():
        raise ToolError("instructions_file is empty: %s" % path)
    return text.strip()


def owned_orphan(name, caller, env, earlier, other):
    """True only when a terminal we may have started exists under our parent and no other spawn of this name owns it.

    earlier: the address bound before our spawn registered; other(address): another live assignment bound it."""
    try:
        raw = json.loads(hc(["get", name, "--json"], env, Context()))
    except (ToolError, ValueError):
        return False
    return bool(
        isinstance(raw, dict)
        and raw.get("parent") == caller
        and raw.get("address")
        and raw.get("address") != earlier
        and not other(raw["address"])
    )


def profile_launch_error(name, env, ctx):
    """Allow a bounded startup window for a launcher diagnostic, including a delayed exit."""
    deadline = time.monotonic() + PROFILE_STARTUP_WINDOW_SECONDS
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if ctx.cancelled.is_set():
            return ToolError("cancelled")
        exited = False
        try:
            raw = json.loads(hc(["get", name, "--json"], env, ctx, min(1.0, remaining)))
            exited = isinstance(raw, dict) and lib.hc_state(raw) == "exited"
        except (ToolError, ValueError):
            pass
        remaining = deadline - time.monotonic()
        try:
            screen = (
                hc(["read", name, "--lines", "30"], env, ctx, min(1.0, remaining))
                if remaining > 0
                else ""
            )
        except ToolError:
            screen = ""
        for line in screen.splitlines():
            if line.startswith("agent-runtime-profile:"):
                return ToolError(line)
        if exited:
            return ToolError("worker %s launcher exited during startup" % name)
        ctx.cancelled.wait(min(0.05, max(0, deadline - time.monotonic())))
    return None


def profile_launcher():
    """The checkout's profile launcher; an installed runtime falls back to the command on PATH.

    The runtime lives in the member (`packages/agent-runtime/src/agent_runtime`), so the repository root
    is found by walking up to the directory that carries the launcher, not by assuming a layout.
    """
    for parent in Path(__file__).resolve().parents:
        script = parent / "bin" / "agent-runtime-profile"
        if script.is_file():
            return str(script)
    return "agent-runtime-profile"
