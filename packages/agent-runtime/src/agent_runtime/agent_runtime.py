#!/usr/bin/env python3
"""Agent runtime library and debug CLI (resolve, status, list). No model calls or agent spawns."""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

HC_TIMEOUT_SECONDS = 2
FORCED_GENERATION: str | None = (
    None  # set in-process by `agent-runtime call --lead`; never read from the env
)
FORCED_LEAD: bool = False  # same process-local override, set only after the ancestry gate
LEAD_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
RUNTIMES = ("herdmates", "hc", "native")
FIELDS = ("runtime", "role", "parent", "cli")
HARNESSES = ("claude", "codex", "pi")  # the hosts a session can run under
CLI_CHOICES = HARNESSES


def hc_get(terminal_id):
    result = subprocess.run(
        ["hc", "get", terminal_id],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=HC_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "hc get failed")
    return json.loads(result.stdout)


CODEX_ENV_MARKERS = ("CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID")
CLAUDE_ENV_MARKERS = ("CLAUDECODE",)
# Pi sets PI_CODING_AGENT in its own environment so child processes can detect it. It is a hint
# about the host, never an identity: the process ancestry and the dispatch store decide that.
PI_ENV_MARKERS = ("PI_CODING_AGENT",)
PI_PACKAGE = "pi-coding-agent"
MAX_ANCESTOR_HOPS = 15
ANCESTRY_BUDGET_SECONDS = 0.5
PS_TIMEOUT_SECONDS = 0.2


def real_ps_entry(pid: int, timeout: float = PS_TIMEOUT_SECONDS) -> tuple[int, str] | None:
    """(ppid, args) of one process via ps, or None on any failure."""
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=,args=", "-p", str(pid)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        ppid, _, args = result.stdout.strip().partition(" ")
        return int(ppid), args.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


# The seam the tests replace: the process-tree lookup, typed so a replacement has the same shape.
ps_entry: Callable[..., tuple[int, str] | None] = real_ps_entry


def pi_program(token):
    """True when a path names the Pi CLI: `pi` itself, its npm bin link or its package entry.

    Pi rewrites its process title to `pi`, so argv[0] is the bare name; an installed Pi may also
    run as `node .../node_modules/.bin/pi` or through its package entry point."""
    if os.path.dirname(token) == "":
        return True
    return "/node_modules/" in token


def cli_of_args(args):
    """`codex`, `claude` or `pi` when argv[0] (or argv[1] after a node/bun wrapper) has that basename."""
    tokens = args.split()
    for token in tokens[:2]:
        name = os.path.basename(token)
        if name in ("codex", "claude"):
            return name
        if name == "pi" and pi_program(token):
            return "pi"
        if PI_PACKAGE in token:
            return "pi"
        if name not in ("node", "bun", "deno"):
            break
    return None


def parent_cli():
    """The harness that started this process (its direct parent), or None.

    The nearest harness ancestor is the session's generation; the direct parent is the host that owns
    this process, and the host's trust model is the one the runtime applies."""
    entry = ps_entry(os.getppid())
    if not entry:
        return None
    return cli_of_args(entry[1])


def ancestor_cli():
    """Nearest ancestor process that is codex, claude or pi; None when the walk fails or finds nothing."""
    deadline = time.monotonic() + ANCESTRY_BUDGET_SECONDS
    pid, seen = os.getppid(), set()
    for _ in range(MAX_ANCESTOR_HOPS):
        if pid in seen or pid <= 1 or time.monotonic() > deadline:
            return None
        seen.add(pid)
        entry = ps_entry(pid)
        if not entry:
            return None
        pid, args = entry
        found = cli_of_args(args)
        if found:
            return found
    return None


