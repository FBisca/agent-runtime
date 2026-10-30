#!/usr/bin/env python3
"""Agent runtime library and debug CLI (resolve, status, list). No model calls or agent spawns."""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HC_TIMEOUT_SECONDS = 2
RUNTIMES = ("herdmates", "hc", "native")
FIELDS = ("runtime", "role", "parent", "cli")


def hc_get(terminal_id):
    result = subprocess.run(["hc", "get", terminal_id], capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=HC_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "hc get failed")
    return json.loads(result.stdout)


CODEX_ENV_MARKERS = ("CODEX_THREAD_ID", "CODEX_TOOL_CALL_ID")
MAX_ANCESTOR_HOPS = 15
ANCESTRY_BUDGET_SECONDS = 0.5
PS_TIMEOUT_SECONDS = 0.2


def real_ps_entry(pid, timeout=PS_TIMEOUT_SECONDS):
    """(ppid, args) of one process via ps, or None on any failure."""
    try:
        result = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=timeout)
        if result.returncode != 0:
            return None
        ppid, _, args = result.stdout.strip().partition(" ")
        return int(ppid), args.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


ps_entry = real_ps_entry


def cli_of_args(args):
    """`codex` or `claude` when argv[0] (or argv[1] after a node/bun wrapper) has that basename."""
    tokens = args.split()
    for token in tokens[:2]:
        name = os.path.basename(token)
        if name in ("codex", "claude"):
            return name
        if name not in ("node", "bun", "deno"):
            break
    return None


def ancestor_cli():
    """Nearest ancestor process that is codex or claude; None when the walk fails or finds nothing."""
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


def detect_cli(env):
    """Process ancestry first (env markers are inherited when one CLI starts the other), then env
    markers (Codex sets CODEX_THREAD_ID/CODEX_TOOL_CALL_ID; Claude Code sets CLAUDECODE), else None."""
    found = ancestor_cli()
    if found:
        return found
    if any(env.get(name) for name in CODEX_ENV_MARKERS):
        return "codex"
    return "claude" if env.get("CLAUDECODE") else None


def foreground_cli(reply):
    """Fallback under hc: the terminal's foreground program is codex or claude."""
    foreground = reply.get("foreground") if isinstance(reply, dict) else None
    argv = foreground.get("argv") if isinstance(foreground, dict) else None
    if isinstance(argv, list) and argv and isinstance(argv[0], str):
        name = os.path.basename(argv[0])
        return name if name in ("codex", "claude") else None
    return None


def resolve(env, cli=None, hc_runner=hc_get):
    """Pure resolution: HERDR_PANE_ID > HC_TERMINAL_ID > native. Never raises on hc failure.
    cli=None detects it from the environment, then from hc, then defaults to claude."""
    explicit = cli
    detected = None if explicit else detect_cli(env)
    cli = explicit or detected or "claude"
    if env.get("HERDR_PANE_ID"):
        runtime = "herdmates" if cli == "claude" else "native"
        return {"runtime": runtime, "role": "lead" if runtime == "native" else "unknown", "cli": cli}
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
            return {"runtime": "hc", "role": "worker", "parent": parent, "cli": cli}
        return unknown
    return {"runtime": "native", "role": "lead", "cli": cli}


def format_line(info):
    return " ".join("%s=%s" % (key, info[key]) for key in FIELDS if key in info)


CONTRACT_STATES = ("working", "idle", "interrupted", "blocked", "done", "exited", "unknown")
READ_ERRORS = (OSError, RuntimeError, ValueError, subprocess.SubprocessError)


def run_command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                            timeout=HC_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Command failed: " + argv[0])
    return result.stdout.strip()


def row(why, do, verb=None):
    return {"why": why, "do": do, "verb": verb, "gap": verb is None}


def record(runtime, name, state, role="unknown", parent=None, turn=None, native=None, next_rows=()):
    return {"name": name, "runtime": runtime, "role": role, "parent": parent, "state": state,
            "turn": turn, "native": native, "next": list(next_rows)}


def hc_state(raw):
    """Map an `hc get`/`hc ls` record (status is an object {app, state} or a string) to a contract state."""
    status = raw.get("status")
    if isinstance(status, dict):
        status = status.get("state")
    if "exited" in (raw.get("state"), status) or "finished" in (raw.get("state"), status):
        return "exited"
    return status if status in ("working", "idle", "blocked", "done") else "unknown"


