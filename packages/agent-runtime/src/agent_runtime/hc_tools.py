"""Tool implementations for the agent-runtime MCP server. Each tool resolves the runtime and runs
the runtime's command itself. Terminal mutations go through hc; durable reports use the private store."""

import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import threading
import time
from pathlib import Path

from . import agent_runtime as lib
from . import dispatch_store as dispatch
from . import dispatch_wait, host_policy, pi_mcp, profiles, usage

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
COLLECT_LINES = 200
HISTORY_LINES = 1000
SHORT_SECONDS = 30
# Configured launches get at most one second to expose a diagnostic or terminal exit.
PROFILE_STARTUP_WINDOW_SECONDS = 3.0
WAIT_DEFAULT, WAIT_MAX = 170, 590
KINDS = ("worker", "lookup")
CLIS = profiles.BUILTINS
SUBAGENT_TOOL = {"claude": "Claude's Agent tool", "codex": "Codex's MultiAgent V2 spawn tool"}
NATIVE_TOOLS = {
    "herdmates": "SendMessage and the Agent tool (named teammate), the CLI's own subagent tool",
    "native": "the CLI's own subagent tool (Claude Agent tool, Codex MultiAgent spawn tool)",
}


class ToolError(Exception):
    """A tool failure whose text goes to the model."""


class DiagnosticUnavailable(Exception):
    """Dispatch cannot be consulted (store, session identity or hc role lookup failed), as opposed to a
    caller being refused. Diagnostics (status, collect, list) degrade to hc-only state on this alone."""


DIAGNOSTICS = ("status", "collect", "list")


class Context:
    """Per-call cancellation: kills the running subprocess when the client cancels.

    `session` is the state one server process shares between its calls: the session's identity is
    resolved once and kept, so a slow ancestry walk cannot change it between two calls.
    """

    def __init__(self, session=None):
        self.cancelled = threading.Event()
        self.proc = None
        self.wake = None
        self.session = session if session is not None else {}

    def cancel(self):
        self.cancelled.set()
        wake = self.wake
        if wake is not None:
            wake()
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()


def dispatch_enabled(env):
    return env.get("AGENT_RUNTIME_DISPATCH") == "1"


def result(payload, is_error=False, structured=False):
    body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
    response = {"content": [{"type": "text", "text": body}], "isError": is_error}
    if structured and isinstance(payload, dict):
        response["structuredContent"] = payload
    return response


def gap(what, command):
    return ToolError("gap: add %s → %s; ask the user" % (what, command))