def lstart(pid):
    """Process start time string via ps, or None on any failure."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=PS_TIMEOUT_SECONDS,
            env=dict(os.environ, LC_ALL="C"),
        )  # locale-stable; the server and hooks differ in env
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def cli_generation():
    """`<pid>:<lstart>` of the nearest codex/claude/pi ancestor, else None. Shared by the MCP server and
    hooks; session IDs are deliberately ignored so /clear and /new stay on one team. Read-only.
    `agent-runtime call --lead` sets FORCED_GENERATION for its own process, which pins the generation."""
    if FORCED_GENERATION:
        return FORCED_GENERATION
    deadline = time.monotonic() + ANCESTRY_BUDGET_SECONDS
    pid = os.getppid()
    for _ in range(MAX_ANCESTOR_HOPS):
        if pid <= 1 or time.monotonic() >= deadline:
            return None
        entry = ps_entry(pid)
        if not entry:
            return None
        parent, args = entry
        if cli_of_args(args):
            stamp = lstart(pid)
            return "%s:%s" % (pid, stamp) if stamp else None
        pid = parent
    return None


def marked_harnesses(env):
    """The host families the environment claims; more than one is ambiguous."""
    found = []
    if any(env.get(name) for name in CODEX_ENV_MARKERS):
        found.append("codex")
    if any(env.get(name) for name in CLAUDE_ENV_MARKERS):
        found.append("claude")
    if any(env.get(name) for name in PI_ENV_MARKERS):
        found.append("pi")
    return found


def pi_marked(env):
    """True when the environment says a Pi session started this process (a hint, never an identity)."""
    return "pi" in marked_harnesses(env)


def detect_cli(env):
    """Process ancestry first (env markers are inherited when one CLI starts the other), then env
    markers (Codex sets CODEX_THREAD_ID/CODEX_TOOL_CALL_ID; Claude Code sets CLAUDECODE; Pi sets
    PI_CODING_AGENT), else None."""
    found = ancestor_cli()
    if found:
        return found
    marked = marked_harnesses(env)
    for harness in ("codex", "claude", "pi"):
        if harness in marked:
            return harness
    return None


def foreground_cli(reply):
    """Fallback under hc: the terminal's foreground program is codex, claude or pi."""
    foreground = reply.get("foreground") if isinstance(reply, dict) else None
    argv = foreground.get("argv") if isinstance(foreground, dict) else None
    if isinstance(argv, list) and argv and isinstance(argv[0], str):
        name = os.path.basename(argv[0])
        return name if name in HARNESSES else None
    return None


def resolve(env, cli=None, hc_runner=hc_get):
    """Pure resolution: HERDR_PANE_ID > HC_TERMINAL_ID > native. Never raises on hc failure.
    cli=None detects it from the environment, then from hc, then defaults to claude."""
    explicit = cli
    detected = None if explicit else detect_cli(env)
    cli = explicit or detected or "claude"
    if env.get("HERDR_PANE_ID"):
        runtime = "herdmates" if cli == "claude" else "native"
        return {
            "runtime": runtime,
            "role": "lead" if runtime == "native" else "unknown",
            "cli": cli,
        }
    terminal_id = env.get("HC_TERMINAL_ID")
    if terminal_id:
        unknown = {"runtime": "hc", "role": "unknown", "cli": cli}
        try:
            reply = hc_runner(terminal_id)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            return unknown
        if not isinstance(reply, dict) or "parent" not in reply:
            return unknown
        if not explicit and not detected:
            cli = foreground_cli(reply) or cli
            unknown["cli"] = cli
        parent = reply["parent"]
        if parent is None:
            return {"runtime": "hc", "role": "lead", "cli": cli}
        if isinstance(parent, str) and parent and not any(c.isspace() for c in parent):
            if FORCED_LEAD:
                return {"runtime": "hc", "role": "lead", "cli": cli}
            return {"runtime": "hc", "role": "worker", "parent": parent, "cli": cli}
        return unknown
    return {"runtime": "native", "role": "lead", "cli": cli}


def hc_terminals():
    """Every hc terminal as `hc ls` reports it; [] when hc cannot be read."""
    try:
        listing = json.loads(run_command(["hc", "ls", "--all", "--json"]))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return []
    terminals = listing.get("terminals") if isinstance(listing, dict) else None
    return [t for t in terminals or [] if isinstance(t, dict)]


