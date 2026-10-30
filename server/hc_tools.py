"""Tool implementations for the agent-runtime MCP server. Each tool resolves the runtime and runs
the runtime's command itself. Terminal mutations go through hc; durable reports use the private store."""

import json
import os
import re
import subprocess
import threading
import time

import agent_runtime as lib
import dispatch_store as dispatch
import dispatch_wait

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
COLLECT_LINES = 200
HISTORY_LINES = 1000
SHORT_SECONDS = 30
WAIT_DEFAULT, WAIT_MAX = 170, 590
KINDS = ("worker", "lookup")
CLIS = ("claude", "codex")
SUBAGENT_TOOL = {"claude": "Claude's Agent tool", "codex": "Codex's MultiAgent V2 spawn tool"}
NATIVE_TOOLS = {"herdmates": "SendMessage and the Agent tool (named teammate), the CLI's own subagent tool",
                "native": "the CLI's own subagent tool (Claude Agent tool, Codex MultiAgent spawn tool)"}


class ToolError(Exception):
    """A tool failure whose text goes to the model."""


class Context:
    """Per-call cancellation: kills the running subprocess when the client cancels."""

    def __init__(self):
        self.cancelled = threading.Event()
        self.proc = None
        self.wake = None

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
        proc = subprocess.Popen(argv, env=env, text=True, errors="replace", stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
        raise ToolError("invalid name %r: use 1-32 of a-z 0-9 - _ , not starting with - or _" % value)
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


def codex_instructions():
    path = os.path.join(os.path.expanduser("~"), ".codex", "agents", "implementer.toml")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            import tomllib
            return tomllib.loads(text)["developer_instructions"].strip()
        except ImportError:  # Python < 3.11
            found = re.search(r'^developer_instructions\s*=\s*"""(.*?)"""', text, re.S | re.M)
            if not found:
                raise KeyError("developer_instructions")
            return found.group(1).strip()
    except (OSError, KeyError, ValueError) as error:
        raise ToolError("cannot read Codex implementer instructions from %s: %s" % (path, error))


def discard(name, env):
    """Best effort: end and remove a terminal we may have started."""
    fresh = Context()
    for command in ("kill", "rm"):
        try:
            hc([command, name], env, fresh)
        except ToolError:
            pass


def dispatch_identity(env):
    """Scope plus CLI session generation; MCP process restarts retain the CLI ancestor.

    Prefer the nearest CLI process PID/start time over possibly inherited session
    markers. Hosts without visible CLI ancestry must supply a session ID.
    """
    scope = env.get("HC_SCOPE") or env.get("HC_TERMINAL_SOCKET") or "default"
    generation = None
    pid = os.getppid()
    deadline = time.monotonic() + lib.ANCESTRY_BUDGET_SECONDS
    for _ in range(lib.MAX_ANCESTOR_HOPS):
        if pid <= 1 or time.monotonic() >= deadline:
            break
        entry = lib.ps_entry(pid)
        if not entry:
            break
        parent, args = entry
        if lib.cli_of_args(args):
            try:
                stamp = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True,
                                       text=True, timeout=lib.PS_TIMEOUT_SECONDS)
                if stamp.returncode == 0 and stamp.stdout.strip():
                    generation = "%s:%s" % (pid, stamp.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                pass
            break
        pid = parent
    session = env.get("CODEX_THREAD_ID") or env.get("CLAUDE_SESSION_ID")
    generation = "%s:%s" % (generation, session) if generation and session else generation or session
    if not generation:
        raise ToolError("cannot establish lead session generation; host must supply a CLI session ID")
    return scope, generation


def lead_store(env):
    _, caller = require_hc(env)
    scope, generation = dispatch_identity(env)
    store = dispatch.Store(env)
    return store, store.team(scope, caller, generation)


def spawn(arguments, env, ctx):
    kind = arguments.get("kind")
    cli = arguments.get("cli") or "claude"
    if kind == "lookup":
        raise ToolError("lookup is the CLI's own subagent tool, not a spawn: use %s (named workers only through spawn kind=worker)"
                        % SUBAGENT_TOOL.get(cli, "the CLI's subagent tool"))
    if kind != "worker":
        raise gap("spawn kind=%s" % kind, "hc run --bg ...")
    if cli not in CLIS:
        raise gap("spawn cli=%s" % cli, "hc run --bg --name <name> %s" % cli)
    name, task, cwd = need(arguments, "name", "task", "cwd")
    worker_name(name)
    _, caller = require_hc(env)
    if not caller:
        raise ToolError("HC_TERMINAL_ID is not set; cannot address the report")
    store = None
    message = "%s\n\nWhen done or stuck, call the agent-runtime report tool with your result." % task
    if dispatch_enabled(env):
        store, team = lead_store(env)
        identity = store.register(team, name)
        message += " Include assignment_id=%s and kind=progress|complete|blocked|decision." % identity["assignment_id"]
    if cli == "claude":
        program = ["claude", "--agent", "implementer", "--permission-mode", "auto", message]
    else:
        program = ["codex", "--no-daemon", "%s\n\n%s" % (codex_instructions(), message)]
    # A cancel can land while hc run is starting the terminal; whatever it created is ended again.
    try:
        out = hc(["run", "--bg", "--name", name, "--cwd", cwd, "--parent", caller, "--json"] + program, env, ctx)
    except ToolError:
        if store:
            store.fail(identity["assignment_id"])
        if ctx.cancelled.is_set():
            discard(name, env)
        raise
    if ctx.cancelled.is_set():
        if store:
            store.fail(identity["assignment_id"])
        discard(name, env)
        raise ToolError("cancelled")
    try:
        started = json.loads(out)
    except ValueError:
        started = {}
    sent = (started.get("first_message") or {}).get("sent") if isinstance(started, dict) else None
    if sent is False:
        if store:
            store.fail(identity["assignment_id"])
        raise ToolError("worker %s started but its task was not sent: hc read %s; no blind resend" % (name, name))
    info = {"name": name, "address": None, "parent": caller}
    try:
        raw = json.loads(hc(["get", name, "--json"], env, ctx))
        info.update(address=raw.get("address"), parent=raw.get("parent") or caller)
    except (ToolError, ValueError, AttributeError):
        pass
    if not store:
        return result(info)
    if info["address"]:
        scope, _ = dispatch_identity(env)
        store.bind(identity["assignment_id"], info["address"], scope)
    else:
        store.fail(identity["assignment_id"])
        raise ToolError("worker started but address unavailable; assignment cannot accept reports")
    return result(dict(info, **identity), structured=True)


def require_child(name, env, ctx):
    _, caller = require_hc(env)
    try:
        raw = json.loads(hc(["get", name, "--json"], env, ctx))
    except ValueError:
        raise ToolError("cannot read %s" % name)
    if not caller or not isinstance(raw, dict) or raw.get("parent") != caller:
        raise ToolError("%s is not your child; refusing" % name)


def send(args_before, target, text, env, ctx, timeout=SHORT_SECONDS):
    worker_name(target)
    require_child(target, env, ctx)
    out = hc(["send"] + args_before + [target, text], env, ctx, timeout)
    return out


def message(arguments, env, ctx):
    to, text = need(arguments, "to", "text")
    return result(send([], to, text, env, ctx) or "sent")


def resume(arguments, env, ctx):
    name, task = need(arguments, "name", "task")
    out = send(["--wait", "--timeout", str(WAIT_DEFAULT)], name, task, env, ctx, WAIT_DEFAULT + 10)
    return result(out or "turn ended")


def legacy_wait_seconds(arguments):
    value = arguments.get("timeout", WAIT_DEFAULT)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ToolError("timeout must be a positive number of seconds")
    return int(min(value, WAIT_MAX))


def legacy_wait(arguments, env, ctx):
    (target,) = need(arguments, "target")
    seconds = legacy_wait_seconds(arguments)
    _, caller = require_hc(env)
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
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float("inf"):
        raise ToolError("timeout must be a nonnegative finite number of seconds")
    return min(value, WAIT_MAX)


def wait(arguments, env, ctx):
    if not dispatch_enabled(env):
        return legacy_wait(arguments, env, ctx)
    (target,) = need(arguments, "target")
    seconds = wait_seconds(arguments)
    store, team = lead_store(env)
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
            row = next((r for r in raw["terminals"] if isinstance(r, dict) and r.get("address") == member["address"]), None)
            state = "exited"
            if row:
                state = row.get("status")
                state = state.get("state") if isinstance(state, dict) else state
                if row.get("state") in ("exited", "finished", "interrupted"):
                    state = row["state"]
                if (row.get("name"), row.get("parent")) != (member["name"], member["lead"]):
                    state = "replaced"
            if state not in ("working", "idle", "done", "blocked", "exited", "finished", "interrupted", "replaced"):
                state = "unknown"
            observations[member["assignment_id"]] = state
        return observations
    try:
        return result(dispatch_wait.wait(store, team, env["HC_TERMINAL_ID"], target, seconds,
                      arguments.get("receipt"), ctx, observe), structured=True)
    except InterruptedError:
        raise ToolError("cancelled")


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


def stop(arguments, env, ctx):
    (name,) = need(arguments, "name")
    worker_name(name)
    require_child(name, env, ctx)
    note = ""
    try:
        hc(["kill", name], env, ctx)
    except ToolError as error:
        note = " (kill: %s)" % error  # an exited terminal still needs removing
    hc(["rm", name], env, ctx)
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
    scope = env.get("HC_SCOPE") or env.get("HC_TERMINAL_SOCKET") or "default"
    if ctx.cancelled.is_set():
        raise ToolError("cancelled")
    try:
        raw = json.loads(hc(["get", caller, "--json"], env, ctx))
    except ValueError:
        raise ToolError("cannot verify report sender")
    if not isinstance(raw, dict) or raw.get("parent") != info["parent"]:
        raise ToolError("report sender changed during resolution")
    return result(dispatch.Store(env).publish(scope, caller, info["parent"], assignment, text,
                  arguments.get("kind"), arguments.get("message_id"), caller_name=raw.get("name")), structured=True)


def status(arguments, env, ctx):
    (name,) = need(arguments, "name")
    return result(lib.status(lib.resolve(env)["runtime"], name, env))


def list_agents(arguments, env, ctx):
    runtime = lib.resolve(env)["runtime"]
    if runtime != "hc":
        raise ToolError("list is hc-only; use the runtime's own tools")
    return result(lib.list_children(runtime, env))


HANDLERS = {"spawn": spawn, "message": message, "resume": resume, "wait": wait, "status": status,
            "list": list_agents, "collect": collect, "stop": stop, "report": report}


def call(name, arguments, env, ctx):
    try:
        return HANDLERS[name](arguments, env, ctx)
    except (ToolError, dispatch.StoreError) as error:
        return result(str(error), True)