def run(argv, env, ctx, timeout=SHORT_SECONDS):
    """(returncode, stdout, stderr); ToolError on a missing binary, timeout or cancel."""
    try:
        proc = subprocess.Popen(
            argv,
            env=env,
            text=True,
            errors="replace",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as error:
        raise ToolError("cannot run %s: %s" % (argv[0], error))
    ctx.proc = proc
    deadline = time.monotonic() + timeout
    while True:
        if ctx.cancelled.is_set():
            proc.kill()
            proc.communicate()
            raise ToolError("cancelled")
        try:
            out, err = proc.communicate(timeout=0.2)
            return proc.returncode, out, err
        except subprocess.TimeoutExpired:
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise ToolError("%s timed out after %ss" % (" ".join(argv[:2]), timeout))


def hc(args, env, ctx, timeout=SHORT_SECONDS):
    code, out, err = run(["hc"] + args, env, ctx, timeout)
    if code != 0:
        raise ToolError((err.strip() or out.strip() or "hc %s failed" % args[0]))
    return out.strip()


def need(arguments, *names):
    values = []
    for name in names:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ToolError("%s is required" % name)
        values.append(value)
    return values


def worker_name(value):
    if not NAME_RE.match(value):
        raise ToolError(
            "invalid name %r: use 1-32 of a-z 0-9 - _ , not starting with - or _" % value
        )
    return value


def me(env):
    """Caller identity, and its hc address (None outside hc)."""
    info = lib.resolve(env)
    return info, env.get("HC_TERMINAL_ID") if info["runtime"] == "hc" else None


def require_hc(env):
    info, caller = me(env)
    if info["runtime"] != "hc":
        raise gap("%s support for worker tools" % info["runtime"], NATIVE_TOOLS[info["runtime"]])
    return info, caller


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


def discard(name, env):
    """Best effort: end and remove a terminal we may have started."""
    fresh = Context()
    for command in ("kill", "rm"):
        try:
            hc([command, name], env, fresh)
        except ToolError:
            pass


# What a store failure leaves behind, by tool, when it escapes the handler before the tool's external effect:
# every pending change was a transaction the store already rolled back, so retrying the call is safe.
STORE_DOWN = {
    "spawn": "nothing was started",
    "resume": "nothing was sent",
    "message": "the message was not delivered",
    "report": "the report was not recorded",
    "stop": "nothing was stopped",
    "task": "the task was not changed",
}


def store_down(name, error):
    return ToolError("store failure (%s): %s; retry the %s" % (error, STORE_DOWN[name], name))


def settle(store, assignment, **kwargs):
    """Fail an assignment after its launch or send went wrong; returns the store error that stopped it, never raises.

    The caller is already reporting a failure of its own: a second one must not replace it."""
    try:
        store.fail(assignment, **kwargs)
    except (sqlite3.Error, dispatch.StoreError) as error:
        return error
    return None


def row_state(raw):
    status = raw.get("status")
    return status.get("state") if isinstance(status, dict) else status


def hc_server(env):
    """Runtime scope is the hc server, shared by a lead and its workers. HC_SCOPE is a per-starter
    token that differs between them, so it is only a fallback when no socket is visible."""
    socket = env.get("HC_TERMINAL_SOCKET")
    return os.path.realpath(socket) if socket else env.get("HC_SCOPE") or "default"


def dispatch_identity(env, ctx=None):
    """Scope plus CLI session generation; MCP process restarts retain the CLI ancestor.

    The generation is the nearest CLI process PID/start time alone, shared with hooks. Hosts
    without visible CLI ancestry must supply a session ID.

    A session's generation is resolved once and kept: the walk spends a `ps` per hop, and a loaded
    machine can push a hop past its budget. An identity that changes between two calls of one
    session forks the team, and the workers of the first team go stale.
    """
    cached = getattr(ctx, "identity", None)
    if cached:
        return cached  # computed once per call
    session = getattr(ctx, "session", None) if ctx is not None else None
    if session is not None and session.get("identity"):
        return session["identity"]  # the session's own identity, resolved when the session started
    scope = hc_server(env)
    generation = lib.cli_generation()
    if not generation:
        # A Pi lead is identified by its own process: an environment variable alone never stands in
        # for a session, and two hosts claiming the same session are ambiguous, not Claude.
        if lib.pi_marked(env):
            if len(lib.marked_harnesses(env)) > 1:
                raise ToolError(
                    "cannot establish the lead session: the environment claims more than "
                    "one host, so the lead identity is ambiguous"
                )
            raise ToolError(
                "cannot establish a Pi lead session: no Pi process runs above this "
                "server; start the MCP server from Pi"
            )
        # The Claude variable that is actually set is CLAUDE_CODE_SESSION_ID.
        generation = env.get("CODEX_THREAD_ID") or env.get("CLAUDE_CODE_SESSION_ID")
    if not generation:
        raise ToolError(
            "cannot establish lead session generation; host must supply a CLI session ID"
        )
    if session is not None:
        session["identity"] = (scope, generation)
    return scope, generation


def lead_store(env, ctx=None):
    if ctx is not None and getattr(ctx, "owned_team", None):
        return ctx.store, ctx.owned_team
    _, caller = require_hc(env)
    scope, generation = dispatch_identity(env, ctx)
    store = getattr(ctx, "store", None) or dispatch.Store(env)
    team = store.claim_team(scope, caller, generation)
    if ctx is not None:
        ctx.owned_team = team
    return store, team


def linked_task(arguments, env):
    task_id = arguments.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        raise ToolError("task_id must be a task ID")
    if task_id is not None and not dispatch_enabled(env):
        raise ToolError("task_id requires AGENT_RUNTIME_DISPATCH=1")
    return task_id


OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


def operation_id(arguments, env, required=False):
    """The caller's idempotency key, or None. It lives in the durable store, so it needs dispatch."""
    key = arguments.get("operation_id")
    if key is None and not required:
        return None
    if not isinstance(key, str) or not OPERATION_ID.fullmatch(key):
        raise ToolError(
            "operation_id must be 1-128 characters of A-Z a-z 0-9 . _ : - starting with a letter or digit"
        )
    if not dispatch_enabled(env):
        raise ToolError("operation_id requires AGENT_RUNTIME_DISPATCH=1")
    return key


def operation_owner(env, caller):
    """The terminal on one bound hc socket, independent of orchestrator generations.

    Path and terminal addresses can both be reused after restart. Device/inode/ctime identify
    the socket incarnation; no session marker or path-only fallback can prove that identity.
    """
    socket = env.get("HC_TERMINAL_SOCKET")
    if not socket:
        raise OSError(
            "cannot identify the hc server instance; restore HC_TERMINAL_SOCKET and retry"
        )
    info = os.stat(socket)
    if not stat.S_ISSOCK(info.st_mode):
        raise OSError("cannot identify the hc server instance; restore its Unix socket and retry")
    return "%s\n%s\nsocket:%s:%s:%s" % (
        hc_server(env),
        caller,
        info.st_dev,
        info.st_ino,
        info.st_ctime_ns,
    )


def operation_fingerprint(verb, **fields):
    """Every argument that changes what a spawn or resume does, normalized; a replay must match it exactly."""
    body = json.dumps(dict(fields, verb=verb), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def replayed(store, identity, name, caller, address=None):
    """The answer to a repeated operation_id: the first call's assignment and where it stands now. Nothing is sent."""
    view = store.assignment_info(identity["assignment_id"])
    payload = dict(
        identity,
        name=view.get("name") or name,
        address=address or store.worker_address(identity["team_id"], view.get("name") or name),
        parent=caller,
        state=view["state"],
        replayed=True,
    )
    if view["state"] == "failed":
        payload["reason"] = (
            "this operation_id already failed: start the work again with a new operation_id"
        )
    elif view.get("reason"):
        payload["reason"] = view["reason"]
    return result(payload, structured=True)


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


def spawn(arguments, env, ctx):
    kind = arguments.get("kind")
    cli = arguments.get("cli") or "claude"
    if kind == "lookup":
        raise ToolError(
            "lookup is the CLI's own subagent tool, not a spawn: use %s (named workers only through spawn kind=worker)"
            % SUBAGENT_TOOL.get(cli, "the CLI's subagent tool")
        )
    if kind != "worker":
        raise gap("spawn kind=%s" % kind, "hc run --bg ...")
    profile = None
    if cli not in CLIS:
        profile = cli
        try:
            configured = profiles.load(env)
        except profiles.ProfileError as error:
            raise ToolError(str(error)) from error
        if cli not in configured:
            raise gap("unknown profile %r" % cli, "configure a profile")
        cli = configured[cli]["cli"]
        if cli not in CLIS:
            raise ToolError("profile %r has unsupported CLI family %r" % (profile, cli))
    name, task, cwd = need(arguments, "name", "task", "cwd")
    worker_name(name)
    task_id = linked_task(arguments, env)
    key = operation_id(arguments, env)
    model = spawn_model(arguments) + spawn_effort(arguments, cli)
    agent = spawn_agent(arguments, cli)
    instructions = spawn_instructions(arguments, env)
    if instructions is not None:
        if cli in ROLE_FILE_CLIS:
            raise ToolError(
                "instructions_file applies to a backend with no role file of its own; the %s family "
                "loads the role itself" % cli
            )
        if arguments.get("agent") is not None:
            raise ToolError("agent and instructions_file are mutually exclusive")
    launch_args = []
    if cli == "claude":
        try:
            launch_args = profiles.claude_launch_args(configured[profile] if profile else None, env)
        except profiles.ProfileError as error:
            raise ToolError("profile %r: %s" % (profile, error)) from error
    _, caller = require_hc(env)
    if not caller:
        raise ToolError("HC_TERMINAL_ID is not set; cannot address the report")
    store = None
    message = (
        "%s\n\nWhen done or stuck, call the agent-runtime report tool with your result." % task
    )
    protocol = WORKER_PROTOCOL if dispatch_enabled(env) else LEGACY_WORKER_PROTOCOL
    if cli == "pi":
        protocol = protocol.replace(
            "the agent-runtime report tool as report(",
            "the agent-runtime report tool `mcp__agent-runtime__report` as ",
        )
        # Pi has no role file: the role's content and the dispatch contract travel in the task itself.
        if instructions is not None:
            message = "%s\n\n%s" % (instructions, message)
        elif agent:
            message = "Role: %s\n\n%s\n\n%s" % (agent, role_text(agent, env), message)
        message += "\n\n" + protocol
    elif agent is None or instructions is not None:
        # No role file supplies the dispatch contract, so the runtime states it.
        message += "\n\n" + protocol
    if dispatch_enabled(env):
        # D4: identity and inputs are resolved before hc run, so nothing is started on an invalid spawn.
        scope, _ = dispatch_identity(env, ctx)
        store, team = lead_store(env, ctx)
    codex_profile = None
    if cli == "claude":
        program = (
            ["claude"] + (["--agent", agent] if agent else []) + launch_args + model + [message]
        )
    elif cli == "pi":
        # A Pi worker finds the report tool in Pi's own configuration: the runtime owns that entry,
        # so the path in it is the running runtime's and a moved checkout does not leave it quiet.
        try:
            pi_mcp.ensure(env)
        except pi_mcp.PiMcpError as error:
            raise ToolError(str(error)) from None
        # Interactive Pi stays available for follow-up assignments, and --approve keeps the worker
        # from stopping at a trust prompt in a folder nobody can answer in.
        program = ["pi", "--approve"] + model + [message]
    else:
        # The worker profile names the policy Codex actually runs; without it the worker runs the
        # CLI's defaults, so the profile is only passed when it is usable.
        codex_profile = usage.resolve_codex_profile(env)
        program = ["codex", "--no-daemon"]
        if codex_profile.applied:
            program += ["-p", codex_profile.name]
        preamble = (
            instructions
            if instructions is not None
            else (codex_instructions(agent) if agent else "")
        )
        program += model + ["%s\n\n%s" % (preamble, message) if preamble else message]
    if profile:
        program = [profile_launcher(), profile, str(profiles.config_path(env)), "--"] + program
    else:
        program = profiles.scrub_prefix(env, cli) + program
    if store:
        earlier = store.worker_address(
            team, name
        )  # a fast report may bind the new address before we look
        keyed = None
        if key:
            keyed = (
                operation_owner(env, caller),
                key,
                operation_fingerprint(
                    "spawn",
                    name=name,
                    cli=cli,
                    profile=profile,
                    task=task,
                    cwd=os.path.normpath(cwd),
                    model=arguments.get("model"),
                    effort=arguments.get("effort"),
                    agent=agent,
                    instructions=instructions,
                    task_id=task_id,
                ),
            )
        identity = store.register(team, name, task_id, operation=keyed)
        if identity.pop("_replay", None):
            return replayed(store, identity, name, caller)
        identity.pop("_prior", None)
        suffix = (
            " Include assignment_id=%s and kind=progress|complete|blocked|decision."
            % identity["assignment_id"]
        )
        suffix += " Include assignment_id on your first runtime call to prove your session; later non-report calls may omit it."
        program[-1] += suffix
    # Capture the checkout and time before startup can create a log or change the commit.
    base = usage.base_commit(cwd, env) if store else None
    spawned_ms = usage.now_ms() if store else None

    def record():
        """The worker's own record: the assignment it belongs to and the profile it ran under."""
        if not store:
            return
        usage.write_worker_metadata(
            env,
            name,
            assignment_id=identity["assignment_id"],
            cli=cli,
            cwd=cwd,
            base_commit=base,
            spawned_ms=spawned_ms,
            profile_applied=None if codex_profile is None else bool(codex_profile.applied),
            profile_reason=codex_profile.reason if codex_profile else None,
            profile_name=codex_profile.name if codex_profile else None,
            session_id=None,
        )

    try:
        code, out, err = run(
            [
                "hc",
                "run",
                "--bg",
                "--name",
                name,
                "--cwd",
                cwd,
                "--parent",
                caller,
                "--json",
                "--no-notify",
            ]
            + program,
            env,
            ctx,
            SHORT_SECONDS,
        )
        if (
            code != 0 and ctx.cancelled.is_set()
        ):  # cancel killed hc run mid-flight: not an hc refusal
            raise ToolError("cancelled")
    except ToolError as error:
        # Cancel/timeout may leave a terminal behind; a missing binary cannot.
        if store:
            mine = identity["assignment_id"]
            try:
                orphan = not str(error).startswith("cannot run") and owned_orphan(
                    name,
                    caller,
                    env,
                    earlier,
                    lambda address: store.started_by_other(mine, address),
                )
            except (dispatch.StoreError, sqlite3.Error):
                orphan = False  # keep the original error; fail below still runs
            # Without an orphan the terminal is not removed (hc get failed, the worker exited, or another spawn owns
            # it), so a report that landed first stands until a later wait observes the exit.
            stuck = settle(store, mine, inactive=orphan)
            if orphan:
                discard(name, env)
            if stuck:
                raise ToolError(
                    "%s; store failure (%s) left assignment %s pending%s"
                    % (error, stuck, mine, "; the terminal was removed" if orphan else "")
                ) from stuck
            if not orphan and not str(error).startswith("cannot run"):
                record()  # the terminal was not discarded: the worker may still be running
            try:
                landed = store.assignment_state(mine) == "complete"
            except (dispatch.StoreError, sqlite3.Error):
                landed = False
            if landed:
                raise ToolError("%s; the worker's result had already landed" % error)
        elif ctx.cancelled.is_set():
            discard(name, env)
        raise
    if code != 0:  # hc refused (name_taken, ...): nothing was started, so nothing is discarded
        refusal = err.strip() or out.strip() or "hc run failed"
        if store:
            stuck = settle(store, identity["assignment_id"])
            if stuck:
                raise ToolError(
                    "%s; store failure (%s) left assignment %s pending; nothing was started"
                    % (refusal, stuck, identity["assignment_id"])
                ) from stuck
        raise ToolError(refusal)
    out = out.strip()

    def undo(error):
        """Every failure after a successful hc run ends the terminal, fails the assignment, leaves the worker inactive."""
        stuck = settle(store, identity["assignment_id"], inactive=True) if store else None
        discard(name, env)  # even when the store cannot fail the assignment
        removed = "worker %s's terminal was removed" % name
        if stuck:
            raise ToolError(
                "%s; %s, but store failure (%s) left assignment %s pending"
                % (error, removed, stuck, identity["assignment_id"])
            ) from error
        if isinstance(error, sqlite3.Error):
            raise ToolError(
                "store failure (%s): %s and its assignment was failed; spawn it again"
                % (error, removed)
            ) from error
        if isinstance(error, dispatch.StoreError):
            raise dispatch.StoreError(
                "%s; %s and its assignment was failed; spawn it again" % (error, removed)
            ) from error
        raise error

    if store and ctx.cancelled.is_set():
        undo(ToolError("cancelled"))
    try:
        started = json.loads(out)
    except ValueError:
        started = {}
    sent = (started.get("first_message") or {}).get("sent") if isinstance(started, dict) else None
    if profile:
        error = profile_launch_error(name, env, ctx)
        if error:
            if store:
                undo(error)
            discard(name, env)
            raise error
    if sent is False:
        if not store:
            raise ToolError(
                "worker %s started but its task was not sent: hc read %s; no blind resend"
                % (name, name)
            )
        undo(
            ToolError(
                "worker %s started but its task was not sent and was removed; spawn it again" % name
            )
        )
    info = {"name": name, "address": None, "parent": caller}
    turn, mode = None, "fresh"
    try:
        raw = json.loads(hc(["get", name, "--json"], env, ctx))
        info.update(address=raw.get("address"), parent=raw.get("parent") or caller)
        turn = raw.get("turn")
    except (ToolError, ValueError, AttributeError):
        pass
    if not store:
        return result(info)
    if ctx.cancelled.is_set():
        undo(ToolError("cancelled"))
    address = info.get("address")
    if not address:
        undo(ToolError("worker started but address unavailable; assignment cannot accept reports"))
    try:
        store.bind(identity["assignment_id"], address, scope, getattr(ctx, "ack", None), turn, mode)
    except (dispatch.StoreError, sqlite3.Error) as error:
        undo(error)
    record()  # the worker is live and bound: only now does its record describe a running worker
    return result(dict(info, **identity), structured=True)


def require_child(name, env, ctx):
    _, caller = require_hc(env)
    try:
        raw = json.loads(hc(["get", name, "--json"], env, ctx))
    except ValueError:
        raise ToolError("cannot read %s" % name)
    if not caller or not isinstance(raw, dict) or raw.get("parent") != caller:
        raise ToolError("%s is not your child; refusing" % name)
    return raw


def send(args_before, target, text, env, ctx, timeout=SHORT_SECONDS):
    worker_name(target)
    require_child(target, env, ctx)
    out = hc(["send"] + args_before + [target, text], env, ctx, timeout)
    return out


def message(arguments, env, ctx):
    to, text = need(arguments, "to", "text")
    if dispatch_enabled(env):
        actor = ctx.actor
        if actor["worker_id"]:
            store, own_team = lead_store(env, ctx)
            if store.has_worker(own_team, to):
                actor = store.actor(actor["scope"], actor["address"], None, ctx.generation)
        return result(
            ctx.store.peer(actor, to, text, arguments.get("message_id"), ctx.ack), structured=True
        )
    return result(send([], to, text, env, ctx) or "sent")


def resume(arguments, env, ctx):
    name, task = need(arguments, "name", "task")
    task_id = linked_task(arguments, env)
    key = operation_id(arguments, env)
    if dispatch_enabled(env):
        worker_name(name)
        raw = require_child(name, env, ctx)
        if not raw.get("address"):
            raise ToolError("worker address unavailable")
        store, team = lead_store(env, ctx)
        keyed = None
        if key:
            keyed = (
                operation_owner(env, require_hc(env)[1]),
                key,
                operation_fingerprint("resume", name=name, task=task, task_id=task_id),
            )
        identity = store.register(team, name, task_id, transfer=True, operation=keyed)
        if identity.pop("_replay", None):
            return replayed(store, identity, name, raw["parent"], raw["address"])
        prior = identity.pop("_prior", None)
        prompt = (
            "%s\n\nUse assignment_id=%s for report(kind=progress|complete|blocked|decision)."
            % (task, identity["assignment_id"])
        )
        prompt += " Include assignment_id on your first runtime call to prove your session; later non-report calls may omit it."
        assignment = identity["assignment_id"]
        delivered = (
            False  # the send is the irreversible step: after it, the worker holds the assignment
        )
        try:
            mode = "ended" if row_state(raw) in ("done", "idle") else "working"
            store.mark_resume(assignment, raw.get("turn"), mode)
            out = hc(["send", name, prompt], env, ctx)
            try:
                sent = json.loads(out)
            except ValueError:
                sent = None
            if isinstance(sent, dict) and (
                sent.get("sent") is False or (sent.get("first_message") or {}).get("sent") is False
            ):
                raise ToolError("resume task was not sent; no blind resend")
            delivered = True
            if ctx.cancelled.is_set():
                raise ToolError("cancelled")
            store.bind(
                assignment,
                raw["address"],
                ctx.actor["scope"],
                ctx.ack,
                raw.get("turn"),
                mode,
            )
        except sqlite3.Error as error:
            if delivered:
                # Failing it would refuse the report of a worker that is already working on it; left pending,
                # that report binds it.
                raise ToolError(
                    "the resume task was sent to %s but store failure (%s) stopped binding assignment %s: "
                    "it is left pending and binds when %s reports; wait for that report, or stop %s and "
                    "resume it again" % (name, error, assignment, name, name)
                ) from error
            stuck = settle(store, assignment, restore=prior)
            if stuck:
                raise ToolError(
                    "store failure (%s) before the task was sent, and store failure (%s) left assignment %s "
                    "pending; nothing was sent; retry the resume" % (error, stuck, assignment)
                ) from error
            raise ToolError(
                "store failure (%s) before the task was sent: the pending assignment was failed and its "
                "task released; nothing was sent; retry the resume" % error
            ) from error
        except (ToolError, dispatch.StoreError) as error:
            stuck = settle(store, assignment, restore=prior)
            if stuck:
                raise ToolError(
                    "%s; store failure (%s) left assignment %s pending" % (error, stuck, assignment)
                ) from error
            if delivered and isinstance(error, dispatch.StoreError):
                raise dispatch.StoreError(
                    "%s; the task was already sent to %s and its assignment was failed"
                    % (error, name)
                ) from error
            raise
        return result(
            dict(identity, name=name, address=raw["address"], parent=raw["parent"]), structured=True
        )
    out = send(["--wait", "--timeout", str(WAIT_DEFAULT)], name, task, env, ctx, WAIT_DEFAULT + 10)
    return result(out or "turn ended")


def operation(arguments, env, ctx):
    """Where a spawn or resume with this operation_id stands, for the lead that made it. Read-only: it never
    acknowledges a report, so it takes no receipt and returns no inbox."""
    key = operation_id(arguments, env, required=True)
    _, caller = require_hc(env)
    try:
        store = getattr(ctx, "store", None) or dispatch.Store(env)
        found = store.operation(operation_owner(env, caller), key)
    except (sqlite3.Error, OSError, dispatch.StoreError) as error:
        found = {
            "state": "unknown",
            "assignment_id": None,
            "reason": "the store could not be read (%s); restore access to the store and retry the lookup"
            % error,
        }
    return result(dict(found, operation_id=key), structured=True)


def legacy_wait_seconds(arguments):
    value = arguments.get("timeout", WAIT_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ToolError("timeout must be a positive number of seconds")
    return int(min(value, WAIT_MAX))


def legacy_wait(arguments, env, ctx):
    (target,) = need(arguments, "target")
    seconds = legacy_wait_seconds(arguments)
    _, _ = require_hc(env)
    if target in ("any", "all"):
        argv = ["wait", "--children", target, "--timeout", str(seconds), "--json"]
    else:
        argv = ["wait", worker_name(target), "--timeout", str(seconds), "--json"]
    try:
        code, out, err = run(["hc"] + argv, env, ctx, seconds + 10)
        text = out.strip() or err.strip()
        try:
            news = {"exit": code, "news": json.loads(text)}
        except ValueError:
            news = {"exit": code, "output": text}
    except ToolError as error:
        if ctx.cancelled.is_set():
            raise
        news = {"exit": None, "output": str(error)}
    if target in ("any", "all"):
        workers = lib.list_children("hc", env)
    else:
        workers = [lib.status("hc", target, env)]
    return result({"wait": news, "workers": workers})


def wait_seconds(arguments):
    value = arguments.get("timeout", WAIT_DEFAULT)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 <= value < float("inf")
    ):
        raise ToolError("timeout must be a nonnegative finite number of seconds")
    return min(value, WAIT_MAX)


def wait(arguments, env, ctx):
    if not dispatch_enabled(env):
        return legacy_wait(arguments, env, ctx)
    (target,) = need(arguments, "target")
    seconds = wait_seconds(arguments)
    actor = getattr(ctx, "actor", None)
    inbox = target == "inbox" or bool(actor and actor["worker_id"] and target == "any")
    store, team = (ctx.store, actor["team_id"]) if inbox and actor else lead_store(env, ctx)
    recipient = actor["recipient"] if inbox and actor else env["HC_TERMINAL_ID"]
    reported = set()  # addresses that ever reported: their null status is never a silent worker

    def observe(members, timeout):
        try:
            code, out, _ = run(["hc", "ls", "--all", "--json"], env, ctx, timeout)
            raw = json.loads(out) if code == 0 else None
            if not isinstance(raw, dict) or not isinstance(raw.get("terminals"), list):
                return {}
        except (ToolError, ValueError):
            return {}
        observations = {}
        for member in members:
            row = next(
                (
                    r
                    for r in raw["terminals"]
                    if isinstance(r, dict) and r.get("address") == member["address"]
                ),
                None,
            )
            state = "exited"
            if row:
                state = row.get("status")
                state = state.get("state") if isinstance(state, dict) else state
                if row.get("state") in ("exited", "finished", "interrupted"):
                    state = row["state"]
                if (row.get("name"), row.get("parent")) != (member["name"], member["lead"]):
                    state = "replaced"
            if row and state is not None:
                reported.add(member["address"])
            if (
                row
                and state is None
                and row.get("state") == "live"
                and member["address"] not in reported
            ):
                # ls lacks created_ms/reported: read the one terminal, only for silent live members.
                try:
                    code, out, _ = run(
                        ["hc", "get", member["address"], "--json"], env, ctx, timeout
                    )
                    detail = json.loads(out) if code == 0 else None
                except (ToolError, ValueError):
                    detail = None
                if isinstance(detail, dict) and lib.never_reported(detail):
                    state = "unreported"
                elif isinstance(detail, dict) and (
                    detail.get("reported") is True or detail.get("status_ms") is not None
                ):
                    reported.add(member["address"])
            if state not in (
                "working",
                "idle",
                "done",
                "blocked",
                "error",
                "unreported",
                "exited",
                "finished",
                "interrupted",
                "replaced",
            ):
                state = "unknown"
            observations[member["assignment_id"]] = state
            if row:
                turn = row.get("turn")
                status = row.get("status")
                turn = status.get("turn") if turn is None and isinstance(status, dict) else turn
                observations[(member["assignment_id"], "turn")] = turn
        return observations

    try:
        batch = dispatch_wait.wait(
            store,
            team,
            recipient,
            target,
            seconds,
            arguments.get("receipt"),
            ctx,
            observe,
            inbox=inbox,
        )
    except InterruptedError:
        raise ToolError("cancelled")
    attach_usage(batch, env)
    return result(batch, structured=True)


def attach_usage(batch, env):
    """Add each returned row's measured `usage`; a row whose worker has no measurement gets no key.

    Done once, on the batch the wait returns, outside the store's transaction and the listener. The
    store budgets a worst-case dict per row, so the attached response stays under its cap.
    """
    measured = {}

    def figures(worker):
        if worker not in measured:
            try:
                measured[worker] = usage.assignment_usage(env, worker)
            except Exception:  # measuring is garnish: it never fails a wait
                measured[worker] = {}
        return measured[worker]

    for row in batch.get("assignments") or ():
        found = figures(row.get("name")).get(row.get("assignment_id"))
        if found:
            row["usage"] = found


def collect(arguments, env, ctx):
    (name,) = need(arguments, "name")
    worker_name(name)
    require_hc(env)
    try:
        app = lib.hc_app(json.loads(hc(["get", name, "--json"], env, ctx)))
    except ValueError:
        app = None
    screen = hc(["read", name, "--lines", str(HISTORY_LINES)], env, ctx)
    block = lib.last_prompt_block(screen, app) if app else []
    lines = block or screen.splitlines()  # unknown CLI or no task prompt: the whole screen
    return result("\n".join(lines[-COLLECT_LINES:]))


def terminal_gone(error, name):
    """hc reports a missing terminal as error.kind == "not_found" (or `no terminal "<name>"`); nothing else is gone."""
    text = str(error)
    try:
        body = json.loads(text)
        info = body.get("error") if isinstance(body, dict) else None
        if isinstance(info, dict):
            return info.get("kind") == "not_found"
    except ValueError:
        pass
    return text.strip() == 'no terminal "%s"' % name


def stop(arguments, env, ctx):
    (name,) = need(arguments, "name")
    worker_name(name)
    if not dispatch_enabled(env):
        require_child(name, env, ctx)
        note = ""
        try:
            hc(["kill", name], env, ctx)
        except ToolError as error:
            note = " (kill: %s)" % error  # an exited terminal still needs removing
        hc(["rm", name], env, ctx)
        return result("stopped %s%s" % (name, note))
    _, caller = require_hc(env)
    store, team = lead_store(env, ctx)
    # D1/D13: a terminal that is gone or now belongs to another lead is never touched; our record is still stopped.
    try:
        raw = json.loads(hc(["get", name, "--json"], env, ctx))
        ours = bool(caller) and isinstance(raw, dict) and raw.get("parent") == caller
        reason = "not your child" if not ours else None
    except ToolError as error:
        if not terminal_gone(error, name):
            raise
        reason = "terminal already gone"
    except ValueError:
        raise ToolError("cannot read %s" % name)
    note = ""
    if reason is None:
        try:
            hc(["kill", name], env, ctx)
        except ToolError as error:
            note = " (kill: %s)" % error  # an exited terminal still needs removing
        try:
            hc(["rm", name], env, ctx)
        except ToolError as error:
            if not terminal_gone(error, name):
                raise
            note += " (rm: %s)" % error
    else:
        note = " (%s; terminal untouched)" % reason
    try:
        store.stopped(team, name, ctx.ack)
    except (dispatch.StoreError, sqlite3.Error) as error:
        # Only an outage of the store can be retried. A refusal ("not registered", a foreign receipt) answers
        # the same every time, so it carries no retry advice.
        refused = isinstance(error, dispatch.StoreError) and not isinstance(
            error, dispatch.StorageError
        )
        if refused and reason == "not your child":
            raise ToolError("%s is not your child; refusing" % name)
        if refused:
            if reason is None:
                raise ToolError(
                    "stop %s: the terminal was killed and removed, but the store refused to record it: %s"
                    % (name, error)
                ) from error
            raise
        # The kill is irreversible; the record update was one transaction, so it is rolled back. A retry
        # finds the terminal gone and only reconciles the record.
        effect = (
            "the terminal was killed and removed"
            if reason is None
            else "the terminal was not touched (%s)" % reason
        )
        raise ToolError(
            "%s: %s, and the store failure (%s) left %s still recorded active; retry the stop"
            % ("stop %s" % name, effect, error, name)
        ) from error
    return result("stopped %s%s" % (name, note))


def legacy_report(arguments, env, ctx):
    (text,) = need(arguments, "text")
    info, _ = me(env)
    if info["runtime"] != "hc":
        raise gap("%s support for report" % info["runtime"], NATIVE_TOOLS[info["runtime"]])
    if info["role"] != "worker" or not info.get("parent"):
        raise ToolError("report is for hc workers; a lead reads results with collect")
    hc(["send", info["parent"], text], env, ctx)
    return result("reported to %s" % info["parent"])


def report(arguments, env, ctx):
    if not dispatch_enabled(env):
        return legacy_report(arguments, env, ctx)
    text, assignment = need(arguments, "text", "assignment_id")
    info, caller = me(env)
    if info["runtime"] != "hc":
        raise gap("%s support for report" % info["runtime"], NATIVE_TOOLS[info["runtime"]])
    if info["role"] != "worker" or not info.get("parent"):
        raise ToolError("report is for hc workers with a registered assignment")
    scope = hc_server(env)
    if ctx.cancelled.is_set():
        raise ToolError("cancelled")
    try:
        raw = json.loads(hc(["get", caller, "--json"], env, ctx))
    except ValueError:
        raise ToolError("cannot verify report sender")
    if not isinstance(raw, dict) or raw.get("parent") != info["parent"]:
        raise ToolError("report sender changed during resolution")
    return result(
        ctx.store.publish(
            scope,
            caller,
            info["parent"],
            assignment,
            text,
            arguments.get("kind"),
            arguments.get("message_id"),
            caller_name=raw.get("name"),
            ack=ctx.ack,
            generation=ctx.generation,
        ),
        structured=True,
    )


def task(arguments, env, ctx):
    if not dispatch_enabled(env):
        raise ToolError("task requires AGENT_RUNTIME_DISPATCH=1")
    (action,) = need(arguments, "action")
    return result(ctx.store.task(ctx.actor, action, arguments, ack=ctx.ack), structured=True)


def status(arguments, env, _ctx):
    (name,) = need(arguments, "name")
    return result(lib.status(lib.resolve(env)["runtime"], name, env))


def list_agents(_arguments, env, _ctx):
    runtime = lib.resolve(env)["runtime"]
    if runtime != "hc":
        raise ToolError("list is hc-only; use the runtime's own tools")
    return result(lib.list_children(runtime, env))


HANDLERS = {
    "spawn": spawn,
    "message": message,
    "resume": resume,
    "wait": wait,
    "status": status,
    "list": list_agents,
    "collect": collect,
    "stop": stop,
    "report": report,
    "task": task,
    "operation": operation,  # a `call` verb only: the MCP tool list stays the ten tools
}


def bounded(out):
    """Dispatch mode: a text result whose JSON encoding exceeds half MAX_BASE_BYTES becomes an explicit
    partial structured result keeping the tail, where collect's
    result is (escaped size, not raw bytes, is what the canonical payload counts)."""
    if out.get("isError") or out.get("structuredContent") is not None:
        return out
    body = out["content"][0]["text"]
    if len(json.dumps(body)) <= dispatch.MAX_BASE_BYTES // 2:
        return out
    budget = (
        dispatch.MAX_BASE_BYTES // 2 - 1024
    )  # headroom for the partial/kept/original_bytes fields
    keep = body[
        -budget:
    ]  # escaped size is at least the char count; shrink from the head until it fits
    while len(json.dumps(keep)) > budget:
        keep = keep[max(1, (len(json.dumps(keep)) - budget) // 6) :]
    return result(
        {"result": keep, "partial": True, "kept": "tail", "original_bytes": len(body.encode())},
        structured=True,
    )


def diagnostic(name, arguments, env, ctx, error):
    """Storage is unavailable, or the terminal belongs to another session: return HC-only state/screen
    with an explicit delivery error.

    No store call happens, so nothing is acknowledged, pinned, adopted or completed.
    """
    # An unproven caller (the store failed before actor() succeeded) gets no wider than its own children.
    if name in ("status", "collect") and not getattr(ctx, "actor", None):
        (target,) = need(arguments, "name")
        worker_name(target)
        require_child(target, env, ctx)
    out = HANDLERS[name](arguments, env, ctx)
    out = bounded(out)
    body = out["content"][0]["text"]
    try:
        payload = out.get("structuredContent") or json.loads(body)
    except ValueError:
        payload = {"result": body}
    if not isinstance(payload, dict):
        payload = {"result": payload}
    reason = "refused" if isinstance(error, dispatch.TerminalRefused) else "dispatch unavailable"
    payload["delivery_error"] = (
        "%s (%s); diagnostic only, not an accepted report; "
        "any receipt was not processed" % (reason, error)
    )
    return result(payload, structured=True)


WAIT_NUDGE = "call wait(target=all) to receive their reports"


def lead_unfinished(name, arguments, ctx, scope, generation, caller):
    """Unfinished count over the caller's own team, or 0; a failed count never fails the call."""
    try:
        if not ctx.actor["worker_id"]:
            return ctx.store.unfinished_count(ctx.actor["team_id"])
        team = ctx.store.owned_team(
            scope, caller, generation
        )  # a worker with children leads its own team
        if not team or (name == "message" and not ctx.store.has_worker(team, arguments.get("to"))):
            return 0
        return ctx.store.unfinished_count(team)
    except (dispatch.StoreError, sqlite3.Error):
        return 0


def require_dispatch(env):
    """A restricted host needs the durable store: without it there is no identity, no attribution
    and no scope to check."""
    host = host_policy.host_of(env)
    if host_policy.restricted(host):
        raise ToolError(
            "the %s host needs the durable dispatch store: start the MCP server with "
            "AGENT_RUNTIME_DISPATCH=1" % host
        )


def call(name, arguments, env, ctx):
    try:
        try:
            return call_dispatch(name, arguments, env, ctx)
        except (
            sqlite3.Error,
            OSError,
            dispatch.StorageError,
            dispatch.TerminalRefused,
            DiagnosticUnavailable,
        ) as error:
            if name in DIAGNOSTICS and dispatch_enabled(env):
                return diagnostic(name, arguments, env, ctx, error)
            # A handler names a failure that came after its external effect itself; one that reaches this
            # point came before it, where the store already rolled the pending change back.
            if name in STORE_DOWN and dispatch_enabled(env) and isinstance(error, sqlite3.Error):
                raise store_down(name, error) from error
            raise
    except (ToolError, dispatch.StoreError) as error:
        return result(str(error), True)


def call_dispatch(name, arguments, env, ctx):
    if not dispatch_enabled(env):
        require_dispatch(env)
        return HANDLERS[name](arguments, env, ctx)
    if name in ("status", "list", "collect") and lib.resolve(env)["runtime"] != "hc":
        return HANDLERS[name](arguments, env, ctx)
    if name == "operation":
        return operation(
            arguments, env, ctx
        )  # before the inbox: a lookup must not acknowledge anything
    info, caller = require_hc(env)
    diagnostic_call = name in DIAGNOSTICS
    # D15: read tools piggyback the inbox only when the call carries a receipt (subagents are denied receipts).
    gated_read = diagnostic_call or (name == "task" and arguments.get("action") == "list")
    if info["role"] not in ("lead", "worker"):
        if diagnostic_call:
            raise DiagnosticUnavailable("hc role lookup failed")
        raise ToolError("cannot establish dispatch caller")
    try:
        scope, generation = dispatch_identity(env, ctx)
    except ToolError as error:
        if diagnostic_call:
            raise DiagnosticUnavailable("caller identity unresolved: %s" % error)
        raise
    ctx.identity = (scope, generation)
    ctx.generation = generation
    ctx.store = dispatch.Store(env)
    raw_name = None
    if info["role"] == "worker" and name == "report":
        raw_name = json.loads(hc(["get", caller, "--json"], env, ctx)).get("name")
    ctx.actor = ctx.store.actor(
        scope,
        caller,
        info.get("parent"),
        generation,
        arguments.get("assignment_id"),
        raw_name,
        defer_pin=gated_read,
    )
    if not ctx.actor["worker_id"]:
        ctx.owned_team = ctx.actor["team_id"]
    # The host that owns this session decides what it may mutate; the server is the authority.
    try:
        host_policy.authorize(env, host_policy.host_of(env), info["role"], name, arguments, ctx)
    except host_policy.Refused as error:
        raise ToolError(str(error))
    ctx.ack = None
    if arguments.get("receipt") is not None:
        ctx.ack = ctx.store.receipt_owner(arguments["receipt"])
        if ctx.ack[:2] != (ctx.actor["team_id"], ctx.actor["recipient"]):
            _, own_team = lead_store(env, ctx)
            if ctx.ack[:2] != (own_team, caller):
                raise ToolError("foreign receipt")
    outcome = HANDLERS[name](arguments, env, ctx)
    if name == "wait":
        return outcome
    outcome = bounded(outcome)
    payload = outcome.get("structuredContent")
    if payload is None:
        try:
            payload = json.loads(outcome["content"][0]["text"])
        except ValueError:
            payload = outcome["content"][0]["text"]
    if not isinstance(payload, dict):
        payload = {"result": payload}
    if name in ("spawn", "resume", "message") and not outcome.get("isError"):
        pending = lead_unfinished(name, arguments, ctx, scope, generation, caller)
        if pending:
            payload = dict(payload, unfinished=pending, next=WAIT_NUDGE)
            outcome = result(payload, structured=True)
    if gated_read and arguments.get("receipt") is None:
        if ctx.actor.get("pin"):  # session state, not inbox state: first contact still pins
            ctx.store.pin(ctx.actor["pin"])
        return outcome  # no inbox read, ack or receipt row
    # The inbox page budgets against the already-bounded base, then receipt/pin/ack commit together.
    reserve = len(json.dumps(payload, separators=(",", ":")).encode()) + len(',"inbox":')
    try:
        inbox = ctx.store.read(
            ctx.actor["team_id"],
            ctx.actor["recipient"],
            "any",
            assignment_ids=[],
            inbox=True,
            ack=ctx.ack,
            pin=ctx.actor.get("pin"),
            reserve=reserve,
        )
    except (sqlite3.Error, dispatch.StoreError):
        if name not in STORE_DOWN:
            raise
        # The handler's effect is committed (a report, a task, a message, a started worker): the inbox only
        # piggybacks, and unread messages wait for the next call. An error here would make the caller redo it.
        return outcome
    if inbox["messages"] or inbox["more"]:
        return result(dict(payload, inbox=inbox), structured=True)
    return outcome