def ancestor_pids():
    """The pids from this process's parent up to init; stops on an unreadable entry or a loop."""
    deadline = time.monotonic() + ANCESTRY_BUDGET_SECONDS
    pid, seen = os.getppid(), []
    for _ in range(MAX_ANCESTOR_HOPS):
        if pid <= 1 or pid in seen or time.monotonic() > deadline:
            break
        seen.append(pid)
        entry = ps_entry(pid)
        if not entry:
            break
        pid = entry[0]
    return seen


def caller_identity(env, info):
    """Who is calling, and whether the process ancestry proves it. `verified` is true only when an
    ancestor is the root process of a live hc terminal: that terminal's parent (from hc, not the
    environment) says worker or lead, and it wins over missing markers. A marker in the environment
    is inherited and forgeable, so it gives a kind at most, never `verified`."""
    ancestors = ancestor_pids()
    if ancestors:
        by_pid = {t.get("pid"): t for t in hc_terminals() if isinstance(t.get("pid"), int)}
        for pid in ancestors:  # nearest first
            terminal = by_pid.get(pid)
            if terminal:
                kind = "lead" if terminal.get("parent") is None else "worker"
                return {"kind": kind, "verified": True}
    if env.get("HC_TERMINAL_ID") and info.get("role") in ("lead", "worker"):
        return {"kind": info["role"], "verified": False}
    return {"kind": "unknown", "verified": False}


def format_line(info):
    return " ".join("%s=%s" % (key, info[key]) for key in FIELDS if key in info)


CONTRACT_STATES = (
    "working",
    "idle",
    "interrupted",
    "blocked",
    "done",
    "exited",
    "unreported",
    "error",
    "unknown",
)
READ_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


def run_command(argv):
    result = subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=HC_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Command failed: " + argv[0])
    return result.stdout.strip()


def row(why, do, verb=None):
    return {"why": why, "do": do, "verb": verb, "gap": verb is None}


def record(runtime, name, state, role="unknown", parent=None, turn=None, native=None, next_rows=()):
    return {
        "name": name,
        "runtime": runtime,
        "role": role,
        "parent": parent,
        "state": state,
        "turn": turn,
        "native": native,
        "next": list(next_rows),
    }


UNREPORTED_GRACE_SECONDS = 30
UNREPORTED_WHY = "never reported a status and may be at a folder-trust or startup dialog"
UNREPORTED_STEPS = (
    ("it " + UNREPORTED_WHY, "collect the screen", "collect"),
    ("a startup dialog needs the user's say-so", "ask the user what to answer", None),
    (
        "the user approved hc keys, or the worker is not needed",
        "send the user-approved hc keys, or stop it",
        "stop",
    ),
)
# wait's `next` text is the status rows' actions joined, so both surfaces say the same thing.
UNREPORTED_NEXT = (
    "worker " + UNREPORTED_WHY + ": " + "; ".join(step[1] for step in UNREPORTED_STEPS)
)
ERROR_NEXT = "hc reports an error status: collect it, ask the user, or stop it"


def never_reported(raw, now=None):
    """A live terminal that reported no status ever, more than the grace period after it was created."""
    created = raw.get("created_ms")
    if raw.get("state") != "live" or raw.get("status") is not None:
        return False
    if "status_ms" not in raw or raw["status_ms"] is not None or raw.get("reported") is not False:
        return False
    turn = raw.get("turn")
    if (
        isinstance(turn, bool)
        or turn != 0
        or isinstance(created, bool)
        or not isinstance(created, (int, float))
    ):
        return False
    return (
        (now if now is not None else time.time()) * 1000 - created
    ) > UNREPORTED_GRACE_SECONDS * 1000


