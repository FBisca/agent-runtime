"""Tool implementations for the agent-runtime MCP server. Each tool resolves the runtime and runs
the runtime's command itself. Terminal mutations go through hc; durable reports use the private store."""

import json
import os
import re
import sqlite3
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


class DiagnosticUnavailable(Exception):
    """Dispatch cannot be consulted (store, session identity or hc role lookup failed), as opposed to a
    caller being refused. Diagnostics (status, collect, list) degrade to hc-only state on this alone."""


DIAGNOSTICS = ("status", "collect", "list")


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

    Prefer the nearest CLI process PID/start time over possibly inherited session
    markers. Hosts without visible CLI ancestry must supply a session ID.
    """
    cached = getattr(ctx, "identity", None)
    if cached:
        return cached  # computed once per call
    scope = hc_server(env)
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


def lead_store(env, ctx=None):
    if ctx is not None and getattr(ctx, "owned_team", None):
        return ctx.store, ctx.owned_team
    _, caller = require_hc(env)
    scope, generation = dispatch_identity(env, ctx)
    store = getattr(ctx, "store", None) or dispatch.Store(env)
    team = store.team(scope, caller, generation)
    if ctx is not None:
        ctx.owned_team = team
    return store, team


def owned_orphan(name, caller, env, store, team):
    """True only when a terminal we may have started exists under our parent and is not an earlier bound worker's."""
    try:
        raw = json.loads(hc(["get", name, "--json"], env, Context()))
    except (ToolError, ValueError):
        return False
    return bool(isinstance(raw, dict) and raw.get("parent") == caller and raw.get("address")
                and raw.get("address") != store.worker_address(team, name))


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
        # D4: identity and inputs are resolved before hc run, so nothing is started on an invalid spawn.
        scope, _ = dispatch_identity(env, ctx)
        store, team = lead_store(env, ctx)
    if cli == "claude":
        program = ["claude", "--agent", "implementer", "--permission-mode", "auto", message]
    else:
        program = ["codex", "--no-daemon", "%s\n\n%s" % (codex_instructions(), message)]
    if store:
        identity = store.register(team, name)
        suffix = " Include assignment_id=%s and kind=progress|complete|blocked|decision." % identity["assignment_id"]
        suffix += " Include assignment_id on your first runtime call to prove your session; later non-report calls may omit it."
        program[-1] += suffix
    try:
        code, out, err = run(["hc", "run", "--bg", "--name", name, "--cwd", cwd, "--parent", caller, "--json"] + program,
                             env, ctx, SHORT_SECONDS)
        if code != 0 and ctx.cancelled.is_set():  # cancel killed hc run mid-flight: not an hc refusal
            raise ToolError("cancelled")
    except ToolError as error:
        # Cancel/timeout may leave a terminal behind; a missing binary cannot.
        if store:
            store.fail(identity["assignment_id"])
            if not str(error).startswith("cannot run") and owned_orphan(name, caller, env, store, team):
                discard(name, env)
        elif ctx.cancelled.is_set():
            discard(name, env)
        raise
    if code != 0:  # hc refused (name_taken, ...): nothing was started, so nothing is discarded
        if store:
            store.fail(identity["assignment_id"])
        raise ToolError(err.strip() or out.strip() or "hc run failed")
    out = out.strip()

    def undo(error):
        """Every failure after a successful hc run ends the terminal, fails the assignment, leaves the worker inactive."""
        try:
            store.fail(identity["assignment_id"], inactive=True)
        finally:
            discard(name, env)
        raise error

    if store and ctx.cancelled.is_set():
        undo(ToolError("cancelled"))
    try:
        started = json.loads(out)
    except ValueError:
        started = {}
    sent = (started.get("first_message") or {}).get("sent") if isinstance(started, dict) else None
    if sent is False:
        if not store:
            raise ToolError("worker %s started but its task was not sent: hc read %s; no blind resend" % (name, name))
        undo(ToolError("worker %s started but its task was not sent and was removed; spawn it again" % name))
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
    if not info["address"]:
        undo(ToolError("worker started but address unavailable; assignment cannot accept reports"))
    try:
        store.bind(identity["assignment_id"], info["address"], scope, getattr(ctx, "ack", None), turn, mode)
    except (dispatch.StoreError, sqlite3.Error) as error:
        undo(error)
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
        return result(ctx.store.peer(actor, to, text, arguments.get("message_id"), ctx.ack), structured=True)
    return result(send([], to, text, env, ctx) or "sent")


