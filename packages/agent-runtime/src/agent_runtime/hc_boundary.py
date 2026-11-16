"""The hc boundary: the process a tool starts, the child it may address, the text it sends there and
the terminal it ends. Every call into hc goes through here, so a new backend replaces this module."""

import json
import subprocess
import time

from . import agent_runtime as lib
from .team_session import lead_store, require_hc
from .tool_call import (
    SHORT_SECONDS,
    Context,
    ToolError,
    dispatch_enabled,
    legacy_wait_seconds,
    need,
    result,
    worker_name,
)


def run(argv, env, ctx, timeout: float = SHORT_SECONDS):
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


def hc(args, env, ctx, timeout: float = SHORT_SECONDS):
    code, out, err = run(["hc"] + args, env, ctx, timeout)
    if code != 0:
        raise ToolError((err.strip() or out.strip() or "hc %s failed" % args[0]))
    return out.strip()


def discard(name, env):
    """Best effort: end and remove a terminal we may have started."""
    fresh = Context()
    for command in ("kill", "rm"):
        try:
            hc([command, name], env, fresh)
        except ToolError:
            pass


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