def hc_state(raw):
    """Map an `hc get`/`hc ls` record (status is an object {app, state} or a string) to a contract state."""
    status = raw.get("status")
    if isinstance(status, dict):
        status = status.get("state")
    if "exited" in (raw.get("state"), status) or "finished" in (raw.get("state"), status):
        return "exited"
    if never_reported(raw):
        return "unreported"
    return status if status in ("working", "idle", "blocked", "done", "error") else "unknown"


def hc_next(_name, state):
    if state == "done":
        return [
            row("work finished", "read the result", "collect"),
            row("worker no longer needed", "end it", "stop"),
        ]
    if state == "idle":
        return [
            row("turn ended, awaiting input", "send the next instruction", "message"),
            row("read what it produced", "read the result", "collect"),
        ]
    if state == "working":
        return [row("still running", "block until it reports", "wait")]
    if state == "blocked":
        return [
            row(
                "a person is needed (permission or question)",
                "read the screen and ask the user",
                "collect",
            ),
            row("answering the dialog needs the user's say-so", "ask the user what to answer"),
        ]
    if state == "unreported":
        return [row(*step) for step in UNREPORTED_STEPS]
    if state == "error":
        return [
            row("hc reports an error status", "collect the screen", "collect"),
            row("a person is needed", "ask the user what to do"),
            row("worker no longer needed", "end it", "stop"),
        ]
    if state == "exited":
        return [
            row("process ended", "read the output", "collect"),
            row("clean up", "end it", "stop"),
        ]
    return [
        row("state not reported", "check again", "status"),
        row("see the screen", "read the output", "collect"),
    ]


HC_NATIVE_KEYS = ("address", "name", "state", "status", "turn", "exit_code", "reason")
HERDR_NOTE = "read only; verify live agent identity before mapping a task to this pane"


ARGV_MAX_LENGTH = 40
MARKERS_PATH = Path(__file__).resolve().with_name("markers.json")
READ_LINES = 200
RESUME_TEXT = "continue the task where you stopped"


def trim_argv(argv):
    """Arguments with whitespace or over ARGV_MAX_LENGTH characters read <task>."""
    if not isinstance(argv, list):
        return argv
    return [
        "<task>"
        if isinstance(a, str) and (len(a) > ARGV_MAX_LENGTH or any(c.isspace() for c in a))
        else a
        for a in argv
    ]


def hc_native(raw):
    native = {key: raw.get(key) for key in HC_NATIVE_KEYS}
    foreground = raw.get("foreground")
    native["foreground"] = (
        {"argv": trim_argv(foreground.get("argv"))} if isinstance(foreground, dict) else None
    )
    return native


def hc_app(raw):
    """claude-code or codex, from status.app, else the foreground argv[0]."""
    status = raw.get("status")
    app = status.get("app") if isinstance(status, dict) else None
    if app in ("claude-code", "codex"):
        return app
    foreground = raw.get("foreground")
    argv = foreground.get("argv") if isinstance(foreground, dict) else None
    if isinstance(argv, list) and argv and isinstance(argv[0], str):
        return {"claude": "claude-code", "codex": "codex"}.get(os.path.basename(argv[0]))
    return None


def load_spec(app_name):
    return json.loads(MARKERS_PATH.read_text(encoding="utf-8")).get(app_name)


def is_rule(line):
    stripped = line.strip()
    return bool(stripped) and set(stripped) <= {"─", "━", "-"} and len(stripped) >= 8


def prompt_body(line, spec):
    stripped = line.strip()
    return stripped[len(spec["prompt"]) :].strip() if stripped.startswith(spec["prompt"]) else None


def history_lines(lines, app_name, spec):
    """The lines before the input box: Claude's box is the prompt line between the two bottom rules,
    Codex's is the last prompt line (composer, with placeholder or suggestion text)."""
    if app_name == "codex":
        for index in range(len(lines) - 1, -1, -1):
            if prompt_body(lines[index], spec) is not None:
                return lines[:index]
        return lines
    rules = [i for i, line in enumerate(lines) if is_rule(line)]
    for upper, lower in zip(rules, rules[1:]):
        inside = [l for l in lines[upper + 1 : lower] if l.strip()]
        if (
            len(inside) == 1
            and prompt_body(inside[0], spec) is not None
            and not any(prompt_body(l, spec) is not None for l in lines[lower + 1 :])
        ):
            return lines[:upper]
    return lines


