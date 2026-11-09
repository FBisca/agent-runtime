"""Pi's MCP registration: the runtime owns the entry a Pi worker needs to report.

A Pi worker reaches its parent through the agent-runtime MCP server, and it finds that server the way
Pi finds every server: in the user-level `mcp.json`. Pi reads the project configuration only in a
trusted project, and the tracked `.mcp.json` of a repository is Claude Code's format, so the user-level
file is the only source of the report tool. Lose it and the worker finishes the work and never reports:
the ticket parks as unreported and nothing says why.

The entry names an absolute path, and the path is into the checkout that runs the runtime: move the
checkout and every Pi worker goes quiet the same way. So the runtime writes the entry itself, before it
starts a Pi worker, and the path it writes is always the running runtime's own.
"""

import json
import os
import tempfile
from pathlib import Path

SERVER = "agent-runtime"  # the name Pi's tools are namespaced with
TIMEOUT = 600  # the tool timeout the installer gives the server elsewhere
DISPATCH = {"AGENT_RUNTIME_DISPATCH": "1"}


class PiMcpError(Exception):
    """Pi's MCP configuration cannot be read, parsed or written."""


def config_dir(env):
    """The directory Pi reads its user-level configuration from."""
    override = env.get("PI_CODING_AGENT_DIR")
    if override:
        return Path(override)
    home = env.get("HOME") or os.path.expanduser("~")
    return Path(home) / ".pi" / "agent"


def config_file(env):
    """The file Pi reads its user-level MCP servers from."""
    return config_dir(env) / "mcp.json"


def server_command():
    """The command and the arguments that start this runtime's MCP server.

    A checkout runs its own `server/mcp_server.py`, so the worker talks to the same runtime as the
    lead. An installed runtime has no checkout around it: the installer's shim is the server."""
    for parent in Path(__file__).resolve().parents:
        script = parent / "server" / "mcp_server.py"
        if script.is_file():
            return "python3", [str(script)]
    return "agent-runtime-mcp", []


def entry():
    """The server entry a Pi worker needs: this runtime's server, with dispatch on and direct exposure."""
    command, args = server_command()
    return {
        "command": command,
        "args": args,
        "env": dict(DISPATCH),
        "exposure": "direct",
        "timeout": TIMEOUT,
    }


def _read(path):
    """The configuration the file holds: an empty one when the file is not there yet."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise PiMcpError(
            "cannot read Pi's MCP configuration %s: %s"
            % (path, error.strerror or type(error).__name__)
        ) from None
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise PiMcpError(
            "Pi's MCP configuration %s is not valid JSON: %s" % (path, error)
        ) from None
    if not isinstance(data, dict):
        raise PiMcpError("Pi's MCP configuration %s is not an object" % path)
    return data


def _write(path, data):
    """The configuration, written atomically: a half-written file leaves every Pi worker without a tool."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2) + "\n"
    handle, temporary = tempfile.mkstemp(prefix=".mcp-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def ensure(env):
    """Make sure Pi's user-level configuration carries this runtime's MCP server.

    The other servers in that file are the operator's and stay as they are. The path in the entry is
    the running runtime's own, so a checkout that moves is followed by the next spawn."""
    path = config_file(env)
    data = _read(path)
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
    if not isinstance(servers, dict):
        raise PiMcpError("Pi's MCP configuration %s has no 'mcpServers' object" % path)
    wanted = entry()
    if servers.get(SERVER) == wanted:
        return path
    servers[SERVER] = wanted
    data["mcpServers"] = servers
    try:
        _write(path, data)
    except OSError as error:
        raise PiMcpError(
            "cannot write Pi's MCP configuration %s: %s"
            % (path, error.strerror or type(error).__name__)
        ) from None
    return path
