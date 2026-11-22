"""Tool implementations for the agent-runtime MCP server. Each tool resolves the runtime and runs
the runtime's command itself. Terminal mutations go through hc; durable reports use the private store.

The names the split moved - and the standard library modules they took with them - are still bound
here: a caller or a test reads `hc_tools.<name>`, so each is re-exported explicitly rather than left
as an unused import.
"""

import hashlib as hashlib
import json
import os
import re as re
import sqlite3
import stat as stat
import subprocess as subprocess
import threading as threading
import time as time
from pathlib import Path as Path

from . import agent_runtime as lib
from . import dispatch_store as dispatch
from . import dispatch_wait, host_policy, pi_mcp, profiles, usage
from .dispatch_guards import (
    MUTATING,
    WAIT_NUDGE,
    lead_unfinished,
    refuse_unread,
    require_dispatch,
)
from .dispatch_guards import (
    undelivered_reports as undelivered_reports,
)
from .hc_boundary import (
    discard,
    hc,
    legacy_wait,
    message,
    require_child,
    run,
    send,
    terminal_gone,
)
from .observation import (
    COLLECT_LINES as COLLECT_LINES,
)
from .observation import (
    HISTORY_LINES as HISTORY_LINES,
)
from .observation import (
    attach_usage,
    collect,
    list_agents,
    status,
)
from .operations import (
    OPERATION_ID as OPERATION_ID,
)
from .operations import (
    linked_task,
    operation,
    operation_fingerprint,
    operation_id,
    operation_owner,
    replayed,
)
from .reports import legacy_report as legacy_report
from .reports import report, task
from .store_failures import STORE_DOWN, row_state, settle, store_down
from .team_session import (
    NATIVE_TOOLS as NATIVE_TOOLS,
)
from .team_session import (
    dispatch_identity,
    lead_store,
    require_hc,
)
from .team_session import (
    hc_server as hc_server,
)
from .team_session import (
    me as me,
)
from .tool_call import (
    DIAGNOSTICS,
    SHORT_SECONDS,
    WAIT_DEFAULT,
    DiagnosticUnavailable,
    ToolError,
    bounded,
    dispatch_enabled,
    gap,
    need,
    result,
    wait_seconds,
    worker_name,
)
from .tool_call import (
    NAME_RE as NAME_RE,
)
from .tool_call import (
    WAIT_MAX as WAIT_MAX,
)
from .tool_call import (
    Context as Context,
)
from .tool_call import (
    legacy_wait_seconds as legacy_wait_seconds,
)
from .worker_launch import (
    AGENT_CLAUDE as AGENT_CLAUDE,
)
from .worker_launch import (
    AGENT_CODEX as AGENT_CODEX,
)
from .worker_launch import (
    EFFORTS as EFFORTS,
)
from .worker_launch import (
    LEGACY_WORKER_PROTOCOL,
    ROLE_FILE_CLIS,
    WORKER_PROTOCOL,
    codex_instructions,
    owned_orphan,
    profile_launch_error,
    profile_launcher,
    role_text,
    spawn_agent,
    spawn_effort,
    spawn_instructions,
    spawn_model,
)
from .worker_launch import (
    NO_AGENT as NO_AGENT,
)
from .worker_launch import (
    PROFILE_STARTUP_WINDOW_SECONDS as PROFILE_STARTUP_WINDOW_SECONDS,
)
from .worker_launch import (
    role_body as role_body,
)
from .worker_launch import (
    role_path as role_path,
)

KINDS = ("worker", "lookup")


CLIS = profiles.BUILTINS


SUBAGENT_TOOL = {"claude": "Claude's Agent tool", "codex": "Codex's MultiAgent V2 spawn tool"}


def spawn(arguments, env, ctx):  # noqa: PLR0912, PLR0915
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
    configured = {}
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
    team = None
    scope = None
    identity = {}
    earlier = None
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
    program = profiles.login_shell(env, program)
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


def resume(arguments, env, ctx):  # noqa: PLR0912
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

    def observe(members, timeout):  # noqa: PLR0912
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


def stop(arguments, env, ctx):  # noqa: PLR0912
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


def call_dispatch(name, arguments, env, ctx):  # noqa: PLR0912, PLR0915
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
    if name in MUTATING:
        refuse_unread(ctx, scope, generation, caller, name)
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