def prompt_segments(text, app_name):
    """[(prompt line, lines after it)] over the history, input box excluded; [] for an unknown app."""
    spec = load_spec(app_name)
    if not spec:
        return []
    segments = []
    for line in history_lines(text.splitlines(), app_name, spec):
        if prompt_body(line, spec):
            segments.append((line.strip(), []))
        elif segments:
            segments[-1][1].append(line)
        else:
            segments.append((None, [line]))
    return segments


def last_prompt_block(screen_text, app_name):
    """Lines after the last real task or message prompt, input box and its footer excluded."""
    segments = [s for s in prompt_segments(screen_text, app_name) if s[0] is not None]
    return list(segments[-1][1]) if segments else []


def is_marker(line, spec):
    stripped = line.strip()
    return stripped.startswith(spec["anchor"]) and any(m in stripped for m in spec["interrupt"])


def interruption_count(text, app_name):
    """(interrupted after the last prompt, distinct interrupt events in the history). Redrawn history
    repeats identical segments, so a segment equal to the one before it is one event."""
    spec = load_spec(app_name)
    segments = prompt_segments(text, app_name)
    if not spec or not any(s[0] for s in segments):
        return False, 0
    tail = any(is_marker(l, spec) for l in segments[-1][1]) if segments[-1][0] else False
    events, previous = 0, None
    for prompt, lines in segments:
        key = (prompt, tuple(l.strip() for l in lines if l.strip()))
        if key != previous and any(is_marker(l, spec) for l in lines):
            events += 1
        previous = key
    return tail, events


def interrupted_rows(name, history):
    if history >= 2:
        return [
            row(
                "interrupted again; history holds %d interrupts" % history,
                "ask the user whether to resume %s" % name,
            )
        ]
    return [row("interrupted by a person or Esc", RESUME_TEXT, "resume")]


def read_interruption(raw, state, name):
    """(state, next rows) when the screen shows an interrupt; None when it does not or cannot be read."""
    cli = hc_app(raw)
    try:
        spec = load_spec(cli)
        if not spec or state not in spec["read_states"]:
            return None
        text = json.loads(run_command(["hc", "read", name, "--lines", str(READ_LINES), "--json"]))[
            "text"
        ]
        tail, history = interruption_count(text, cli)
    except Exception:
        return None
    return ("interrupted", interrupted_rows(name, history)) if tail else None


def hc_record(name, raw):
    state = hc_state(raw)
    parent = raw.get("parent")
    label = raw.get("name") or raw.get("address") or name
    rows = hc_next(label, state)
    found = read_interruption(raw, state, raw.get("address") or name)
    if found:
        state, rows = found
    return record(
        "hc",
        label,
        state,
        role="worker" if parent else "lead",
        parent=parent,
        turn=raw.get("turn"),
        native=hc_native(raw),
        next_rows=rows,
    )


def herdr_snapshot(pane, session, env):
    if env.get("HERDR_ENV") != "1":
        raise ValueError("Herdr inspection requires a Herdr-managed caller")
    if not (env.get("HERDR_SOCKET_PATH") and env.get("HERDR_PANE_ID")):
        raise ValueError("missing Herdr caller identity")
    if not (session and session.strip()):
        raise ValueError("supply the exact Herdr session name")
    inherited = env.get("HERDR_SESSION")
    if inherited and inherited != session:
        raise ValueError("session differs from caller's session")
    target = pane or env["HERDR_PANE_ID"]
    if target.startswith("-"):
        raise ValueError("invalid pane ID")
    result = json.loads(run_command(["herdr", "pane", "get", target, "--session", session]))
    return {"session": session, "pane": target, "snapshot": result}