def resume(arguments, env, ctx):
    name, task = need(arguments, "name", "task")
    if dispatch_enabled(env):
        worker_name(name)
        raw = require_child(name, env, ctx)
        if not raw.get("address"):
            raise ToolError("worker address unavailable")
        store, team = lead_store(env, ctx)
        identity = store.register(team, name)
        prompt = "%s\n\nUse assignment_id=%s for report(kind=progress|complete|blocked|decision)." % (task, identity["assignment_id"])
        prompt += " Include assignment_id on your first runtime call to prove your session; later non-report calls may omit it."
        try:
            out = hc(["send", name, prompt], env, ctx)
            try:
                sent = json.loads(out)
            except ValueError:
                sent = None
            if isinstance(sent, dict) and (sent.get("sent") is False or (sent.get("first_message") or {}).get("sent") is False):
                raise ToolError("resume task was not sent; no blind resend")
            if ctx.cancelled.is_set():
                raise ToolError("cancelled")
            store.bind(identity["assignment_id"], raw["address"], ctx.actor["scope"], ctx.ack, raw.get("turn"),
                       "ended" if row_state(raw) in ("done", "idle") else "working")
        except (ToolError, dispatch.StoreError):
            store.fail(identity["assignment_id"])
            raise
        return result(dict(identity, name=name, address=raw["address"], parent=raw["parent"]), structured=True)
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
    actor = getattr(ctx, "actor", None)
    inbox = target == "inbox" or bool(actor and actor["worker_id"] and target == "any")
    store, team = (ctx.store, actor["team_id"]) if inbox and actor else lead_store(env, ctx)
    recipient = actor["recipient"] if inbox and actor else env["HC_TERMINAL_ID"]
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
            if row:
                turn = row.get("turn")
                status = row.get("status")
                turn = status.get("turn") if turn is None and isinstance(status, dict) else turn
                observations[(member["assignment_id"], "turn")] = turn
        return observations
    try:
        return result(dispatch_wait.wait(store, team, recipient, target, seconds,
                      arguments.get("receipt"), ctx, observe, inbox=inbox), structured=True)
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
    except dispatch.StoreError:
        if reason == "not your child":
            raise ToolError("%s is not your child; refusing" % name)
        raise
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
    return result(ctx.store.publish(scope, caller, info["parent"], assignment, text,
                  arguments.get("kind"), arguments.get("message_id"), caller_name=raw.get("name"), ack=ctx.ack,
                  generation=ctx.generation), structured=True)


def task(arguments, env, ctx):
    if not dispatch_enabled(env):
        raise ToolError("task requires AGENT_RUNTIME_DISPATCH=1")
    (action,) = need(arguments, "action")
    return result(ctx.store.task(ctx.actor, action, arguments, ack=ctx.ack), structured=True)


def status(arguments, env, ctx):
    (name,) = need(arguments, "name")
    return result(lib.status(lib.resolve(env)["runtime"], name, env))


def list_agents(arguments, env, ctx):
    runtime = lib.resolve(env)["runtime"]
    if runtime != "hc":
        raise ToolError("list is hc-only; use the runtime's own tools")
    return result(lib.list_children(runtime, env))


HANDLERS = {"spawn": spawn, "message": message, "resume": resume, "wait": wait, "status": status,
            "list": list_agents, "collect": collect, "stop": stop, "report": report, "task": task}


def bounded(out):
    """Dispatch mode: a text result whose JSON encoding exceeds half MAX_BASE_BYTES becomes an explicit
    partial structured result keeping the tail, where collect's
    result is (escaped size, not raw bytes, is what the canonical payload counts)."""
    if out.get("isError") or out.get("structuredContent") is not None:
        return out
    body = out["content"][0]["text"]
    if len(json.dumps(body)) <= dispatch.MAX_BASE_BYTES // 2:
        return out
    budget = dispatch.MAX_BASE_BYTES // 2 - 1024  # headroom for the partial/kept/original_bytes fields
    keep = body[-budget:]  # escaped size is at least the char count; shrink from the head until it fits
    while len(json.dumps(keep)) > budget:
        keep = keep[max(1, (len(json.dumps(keep)) - budget) // 6):]
    return result({"result": keep, "partial": True, "kept": "tail", "original_bytes": len(body.encode())}, structured=True)


def diagnostic(name, arguments, env, ctx, error):
    """Storage is unavailable: return HC-only state/screen with an explicit delivery error.

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
    payload["delivery_error"] = ("dispatch unavailable (%s); diagnostic only, not an accepted report; "
                                 "any receipt was not processed" % error)
    return result(payload, structured=True)


def call(name, arguments, env, ctx):
    try:
        try:
            return call_dispatch(name, arguments, env, ctx)
        except (sqlite3.Error, OSError, dispatch.StorageError, DiagnosticUnavailable) as error:
            if name in DIAGNOSTICS and dispatch_enabled(env):
                return diagnostic(name, arguments, env, ctx, error)
            raise
    except (ToolError, dispatch.StoreError) as error:
        return result(str(error), True)


def call_dispatch(name, arguments, env, ctx):
    if not dispatch_enabled(env):
        return HANDLERS[name](arguments, env, ctx)
    if name in ("status", "list", "collect") and lib.resolve(env)["runtime"] != "hc":
        return HANDLERS[name](arguments, env, ctx)
    info, caller = require_hc(env)
    diagnostic_call = name in DIAGNOSTICS
    if info["role"] not in ("lead", "worker"):
        if diagnostic_call:
            raise DiagnosticUnavailable("hc role lookup failed")
        raise ToolError("cannot establish dispatch caller")
    try:
        scope, generation = dispatch_identity(env)
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
    ctx.actor = ctx.store.actor(scope, caller, info.get("parent"), generation,
                               arguments.get("assignment_id"), raw_name,
                                defer_pin=name in ("status", "collect"))
    if not ctx.actor["worker_id"]:
        ctx.owned_team = ctx.actor["team_id"]
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
    # The inbox page budgets against the already-bounded base, then receipt/pin/ack commit together.
    reserve = len(json.dumps(payload, separators=(",", ":")).encode()) + len(',"inbox":')
    inbox = ctx.store.read(ctx.actor["team_id"], ctx.actor["recipient"], "any", assignment_ids=[],
                           inbox=True, ack=ctx.ack, pin=ctx.actor.get("pin"), reserve=reserve)
    if inbox["messages"] or inbox["more"]:
        return result(dict(payload, inbox=inbox), structured=True)
    return outcome
