"""The fake agent-runtime: the runtime the driver talks to, answered in process.

The driver asks the runtime through one seam (`runtime_process` in `orchestrate.py`), so the tests answer it here
instead of starting a process per call. The executable the fixture puts on PATH is a wrapper around this module: one
contract, two ways to reach it.

The state lives in files under `FAKE_RUNTIME_HOME`, so a run can be scripted before it starts and read after it ends:
`calls.jsonl` (every call), `waits.json` (the scripted wait steps), `states.json` (the agents' states: a name's state is what `status` answers, `error` makes the call fail, `not_found`
answers `unknown` the way hc does for a terminal it has none of, and `unreadable` answers `unknown` for a failed
read), `refuse.json`
(the names the runtime refuses to spawn), `stopped.json` (the names the driver stopped) and `assignments.json` (the
assignment ids each agent was given).

A wait step may name the agents it reports for: `agents` lists the agents the runtime still holds open, `complete`
lists the agents that finished their turn with a `complete` message, and `runtime_state` is the state the runtime
observed for them (`idle` when the turn ended without a message). The ids come from the assignments the runtime gave
out, so a step keeps naming the right agent after a resume.
"""

import json
import os
import subprocess
import sys
from typing import Any


class Completed:
    """What `subprocess.run` returns: the exit code and the two streams."""

    def __init__(self, argv, returncode=0, stdout="", stderr=""):
        self.args = argv
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def read_lines(path):
    """The file's lines, or nothing when it is not there."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as stream:
        return stream.read().splitlines()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)


def read_json(path, default=None) -> Any:
    """The file's JSON, or the default when it is not there."""
    if not os.path.exists(path):
        return default if default is not None else []
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    """The file's new content."""
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream)


def assignment_ids(home):
    """The assignment ids each agent was given, oldest first: name -> [id, ...]."""
    return read_json(os.path.join(home, "assignments.json"), {}) or {}


def remember_assignment(home, name, assignment_id):
    """The agent's assignment ids, for the wait steps that name it."""
    ids = assignment_ids(home)
    known = list(ids.get(name) or [])
    if assignment_id not in known:
        known.append(assignment_id)
    ids[name] = known[-5:]
    write_json(os.path.join(home, "assignments.json"), ids)


def answer(name, assignment_id):
    """The agent's answer: its worker id, assignment id, name, address and parent."""
    return {
        "worker_id": "w-" + name,
        "assignment_id": assignment_id,
        "name": name,
        "address": "term-" + name,
        "parent": "driver",
    }