def find_key(value, key):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        value = list(value.values())
    if isinstance(value, list):
        for item in value:
            found = find_key(item, key)
            if found is not None:
                return found
    return None


def status(runtime, name, env, session=None):
    """One contract record for one agent. Never raises on runtime failures."""
    try:
        if runtime == "hc":
            return hc_record(name, json.loads(run_command(["hc", "get", name, "--json"])))
        if runtime == "herdmates":
            snap = herdr_snapshot(name, session or env.get("HERDR_SESSION"), env)
            herdr = find_key(snap["snapshot"], "agent_status")
            state = herdr if herdr in ("idle", "working", "blocked", "done") else "unknown"
            rows = {
                "working": [
                    row("still running", "end your turn; its message arrives as input", "wait")
                ],
                "idle": [
                    row("turn ended", "send the next instruction", "message"),
                    row("read what it produced", "read its last message", "collect"),
                ],
                "done": [
                    row("work finished", "read its last message and the task result", "collect"),
                    row("no longer needed", "ask Claude to shut it down", "stop"),
                ],
                "blocked": [row("a person is needed", "read the pane and ask the user", "collect")],
            }.get(state, [row("state not reported", "check the native task list", "status")])
            native = {
                "pane": snap["pane"],
                "session": snap["session"],
                "agent_status": herdr,
                "note": HERDR_NOTE,
            }
            return record("herdmates", snap["pane"], state, native=native, next_rows=rows)
    except READ_ERRORS as error:
        return record(
            runtime,
            name,
            "unknown",
            native={"error": str(error)},
            next_rows=[
                row("status could not be read", "check again", "status"),
                row("see the output", "read it", "collect"),
            ],
        )
    return record(
        "native",
        name,
        "unknown",
        native={},
        next_rows=[
            row("no process to query", "use the subagent's return value", "collect"),
            row("ask it directly", "SendMessage to %s" % name, "message"),
        ],
    )


def list_children(runtime, env):
    """hc: one record per live terminal whose parent is the caller. Other runtimes have no list."""
    if runtime != "hc":
        return []
    me = env.get("HC_TERMINAL_ID")
    infos = []
    if not me:
        return infos
    try:
        listing = json.loads(run_command(["hc", "ls", "--all", "--json"]))
    except READ_ERRORS:
        return infos
    for entry in listing.get("terminals", []):
        if entry.get("parent") != me:
            continue
        address = entry.get("address")
        try:
            infos.append(
                hc_record(address, json.loads(run_command(["hc", "get", address, "--json"])))
            )
        except READ_ERRORS:
            continue
    return infos


def cmd_resolve(args):
    info = resolve(os.environ, args.cli)
    if args.json:
        info = dict(info, caller=caller_identity(os.environ, info))
    print(json.dumps(info) if args.json else format_line(info))
    return 0


def runtime_arg(args):
    return args.runtime or resolve(os.environ, args.cli)["runtime"]


def dump(value, args):
    return json.dumps(value, indent=2) if args.pretty else json.dumps(value, separators=(",", ":"))


def cmd_status(args):
    info = status(runtime_arg(args), args.name, os.environ, session=args.session)
    print(dump(info, args))
    return 0


def cmd_list(args):
    runtime = runtime_arg(args)
    if runtime != "hc":
        print("list is only available under hc; use the runtime's own tools", file=sys.stderr)
    print(dump(list_children(runtime, os.environ), args))
    return 0


def hc_tools_module():
    """The MCP tool table; imported lazily so resolve, status and list never load the dispatch store."""
    from . import hc_tools

    return hc_tools


def live_lead_conflict(hc_tools, env):
    """The generation of this terminal's latest team when it differs from ours and that team is live, else None.
    Live means wait(all) still has something for it: unfinished assignments or results not yet acknowledged,
    or any unacknowledged message to its lead. One hc terminal has one live lead generation. The check reads
    in one transaction; the team row is inserted later by the tool itself, so a concurrent call under the
    live generation can still land in between (the driver runs in its own terminal, which keeps this rare)."""
    _, caller = hc_tools.require_hc(env)
    scope, generation = hc_tools.dispatch_identity(env)
    store = hc_tools.dispatch.Store(env)
    with store.transaction() as db:
        row = db.execute(
            "SELECT id, generation FROM teams WHERE scope=? AND lead=? ORDER BY rowid DESC LIMIT 1",
            (scope, caller),
        ).fetchone()
        if not row or row[1] == generation:
            return None
        live = (
            db.execute(
                "SELECT 1 FROM (%s) LIMIT 1" % hc_tools.dispatch.SNAPSHOT_SQL,
                (row[0], "all", "all", "all", "all", "all", "all"),
            ).fetchone()
            or db.execute(
                "SELECT 1 FROM messages WHERE team=? AND recipient=? AND acked=0 LIMIT 1",
                (row[0], caller),
            ).fetchone()
        )
    return row[1] if live else None


MAX_CALL_ANCESTOR_HOPS = 40
CALL_ANCESTRY_BUDGET_SECONDS = 5.0
CALL_PS_TIMEOUT_SECONDS = 2.0  # a refusal is cheaper than a timeout flake on a loaded machine


def call_ancestry_refusal(terminal_pid):
    """Why `call` must not run here, or None. Walks the process ancestors up to this hc terminal's root
    process, checking that one too: a claude/codex executable on the way means an LLM launched this process,
    whatever shell form it used. The stop point is the terminal's pid from `hc get`, never a process name,
    which a caller controls. Reaching pid 1 first (an orphan, or an `hc exec` daemon) refuses. Fail closed:
    an unreadable table or a walk that ends elsewhere refuses."""
    deadline = time.monotonic() + CALL_ANCESTRY_BUDGET_SECONDS
    pid, seen = os.getppid(), set()
    for _ in range(MAX_CALL_ANCESTOR_HOPS):
        if pid <= 1:
            return (
                "process ancestry reached pid 1 without reaching this hc terminal, so cannot prove this "
                "is not an agent session"
            )
        if pid in seen or time.monotonic() > deadline:
            break
        seen.add(pid)
        entry = (
            real_ps_entry(pid, CALL_PS_TIMEOUT_SECONDS)
            if ps_entry is real_ps_entry
            else ps_entry(pid)
        )
        if not entry:
            return (
                "cannot read the process table at pid %d, so cannot prove this is not an agent session"
                % pid
            )
        parent, args = entry
        found = cli_of_args(args)
        if found:
            return (
                "refusing: ancestor process %d (%s) is an agent session; `call` is for scripts in a plain "
                "terminal or hc pane, agents use the MCP tools" % (pid, found)
            )
        if pid == terminal_pid:
            return None
        pid = parent
    return "process ancestry did not reach this hc terminal, so cannot prove this is not an agent session"


def terminal_root_pid(terminal_id):
    """The root process of an hc terminal (`hc get` field pid), or None when it cannot be read."""
    if not terminal_id:
        return None
    try:
        pid = hc_get(terminal_id).get("pid")
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, AttributeError):
        return None
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 1 else None