def hc_next(name, state):
    if state == "done":
        return [row("work finished", "read the result", "collect"), row("worker no longer needed", "end it", "stop")]
    if state == "idle":
        return [row("turn ended, awaiting input", "send the next instruction", "message"),
                row("read what it produced", "read the result", "collect")]
    if state == "working":
        return [row("still running", "block until it reports", "wait")]
    if state == "blocked":
        return [row("a person is needed (permission or question)", "read the screen and ask the user", "collect"),
                row("answering the dialog needs the user's say-so", "ask the user what to answer")]
    if state == "exited":
        return [row("process ended", "read the output", "collect"), row("clean up", "end it", "stop")]
    return [row("state not reported", "check again", "status"), row("see the screen", "read the output", "collect")]


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
    return ["<task>" if isinstance(a, str) and (len(a) > ARGV_MAX_LENGTH or any(c.isspace() for c in a)) else a
            for a in argv]


def hc_native(raw):
    native = {key: raw.get(key) for key in HC_NATIVE_KEYS}
    foreground = raw.get("foreground")
    native["foreground"] = {"argv": trim_argv(foreground.get("argv"))} if isinstance(foreground, dict) else None
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
    return stripped[len(spec["prompt"]):].strip() if stripped.startswith(spec["prompt"]) else None


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
        inside = [l for l in lines[upper + 1:lower] if l.strip()]
        if len(inside) == 1 and prompt_body(inside[0], spec) is not None and not any(
                prompt_body(l, spec) is not None for l in lines[lower + 1:]):
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
        return [row("interrupted again; history holds %d interrupts" % history,
                    "ask the user whether to resume %s" % name)]
    return [row("interrupted by a person or Esc", RESUME_TEXT, "resume")]


def read_interruption(raw, state, name):
    """(state, next rows) when the screen shows an interrupt; None when it does not or cannot be read."""
    cli = hc_app(raw)
    try:
        spec = load_spec(cli)
        if not spec or state not in spec["read_states"]:
            return None
        text = json.loads(run_command(["hc", "read", name, "--lines", str(READ_LINES), "--json"]))["text"]
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
    return record("hc", label, state, role="worker" if parent else "lead", parent=parent,
                  turn=raw.get("turn"), native=hc_native(raw), next_rows=rows)


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
            rows = {"working": [row("still running", "end your turn; its message arrives as input", "wait")],
                    "idle": [row("turn ended", "send the next instruction", "message"),
                             row("read what it produced", "read its last message", "collect")],
                    "done": [row("work finished", "read its last message and the task result", "collect"),
                             row("no longer needed", "ask Claude to shut it down", "stop")],
                    "blocked": [row("a person is needed", "read the pane and ask the user", "collect")],
                    }.get(state, [row("state not reported", "check the native task list", "status")])
            native = {"pane": snap["pane"], "session": snap["session"], "agent_status": herdr, "note": HERDR_NOTE}
            return record("herdmates", snap["pane"], state, native=native, next_rows=rows)
    except READ_ERRORS as error:
        return record(runtime, name, "unknown", native={"error": str(error)},
                      next_rows=[row("status could not be read", "check again", "status"),
                                 row("see the output", "read it", "collect")])
    return record("native", name, "unknown", native={},
                  next_rows=[row("no process to query", "use the subagent's return value", "collect"),
                             row("ask it directly", "SendMessage to %s" % name, "message")])


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
            infos.append(hc_record(address, json.loads(run_command(["hc", "get", address, "--json"]))))
        except READ_ERRORS:
            continue
    return infos


def cmd_resolve(args):
    info = resolve(os.environ, args.cli)
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


def build_parser():
    parser = argparse.ArgumentParser(prog="agent-runtime", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve", help="print the runtime, role and parent of this agent")
    p.add_argument("--cli", choices=("claude", "codex"), default=None,
                   help="default: detected from the environment, else claude")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_resolve)
    p = sub.add_parser("status", help="one JSON record for one agent")
    p.add_argument("name", help="hc terminal name or address; Herdr pane ID under herdmates")
    p.add_argument("--session", help="exact Herdr session name (herdmates)")
    p.add_argument("--cli", choices=("claude", "codex"), default=None,
                   help="default: detected from the environment, else claude")
    p.add_argument("--runtime", choices=RUNTIMES, help="override the resolved runtime")
    p.add_argument("--pretty", action="store_true", help="indent the JSON")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("list", help="JSON records for the agents you started (hc)")
    p.add_argument("--cli", choices=("claude", "codex"), default=None,
                   help="default: detected from the environment, else claude")
    p.add_argument("--runtime", choices=RUNTIMES, help="override the resolved runtime")
    p.add_argument("--pretty", action="store_true", help="indent the JSON")
    p.set_defaults(func=cmd_list)
    return parser


def fallback_resolve(argv):
    """Last resort for hooks: always a valid line, never a failure."""
    cli = detect_cli(os.environ) or "claude"
    for i, arg in enumerate(argv):
        value = arg.split("=", 1)[1] if arg.startswith("--cli=") else (
            argv[i + 1] if arg == "--cli" and i + 1 < len(argv) else None)
        if value in ("claude", "codex"):
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
