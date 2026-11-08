"""Local authorization for MCP hosts whose trust model the runtime cannot inherit.

Claude Code and Codex leads are gated by their own PreToolUse hooks: the runtime treats those hooks as
defence in depth and never as the authority. Pi ships no such gate by default, so for a Pi session the
server itself decides what the session may mutate. A Pi **lead** mutates only what the operator's
policy allows; a Pi **worker** gets a fixed, narrow set of its own (it reports and reads its team, it
starts no worker and ends no worker). An operator writes the policy file and the runtime enforces it;
without one a Pi lead may only read its own team.

    $XDG_CONFIG_HOME/agent-runtime/hosts.json   (default ~/.config/agent-runtime/hosts.json)

    {"hosts": {"pi": {"mutations": true, "backends": ["codex"], "paths": ["/abs/checkout"]}}}

`mutations` allows the mutating tools at all. `backends` names the worker backends the lead may start
(built-ins or configured profiles); an empty list starts no worker, so no company-funded model is used
until the operator says so. `paths` limits where workers may run; without it only the lead's own
working directory is allowed. The file never grants a host a role: the process ancestry, the dispatch
store and the terminal own that.
"""

import json
import os
from pathlib import Path

from . import agent_runtime as lib

RESTRICTED_HOSTS = ("pi",)  # hosts the server authorizes itself
MUTATING_TOOLS = ("spawn", "message", "resume", "stop", "report")
# What a Pi worker may use: its own report, the team's reads and the peer messages. Pi ships no
# PreToolUse hook, so the server is the authority and a worker's set stays small on purpose.
WORKER_TOOLS = ("report", "message", "wait", "status", "collect", "list")
# `task` only reads when it lists.
READ_ONLY_TASK = "list"
# The tool argument that names the worker a call acts on.
SCOPED_TOOLS = {
    "status": "name",
    "collect": "name",
    "resume": "name",
    "stop": "name",
    "message": "to",
}
# Targets that name a team or the inbox rather than a worker.
NOT_A_WORKER = ("lead", "all", "any", "inbox")


class Refused(Exception):
    """The host's policy does not allow this call; the text goes to the model."""


def config_path(env):
    home = env.get("HOME") or os.path.expanduser("~")
    override = env.get("AGENT_RUNTIME_HOSTS")
    if override == "~" or (override and override.startswith("~/")):
        override = home + override[1:]
    return Path(
        override
        or Path(env.get("XDG_CONFIG_HOME") or Path(home) / ".config")
        / "agent-runtime"
        / "hosts.json"
    ).absolute()


def load(env):
    """{host: policy} for every host; {} when the operator wrote no file."""
    path = config_path(env)
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        # Do not echo file contents: the file may hold paths and names.
        raise Refused(
            "invalid host policy %s: cannot read JSON (%s)" % (path, type(error).__name__)
        )
    if not isinstance(data, dict) or not isinstance(data.get("hosts", {}), dict):
        raise Refused("invalid host policy %s: expected a hosts object" % path)
    hosts = data.get("hosts", {})
    for name, rules in hosts.items():
        if not isinstance(name, str) or not name or not isinstance(rules, dict):
            raise Refused("invalid host policy %s: every host needs an object" % path)
        if not isinstance(rules.get("mutations", False), bool):
            raise Refused("invalid host policy %s: mutations must be true or false" % path)
        backends = rules.get("backends", [])
        if not isinstance(backends, list) or not all(
            isinstance(b, str) and b.strip() for b in backends
        ):
            raise Refused("invalid host policy %s: backends must be a list of names" % path)
        paths = rules.get("paths", [])
        if not isinstance(paths, list) or not all(
            isinstance(p, str) and os.path.isabs(p) for p in paths
        ):
            raise Refused("invalid host policy %s: paths must be absolute directories" % path)
    return hosts


def rules_for(env, host):
    """The policy for one host; {} when the operator wrote none."""
    return load(env).get(host) or {}


def host_of(env):
    """The host that owns this MCP session, or None when no harness started it.

    The direct parent is the process that started this server: its trust model is the one the runtime
    applies, and the nearest harness ancestor is only the session's generation. A session that claims
    Pi in its environment without a Pi process is treated as Pi, so a failed identification is never
    silently downgraded to another host."""
    parent = lib.parent_cli()
    if parent:
        return parent
    return "pi" if lib.pi_marked(env) else None


def restricted(host):
    """True when the server authorizes this host itself."""
    return host in RESTRICTED_HOSTS


def mutating(tool, arguments):
    """True when the call changes durable state."""
    if tool in MUTATING_TOOLS:
        return True
    return tool == "task" and arguments.get("action") != READ_ONLY_TASK


def allowed_roots(env, rules):
    """The directories a worker may run in: the operator's list, else the lead's own directory."""
    paths = rules.get("paths") or []
    if not paths:
        return [os.path.realpath(os.getcwd())]
    return [os.path.realpath(path) for path in paths]


def within(path, roots):
    """True when a path is one of the roots or below one of them (symlinks resolved)."""
    try:
        target = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    for root in roots:
        base = root.rstrip(os.sep) or os.sep
        if target == base or target.startswith(base + os.sep):
            return True
    return False


def authorize(env, host, role, tool, arguments, ctx):
    """Refuse what the host's policy does not allow; raise Refused with the reason."""
    if not restricted(host):
        return
    if host == "pi" and role != "lead":
        check_worker(host, role, tool, arguments)
    rules = rules_for(env, host)
    if mutating(tool, arguments) and not rules.get("mutations"):
        raise Refused(
            "the %s host is not authorized to mutate: write %s with "
            '{"hosts": {"%s": {"mutations": true, "backends": [...], "paths": [...]}}}'
            % (host, config_path(env), host)
        )
    if tool == "spawn":
        check_spawn(env, host, rules, arguments)
    check_scope(env, host, tool, arguments, ctx)


def check_worker(host, role, tool, arguments):
    """A Pi worker reports and reads its own team: it starts no worker and ends no worker.

    A worker has no children of its own, so the tools that manage other workers stay closed to it.
    """
    if tool in WORKER_TOOLS:
        return
    if tool == "task" and arguments.get("action") == READ_ONLY_TASK:
        return
    raise Refused(
        "a %s worker may only %s: it starts no worker and ends no worker"
        % (host, ", ".join(WORKER_TOOLS))
    )


def check_spawn(env, host, rules, arguments):
    """An explicit, authorized backend and an authorized directory: the runtime never picks one."""
    backends = rules.get("backends") or []
    backend = arguments.get("cli")
    if not isinstance(backend, str) or not backend.strip():
        raise Refused(
            "spawn needs an explicit cli for a %s host: the runtime never picks a provider "
            "for you (authorized: %s)" % (host, ", ".join(backends) or "none")
        )
    if backend not in backends:
        raise Refused(
            "worker backend %r is not authorized for a %s host (authorized: %s)"
            % (backend, host, ", ".join(backends) or "none")
        )
    cwd = arguments.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        raise Refused("spawn needs a cwd for a %s host" % host)
    roots = allowed_roots(env, rules)
    if not within(cwd, roots):
        raise Refused(
            "a %s host may not start a worker in %r (authorized: %s)"
            % (host, cwd, ", ".join(roots))
        )


def check_scope(env, host, tool, arguments, ctx):
    """A host reads and mutates only the workers of its own team."""
    key = SCOPED_TOOLS.get(tool)
    if not key:
        return
    target = arguments.get(key)
    if not isinstance(target, str) or not target.strip() or target in NOT_A_WORKER:
        return  # a missing or team-wide target is the tool's own business
    actor = getattr(ctx, "actor", None)
    store = getattr(ctx, "store", None)
    team = (actor or {}).get("team_id")
    if not team or store is None:
        return
    if not store.team_worker(team, target):
        raise Refused(
            "%s %r is not a worker of your team: a %s host reaches only its own team"
            % (tool, target, host)
        )