def cmd_call(args):
    hc_tools = hc_tools_module()
    try:
        arguments = json.loads(args.json)
    except ValueError:
        arguments = None
    if (
        args.tool not in hc_tools.HANDLERS
        or not isinstance(arguments, dict)
        or (args.lead is not None and not LEAD_ID.fullmatch(args.lead))
    ):
        print(
            "usage: agent-runtime call <tool> [--lead ID] [--json '{...}'] (tools: %s)"
            % " ".join(sorted(hc_tools.HANDLERS)),
            file=sys.stderr,
        )
        return 2
    env = dict(os.environ, AGENT_RUNTIME_DISPATCH="1")
    try:
        hc_tools.require_hc(env)
    except hc_tools.ToolError as error:
        print(error, file=sys.stderr)
        return 2
    refusal = call_ancestry_refusal(terminal_root_pid(os.environ.get("HC_TERMINAL_ID")))
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    forced = (hc_tools.lib.FORCED_GENERATION, hc_tools.lib.FORCED_LEAD)
    if args.lead:
        hc_tools.lib.FORCED_GENERATION = (
            args.lead
        )  # hc_tools reads the generation from its own module copy
        hc_tools.lib.FORCED_LEAD = True
    out: dict
    try:
        live = live_lead_conflict(hc_tools, env)
        if live:
            print(
                "this terminal's live lead generation is %s with unfinished assignments or unacknowledged reports; "
                "run this driver in its own hc terminal or pass --lead %s" % (live, live),
                file=sys.stderr,
            )
            return 2
        out = hc_tools.call(args.tool, arguments, env, hc_tools.Context())
    except (
        Exception
    ) as error:  # the MCP server reports any failure as an error result; so does the CLI
        out = {
            "content": [{"type": "text", "text": "%s failed: %s" % (args.tool, error)}],
            "isError": True,
        }
    finally:
        # the override lives only for this call: a later call in the same process must resolve as it did before
        hc_tools.lib.FORCED_GENERATION, hc_tools.lib.FORCED_LEAD = forced
    if out.get("isError"):
        print(json.dumps(out, separators=(",", ":")))
        return 1
    text = out["content"][0]["text"]
    try:
        json.loads(text)
    except ValueError:
        text = json.dumps({"result": text})
    print(text)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="agent-runtime", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve", help="print the runtime, role and parent of this agent")
    p.add_argument(
        "--cli",
        choices=CLI_CHOICES,
        default=None,
        help="default: detected from the environment, else claude",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("status", help="one JSON record for one agent")
    p.add_argument("name", help="hc terminal name or address; Herdr pane ID under herdmates")
    p.add_argument("--session", help="exact Herdr session name (herdmates)")
    p.add_argument(
        "--cli",
        choices=CLI_CHOICES,
        default=None,
        help="default: detected from the environment, else claude",
    )
    p.add_argument("--runtime", choices=RUNTIMES, help="override the resolved runtime")
    p.add_argument("--pretty", action="store_true", help="indent the JSON")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("list", help="JSON records for the agents you started (hc)")
    p.add_argument(
        "--cli",
        choices=CLI_CHOICES,
        default=None,
        help="default: detected from the environment, else claude",
    )
    p.add_argument("--runtime", choices=RUNTIMES, help="override the resolved runtime")
    p.add_argument("--pretty", action="store_true", help="indent the JSON")
    p.set_defaults(func=cmd_list)
    # allow_abbrev=False: hooks/pre_tool_use.py parse_call() reads these options exactly; keep both in step.
    p = sub.add_parser(
        "call",
        help="run one dispatch tool in an hc terminal and print its JSON result",
        allow_abbrev=False,
    )
    p.add_argument("tool", help="an MCP tool name (the usage error lists them)")
    p.add_argument(
        "--lead",
        help="lead identity (A-Z a-z 0-9 . _ : -, up to 128); processes passing the same ID share one team",
    )
    p.add_argument("--json", default="{}", help="the tool arguments as one JSON object")
    p.set_defaults(func=cmd_call)
    return parser


def fallback_resolve(argv):
    """Last resort for hooks: always a valid line, never a failure."""
    cli = detect_cli(os.environ) or "claude"
    for i, arg in enumerate(argv):
        value = (
            arg.split("=", 1)[1]
            if arg.startswith("--cli=")
            else (argv[i + 1] if arg == "--cli" and i + 1 < len(argv) else None)
        )
        if value in CLI_CHOICES:
            cli = value
    info = {"runtime": "native", "role": "unknown", "cli": cli}
    print(json.dumps(info) if "--json" in argv else format_line(info))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["resolve"]:
        try:
            args = build_parser().parse_args(argv)
            return args.func(args)
        except (Exception, SystemExit):
            return fallback_resolve(argv)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