def process(argv, timeout=None):
    """The answer to one `agent-runtime call <tool> --lead <lead> --json <arguments>` argv (without the program name)."""
    del timeout  # the seam passes one; the fake answers at once, so there is nothing to time out
    home = os.environ["FAKE_RUNTIME_HOME"]
    tool = argv[1]
    lead = argv[argv.index("--lead") + 1] if "--lead" in argv else None
    args = json.loads(argv[argv.index("--json") + 1]) if "--json" in argv else {}
    events_path = os.environ.get("FAKE_EVENTS", "")
    seen = read_lines(events_path) if events_path and os.path.exists(events_path) else []
    with open(os.path.join(home, "calls.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps({"tool": tool, "lead": lead, "args": args, "events": seen}) + "\n")
    rows = [json.loads(line) for line in read_lines(os.path.join(home, "calls.jsonl"))]
    nth = sum(1 for r in rows if r["tool"] == tool and r["args"].get("name") == args.get("name"))
    if tool == "spawn":
        if args["name"] in read_json(os.path.join(home, "refuse.json")):
            return Completed(
                argv,
                1,
                json.dumps({"content": [{"type": "text", "text": "name_taken"}], "isError": True}),
            )
        # A name spawned again has a new, live terminal. The first spawn keeps the state a test scripted for the name.
        states = read_json(os.path.join(home, "states.json"))
        if nth > 1 and states.pop(args["name"], None) is not None:
            write_json(os.path.join(home, "states.json"), states)
        stopped_path = os.path.join(home, "stopped.json")
        stopped = read_json(stopped_path)
        if args["name"] in stopped:
            write_json(stopped_path, [n for n in stopped if n != args["name"]])
        assignment = answer(
            args["name"], "a-%s%s" % (args["name"], "" if nth == 1 else "-%d" % nth)
        )
        remember_assignment(home, args["name"], assignment["assignment_id"])
        return Completed(argv, 0, json.dumps(assignment))
    if tool == "wait":
        queue_path = os.path.join(home, "waits.json")
        queue = read_json(queue_path)
        step = queue.pop(0) if queue else {}
        write_json(queue_path, queue)
        # A step's files land first and its commands run next; a file naming `@HEAD` is written last, with the sha
        # the checkout is at once those commands ran, as a reviewer who reviewed that commit would record it.
        writes = step.pop("write", [])
        for path, text in writes:
            if "@HEAD" not in text:
                write_text(path, text)
        for path, old, new in step.pop("replace", []):
            with open(path, encoding="utf-8") as stream:
                text = stream.read()
            assert old in text, old
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(text.replace(old, new, 1))
        for command in step.pop("run", []):
            subprocess.run(command, check=True, capture_output=True)
        for path, text in writes:
            if "@HEAD" in text:
                head = subprocess.run(
                    ["git", "-C", os.environ["FAKE_CHECKOUT"], "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                write_text(path, text.replace("@HEAD", head))
        return Completed(argv, 0, json.dumps(wait_answer(home, step)))
    if tool == "status":
        states = read_json(os.path.join(home, "states.json"))
        state = states.get(args["name"], "idle")
        if state == "error":
            return Completed(argv, 1, "", "the runtime does not know this agent")
        if state in ("not_found", "unreadable"):
            # what the real `status` answers when hc cannot read the terminal: `unknown`, with the reason in
            # `native.error`. hc's own "no such terminal" is `not_found`; anything else is a failed read.
            reason = (
                json.dumps(
                    {"error": {"kind": "not_found", "message": 'no terminal "%s"' % args["name"]}}
                )
                if state == "not_found"
                else "hc get timed out"
            )
            return Completed(
                argv,
                0,
                json.dumps({"name": args["name"], "state": "unknown", "native": {"error": reason}}),
            )
        return Completed(argv, 0, json.dumps({"name": args["name"], "state": state}))
    if tool == "stop":
        states = read_json(os.path.join(home, "states.json"))
        states[args["name"]] = "exited"
        write_json(os.path.join(home, "states.json"), states)
        stopped_path = os.path.join(home, "stopped.json")
        write_json(stopped_path, read_json(stopped_path) + [args["name"]])
        return Completed(argv, 0, json.dumps({"name": args["name"], "state": "exited"}))
    if tool == "resume":
        assignment = answer(args["name"], "r%d-%s" % (nth, args["name"]))
        remember_assignment(home, args["name"], assignment["assignment_id"])
        return Completed(argv, 0, json.dumps(assignment))
    return Completed(argv, 0, json.dumps({}))


def wait_answer(home, step):
    """What one wait answers: the step's own fields, plus the agents it names.

    `agents` lists the agents the runtime still holds open, `complete` the ones that finished their turn with a
    `complete` message, and `runtime_state` the state the runtime observed for them (`idle` when the turn ended
    without a message). The ids are the ones the runtime gave out, so a step keeps naming the right agent after a
    resume.
    """
    result: dict[str, Any] = dict({"reason": "empty", "reports": [], "unfinished": []}, **step)
    runtime_state = result.pop("runtime_state", None)
    complete = [name for name in result.pop("complete", []) or []]
    agents = [name for name in result.pop("agents", []) or [] if name not in complete]
    if not complete and not agents:
        return result
    known = assignment_ids(home)
    entries = list(result.get("assignments") or [])
    messages = list(result.get("messages") or [])
    for name in agents + complete:
        ids = list(known.get(name) or []) or ["a-" + name]
        entry = {
            "assignment_id": ids[-1],
            "worker_id": "w-" + name,
            "state": "working",
            "name": name,
        }
        if runtime_state:
            entry["runtime_state"] = runtime_state
        if name in complete:
            entry["state"] = "complete"
            for (
                assignment_id
            ) in ids:  # the message names every id the agent had: the driver keeps the one it knows
                messages.append(
                    {
                        "message_id": "m-fake-%s-%s" % (name, assignment_id),
                        "team_id": "team",
                        "worker_id": "w-" + name,
                        "assignment_id": assignment_id,
                        "sender": "w-" + name,
                        "recipient": "lead",
                        "kind": "complete",
                        "text": "complete - see run directory",
                    }
                )
        entries.append(entry)
    result["assignments"] = entries
    result["messages"] = messages
    result["unfinished"] = [
        entry["assignment_id"] for entry in entries if entry["state"] != "complete"
    ]
    return result


def main(argv):
    """The executable on PATH: answer one call and exit with its code."""
    result = process(argv)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode
