#!/usr/bin/env python3
"""Small orchestration records and the `run` dispatch driver.

No model calls and no shell evaluation. Only `run` starts workers, and only through `agent-runtime call`."""

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import contracts, events, evidence, routes, verify
from . import plan as yaml_plan
from .plan import PlanError
from .runtime_client import CliRuntimeClient
from .runtime_client import RuntimeError as RuntimeCallError


def now():
    return datetime.now(timezone.utc).isoformat()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as stream:
            raw = stream.read(65537)
    except OSError as error:
        raise ValueError("cannot read %s: %s" % (path, error)) from None
    require(len(raw) <= 65536, "Input exceeds 64 KiB; provide a compact record, not a transcript")
    try:
        return json.loads(raw)
    except ValueError as error:
        raise ValueError("%s is not valid JSON: %s" % (path, error)) from None


# A worker reports when its turn ends; the file the report names may land a moment later. A report is the news,
# the file is the payload, and the driver waits for the payload for this long before it believes it missing.
PAYLOAD_GRACE = 5.0
# Warnings one agent gets about a payload it announced and did not write; past this the Lead is asked instead.
PAYLOAD_WARNINGS = 2


def payload_waits(path, grace=None):
    """Wait, bounded, for the file a report names; True when it is there."""
    deadline = time.monotonic() + (PAYLOAD_GRACE if grace is None else grace)
    while not Path(path).exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


# A call that walks or writes the whole tree (`worktree add`, `status`) scales with the repository: a large one
# outruns `run()`'s default, which stays short so the small calls still fail fast.
TREE_TIMEOUT = 600


def run(argv, timeout=15):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, result.stderr.strip() or "Command failed: " + argv[0])
    return result.stdout.strip()


def checkout_snapshot(path):
    root = Path(path).resolve(strict=True)
    # one `git rev-parse` for the three answers: the root, the head and the branch
    identity = run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"]
    ).splitlines()
    require(len(identity) == 3, "git rev-parse answered %r" % identity)
    actual = Path(identity[0]).resolve()
    require(root == actual, "checkout must be the repository/worktree root")
    return {
        "checkout": str(root),
        "head": identity[1],
        "branch": identity[2],
        # This is an inventory, NOT a content fingerprint. Re-read affected source.
        "worktree_status": run(
            ["git", "-C", str(root), "status", "--porcelain=v1"], timeout=TREE_TIMEOUT
        ),
    }


def owned_paths(packet):
    root = Path(packet["checkout"]).resolve()
    owned = packet.get("owns")
    require(isinstance(owned, list) and owned, "owns must contain relative files or directories")
    paths = []
    for item in owned:
        require(
            isinstance(item, str) and item and not any(c in item for c in "*?[]"),
            "owns requires literal paths, not glob patterns",
        )
        rel = Path(item)
        require(
            not rel.is_absolute() and ".." not in rel.parts, "ownership must stay inside checkout"
        )
        target = (root / rel).resolve()
        require(target.is_relative_to(root), "ownership follows a symlink outside checkout")
        paths.append(target)
    return paths


@contextmanager
def ledger(path):
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    require(not path.is_symlink(), "ledger must not be a symlink")
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("ledger busy; inspect or retry after the other update") from None
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                try:
                    data = json.load(stream)
                except ValueError as error:
                    raise ValueError(
                        "the ledger %s is not valid JSON: %s" % (path, error)
                    ) from None
        else:
            data = {"version": 1, "tasks": {}}
        require(data.get("version") == 1 and isinstance(data.get("tasks"), dict), "invalid ledger")
        before = json.dumps(data, sort_keys=True)
        yield data
        if json.dumps(data, sort_keys=True) != before:
            tmp_fd, tmp_path = tempfile.mkstemp(prefix=".orchestrate-", dir=path.parent)
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, indent=2)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp_path, path)
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)


ROUTE = {
    "size": ("small", "regular", "big"),
    "difficulty": ("simple", "complex"),
    "class": ("small", "medium", "large"),
    "effort": ("low", "medium", "high", "xhigh", "max"),
}


def routed(packet):
    """A packet opts into routing with a cell key; model or effort alone stay legacy metadata."""
    return any(key in packet for key in ("size", "difficulty", "class"))


def whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def claim(data, packet):
    require(isinstance(packet, dict), "packet must be an object")
    for key in ("task_id", "worker", "plan", "objective", "checkout", "backend"):
        require(isinstance(packet.get(key), str) and packet[key].strip(), "missing " + key)
    require(packet["backend"] in ("codex", "claude", "pi"), "backend must be codex, claude or pi")
    require(
        isinstance(packet.get("acceptance"), list) and packet["acceptance"],
        "acceptance is required",
    )
    require(isinstance(packet.get("checks"), list), "checks must be a list")
    require(
        all(isinstance(c, str) and c.strip() for c in packet["checks"]), "invalid check command"
    )
    require(
        all(isinstance(c, str) and c.strip() for c in packet["acceptance"]),
        "invalid acceptance criterion",
    )
    require(
        isinstance(packet.get("depends_on", []), list)
        and all(isinstance(c, str) for c in packet.get("depends_on", [])),
        "invalid dependency list",
    )
    if routed(packet):
        for key, allowed in ROUTE.items():
            require(packet.get(key) in allowed, key + " must be one of " + ", ".join(allowed))
        require(
            isinstance(packet.get("model"), str) and packet["model"].strip(),
            "a routed packet needs its model",
        )
        require(
            isinstance(packet.get("unit"), str) and packet["unit"].strip(),
            "a routed packet needs its unit",
        )
    snapshot = checkout_snapshot(packet["checkout"])
    packet = dict(packet, checkout=snapshot["checkout"])
    paths = owned_paths(packet)
    task_id = packet["task_id"]
    tasks = data["tasks"]
    if task_id in tasks:
        require(tasks[task_id]["packet"] == packet, "task ID already has a different assignment")
        return {"action": "existing", "task": tasks[task_id]}
    for dep in packet.get("depends_on", []):
        require(
            dep in tasks and tasks[dep]["status"] == "COMPLETE", "dependency not complete: " + dep
        )
    for task in tasks.values():
        if task["status"] in ("COMPLETE", "RELEASED"):
            continue
        other = task["packet"]
        require(other["worker"] != packet["worker"], "worker already has an unfinished assignment")
        for left in paths:
            for right in owned_paths(other):
                require(
                    not (left.is_relative_to(right) or right.is_relative_to(left)),
                    "ownership overlaps task " + other["task_id"],
                )
    tasks[task_id] = {
        "packet": packet,
        "status": "RESERVED",
        "runtime_id": None,
        "started_at": now(),
        "checkout_at_claim": snapshot,
    }
    return {"action": "reserved", "task": tasks[task_id]}


def bind(data, task_id, runtime_id):
    task = data["tasks"][task_id]
    require(task["status"] not in ("COMPLETE", "RELEASED"), "assignment is closed")
    require(runtime_id.strip(), "runtime ID is empty")
    require(
        task["runtime_id"] in (None, runtime_id),
        "assignment already belongs to another runtime agent",
    )
    for other_id, other in data["tasks"].items():
        if other_id != task_id and other["status"] not in ("COMPLETE", "RELEASED"):
            require(
                other["runtime_id"] != runtime_id,
                "runtime agent already has an unfinished assignment",
            )
    task.update(runtime_id=runtime_id, status="ACTIVE")
    return task


# Launcher words a worker may put in front of a gate command. Closed on purpose: an
# `endswith` match would let `echo scripts/checks.sh` pass as the gate.
# Whole prefix sequences, never single words: `python` alone is not a launcher of the gate.
CHECK_PREFIXES = (("bash",), ("sh",), ("uv", "run"), ("python", "-m"), ("python3", "-m"))


def normalise_check(command):
    """The gate command, with launcher prefixes and a leading `./` removed, so `bash scripts/checks.sh` matches `scripts/checks.sh`."""
    tokens = command.split()
    changed = True
    while changed and tokens:
        changed = False
        for prefix in CHECK_PREFIXES:
            if tuple(tokens[: len(prefix)]) == prefix:
                del tokens[: len(prefix)]
                changed = True
                break
        else:
            if tokens[0].startswith("./"):
                tokens[0] = tokens[0][2:]
                changed = True
    return " ".join(tokens)


def merged_snapshot(path):
    """The checkout's snapshot, or None when the tree is gone: after a merge the snapshot is evidence, not a
    precondition."""
    try:
        return checkout_snapshot(path)
    except FileNotFoundError:
        return None


def require_completable(task, report):
    """The rules a report meets to be COMPLETE, apart from the ledger's own state. Raises ValueError naming the first
    that fails; `checkpoint` and the report gate share it, so a report is refused for the same reason in both."""
    # Total over a schema-valid report: the report gate runs before the ledger's structural checks, which
    # turn a malformed `remaining` or check into the Lead request they always did.
    checks = [c for c in report.get("checks") or [] if isinstance(c, dict)]
    require(report.get("acceptance_met") is True, "acceptance criteria are not confirmed")
    require(not report.get("remaining"), "complete result has remaining work")
    failed = [str(c.get("command")) for c in checks if c.get("outcome") == "failed"]
    require(not failed, "complete result has failed checks: " + ", ".join(failed))
    required = {normalise_check(c): c for c in task["packet"]["checks"]}
    passed = {
        normalise_check(c["command"])
        for c in checks
        if c.get("outcome") == "passed" and isinstance(c.get("command"), str)
    }
    missing = [command for key, command in required.items() if key not in passed]
    require(
        not missing,
        "required checks are not passed: " + ", ".join(sorted(missing)),
    )
    if routed(task["packet"]):
        require("review_rounds" in report, "a routed task needs review_rounds")
        require(
            isinstance(report.get("usage"), str) and report["usage"].strip(),
            "a routed task needs usage (the runtime's metric line, or unknown)",
        )


def checkpoint(data, task_id, report, merged=False):
    task = data["tasks"][task_id]
    require(isinstance(report, dict), "report must be an object")
    require(task["status"] not in ("COMPLETE", "RELEASED"), "assignment is closed")
    require(report.get("task_id") == task_id, "report task ID does not match")
    status = report.get("status")
    require(status in ("PARTIAL", "BLOCKED", "COMPLETE"), "invalid result status")
    require(
        isinstance(report.get("summary"), str) and report["summary"].strip(), "summary is required"
    )
    for key in ("files", "decisions", "interfaces", "remaining", "checks"):
        require(isinstance(report.get(key), list), key + " must be a list")
    checks = report["checks"]
    for check in checks:
        require(isinstance(check, dict) and isinstance(check.get("command"), str), "invalid check")
        require(check.get("outcome") in ("passed", "failed", "not_run"), "invalid check outcome")
        require(
            check.get("outcome") == "passed" or check.get("reason"),
            "failed or omitted check needs a reason",
        )
    require(
        "review_rounds" not in report or whole(report["review_rounds"]),
        "review_rounds must be a whole number",
    )
    if status == "COMPLETE":
        require(task["runtime_id"], "bind the runtime agent before completion")
        require_completable(task, report)
        task["finished_at"] = now()
    elif status == "BLOCKED":
        require(
            report.get("blocker") and report["remaining"],
            "blocked result needs a blocker and next action",
        )
    task.update(
        status=status,
        report=report,
        updated_at=now(),
        # A live worker with no tree is a real fault, so only the merge path tolerates a missing one.
        checkout_at_checkpoint=(merged_snapshot if merged else checkout_snapshot)(
            task["packet"]["checkout"]
        ),
    )
    return task


def settle_merged(data, task_id, report):
    """A merged unit is COMPLETE whatever its report says: the merge is final. The checks stay as the worker reported
    them; the caller records why the rules were not met."""
    task = data["tasks"][task_id]
    require(task["status"] not in ("COMPLETE", "RELEASED"), "assignment is closed")
    task["finished_at"] = now()
    task.update(
        status="COMPLETE",
        report=report,
        updated_at=now(),
        checkout_at_checkpoint=merged_snapshot(task["packet"]["checkout"]),
    )
    return task


def release(data, task_id, reason, cutoff=False):
    task = data["tasks"][task_id]
    require(reason.strip(), "release requires a reason")
    require(task["status"] != "COMPLETE", "completed task cannot be released")
    task.update(status="RELEASED", release_reason=reason, cutoff=cutoff, updated_at=now())
    return task


def unit_summary(data, unit):
    """Failed reviews and cutoffs summed over every attempt at one logical unit."""
    attempts = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
    require(attempts, "no attempts for unit " + unit)
    counted = (data.get("rounds") or {}).get(
        unit
    )  # the driver's own count; a run of the old driver has only the reports'
    failed = (
        counted
        if counted is not None
        else sum((t.get("report") or {}).get("review_rounds", 0) for t in attempts)
    )
    cutoffs = sum(1 for t in attempts if t.get("cutoff"))
    return {
        "unit": unit,
        "attempts": [task_summary(t) for t in attempts],
        "failed_reviews": failed,
        "cutoffs": cutoffs,
        "escalate": failed >= 2 or cutoffs >= 2,
    }


def task_summary(task):
    summary = {
        "task_id": task["packet"]["task_id"],
        "worker": task["packet"]["worker"],
        "status": task["status"],
        "runtime_id": task["runtime_id"],
    }
    for key in ("unit", *ROUTE, "model"):
        if key in task["packet"]:
            summary[key] = task["packet"][key]
    return summary


def context_verdict(sample, reserve, max_age=300):
    """Evaluate reported occupancy only; relevance remains an orchestrator decision."""
    require(isinstance(sample, dict), "context sample must be an object")
    require(reserve >= 0 and max_age > 0, "reserve must be nonnegative; max age must be positive")
    unknown = {"status": "unknown", "reason": "missing, stale, or unsupported context measurement"}
    if (
        sample.get("kind") != "current_context"
        or not sample.get("source")
        or not sample.get("model")
    ):
        return unknown
    try:
        observed = datetime.fromisoformat(sample["observed_at"])
        age = (datetime.now(timezone.utc) - observed).total_seconds()
    except (KeyError, TypeError, ValueError):
        return unknown
    used, window = sample.get("context_tokens"), sample.get("effective_window")
    if (
        type(used) is not int
        or type(window) is not int
        or not 0 <= used <= window
        or window <= 0
        or not 0 <= age <= max_age
    ):
        return unknown
    available = window - used
    return {
        "status": "checkpoint" if reserve >= available else "room_available",
        "remaining_tokens": available,
        "next_task_reserve": reserve,
        "model": sample["model"],
        "source": sample["source"],
        "note": "capacity check only; verify relevance and checkout before reuse",
    }


def metrics(samples):
    """Normalize per-request observations. Never price tokens using guessed rates."""
    require(isinstance(samples, list), "metrics input must be a list of per-request records")
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_tokens",
        "output_tokens",
        "cost_usd",
        "duration_seconds",
    )
    seen, groups = set(), {}
    for sample in samples:
        require(
            isinstance(sample, dict) and sample.get("kind") == "request",
            "only per-request records are accepted",
        )
        for key in ("id", "source", "backend", "model", "strategy", "actor"):
            require(isinstance(sample.get(key), str) and sample[key], "missing metric " + key)
        identity = (sample["source"], sample["id"])
        require(identity not in seen, "duplicate usage record")
        seen.add(identity)
        key = (sample["strategy"], sample["actor"], sample["backend"], sample["model"])
        group = groups.setdefault(
            key,
            {
                "requests": 0,
                "totals": dict.fromkeys(fields, 0),
                "observations": dict.fromkeys(fields, 0),
            },
        )
        group["requests"] += 1
        for field in fields:
            value = sample.get(field)
            if value is not None:
                require(
                    type(value) in (int, float) and math.isfinite(value) and value >= 0,
                    "invalid metric " + field,
                )
                group["totals"][field] += value
                group["observations"][field] += 1
    output = []
    for key, group in groups.items():
        for field in fields:
            if not group["observations"][field]:
                group["totals"][field] = None
        output.append(dict(zip(("strategy", "actor", "backend", "model"), key), **group))
    return {
        "groups": output,
        "note": "partial observations are not full workflow cost; include orchestrator and workers; summed request duration is not parallel wall time",
    }


def resolve_runtime():
    """Ask the agent-runtime CLI which runtime this session runs in; never fail inspect.

    Resolution order: `$AGENT_RUNTIME_HOME/server/agent_runtime.py`, then `agent-runtime` on PATH.
    """
    home = os.environ.get("AGENT_RUNTIME_HOME")
    script = Path(home).expanduser() / "server" / "agent_runtime.py" if home else None
    if home and not (script and script.is_file()):
        print(
            f"warning: AGENT_RUNTIME_HOME={home} has no server/agent_runtime.py; falling back to agent-runtime on PATH",
            file=sys.stderr,
        )
    argv = [sys.executable, str(script)] if script and script.is_file() else ["agent-runtime"]
    try:
        return json.loads(run(argv + ["resolve", "--json"]))
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return {"runtime": "unknown"}


TICKET_COLUMNS = (
    "#",
    "What it delivers",
    "Satisfies",
    "Blocked by",
    "Paths",
    "Size",
    "Difficulty",
    "Owner",
    "Status",
)
MATRIX = {
    ("small", "simple"): ("small", "low"),
    ("small", "complex"): ("medium", "medium"),
    ("regular", "simple"): ("medium", "medium"),
    ("regular", "complex"): ("medium", "high"),
}
CONTRACT = """# Contract for every task in this run

- Write your result to the report path your task.md names, as JSON in this shape. The driver validates it; a missing or invalid file counts as no report:
  {"contract":1,"task_id","status":"COMPLETE|PARTIAL|BLOCKED","summary","acceptance_met":bool,"files":[],"decisions":[],"interfaces":[],"remaining":[],"checks":[{"command","outcome":"passed|failed|not_run","reason?"}],"review_rounds":0,"usage":"unknown","head":"<commit sha>","pr":"<url or null>","context":{"status":"unknown"}}
  BLOCKED needs "blocker" and a next action in "remaining". In a fix round report "review_rounds":0: the driver counts failed rounds.
- A YAML plan's ticket reports `"contract":3` and adds `"covers":{"S<n>":["path::test_name"]}`: every scenario the ticket satisfies, mapped to the test nodes that prove it. verify refuses a node that does not exist or that this ticket did not add or change, and a satisfied scenario with no node.
- COMPLETE checks: every required check must be reported `passed`. Match by command: a leading `bash`, `sh`, `uv run`, `python -m`, `python3 -m` or `./` is ignored, so `bash scripts/checks.sh` and `./scripts/checks.sh` both match `scripts/checks.sh`; any other prefix does not. A non-required check may be `not_run` with a `reason`. A `failed` check, or a required check not `passed`, refuses COMPLETE.
- Prose, if any, goes in report.md beside it. Files are write-once: a later attempt writes report.2.json.
- Your agent-runtime `report` call carries ONE line: status + report path. Use kind=complete, blocked or decision.
- Red-first: one test per scenario, named with its S-ID, seen failing before the code exists.
- Fix the class, not the instance: when you fix a defect, search for every other place with the same pattern.
- Edit only the Paths your task owns. Never touch other worktrees or the main checkout.
- Commit with Conventional Commits and push the branch. Describe behaviour, not plan ticket numbers.
"""


def worker_name(slug, number, tail=""):
    """`<slug>-t<n>[tail]` within agent-runtime's 32-character name limit; the `-t<n>` suffix always survives."""
    suffix = "-t" + str(number) + tail
    return slug[: 32 - len(suffix)].rstrip("-_") + suffix


def split_row(line):
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return None
    return [cell.strip() for cell in line[1:-1].split("|")]


def parse_plan(text):
    """Slug, status, scenarios and the Tickets table (with each row's line index) of a plan."""
    lines = text.splitlines()
    head = text.split("\n## ", 1)[0]
    slug = re.search(r"Slug:\s*`([^`]+)`", head)
    status = re.search(r"Status:\s*([A-Za-z-]+)", head)
    scenarios = {}
    for line in lines:
        found = re.match(r"- \*\*(S\d+[a-z]?)\*\*\s", line)
        if found:
            scenarios[found.group(1)] = line
    tickets, columns = [], None
    for index, line in enumerate(lines):
        cells = split_row(line)
        if cells is None:
            columns = columns if tickets else None
            continue
        if columns is None:
            if all(name in cells for name in TICKET_COLUMNS):
                columns = {name: cells.index(name) for name in TICKET_COLUMNS}
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        if len(cells) == len(columns) or len(cells) > max(columns.values()):
            tickets.append({"line": index, **{name: cells[at] for name, at in columns.items()}})
    caps, cap_problems = {}, []
    for line in lines:
        found = re.match(r"Max fix rounds:\s*(.*?)\s*$", line)
        if not found:
            continue
        for entry in [e for e in found.group(1).split(",") if e.strip()] or [""]:
            pair = re.fullmatch(r"\s*(T\d+)\s*=\s*(\d+)\s*", entry)
            if pair:
                caps[pair.group(1)] = int(pair.group(2))
            else:
                cap_problems.append(
                    "`Max fix rounds:` entry %r is not T<n>=<whole number>" % entry.strip()
                )
    return {
        "slug": slug.group(1) if slug else None,
        "status": status.group(1).upper() if status else None,
        "scenarios": scenarios,
        "tickets": tickets,
        "has_table": bool(tickets) or columns is not None,
        "caps": caps,
        "cap_problems": cap_problems,
    }


def is_done(ticket):
    return ticket["Status"].strip().upper().startswith("COMPLETE")


def is_agent(ticket):
    return ticket["Owner"].strip().lower() == "agent"


def ready(ticket, by_id, restartable=False):
    """A TO-DO ticket (or an IN PROGRESS one with nothing alive behind it: `restartable`) whose blockers are all
    COMPLETE."""
    return (restartable or ticket["Status"].strip().upper().startswith("TO-DO")) and all(
        is_done(by_id[b])
        for b in re.split(r"[,\s]+", ticket["Blocked by"].strip())
        if b and b.lower() != "none"
    )


def scenario_number(sid):
    """The number in a scenario id (S12 -> 12); 0 when the id carries none."""
    found = re.match(r"S(\d+)", sid)
    return int(found.group(1)) if found else 0


def satisfied(ticket, scenarios):
    """Scenario IDs a ticket makes true; a range covers every ID numbered inside it."""
    found, missing = [], []
    for token in re.split(r"[,\s]+", ticket["Satisfies"].strip()):
        if not token or token.lower() == "none":
            continue
        ends = re.fullmatch(r"S(\d+)[a-z]?[–-]S?(\d+)[a-z]?", token)
        if ends:
            low, high = int(ends.group(1)), int(ends.group(2))
            ids = [sid for sid in scenarios if low <= scenario_number(sid) <= high]
        else:
            ids = [token] if token in scenarios else []
        (found if ids else missing).extend(ids or [token])
    return found, missing


def check_plan(plan):
    """Every reason `run` cannot start, so one exit names them all."""
    problems = []
    if plan["status"] != "APPROVED":
        problems.append("plan Status is %s, not APPROVED" % (plan["status"] or "missing"))
    if not plan["slug"] or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", plan["slug"]):
        problems.append("plan has no valid Slug (lowercase letters, digits, hyphens)")
    if not plan["tickets"]:
        problems.append(
            "plan has no Tickets table with the fixed columns (" + " | ".join(TICKET_COLUMNS) + ")"
        )
    seen = set()
    for ticket in plan["tickets"]:
        name = ticket["#"]
        if not re.fullmatch(r"T\d+", name) or name in seen:
            problems.append("ticket id %r is not a unique T<n>" % name)
        seen.add(name)
    for ticket in plan["tickets"]:
        if not is_agent(ticket) or is_done(ticket):
            continue
        name = ticket["#"]
        found, missing = satisfied(ticket, plan["scenarios"])
        if not found and not missing:
            problems.append("%s: Satisfies is empty" % name)
        problems += ["%s: scenario %s is not in the plan" % (name, sid) for sid in missing]
        paths = [p.strip() for p in ticket["Paths"].split(",") if p.strip()]
        if not paths:
            problems.append("%s: Paths is empty" % name)
        for path in paths:
            if (
                any(c in path for c in "*?[]")
                or Path(path).is_absolute()
                or ".." in Path(path).parts
            ):
                problems.append(
                    "%s: Paths entry %r must be a literal path inside the repository" % (name, path)
                )
        for column, allowed in (
            ("Size", ("small", "regular")),
            ("Difficulty", ("simple", "complex")),
        ):
            value = ticket[column].strip()
            if not value:
                problems.append("%s: %s is empty" % (name, column))
            elif value not in allowed + (("big",) if column == "Size" else ()):
                problems.append(
                    "%s: %s %r is not one of %s" % (name, column, value, ", ".join(allowed))
                )
        if ticket["Size"].strip() == "big":
            problems.append(
                "%s: a big ticket needs chunked dispatch, which `run` does not do; split it" % name
            )
        for blocker in re.split(r"[,\s]+", ticket["Blocked by"].strip()):
            if (
                blocker
                and blocker.lower() != "none"
                and blocker not in {t["#"] for t in plan["tickets"]}
            ):
                problems.append("%s: Blocked by %s is not a ticket" % (name, blocker))
    problems += plan.get("cap_problems", [])
    problems += [
        "`Max fix rounds:` names %s, which is not a ticket" % name
        for name in plan.get("caps", {})
        if name not in seen
    ]
    problems += cycles(plan["tickets"])
    if problems:
        raise PlanError("; ".join(problems))


def cycles(tickets):
    """One problem per cycle in the Blocked by graph, named by the tickets on it."""
    edges = {
        t["#"]: [
            b for b in re.split(r"[,\s]+", t["Blocked by"].strip()) if b and b.lower() != "none"
        ]
        for t in tickets
    }
    found, state = [], {}

    def walk(name, path):
        state[name] = "open"
        for blocker in edges.get(name, []):
            if state.get(blocker) == "open":
                loop = (
                    path[path.index(blocker) :] + [blocker] if blocker in path else [name, blocker]
                )
                found.append("Blocked by has a cycle: " + " -> ".join(loop))
            elif blocker in edges and blocker not in state:
                walk(blocker, path + [blocker])
        state[name] = "done"

    for name in edges:
        if name not in state:
            walk(name, [name])
    return found


def set_status(plan_path, ticket_id, status):
    """Rewrite one ticket row's Status cell; every other line stays byte-identical."""
    require("|" not in status and "\n" not in status, "status must be one table cell")
    path = Path(plan_path)
    if path.name.endswith(".plan.yaml"):
        return  # YAML contracts are immutable; Driver.write_status owns the ledger copy.
    with path.open(encoding="utf-8", newline="") as stream:
        text = stream.read()
    ticket = next((t for t in parse_plan(text)["tickets"] if t["#"] == ticket_id), None)
    if ticket is None:
        raise ValueError("plan has no ticket " + ticket_id)
    lines = text.splitlines(keepends=True)
    line = lines[ticket["line"]]
    body = line.rstrip("\r\n")
    eol = line[len(body) :]
    trimmed = body.rstrip()
    cut = trimmed.rfind("|", 0, len(trimmed) - 1)
    lines[ticket["line"]] = trimmed[: cut + 1] + " " + status + " |" + body[len(trimmed) :] + eol
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".orchestrate-", dir=path.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="") as stream:
            stream.write("".join(lines))
        shutil.copymode(path, tmp_path)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


MODEL_COLUMNS = {"claude": 1, "codex": 2, "pi": 3}
SKILL_MODELS = Path(__file__).resolve().with_name("models.md")


def model_rows(path):
    """The rows of the 'Model classes' table of `path`, with the index of every named column.

    The header row names the columns (`| Class | Claude | Codex | Pi |`); a table without one keeps the
    positional rule."""
    text = Path(path).expanduser().read_text(encoding="utf-8")
    section = re.split(r"^## Model classes\s*$", text, maxsplit=1, flags=re.M)
    body = section[1].split("\n## ", 1)[0] if len(section) > 1 else ""
    rows, header = [], {}
    for line in body.splitlines():
        cells = split_row(line)
        if not cells or len(cells) < 2:
            continue
        if set("".join(cells)) <= set("-: "):  # the separator row under the header
            continue
        if cells[0].lower() == "class":
            header = {cell.lower(): index for index, cell in enumerate(cells)}
            continue
        rows.append(cells)
    return header, rows


def column_models(header, rows, backend):
    """The class→model map one column holds. The column is read by its header name; a table without that
    header keeps the positional rule, except for Pi: it falls back to the skill's own table."""
    index = header.get(backend)
    if index is None and (not header or backend != "pi"):
        index = MODEL_COLUMNS.get(backend)
    models = {}
    if index is None:
        return models
    for cells in rows:
        if cells[0] in ROUTE["class"] and len(cells) > index:
            value = cells[index].strip("`")
            quoted = re.search(r'"([^"]+)"', value)
            models[cells[0]] = quoted.group(1) if quoted else value
    return models


def class_models(path, backend, required=True):
    """Resolve each class to a model from the 'Model classes' table of the instructions file.

    A Pi run whose file has no Pi column takes its models from the skill's own table. With `required` false a
    class the table does not name is left out instead of being an error: a typed route config may answer it,
    and a table that cannot be read at all is simply no table."""
    try:
        header, rows = model_rows(path)
    except OSError as error:
        if not required:
            return {}
        raise PlanError("cannot read the Model classes table: %s" % error) from None
    models = column_models(header, rows, backend)
    if backend == "pi":
        try:
            skill_header, skill_rows = model_rows(SKILL_MODELS)
        except OSError as error:
            raise PlanError("cannot read the skill's own Model classes table: %s" % error) from None
        for klass, model in column_models(skill_header, skill_rows, "pi").items():
            models.setdefault(klass, model)
    missing = [c for c in ROUTE["class"] if not models.get(c)]
    if missing and required:
        raise PlanError("%s lacks a %s model for class %s" % (path, backend, ", ".join(missing)))
    return models


def runtime_process(argv, timeout=None):
    """One `agent-runtime` process. The tests answer it in process (a fake module), so a driver run starts no
    process per call: the seam is the process, not the answer."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def gh_process(argv, timeout=None):
    """One `gh` process."""
    return subprocess.run(["gh", *argv], capture_output=True, text=True, timeout=timeout)


def runtime_call(lead, tool, arguments, timeout=120):
    """One dispatch tool call, through the runtime's public CLI.

    The seam is the `RuntimeClient` protocol: the driver never imports the runtime's modules and
    never reads its store. The process the client speaks through is the seam the tests answer, so a
    driver run starts no process per call. A refusal is a `ValueError`, the driver's word for it.
    """
    try:
        return CliRuntimeClient(lead, process=runtime_process).call(
            tool, arguments, timeout=timeout
        )
    except RuntimeCallError as error:
        raise ValueError(str(error)) from None


class Unreadable(ValueError):
    """`status` could not say whether an agent is gone."""


def terminal_gone(error, name):
    """True when the runtime's `unknown` says the terminal does not exist: hc's error kind `not_found`, or its
    `no terminal "<name>"`. Any other error is a read that failed, and says nothing about the terminal."""
    text = str(error or "")
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("kind") == "not_found"
    return text.strip() == 'no terminal "%s"' % name or "not_found" in text


def assignment_of(result):
    """The assignment id a `spawn` or `resume` answered with: how `wait` names this agent from then on."""
    found = result.get("assignment_id")
    if not isinstance(found, str) or not found:
        raise ValueError(
            "the runtime answered without an assignment_id: %s" % json.dumps(result)[:200]
        )
    return found


def event(message):
    print("[orchestrate] " + message, file=sys.stderr, flush=True)


GATES = (
    "reported",
    "converged",
    "reviewed",
    "fix-pending",
    "requested",
    "decided",
    "redo-pending",
    "ready-for-ci",
    "awaiting-merge",
    "cleanup",
)  # ledger `gate` stages the loop acts on


def merge_state(view):
    """One word for what a pull request's merge state asks of the driver, in precedence order: merged, closed,
    conflict, unknown, behind, open. A conflict wins over red checks (it can keep CI from starting). A draft reports
    mergeStateStatus DRAFT, which hides BEHIND but not `mergeable: CONFLICTING`: it is open until it is readied."""
    if view.get("error"):
        return "unknown"
    state = str(view.get("state") or "").upper()
    if state in ("MERGED", "CLOSED"):
        return state.lower()
    status = str(view.get("mergeStateStatus") or "").upper()
    mergeable = str(view.get("mergeable") or "").upper()
    if status == "DIRTY" or mergeable == "CONFLICTING":
        return "conflict"
    if status in ("", "UNKNOWN") or mergeable == "UNKNOWN":
        return "unknown"
    if status == "BEHIND":
        return "behind"
    return "open"


PARKED = (
    "attention",
    "blocked",
    "skipped",
)  # nothing runs for the unit until the user acts (`attention`: runs of the old driver)
SILENT = (
    "idle",
    "done",
)  # runtime_state of a `working` assignment whose turn ended: a live agent, maybe waiting on a background job
TURN_ENDED = SILENT + (
    "unreported",
    "error",
)  # runtime_state of a `working` assignment that needs attention
ATTENTION = {
    "blocked": "reported blocked",
    "decision": "asked for a decision",
    "exited": "ended without a report",
}
ARCHIVE = "archive"  # the unit (and task id) of the archive worker
CONVERGE = Path(__file__).resolve().with_name("converge.py")


def next_path(directory, stem, suffix):
    """The first write-once file name free in `directory`: <stem><suffix>, <stem>.2<suffix>, ..."""
    for index in range(1, 1000):
        path = directory / (stem + ("" if index == 1 else ".%d" % index) + suffix)
        if not path.exists():
            return path, index
    raise ValueError("too many %s files in %s" % (stem, directory))


# The tail of a failing job's log that a fix round shows; the full log is at log_path.
CI_EXCERPT_CHARS = 2048


def job_log(run_log, job):
    """The lines of a `gh run view --log-failed` log that belong to one job: each opens with the job's name and a tab.
    A log with no such line is taken whole (it names no job)."""
    lines = [line for line in run_log.splitlines() if line.split("\t", 1)[0] == job]
    return "\n".join(lines) if lines else run_log


def log_tail(text, limit=CI_EXCERPT_CHARS):
    """The last `limit` characters of a log, starting at a whole line."""
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    return tail.split("\n", 1)[1] if "\n" in tail else tail


def cell(text, limit=160):
    """Text safe inside one plan table cell: one line, no pipes, cut at `limit`."""
    flat = " ".join(str(text).replace("|", "/").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def item_text(item):
    """What a finding says, whichever contract wrote it: a contract 2 `summary`, a contract 1 `problem` or deferred
    `note`, a converge or CI `excerpt`."""
    return next(
        (str(item[k]) for k in ("summary", "problem", "note", "excerpt") if item.get(k)), ""
    )


def describe(item):
    """The lines a fix round shows for one findings item (text lines of contract 1 are shown as they are)."""
    if not isinstance(item, dict):
        return str(item)
    if item.get("source") in ("converge", "verify", "ci", "conflict"):
        return "%s %s: %s (exit %s of `%s`; full log %s)" % (
            item["source"],
            item["check"],
            item["excerpt"],
            item["exit"],
            item["command"],
            item["log_path"],
        )
    where = "%s:%s " % (item["file"], item["line"]) if item.get("file") else ""
    line = "%s%s%s: %s%s" % (
        item["id"],
        " (%s)" % item["kind"] if item.get("kind") else "",
        ", answer required" if item.get("answer_required") else "",
        where,
        item_text(item),
    )
    if item.get("fix"):
        line += ". Fix: %s" % item["fix"]
    evidence = item.get("evidence")
    if isinstance(evidence, dict):
        line += ". Evidence: `%s` exited %s (log %s): %s" % (
            evidence["command"],
            evidence["exit"],
            evidence["log_path"],
            cell(evidence["excerpt"], 300),
        )
    return line


def short(item):
    """A finding in a few words, for a request's reason and a ledger `remaining` line."""
    if not isinstance(item, dict):
        return str(item)
    if item.get("source") in ("converge", "verify", "ci"):
        return (
            item["excerpt"].splitlines()[0]
            if item["excerpt"]
            else "%s %s failed" % (item["source"], item["check"])
        )
    return "%s: %s" % (item.get("id"), item_text(item))


def head_of(checkout):
    """The checkout's HEAD sha."""
    return run(["git", "-C", str(checkout), "rev-parse", "HEAD"])


def check_commit(claimed, head, what="review.json"):
    """A claimed commit is at least 7 hex characters and is the checkout's HEAD (or an abbreviation of it)."""
    contracts.need(
        re.fullmatch(r"[0-9a-f]{7,40}", claimed or "") is not None,
        "%s: commit %r is not a sha of at least 7 hex characters" % (what, claimed),
    )
    contracts.need(
        head.startswith(claimed),
        "%s: commit %s is not the checkout's HEAD (%s)" % (what, claimed, head[:12]),
    )


ARCHIVE_ACCEPTANCE = [
    "The living spec exists with a `Paths:` line and every scenario as a requirement with provenance",
    "Every test is renamed from S<n> to <area>/R<m>",
    "The plan's Status is ARCHIVED",
    "converge --final passes",
]


sum_usage = events.sum_usage


def legacy_imports(data):
    """What a ledger written before `events.jsonl` knew about each unit, as `import` events: its route, failed
    rounds, escalations (events sent to a decider plus pauses for the user), decider calls and the runtime's usage per
    assignment. The new driver writes them once, when it takes over such a run dir."""
    units = {}
    for task in data["tasks"].values():
        units.setdefault(task["packet"].get("unit") or task["packet"]["task_id"], []).append(task)
    rows = []
    for unit, tasks in units.items():
        ids = {
            a.get("assignment_id")
            for t in tasks
            for a in (t, t.get("review") or {}, t.get("decision") or {})
        } - {None}
        usage = data.get("usage", {})
        rows.append(
            {
                "unit": unit,
                "route": {
                    key: tasks[0]["packet"].get(key)
                    for key in ("size", "difficulty", "class", "model")
                },
                "review_rounds": sum(
                    (t.get("report") or {}).get("review_rounds", 0) for t in tasks
                ),
                "escalations": sum(1 for t in tasks if t.get("event"))
                + sum(len(t.get("attention") or []) for t in tasks),
                "decider_calls": sum(1 for t in tasks if t.get("decision")),
                "assignments": {
                    i: usage[i] for i in sorted(i for i in ids if i is not None) if i in usage
                },
            }
        )
    return rows


def metrics_table(metrics):
    rows = [("unit", "route", "rounds", "escalations", "deciders", "usage")]
    for unit, row in metrics["tickets"].items():
        route = "%s·%s" % (row["route"]["size"], row["route"]["difficulty"])
        usage = (
            row["usage"]
            if isinstance(row["usage"], str)
            else json.dumps(row["usage"], separators=(",", ":"))
        )
        rows.append(
            (
                unit,
                route,
                str(row["review_rounds"]),
                str(row["escalations"]),
                str(row["decider_calls"]),
                usage,
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    return "\n".join(
        [
            "[orchestrate] metrics for %s" % metrics["slug"],
            *lines,
            "lead model turns: %d" % metrics["lead"]["model_turns"],
        ]
    )


class Driver:
    """The dispatch loop. It keeps no state of its own: the plan, the ledger and the runtime hold it."""

    def __init__(self, args):
        self.args = args
        self.plan_path = Path(args.plan).resolve(strict=True)
        self.repo = (
            Path(args.repo).resolve()
            if args.repo
            else Path(
                run(["git", "-C", str(self.plan_path.parent), "rev-parse", "--show-toplevel"])
            ).resolve()
        )
        self.errors = []
        self.spawned = []
        self.failed = set()
        self.stuck = set()
        self.stranded = (
            set()
        )  # tasks the last reconciliation could not settle: the run must not finish over them
        self.unfinished = set()  # the assignment ids the runtime's last `wait` listed as unfinished
        self.reconciled = False
        self.touched = (
            set()
        )  # the agents this drive spawned, attached or resumed: no dead drive left them
        self.receipt = None
        self.received = False
        self.sleep = time.sleep  # injectable so tests do not really sleep
        self.last_plan = None
        self.model = None  # the approved plan/1 model of a YAML plan
        self.outcome_logged = False
        self.approval = None  # the approval block of the YAML plan the loop last read
        self.ticks = {}  # unit -> [what it showed at the last tick, how many ticks it showed nothing new]
        self.last_tick = self.clock()
        self.working_since = {}  # task id -> when its agent was first seen `working`, unbroken

    # ---- the event log: every state change is on disk before the next step -----------------------------------

    def log(self, kind, **fields):
        events.append(self.run_dir / "events.jsonl", kind, **fields)

    @contextmanager
    def book(self):
        """The ledger, plus one `state` event for every task whose gate or status the block changed. The line is
        appended before the ledger is written, so the log is never behind the state it describes."""
        with ledger(self.ledger_path) as data:
            before = {
                task_id: (task.get("gate"), task["status"])
                for task_id, task in data["tasks"].items()
            }
            yield data
            for task_id, task in data["tasks"].items():
                old, new = before.get(task_id, (None, None)), (task.get("gate"), task["status"])
                if old == new:
                    continue
                packet = task["packet"]
                route = {
                    k: packet[k] for k in ("size", "difficulty", "class", "model") if k in packet
                }
                self.log(
                    "state",
                    unit=packet.get("unit") or task_id,
                    task=task_id,
                    gate=[old[0], new[0]],
                    status=[old[1], new[1]],
                    route=route if task_id not in before and route else None,
                )

    def read_plan(self):
        if self.plan_path.name.endswith(".plan.yaml"):
            model = yaml_plan.load(
                self.plan_path,
                repo=self.repo,
                state_dir=self.args.state_dir,
                require_approval=True,
            )
            self.model = (
                model  # verify gates a ticket against the approved plan, not the legacy table
            )
            self.approval = model.approval
            ledger_path = state_layout(self.args.state_dir, self.plan_path, self.repo, model.slug)[
                0
            ]
            statuses = {}
            if ledger_path.exists():
                with ledger(ledger_path) as data:
                    statuses = dict(data.get("ticket_status", {}))
            return model.legacy(statuses)
        plan = parse_plan(self.plan_path.read_text(encoding="utf-8"))
        check_plan(plan)
        return plan

    def start(self):
        plan = self.last_plan = self.read_plan()
        self.slug = plan["slug"]
        self.brain_cli = getattr(self.args, "brain_cli", None)
        # The route config this run uses, in the plan's order: the file the run names, then the repository's own
        # policy, then the user's default. With one, the legacy table is a fallback, not a prerequisite, so it is
        # not read; with none, the legacy table answers and says it is deprecated.
        self.routes_file, self.route_source = routes.find_config(
            explicit=getattr(self.args, "routes_file", None),
            repo=self.repo,
            home=routes.user_config_dir(),
        )
        if self.routes_file:
            self.models, self.brain_models = {}, {}
        else:
            self.models = class_models(self.args.models_file, self.args.cli, required=True)
            self.brain_models = (
                class_models(self.args.models_file, self.brain_cli, required=True)
                if self.brain_cli
                else self.models
            )
        self.routes, self.step_routes = self.load_routes()
        Path(self.args.state_dir).expanduser().mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ledger_path, self.run_dir = locate_state(
            self.args.state_dir, self.plan_path, self.repo, self.slug, importing=True
        )
        self.run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        contract = self.run_dir / "contract.md"
        if not contract.exists():
            contract.write_text(CONTRACT, encoding="utf-8")
        with ledger(self.ledger_path) as data:
            self.lead = data.setdefault("lead", "orchestrate:" + self.slug)
            data["routes"] = routes.record(
                self.routes, self.route_source or "legacy", self.routes_file, self.step_routes
            )
            legacy = legacy_imports(data) if not (self.run_dir / "events.jsonl").exists() else []
            if is_yaml(
                self.plan_path
            ):  # the ledger owns the plan's state; the plan file is never written
                if not data.get("plan"):
                    enter_plan_state(data, "APPROVED", self.approval and self.approval.at)
                if data["plan"]["state"] == "APPROVED":
                    enter_plan_state(data, "RUNNING")
        for row in legacy:  # a run dir written before events.jsonl: its history enters the log once
            self.log("import", **row)
        self.log(
            "start",
            pid=os.getpid(),
            cli=self.args.cli,
            max_workers=self.args.max_workers,
            route_source=self.route_source or "legacy",
        )

    def loop(self):
        idle = False
        while True:
            if self.plan_path.name.endswith(".plan.yaml"):
                self.read_plan()  # Integrity precedes gates, which can also spawn workers.
            acted = (
                self.advance()
            )  # before the plan is read: a merge it makes unblocks work in this same round
            plan, valid = self.current_plan()
            if valid:
                dispatched = (
                    self.reopen_parked(plan)
                    + self.dispatch_ready(plan)
                    + self.attend_people(plan)
                    + self.start_archive(plan)
                )
            else:  # the last valid plan keeps the work going; what the user's edit might have changed waits for a valid file
                dispatched = self.dispatch_ready(plan)
            if idle and not dispatched and not acted and not self.awaiting():
                # An open task whose agent the runtime no longer lists is work the run would otherwise drop.
                dispatched = self.reconcile(finishing=True)
                if not dispatched:
                    return
            wait = {"target": "all", "timeout": self.args.wait_seconds}
            if self.receipt:
                wait["receipt"] = self.receipt
            result = runtime_call(self.lead, "wait", wait, timeout=self.args.wait_seconds + 60)
            self.receipt = result.get("receipt")
            self.receive(result)
            self.idle_ticks(result.get("assignments") or [])
            self.unfinished = {i for i in result.get("unfinished") or [] if isinstance(i, str)}
            recovered = 0
            if (
                not self.reconciled
            ):  # a drive that starts on a ledger a dead one left looks at it once
                self.reconciled = True
                recovered = self.reconcile()
                dispatched += recovered
            # `unfinished` lists assignment ids. A parked unit's assignment stays open in the runtime and makes `wait`
            # return at once: it is not work, so a run with only parked units left ends.
            parked = self.parked_assignments()
            # An attempt recovered after this wait is one it could not list: the next wait settles it, not `status`.
            idle = not recovered and not [
                entry for entry in result.get("unfinished") or [] if entry not in parked
            ]
            if not (dispatched or acted or self.received):
                self.idle()

    def current_plan(self):
        """(plan, valid): the plan file as it stands, or the last valid one with a `plan-invalid` wake for the error.
        The same error wakes the Lead once; a different one wakes it again."""
        try:
            self.last_plan = self.read_plan()
            return self.last_plan, True
        except PlanError as error:
            if self.plan_path.name.endswith(".plan.yaml"):
                raise  # A stale approval must never fall back to dispatching the last valid plan.
            event("plan not runnable, running on the last valid plan: %s" % error)
            self.wake(
                "plan-invalid",
                "plan",
                "plan-invalid plan: %s -> %s" % (cell(error, 300), self.plan_path),
                key=str(error),
            )
            return self.last_plan, False

    def idle(self):
        """Back off after an iteration that dispatched, advanced and handled nothing: the runtime's wait returns
        at once when nothing is unfinished, so a PR awaiting merge or a parked gate would otherwise spin."""
        self.log("idle", interval=self.args.idle_interval)
        self.sleep(self.args.idle_interval)

    clock = staticmethod(
        time.monotonic
    )  # a seam: the idle tick's tests move time without patching `time`

    def idle_ticks(self, assignments):
        """Every `--idle-minutes`, look at each unit: did its agent show anything new, did its CI move? A unit that
        shows nothing at two consecutive ticks needs the Lead."""
        moment = self.clock()
        if moment - self.last_tick < self.args.idle_minutes * 60:
            return
        self.last_tick = moment
        for unit, task in self.units().items():
            task_id = task["packet"]["task_id"]
            if task["status"] in ("COMPLETE", "RELEASED") or task.get("gate") in PARKED:
                continue
            if task.get("gate") == "requested":
                continue  # the Lead already has it
            if task.get("gate") == "awaiting-merge":
                continue  # the merge is a person's step, and the pr-ready wake already asked for it
            working = self.working_minutes(task_id, assignments, moment)
            if working is not None and working < self.args.working_minutes:
                # A long test is one turn: the runtime says the agent is busy, so there is nothing to ask.
                self.ticks.pop(task_id, None)
                continue
            signature = self.idle_signature(task_id, assignments)
            previous = self.ticks.get(task_id)
            if previous is None or previous[0] != signature:
                self.ticks[task_id] = [signature, 0, moment]
                continue
            streak = previous[1] + 1
            self.ticks[task_id] = [signature, streak, previous[2]]
            waiting = "ci" if task.get("gate") == "ready-for-ci" else "agent"
            self.log("idle", unit=unit, task=task_id, waiting=waiting, ticks=streak)
            if streak >= 2:
                self.request(
                    task_id,
                    "idle",
                    self.idle_reason(task, unit, streak, (moment - previous[2]) / 60, working),
                )

    def idle_reason(self, task, unit, streak, minutes, working):
        """What the Lead reads when a unit showed nothing for two ticks: who it waits on, the file that agent owes,
        how long it has been quiet and the way out."""
        task_id = task["packet"]["task_id"]
        answers = task.get("answers") or {}
        owed = None
        if answers.get("state") == "working":
            owed = ("implementer", task["packet"]["worker"], self.report_file(answers["id"]))
        elif task.get("gate") == "reviewing":
            owed = ("reviewer", task["review"]["name"], task["review"]["path"])
        elif not task.get("gate") and task["status"] == "ACTIVE":
            owed = ("worker", task["packet"]["worker"], self.report_file(task_id))
        text = "idle: %s showed no new state or report for %d ticks (%d minutes)" % (
            unit,
            streak,
            minutes,
        )
        if working is not None:
            text += ", working for %d minutes" % working
        if owed:
            text += (
                "; %s %s is live but has not reported complete or written %s: "
                "look at its terminal, then " % (owed[0], owed[1], owed[2])
            )
        else:
            text += "; "
        return text + "decide %s %s retry | answer | skip" % (self.plan_path, unit)

    def working_minutes(self, task_id, assignments, moment):
        """How long the runtime has reported this unit's agent `working`, or None when it does not."""
        unit = self.task(task_id)["packet"]["unit"]
        with self.book() as data:
            mine = {
                a.get("assignment_id")
                for a in (data.get("agents") or {}).values()
                if a.get("unit") == unit
            }
        if not any(
            a.get("assignment_id") in mine and a.get("runtime_state") == "working"
            for a in assignments
        ):
            self.working_since.pop(task_id, None)
            return None
        since = self.working_since.setdefault(task_id, moment)
        return (moment - since) / 60

    def idle_signature(self, task_id, assignments):
        """What a unit showed since the last tick: its gate, its report, its checks and its agents' state."""
        task = self.task(task_id)
        unit = task["packet"]["unit"]
        with self.book() as data:
            mine = {
                a.get("assignment_id")
                for a in (data.get("agents") or {}).values()
                if a.get("unit") == unit
            }
        agents = sorted(
            [a.get("assignment_id"), a.get("state"), a.get("runtime_state")]
            for a in assignments
            if a.get("assignment_id") in mine
        )
        return json.dumps(
            [task.get("gate"), task.get("report_path"), task.get("ci_state"), agents],
            sort_keys=True,
        )

    # ---- gates: a worker's report -> converge -> review -> fix rounds ---------------------------------------
    # The ledger task's `gate` field is the whole state: reported -> converged -> reviewing -> reviewed ->
    # ready-for-ci, or fix-pending -> superseded. A fix round of any source goes back through converge to the unit's
    # one reviewer, resumed with `<last_reviewed>..HEAD`; the unit reaches CI only when that re-review passes. An event only the Lead can settle (a 3rd failed gate, a worker
    # that is blocked or asks, a handoff file that breaks its contract) goes requested -> decided -> fix-pending
    # or redo-pending (retry, answer, fresh worker), blocked, or skipped. `requested` is the pause: request.json,
    # a wake, nothing else moves for the unit. `ready-for-ci` is the seam for the CI gate.

    def receive(self, result):
        """Turn the runtime's reports into gate stages. Agents are told apart by assignment id only: the runtime's
        messages carry no name, and a resumed worker keeps its name across assignments."""
        self.received = bool(result.get("messages"))
        self.record_usage(result.get("assignments") or [])
        for message in result.get("messages") or []:
            kind, assignment_id = message.get("kind"), message.get("assignment_id")
            if not assignment_id or kind not in ("complete", "blocked", "decision", "failed"):
                continue
            if kind != "complete":
                self.on_agent_attention(
                    assignment_id, "exited" if kind == "failed" else kind, message.get("text") or ""
                )
                continue
            held = None
            with self.book() as data:
                if (data.get("gaps") or {}).get("assignment_id") == assignment_id:
                    data["gaps"]["ended"] = True  # advisory: nothing waits on it any more
                    continue
                for task in data["tasks"].values():
                    if task["status"] in ("COMPLETE", "RELEASED"):
                        continue
                    gate = task.get("gate")
                    answers = task.get("answers") or {}
                    if (
                        answers.get("state") == "working"
                        and answers.get("assignment_id") == assignment_id
                    ):
                        task["answers"] = dict(answers, state="reported")
                        break
                    if gate == "reviewing" and task["review"].get("assignment_id") == assignment_id:
                        task["gate"] = "reviewed"
                        break
                    if not gate and task.get("assignment_id") == assignment_id:
                        task["gate"] = "reported"
                        break
                    if gate == "requested" and task.get("assignment_id") == assignment_id:
                        held = (task["packet"]["task_id"], message.get("text") or "")
                        break
                else:
                    event("complete from assignment %s matches no open gate" % assignment_id)
            if held:
                self.hold(held[0], assignment_id, "complete", held[1])
        self.check_ended(result.get("assignments") or [])

    def check_ended(self, assignments):
        """Attention from assignment states, as `wait` reports them: `failed`, `blocked` or `decision`, or `working`
        with a `runtime_state` that says the turn ended (idle, done: `silent`) or needs a person (unreported, error)."""
        for assignment in assignments:
            state, runtime = assignment.get("state"), assignment.get("runtime_state")
            if state in ("blocked", "decision"):
                kind, text = state, ""
            elif state == "failed" or (state == "working" and runtime in TURN_ENDED):
                kind, text = "exited", ("runtime state " + runtime) if runtime else ""
            else:
                continue
            if assignment.get("assignment_id"):
                self.on_agent_attention(
                    assignment["assignment_id"],
                    kind,
                    text,
                    quiet=True,
                    silent=state == "working" and runtime in SILENT,
                )

    def parked_assignments(self):
        """Assignment ids that belong to a parked unit (paused, blocked or skipped): open in the runtime, not work."""
        with self.book() as data:
            gaps = data.get("gaps") or {}
            return {
                agent.get("assignment_id")
                for task in data["tasks"].values()
                if task.get("gate") in PARKED
                for agent in (task, task.get("review") or {}, task.get("decision") or {})
            } - {None} | ({gaps.get("assignment_id")} if gaps.get("ended") else set())

    def on_agent_attention(self, assignment_id, kind, text="", quiet=False, silent=False):
        """An agent reported `blocked` or `decision`, or its assignment failed or ended with no report. Each event is
        handled once: a unit that already has a gate (reviewing, requested, ...) ignores it, except that a `blocked` or
        `decision` from the worker of a unit with an open request is held (S7). A reviewer whose assignment ended
        moves its gate on, so a missing file is reported as a contract error.

        `silent` is a live agent whose turn ended (runtime idle or done): it may be waiting on a background job, so
        it is not an exit. A worker whose report is already on disk advances; every other role keeps waiting for its
        own `complete`, and only the idle watchdog asks the Lead (R62)."""
        hit = None
        with self.book() as data:
            for task_id, task in data["tasks"].items():
                gate = task.get("gate")
                if task["status"] in ("COMPLETE", "RELEASED"):
                    continue
                answers = task.get("answers") or {}
                if (
                    answers.get("state") == "working"
                    and answers.get("assignment_id") == assignment_id
                ):
                    hit = (task_id, "answerer", task["packet"]["worker"])
                elif gate == "requested" and task.get("assignment_id") == assignment_id:
                    hit = (task_id, "held", task["packet"]["worker"])
                elif gate == "reviewing" and task["review"].get("assignment_id") == assignment_id:
                    hit = (task_id, "reviewer", task["review"]["name"])
                elif not gate and task.get("assignment_id") == assignment_id:
                    hit = (task_id, "worker", task["packet"]["worker"])
        if not hit:
            with self.book() as data:
                if (data.get("gaps") or {}).get("assignment_id") == assignment_id:
                    data["gaps"]["ended"] = (
                        True  # the gaps review is advisory: it never blocks or pauses anything
                    )
                    return event("gaps reviewer %s: %s" % (ATTENTION[kind], text or "no gaps.md"))
            if not quiet:
                event(
                    "assignment %s %s: already handled or not an open assignment"
                    % (assignment_id, ATTENTION[kind])
                )
            return
        task_id, role, name = hit
        reason = "%s %s%s" % (name, ATTENTION[kind], ": " + text if text else "")
        if role == "held":
            return (
                self.hold(task_id, assignment_id, kind, reason)
                if kind in ("blocked", "decision")
                else None
            )
        if silent and role != "worker":
            return None
        if role == "worker":
            if kind == "exited" and self.report_landed(task_id):
                # The worker wrote its report and its turn ended before the runtime said so: the report is the
                # news, not a question for the Lead.
                self.log(
                    "report-landed",
                    unit=self.unit_of(task_id),
                    task=task_id,
                    assignment_id=assignment_id,
                )
                return self.mark(task_id, gate="reported")
            if silent:
                return None
            return self.request(task_id, kind, reason)
        if role == "answerer":
            self.mark(task_id, answers=dict(self.task(task_id)["answers"], state="failed"))
            return self.request(task_id, "answers", reason)
        if kind == "exited":
            return self.mark(task_id, gate="reviewed")
        self.request(task_id, role, reason)

    def advance(self):
        """Run every pending gate stage once; each step changes the task's gate or raises. Returns the steps run."""
        try:
            self.poll()  # one non-blocking look at every pull request before the gates move
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.errors.append("poll: %s" % error)
            self.log("error", gate="poll", error=str(error)[:500])
            event("the pull request poll failed: %s" % error)
        steps = {
            "reported": self.step_reported,
            "converged": self.step_converged,
            "reviewed": self.step_reviewed,
            "fix-pending": self.step_fix,
            "requested": self.step_requested,
            "decided": self.step_decided,
            "redo-pending": self.step_redo,
            "ready-for-ci": self.step_ci,
            "awaiting-merge": self.step_merge,
            "cleanup": self.step_cleanup,
        }
        acted = 0
        with self.book() as data:
            answered = [
                task_id
                for task_id, task in data["tasks"].items()
                if (task.get("answers") or {}).get("state") == "reported"
                and task["status"] not in ("COMPLETE", "RELEASED")
            ]
        for task_id in answered:
            try:
                self.step_answers(task_id)
                acted += 1
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.mark(task_id, answers=dict(self.task(task_id)["answers"], state="failed"))
                self.errors.append("%s: %s" % (task_id, error))
                self.log(
                    "error",
                    unit=self.unit_of(task_id),
                    task=task_id,
                    gate="answers",
                    error=str(error)[:500],
                )
                event("%s answers failed: %s" % (task_id, error))
        with self.book() as data:
            ids = [task_id for task_id, task in data["tasks"].items() if task.get("gate") in steps]
        for task_id in ids:
            while True:
                gate = self.task(task_id).get("gate")
                if gate not in steps or (task_id, gate) in self.stuck:
                    break
                try:
                    if (
                        steps[gate](task_id) is False
                    ):  # nothing to act on yet: poll again next iteration
                        break
                except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                    self.stuck.add((task_id, gate))
                    self.errors.append("%s: %s" % (task_id, error))
                    self.log(
                        "error",
                        unit=self.unit_of(task_id),
                        task=task_id,
                        gate=gate,
                        error=str(error)[:500],
                    )
                    event("%s gate %s failed: %s" % (task_id, gate, error))
                    break
                acted += 1
        return acted

    def awaiting(self):
        """Tickets whose PR is not merged yet, or whose request the Lead has not answered: the run stays up."""
        with self.book() as data:
            return any(
                t.get("gate") in ("ready-for-ci", "awaiting-merge", "requested")
                for t in data["tasks"].values()
            )

    def task(self, task_id):
        with self.book() as data:
            return copy.deepcopy(data["tasks"][task_id])

    def unit_of(self, task_id):
        with self.book() as data:
            task = data["tasks"].get(task_id)
        return (task["packet"].get("unit") or task_id) if task else task_id

    def mark(self, task_id, **fields):
        with self.book() as data:
            data["tasks"][task_id].update(fields, updated_at=now())

    # ---- the Lead: a request.json and one wake per escalation, answered by `decide` -------------------------------
    # A unit that needs a decision (a worker that blocks or asks, a failed gate past the cap, a handoff file that is
    # missing or breaks its contract) gets `<run>/<unit>/request.json` and one wake line in `<run>/wakes.jsonl`; its
    # gate is `requested` and nothing runs for it. Every other unit goes on. `orchestrate.py decide` writes the
    # decision and marks the request resolved; the next round applies it (`step_decided`).

    def wake(self, kind, unit, text, key=None):
        """Append one wake for the Lead, unless a wake of this kind with the same `key` is already there."""
        path = self.run_dir / "wakes.jsonl"
        if key is not None and any(
            w.get("kind") == kind and w.get("key") == key for w in events.read(path)
        ):
            return False
        events.append(path, kind, unit=unit, text=text, key=key)
        self.log("wake", wake=kind, unit=unit, text=text)
        self.alert(text)
        return True

    def alert(self, text):
        """Tell the terminal that launched the run about a new wake, so a Lead that ended its turn hears of it.
        `hc send`, never `--force` or `interrupt`: the notice queues behind the Lead's turn instead of ending it.
        The wake is already on disk, so a send that fails is logged and the run goes on."""
        # A driver built for one call (the wake tests) has no parsed args, and so no terminal to tell.
        terminal = getattr(getattr(self, "args", None), "notify_terminal", None)
        if not terminal:
            return
        line = "%s needs you: %s" % (pane_name(self.slug), " ".join(str(text).split()))
        try:
            result = subprocess.run(
                ["hc", "send", terminal, line[:1000]],
                capture_output=True,
                text=True,
                timeout=30,
            )
            error = None if result.returncode == 0 else (result.stderr or result.stdout).strip()
        except (OSError, subprocess.TimeoutExpired) as caught:
            error = str(caught)
        if error is not None:
            event("could not alert %s: %s" % (terminal, error))
            self.log("notify-failed", terminal=terminal, error=error[:500])

    def request(self, task_id, kind, reason, fix=None):
        """The unit needs the Lead. A unit that already has an open request keeps it."""
        with self.book() as data:
            task = data["tasks"][task_id]
            if task.get("gate") == "requested":
                return
            unit = task["packet"]["unit"]
            path, _ = next_path(self.run_dir / unit, "request", ".json")
            task.update(
                event={"at": now(), "kind": kind, "reason": reason},
                gate="requested",
                updated_at=now(),
                request={"path": str(path), "kind": kind},
            )
            if fix:
                task["fix"] = fix
        event("%s needs a decision (%s): %s" % (task_id, kind, reason))
        self.log("escalation", unit=unit, task=task_id, cause=kind, reason=reason[:500])
        self.write_status(unit.upper(), "IN PROGRESS · decision pending: " + cell(reason))
        self.write_request(task_id)

    def write_request(self, task_id):
        """Write the request file of a `requested` task (again, if the driver died before it was on disk) and its wake."""
        task = self.task(task_id)
        unit, path = task["packet"]["unit"], Path(task["request"]["path"])
        if not path.exists():
            candidates = [
                self.run_dir / unit / "task.md",
                self.run_dir / task_id / "report.json",
                Path((task.get("review") or {}).get("path") or "/nonexistent"),
                self.run_dir / task_id / "findings.json",
                self.run_dir / "events.jsonl",
            ]
            row = contracts.make_request(
                unit,
                task_id,
                task["event"]["kind"],
                task["event"]["reason"],
                [str(c) for c in candidates if c.exists()],
            )
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_atomic(path, json.dumps(row, indent=2) + "\n")
        self.wake(
            "decision",
            unit,
            "decision %s: %s -> %s" % (unit, cell(task["event"]["reason"], 200), path),
            key=str(path),
        )

    def close_request(self, task_id, reason):
        """The unit no longer needs the Lead: its open request is resolved, with the decision that says why."""
        task = self.task(task_id)
        request = task.get("request")
        if not request:
            return
        path = Path(request["path"])
        if not path.exists():
            return
        document = contracts.load(path, contracts.validate_request)
        if document["status"] != "open":
            return
        decision_path = next_path(path.parent, "decision", ".json")[0]
        decision = contracts.validate_decision(
            {"contract": contracts.VERSION, "action": "skip", "reason": reason}
        )
        write_atomic(decision_path, json.dumps(decision, indent=2) + "\n")
        write_atomic(
            path,
            json.dumps(dict(document, status="resolved", decision=str(decision_path)), indent=2)
            + "\n",
        )

    def step_requested(self, task_id):
        """Wait for the Lead: a resolved request moves the unit on to `decided`. False while it is still open."""
        task = self.task(task_id)
        path = Path(task["request"]["path"])
        if not path.exists():
            self.write_request(task_id)  # the driver died after the ledger and before the file
        request = contracts.load(path, contracts.validate_request)
        if request["status"] != "resolved":
            return False
        self.mark(task_id, gate="decided", decision={"path": request["decision"]})

    def hold(self, task_id, assignment_id, kind, text):
        """A worker message that arrives while its unit has an open request is kept on the task and acted on after the
        decision. The event that caused the request, and a repeat of a held message, are not held."""
        with self.book() as data:
            task = data["tasks"][task_id]
            held = task.setdefault("held", [])
            if kind == task["event"]["kind"] or any(
                h["assignment_id"] == assignment_id and h["kind"] == kind for h in held
            ):
                return
            held.append({"at": now(), "kind": kind, "assignment_id": assignment_id, "text": text})
            unit = task["packet"]["unit"]
        self.log(
            "held",
            unit=unit,
            task=task_id,
            held=kind,
            assignment_id=assignment_id,
            text=cell(text, 200),
        )

    def replay_held(self, task, successor_id):
        """After a decision handed the unit on: a held blocked or decision message that the Lead has not seen becomes
        a request on the unit's new task. A held complete never gets here (`step_decided` takes it first)."""
        for held in task.get("held") or []:
            if held["kind"] in ("blocked", "decision"):
                self.log(
                    "held-acted",
                    unit=task["packet"]["unit"],
                    task=successor_id,
                    held=held["kind"],
                    acted="request",
                )
                self.request(
                    successor_id,
                    held["kind"],
                    "%s (arrived while the earlier request was open)"
                    % (held["text"] or "worker " + held["kind"]),
                )

    def escalate(self, task_id, reason, fix):
        """A unit failed its gates past the cap. `fix` is the round a `retry` would run."""
        self.request(task_id, "gate", reason, fix=fix)

    def write_status(self, ticket_id, status):
        if ticket_id == ARCHIVE.upper():
            return  # the archive unit has no ticket row
        if self.plan_path.name.endswith(".plan.yaml"):
            with self.book() as data:
                data.setdefault("ticket_status", {})[ticket_id] = status
            return
        try:
            set_status(self.plan_path, ticket_id, status)
        except (ValueError, OSError) as error:
            self.errors.append("%s: plan Status not written: %s" % (ticket_id, error))
            event("%s: plan Status not written: %s" % (ticket_id, error))

    def note_attention(self, ticket_id, kind, reason):
        path = self.run_dir / "attention.md"
        if not path.exists():
            path.write_text(
                "# Needs you: %s\n\n%s carries the same reason.\n\n"
                % (
                    self.slug,
                    "`status <plan> --json`"
                    if is_yaml(self.plan_path)
                    else "The ticket's Status in the plan",
                ),
                encoding="utf-8",
            )
        with path.open("a", encoding="utf-8") as stream:
            stream.write("- %s %s (%s): %s\n" % (now(), ticket_id, kind, " ".join(reason.split())))

    def record_report(self, task_id, report, **changes):
        """Checkpoint the worker's report as PARTIAL: the unit is not COMPLETE until CI and the merge."""
        path = self.report_file(task_id)
        with self.book() as data:
            # review_rounds is the driver's own count: the worker's value is never trusted.
            checkpoint(
                data,
                task_id,
                dict(report, status="PARTIAL", **dict({"review_rounds": 0}, **changes)),
            )
            data["tasks"][task_id]["report_path"] = str(path)

    def report_file(self, task_id):
        """The newest report in a task directory. Files are write-once: a later attempt writes report.2.json."""
        directory, newest = self.run_dir / task_id, "report.json"
        for index in range(2, 1000):
            if not (directory / ("report.%d.json" % index)).exists():
                break
            newest = "report.%d.json" % index
        return directory / newest

    def read_payload(self, task_id, where, path, load):
        """The payload a report named, read and validated; None when the agent that sent it still owes it.

        A worker's report can reach the driver before the file it announces, and a file can be half written when
        it does. The report is the news and the file is the payload, so a payload that is missing or unusable is
        not a verdict: the sending agent is told once or twice, and only then the Lead is asked. A file that never
        arrives while nobody is left to write it goes straight to the Lead: warning a dead agent buys nothing.
        """
        if not payload_waits(path):
            self.warn_sender(task_id, where, path, "the file is not on disk")
            return None
        try:
            return load()
        except (contracts.ContractError, ValueError) as error:
            self.warn_sender(task_id, where, path, str(error))
            return None

    def warn_sender(self, task_id, where, path, reason):
        """Tell the agent that sent the report what the driver still needs from it. False when the Lead must ask.

        A payload that agent can still fix is its own news; a payload from an agent that is gone, or one it was
        already warned about `PAYLOAD_WARNINGS` times, belongs to the Lead.
        """
        task, unit = self.task(task_id), self.unit_of(task_id)
        record = task.get(where) or {}
        sender = (
            record
            if where == "review"
            else dict(
                task["packet"],
                name=task["packet"]["worker"],
                role="worker",
                assignment_id=task.get("assignment_id"),
            )
        )
        warned = int((task.get("payload_warnings") or {}).get(where) or 0)
        if warned >= PAYLOAD_WARNINGS or not sender.get("assignment_id"):
            self.contract_error(
                task_id,
                where,
                "cannot read %s: %s, after %d warning(s) to the agent that sent the report"
                % (path, reason, warned),
            )
            return False
        name = sender.get("name") or sender.get("worker")
        if not name or not self.agent_live(name):
            self.contract_error(
                task_id, where, "cannot read %s: %s, and no agent left to write it" % (path, reason)
            )
            return False
        text = (
            "The driver cannot read %s: %s. Write or repair that file, then report complete under this assignment."
            % (path, reason)
        )
        _, assignment_id = self.ensure_agent(
            name,
            sender.get("role") or ("reviewer" if where == "review" else "worker"),
            unit,
            {
                "kind": "worker",
                "name": name,
                "task": text,
                "cwd": sender.get("checkout") or task["packet"]["checkout"],
                "cli": sender.get("backend") or self.args.cli,
                "model": sender.get("model") or task["packet"].get("model"),
                "effort": sender.get("effort") or task["packet"].get("effort"),
            },
        )
        # The warned agent comes back as a new assignment: the record the next report is matched against must be
        # the one that will speak, or the report lands on no gate at all.
        if where == "review" and assignment_id:
            self.mark(task_id, review=dict(task["review"], assignment_id=assignment_id))
        elif assignment_id:
            self.mark(task_id, assignment_id=assignment_id)
        self.mark(
            task_id,
            payload_warnings=dict(task.get("payload_warnings") or {}, **{where: warned + 1}),
        )
        self.mark(task_id, gate="reviewing" if where == "review" else None)
        self.log(
            "payload-warning",
            unit=unit,
            task=task_id,
            where=where,
            warnings=warned + 1,
            reason=reason[:200],
        )
        event("%s: warned %s that %s is not readable yet (%s)" % (unit, name, path, reason[:120]))
        return True

    def contract_error(self, task_id, where, error):
        """A handoff file broke its contract: log it, and the Lead decides."""
        self.log(
            "contract-error",
            unit=self.unit_of(task_id),
            task=task_id,
            where=where,
            error=str(error)[:500],
        )
        return self.request(task_id, where, str(error))

    def log_clarifications(self, task_id, source, document, path):
        """One `clarification` event per entry the agent listed; a file is read once, however often its step runs."""
        key = str(path)
        if key in (self.task(task_id).get("clarified") or []):
            return
        for entry in document.get("clarifications") or []:
            self.log(
                "clarification",
                unit=self.unit_of(task_id),
                task=task_id,
                source=source,
                finding=entry["finding"],
                asked_by=entry["asked_by"],
                note=cell(entry["note"], 300),
            )
        with self.book() as data:
            data["tasks"][task_id].setdefault("clarified", []).append(key)

    def resume_for_responses(self, task, directory_id, report_path, missing):
        """Resume the task's worker once with the ids it left unanswered; the new report goes to the next report file."""
        directory = self.run_dir / directory_id
        brief = next_path(directory, "missing", ".md")[0]
        brief.write_text(
            "\n".join(
                [
                    "# Missing responses for %s" % directory_id,
                    "",
                    "Your report %s has no response for the answer_required finding(s): %s."
                    % (report_path, ", ".join(missing)),
                    "Write %s: a full report (same shape, task_id `%s`) whose `responses` answer them, each "
                    '`{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}`.'
                    % (next_path(directory, "report", ".json")[0], directory_id),
                    "Change nothing else.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        text = "Read %s and follow it." % brief
        return self.hand_over(task, dict(task["packet"], task_id=directory_id), brief, text)[1]

    def step_reported(self, task_id):
        task = self.task(task_id)
        path = self.report_file(task_id)

        def read_report():
            report = contracts.load(path, contracts.validate_report)
            contracts.need(
                report["task_id"] == task_id,
                "report task_id %r is not %s" % (report["task_id"], task_id),
            )
            contracts.need(
                report["status"] == "COMPLETE" and report["acceptance_met"],
                "the worker said complete but report.json says %s" % report["status"],
            )
            return report

        report = self.read_payload(task_id, "report", path, read_report)
        if report is None:
            return  # the gate keeps waiting: the worker was warned, or the Lead is asked
        self.log_clarifications(task_id, "report", report, path)
        missing = contracts.missing_responses(report, task.get("answer_ids") or [])
        if missing:
            return self.chase_responses(task_id, path, missing)
        # The merge completes the unit by the same rules, so a report they refuse is the worker's to fix now, while
        # it can still act: a fix round, not a Lead request, until the cap.
        try:
            require_completable(task, dict(report, review_rounds=0))
        except ValueError as error:
            return self.fail_gate(
                task_id,
                report,
                self.gate_name(),
                [
                    {
                        "id": "R1",
                        "kind": "blocking",
                        "source": self.gate_name(),
                        "check": "report",
                        "command": "the COMPLETE rules for %s" % path.name,
                        "exit": 1,
                        "excerpt": str(error),
                        "log_path": str(path),
                    }
                ],
            )
        verdict, lines = self.gate(task_id, task["packet"], report)
        if verdict == "error":
            return self.request(task_id, self.gate_name(), lines[0])
        try:
            if verdict == "FAIL":
                return self.fail_gate(task_id, report, lines[0]["source"], lines)
            self.record_report(task_id, report)
        except ValueError as error:
            return self.request(task_id, "report", "the ledger rejected the report: %s" % error)
        # The archive is gated by the converge --final path alone. Every other report, a fix round of any source included, goes to
        # the unit's reviewer: a delta re-review once the unit has been reviewed, else the first review.
        if task["packet"]["unit"] == ARCHIVE:
            return self.mark(task_id, gate="ready-for-ci")
        reviewed, findings, _ = self.unit_trail(task["packet"]["unit"])
        self.mark(
            task_id,
            gate="converged",
            delta={"from": reviewed, "findings": findings} if reviewed else None,
        )

    def chase_responses(self, task_id, path, missing):
        """A fix-round report lacks a response the reviewer asked for. The first time, resume the worker with the
        missing ids at no round cost; the next report that still lacks them is a request."""
        task = self.task(task_id)
        message = "%s has no response for answer_required finding(s) %s" % (
            path,
            ", ".join(missing),
        )
        if task.get("chased"):
            return self.contract_error(
                task_id, "responses", "the next report still lacks it: " + message
            )
        self.log(
            "contract-error",
            unit=self.unit_of(task_id),
            task=task_id,
            where="responses",
            error=message[:500],
            missing=missing,
        )
        assignment_id = self.resume_for_responses(task, task_id, path, missing)
        self.mark(task_id, gate=None, assignment_id=assignment_id, chased=missing)

    def gate_name(self):
        """The gate that reads a plan: verify for a YAML plan, converge (verify's legacy path) for a Markdown one."""
        return "verify" if self.plan_path.name.endswith(".plan.yaml") else "converge"

    def gate(self, task_id, packet, report):
        """The gate for a unit's report: ("PASS" | "FAIL" | "error", evidence items or the reason)."""
        if self.plan_path.name.endswith(".plan.yaml"):
            # The Markdown converge reads no YAML plan; verify's invariants gate a ticket. The archive's gate is its own.
            reason = "YAML plan: verify gates this report; the Markdown converge does not run"
            self.log("converge-skipped", task=task_id, reason=reason)
            event(reason)
            if packet["unit"] == ARCHIVE:
                return "PASS", []
            return self.verify_ticket(task_id, packet, report)
        if packet["unit"] == ARCHIVE:
            # The archive branch holds the plan with Status ARCHIVED; fall back to the driver's copy if it is untracked.
            copy = Path(packet["checkout"]) / self.plan_rel()
            plan, ticket = (copy if copy.exists() else self.plan_path), None
        else:
            plan, ticket = self.plan_path, packet["unit"]
        return verify.legacy_gate(
            self.args.converge,
            plan,
            packet["checkout"],
            self.args.base,
            lambda: next_path(self.run_dir / task_id, "converge", ".log")[0],
            ticket=ticket,
        )

    def verify_ticket(self, task_id, packet, report):
        """verify's invariants on a YAML plan's ticket: scope, integrity and covers."""
        ticket = packet["unit"].upper()
        try:
            findings = verify.invariants(
                self.model,
                ticket,
                report,
                repo=packet["checkout"],
                base=self.args.base,
                plan_rel=self.plan_rel(),
            )
        except verify.VerifyError as error:
            return "error", [str(error)[:500]]
        if not findings:
            return "PASS", []
        log = next_path(self.run_dir / task_id, "verify", ".log")[0]
        log.write_text(
            "".join("FAIL %(check)s: %(excerpt)s\n" % f for f in findings), encoding="utf-8"
        )
        command = "verify %s --repo %s --base %s" % (
            ticket,
            shlex.quote(str(packet["checkout"])),
            shlex.quote(self.args.base),
        )
        return "FAIL", verify.items(findings, command, log)

    def fail_gate(self, task_id, report, source, items, checks_run=(), answer_ids=(), rebase=False):
        """A gate failed: raise review_rounds, then a fix round, or the escalation hook on the 3rd failure."""
        remaining = [short(item) for item in items]
        with self.book() as data:  # the driver's count: the report's review_rounds is never read
            unit = data["tasks"][task_id]["packet"]["unit"]
            rounds = data.setdefault("rounds", {})
            if (
                unit not in rounds
            ):  # a ledger of the old driver: start from the rounds its reports recorded
                rounds[unit] = unit_summary(data, unit)["failed_reviews"]
            rounds[unit] += 1
            failures = rounds[unit]
        self.record_report(task_id, report, review_rounds=1, remaining=remaining)
        fix = {
            "source": source,
            "items": items,
            "attempt": failures,
            "checks_run": list(checks_run),
            "answer_ids": list(answer_ids),
            "rebase": True if rebase else None,
            "draft": None,
        }
        if self.unit_flag(unit, "pr_ready"):
            # the PR was readied: it goes back to draft before anyone changes its code, a capped one included
            url = self.pr_of(task_id)
            if url and not self.undraft(task_id, url):
                # step_fix tries again and dispatches no worker until it is a draft
                fix["draft"] = url
        self.log("round", unit=unit, task=task_id, source=source, attempt=failures)
        for item in items:
            self.log_finding(
                unit,
                task_id,
                source,
                item.get("kind", "blocking") if isinstance(item, dict) else "blocking",
                item,
            )
        if failures > self.fix_cap(unit):
            return self.escalate(
                task_id,
                "%s failed its gates %d times; last: %s" % (unit, failures, "; ".join(remaining)),
                fix,
            )
        self.mark(task_id, gate="fix-pending", fix=fix)

    def fix_cap(self, unit):
        """The fix rounds a unit may use: its `Max fix rounds:` plan line, else --max-fix-rounds."""
        return ((self.last_plan or {}).get("caps") or {}).get(
            unit.upper(), self.args.max_fix_rounds
        )

    def log_finding(self, unit, task_id, source, kind, item):
        """One `finding` event: a blocking item that goes to a fix round, or a deferred one that goes to deferred.md."""
        found = item.get("id") if isinstance(item, dict) else None
        text = item_text(item) if isinstance(item, dict) else item
        self.log(
            "finding",
            unit=unit,
            task=task_id,
            source=source,
            finding_kind=kind,
            id=found,
            summary=cell(text or "", 200),
        )

    def step_converged(self, task_id):
        task = self.task(task_id)
        packet = task["packet"]
        unit, name = packet["unit"], worker_name(self.slug, packet["unit"][1:], "-review")
        review = task.get("review")
        if not review:
            path, index = next_path(self.run_dir / unit, "review", ".json")
            review = {
                "name": name,
                "path": str(path),
                "dir": str(self.run_dir / (unit + "-review" + ("" if index == 1 else str(index)))),
            }
            self.mark(task_id, review=review)
        review_dir = Path(review["dir"])
        review_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = review_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(
                self.review_text(packet, review["path"], task.get("delta")), encoding="utf-8"
            )
        backend, model = self.gate_step("reviewer", "large")
        effort = self.step_effort("reviewer", "high")
        _, assignment_id = self.ensure_agent(
            name,
            "reviewer",
            unit,
            {
                "kind": "worker",
                "name": name,
                "task": "Read %s and follow it." % task_md,
                "cwd": packet["checkout"],
                "cli": backend,
                "model": model,
                "effort": effort,
            },
        )
        self.mark(task_id, gate="reviewing", review=dict(review, assignment_id=assignment_id))
        event(
            "spawned %s (%s, %s, %s)" % (name, self.gate_label("reviewer", "large"), model, effort)
        )

    def review_text(self, packet, review_path, delta=None):
        checkout = Path(packet["checkout"])
        rules = [str(checkout / n) for n in ("AGENTS.md", "CLAUDE.md") if (checkout / n).exists()]
        return "\n".join(
            [
                "# Review %s: one pass, paths only" % packet["unit"].upper(),
                "",
                "Read these yourself; nothing is pasted here.",
                "- Task: %s" % (self.run_dir / packet["unit"] / "task.md"),
                "- Plan: %s (its Decisions, Scenarios and Seams)" % self.plan_path,
                *(
                    ["- Commit range: run `git -C %s diff %s...HEAD`" % (checkout, self.args.base)]
                    if not delta
                    else [
                        "- This is a re-review. Only `git -C %s diff %s..HEAD` is new; the whole change is `%s...HEAD`."
                        % (checkout, delta["from"], self.args.base),
                        "- The earlier findings and the implementer's answers: %s. Check each one (resolved or not, with evidence) "
                        "and raise new blocking items only inside the delta. A blocking item outside the files the delta changes "
                        "is deferred, not gated, unless it is about an earlier finding: reuse that finding's `id`, or set "
                        "`finding` to it."
                        % (
                            delta.get("findings")
                            or "none; the first review's file is in %s"
                            % (self.run_dir / packet["unit"])
                        ),
                    ]
                ),
                "- Repo rules: %s"
                % (", ".join(rules) or "none found; follow the code's own conventions"),
                "",
                "Block only on MAJOR defects: wrong behaviour, data loss, or a scenario that is not really tested. "
                "Everything else is a `concern`, `question`, `nit` or `deferred` item; it never blocks.",
                "Touch nothing in the repository.",
                "You may wait on background jobs (a long test run, a queued job). Ending a turn does not finish the "
                "review: write the review after the jobs it needs finish, then report complete with the assignment id "
                "this brief came with.",
                "",
                "Write %s as JSON, contract 2:" % review_path,
                '{"contract":2,"verdict":"pass|block","items":[...],"checks_run":[{"command","exit","at_commit"}],'
                '"clarifications":[{"finding","asked_by":"reviewer","note"}],"commit":"<git rev-parse HEAD of the checkout>"}',
                "Every item has `id`, `kind` (blocking|concern|question|nit|deferred), `summary` and `answer_required` "
                "(true only when the implementer must reply). A blocking item also needs `file`, `line`, `fix` and "
                "`evidence` {command, exit, excerpt, log_path}: the command that shows the defect, its exit code, an excerpt "
                "and the path of the full output you saved. `scenario`, `why` and `verify` are optional; any other field you "
                "add is passed on verbatim.",
                'The verdict is "block" exactly when an item has kind blocking. Prose for people may go in review.md beside it.',
                "List in `checks_run` every check you ran, so the implementer does not repeat them. List in `clarifications` "
                "each question you asked the implementer directly.",
                "Then call the agent-runtime `report` tool with kind=complete and one line: `<n> blocking - %s`."
                % review_path,
                "",
            ]
        )

    def defer(self, task_id, items, source_path):
        """Items that go to deferred.md and the retro only: logged as findings, appended with their source file."""
        unit = self.unit_of(task_id)
        for item in items:
            self.log_finding(unit, task_id, "review", item.get("kind", "deferred"), item)
        if not items:
            return
        deferred = self.run_dir / "deferred.md"
        if not deferred.exists():
            deferred.write_text(
                "# Deferred findings: %s (for the major review after the driver ships)\n\n"
                % self.slug,
                encoding="utf-8",
            )
        with deferred.open("a", encoding="utf-8") as stream:
            for item in items:
                stream.write(
                    "- %s %s%s: %s%s (%s)\n"
                    % (
                        unit.upper(),
                        item["id"],
                        " [%s]" % item["kind"] if item.get("kind") else "",
                        item_text(item),
                        " (blocking, but outside the re-review's delta)"
                        if item.get("scope")
                        else "",
                        source_path,
                    )
                )

    def step_reviewed(self, task_id):
        task = self.task(task_id)
        packet = task["packet"]

        def read_review():
            review = contracts.load(task["review"]["path"], contracts.validate_review)
            report = contracts.load(task["report_path"], contracts.validate_report)
            check_commit(review["commit"], head_of(packet["checkout"]), task["review"]["path"])
            return review, report

        loaded = self.read_payload(task_id, "review", task["review"]["path"], read_review)
        if loaded is None:
            return  # the gate keeps waiting: the reviewer was warned, or the Lead is asked
        review, report = loaded
        head = head_of(packet["checkout"])
        self.log_clarifications(task_id, "review", review, task["review"]["path"])
        self.mark(
            task_id, reviewed_commit=head
        )  # what a later re-review's delta starts from, whatever the verdict
        blocking, rest = contracts.review_items(review)
        if task.get("delta"):
            blocking, outside = self.scope_filter(task_id, packet, task["delta"], blocking)
            rest = rest + outside
        asks = [i for i in blocking + rest if i.get("answer_required") is True]
        self.defer(task_id, [i for i in rest if i not in asks], task["review"]["path"])
        if blocking:
            # a fix round holds only blocking and answer_required items, and the reviewer's checks
            return self.fail_gate(
                task_id,
                report,
                "review",
                blocking + [i for i in rest if i in asks],
                checks_run=review.get("checks_run") or [],
                answer_ids=[i["id"] for i in asks],
            )
        if asks:  # a question costs no round: CI goes on while the implementer answers
            self.start_answers(task_id, asks, review)
        self.mark(task_id, gate="ready-for-ci")

    def unit_trail(self, unit):
        """(last reviewed commit, newest findings.json, newest report.json) over the unit's attempts; "" when none yet."""
        reviewed = findings = report = ""
        with self.book() as data:
            tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
        for task in tasks:
            reviewed = task.get("reviewed_commit") or reviewed
            report = task.get("report_path") or report
            candidate = self.run_dir / task["packet"]["task_id"] / "findings.json"
            findings = str(candidate) if candidate.exists() else findings
        return reviewed, findings, report

    def earlier_ids(self, unit, current_review):
        """The ids of every finding the unit has had before this review: fix-round findings and earlier review items."""
        found = set()
        with self.book() as data:
            tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
        paths = [self.run_dir / t["packet"]["task_id"] / "findings.json" for t in tasks]
        paths += [
            Path(t["review"]["path"])
            for t in tasks
            if t.get("review") and t["review"]["path"] != current_review
        ]
        for path in paths:
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(document, dict):
                continue
            for item in (
                (document.get("items") or [])
                + (document.get("major") or [])
                + (document.get("deferred") or [])
            ):
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    found.add(item["id"])
        return found

    @staticmethod
    def references(item):
        """The finding ids an item says it is about: its `finding`, `findings`, `ref`, `refs` or `references` field."""
        found = set()
        for key in ("finding", "findings", "ref", "refs", "references"):
            value = item.get(key)
            values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
            found.update(v for v in values if isinstance(v, str))
        return found

    def scope_filter(self, task_id, packet, delta, blocking):
        """(blocking items to gate on, deferred copies of the rest). In a re-review a blocking item whose file the delta
        did not change and which is not about an earlier finding is outside the scope: it is deferred and logged as a
        `scope` event, never gated. When the delta cannot be read nothing is filtered."""
        checkout = Path(packet["checkout"])
        diff = subprocess.run(
            ["git", "-C", str(checkout), "diff", "--name-only", delta["from"] + "..HEAD"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if diff.returncode != 0:
            return blocking, []
        changed = set(diff.stdout.split())
        unit = packet["unit"]
        earlier = self.earlier_ids(unit, self.task(task_id)["review"]["path"])
        kept, outside = [], []
        for item in blocking:
            file = str(item.get("file") or "")
            if file.startswith(str(checkout) + "/"):
                file = file[len(str(checkout)) + 1 :]
            file = file[2:] if file.startswith("./") else file
            if file in changed or item["id"] in earlier or self.references(item) & earlier:
                kept.append(item)
                continue
            outside.append(
                dict(item, kind="deferred", answer_required=False, scope="outside-delta")
            )
            self.log(
                "scope",
                unit=unit,
                task=task_id,
                id=item["id"],
                file=file,
                delta_from=delta["from"],
                why="blocking item outside the delta, about no earlier finding",
            )
        return kept, outside

    # ---- answers: a passing review's answer_required items, answered while CI runs ---------------------------
    # The task keeps one `answers` record: {id, dir, assignment_id, ids, state working|reported|done|failed, tries}.
    # The implementer is resumed for answers only; the unit does not wait on it for CI but is not pr-ready until the
    # answers are in. A response `disputed` with `changes_code: true` sends the new commits to a delta re-review.

    def start_answers(self, task_id, asks, review):
        task = self.task(task_id)
        packet, unit = task["packet"], task["packet"]["unit"]
        if (task.get("answers") or {}).get("review") == task["review"]["path"]:
            return  # the driver died after starting them
        number = next(
            n for n in range(1, 1000) if not (self.run_dir / ("%sa%d" % (unit, n))).exists()
        )
        answer_id = "%sa%d" % (unit, number)
        directory = self.run_dir / answer_id
        directory.mkdir(exist_ok=True, mode=0o700)
        findings_path = directory / "findings.json"
        write_atomic(
            findings_path,
            json.dumps(
                contracts.make_findings("review", number, asks, review.get("checks_run") or []),
                indent=2,
            )
            + "\n",
        )
        task_md = directory / "task.md"
        task_md.write_text(
            "\n".join(
                [
                    "Contract: %s" % (self.run_dir / "contract.md"),
                    "Report: %s" % (directory / "report.json"),
                    "",
                    "# Answers only for %s (task %s)" % (unit.upper(), answer_id),
                    "",
                    "The review passed. These findings need your answer, nothing else (also in %s):"
                    % findings_path,
                    "",
                    *["- " + describe(item) for item in asks],
                    "",
                    "Change no code unless an answer needs it. Report with task_id `%s`; `responses` holds one "
                    '`{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` per finding above. '
                    "A `disputed` answer that needs a code change sets `changes_code` true and names the commit: the reviewer sees it."
                    % answer_id,
                    "Same checkout and branch: %s on %s. Edit only: %s."
                    % (
                        packet["checkout"],
                        task["checkout_at_claim"]["branch"],
                        ", ".join(packet["owns"]),
                    ),
                    "Re-run only what a change touches, then the final gate if you changed code. Open no new pull request.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        text = (
            "Read %s and follow it. Answers only: the review passed; nothing else changes."
            % task_md
        )
        _, assignment_id = self.hand_over(task, dict(packet, task_id=answer_id), task_md, text)
        self.log(
            "answers", unit=unit, task=task_id, answer_task=answer_id, ids=[i["id"] for i in asks]
        )
        self.mark(
            task_id,
            answers={
                "id": answer_id,
                "dir": str(directory),
                "assignment_id": assignment_id,
                "ids": [i["id"] for i in asks],
                "state": "working",
                "tries": 0,
                "review": task["review"]["path"],
                "findings": str(findings_path),
            },
        )

    def step_answers(self, task_id):
        task = self.task(task_id)
        answers = task["answers"]
        path = self.report_file(answers["id"])
        try:
            report = contracts.load(path, contracts.validate_report)
            contracts.need(
                report["task_id"] == answers["id"],
                "report task_id %r is not %s" % (report["task_id"], answers["id"]),
            )
        except contracts.ContractError as error:
            self.mark(task_id, answers=dict(answers, state="failed"))
            return self.contract_error(task_id, "answers", error)
        self.log_clarifications(task_id, "report", report, path)
        missing = contracts.missing_responses(report, answers["ids"])
        unit = task["packet"]["unit"]
        if missing and answers["tries"] >= 1:
            self.mark(task_id, answers=dict(answers, state="failed"))
            return self.contract_error(
                task_id, "responses", "%s still has no response for %s" % (path, ", ".join(missing))
            )
        if missing:
            self.log(
                "contract-error",
                unit=unit,
                task=task_id,
                where="responses",
                missing=missing,
                error="%s has no response for answer_required finding(s) %s"
                % (path, ", ".join(missing)),
            )
            assignment_id = self.resume_for_responses(task, answers["id"], path, missing)
            return self.mark(
                task_id,
                answers=dict(answers, state="working", tries=1, assignment_id=assignment_id),
            )
        for response in report["responses"]:
            self.log(
                "response",
                unit=unit,
                task=task_id,
                finding=response["id"],
                status=response["status"],
                changes_code=response.get("changes_code"),
                commit=response.get("commit"),
            )
        changing = [
            r["id"]
            for r in report["responses"]
            if r["status"] == "disputed" and r.get("changes_code") is True
        ]
        if changing and task.get("gate") in ("ready-for-ci", "awaiting-merge"):
            # the new commits go to a delta re-review before the unit goes on to CI again; the PR goes back to
            # draft (the poll's ensure_draft, once readiness is forgotten) and is readied again on its return
            self.unready(self.unit_of(task_id))
            reviewed = task.get("reviewed_commit")
            self.mark(
                task_id,
                answers=dict(answers, state="done"),
                gate="converged",
                review=None,
                ci_green=None,
                ci_state=None,
                ci_rows=None,
                ci_head=None,
                delta={"from": reviewed, "findings": answers["findings"]},
            )
            return event(
                "%s: %s disputed with changes_code; delta re-review from %s"
                % (task_id, ", ".join(changing), (reviewed or "the base")[:7])
            )
        self.mark(task_id, answers=dict(answers, state="done"))

    # ---- PR, CI and merge: one non-blocking poll in the loop -----------------------------------------------
    # The worker opens a draft pull request with its report. The driver keeps it a draft while the review runs,
    # polls its checks on every round (nothing waits, no fix round is raised for checks that are not there yet), and
    # marks it ready (`gh pr ready`) once the last review passed with green checks. From then on the unit waits for
    # the merge: it holds no worker slot and the driver only polls `gh` for it.

    def gh(self, argv, timeout=60):
        """Run `gh` and return (exit code, stdout, stderr). A `gh` that cannot run is an error, not a guess."""
        try:
            result = gh_process(argv, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 1, "", "gh timed out"
        except OSError as error:
            return 1, "", str(error)
        return result.returncode, result.stdout, result.stderr

    def pr_url(self, task_id):
        """The unit's pull request: a fix round opens none, so fall back to an earlier attempt's report."""
        task = self.task(task_id)
        reports = []
        if task.get("report_path"):
            try:
                reports.append(contracts.load(task["report_path"], contracts.validate_report))
            except contracts.ContractError:
                pass
        with self.book() as data:
            reports += [
                t["report"]
                for t in data["tasks"].values()
                if t["packet"].get("unit") == task["packet"]["unit"] and t.get("report")
            ]
        url = next((r["pr"] for r in reports if r.get("pr")), None)
        match = re.fullmatch(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)/?", url or "")
        if match is None:
            raise ValueError("report.json names no pull request URL to check (pr: %r)" % url)
        return url, "%s#%s" % match.groups()

    def pr_of(self, task_id):
        """The unit's pull request URL, or None when no report names one."""
        try:
            return self.pr_url(task_id)[0]
        except (contracts.ContractError, ValueError):
            return None

    def pr_view(self, url) -> dict[str, Any]:
        """The pull request as `gh` sees it: {state, isDraft, mergeStateStatus, mergeable, headRefOid}. A `gh` that
        fails is no state at all: the driver polls again rather than guessing."""
        code, out, err = self.gh(
            [
                "pr",
                "view",
                url,
                "--json",
                "state,isDraft,mergeStateStatus,mergeable,mergeCommit,headRefOid",
            ]
        )
        if code != 0:
            return {"error": (err or out).strip()[:200]}
        try:
            state = json.loads(out)
        except ValueError:
            return {"error": "gh pr view printed no JSON"}
        return state if isinstance(state, dict) else {"error": "gh pr view printed no object"}

    def pr_checks(self, url):
        """The pull request's checks as (state, rows): green, pending, none or failed. `gh` prints one JSON row per
        check; a bucket says how it went. An exit code `gh` does not use for checks is an error, not a state."""
        code, out, _err = self.gh(["pr", "checks", url, "--json", "name,bucket,link,description"])
        try:
            rows = json.loads(out or "[]")
        except ValueError:
            rows = []
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        buckets = {str(r.get("bucket") or "").lower() for r in rows}
        if buckets & {"fail", "failing", "cancel", "cancelled", "timed_out", "action_required"}:
            return "failed", rows
        if buckets & {"pending", "queued", "in_progress", "waiting", "requested"}:
            return "pending", rows
        if code == 0 and rows:
            return "green", rows
        if code in (0, 1, 8) and not rows:
            return "none", rows
        if code in (0, 1, 8):
            return "pending", rows
        return "error", rows

    def units(self):
        """Every unit of the run with the task that is still open for it."""
        with self.book() as data:
            tasks = [copy.deepcopy(t) for t in data["tasks"].values()]
        units = {}
        for task in tasks:
            unit = task["packet"].get("unit") or task["packet"]["task_id"]
            units.setdefault(unit, []).append(task)
        return {unit: group[-1] for unit, group in units.items()}

    def unit_flag(self, unit, key):
        """True when any attempt of the unit carries the flag: the pull request belongs to the unit, not to a task."""
        with self.book() as data:
            return any(
                task["packet"].get("unit") == unit and task.get(key)
                for task in data["tasks"].values()
            )

    def poll(self):
        """One non-blocking look at every unit's pull request: was it merged by hand, is it still a draft, how are
        its checks. A unit with no checks yet, or with checks still running, is looked at again on a later round: no
        fix round is raised and no other unit waits. The view taken here is the one `step_ci` acts on this round."""
        self.views = {}
        for unit, task in self.units().items():
            task_id = task["packet"]["task_id"]
            gate = task.get("gate")
            if (
                task["status"] in ("COMPLETE", "RELEASED")
                or gate in PARKED
                or gate in ("cleanup", "done")
            ):
                continue
            url = self.pr_of(task_id)
            if not url:
                continue
            if gate == "awaiting-merge":
                # main moves after the PR is ready: red checks there are a fix round (`step_merge` reads the view)
                self.poll_checks(task_id, url)
                continue
            state = self.pr_view(url)
            if state.get("error"):
                continue
            self.views[url] = state
            if state.get("state") == "MERGED":
                self.merged_early(task_id, state)
                continue
            if gate in ("converged", "reviewing", "reviewed") and not self.unit_flag(
                unit, "pr_ready"
            ):
                self.ensure_draft(task_id, url, state)
            if gate in ("converged", "reviewing", "reviewed", "ready-for-ci"):
                self.poll_checks(task_id, url, state.get("headRefOid"))

    def ensure_draft(self, task_id, url, state):
        """The pull request is a draft while the review runs: a worker that opened it ready is put back."""
        unit = self.unit_of(task_id)
        if self.unit_flag(unit, "draft_done"):
            return
        if not state.get("isDraft"):
            code, _out, _err = self.gh(["pr", "ready", url, "--undo"])
            self.log(
                "draft", unit=unit, task=task_id, pr=url, outcome="draft" if code == 0 else "failed"
            )
            event("%s: the pull request is a draft again (%s)" % (task_id, url))
        self.mark(task_id, draft_done=True)

    def undraft(self, task_id, url):
        """The pull request goes back to draft before its code changes again, so nobody merges it half-fixed. On
        success readiness is cleared across the unit: `gh pr ready` runs again when the unit is back at ready-for-ci.
        False when `gh` refused; the caller holds the round and tries again on a later round."""
        unit = self.unit_of(task_id)
        code, out, err = self.gh(["pr", "ready", url, "--undo"])
        self.log(
            "draft", unit=unit, task=task_id, pr=url, outcome="draft" if code == 0 else "failed"
        )
        if code != 0:
            event(
                "%s: could not put %s back to draft (%s); trying again on a later round"
                % (task_id, url, (err or out).strip()[:200])
            )
            return False
        self.unready(unit)
        event("%s: the pull request is a draft again (%s)" % (task_id, url))
        return True

    def unready(self, unit):
        """Forget that the unit's pull request was readied: `unit_flag` ORs every attempt, so every one is cleared."""
        with self.book() as data:
            for task in data["tasks"].values():
                if task["packet"].get("unit") == unit:
                    task.pop("pr_ready", None)
                    task.pop("draft_done", None)

    def poll_checks(self, task_id, url, head=None):
        """The pull request's checks, once per round, and the head they were read on. The state is kept on the task;
        nothing waits for it."""
        state, rows = self.pr_checks(url)
        task = self.task(task_id)
        if (
            task.get("ci_state") != state
            or task.get("ci_rows") != rows
            or task.get("ci_head") != head
        ):
            self.mark(task_id, ci_state=state, ci_rows=rows, ci_head=head)
        self.log(
            "ci",
            unit=self.unit_of(task_id),
            task=task_id,
            gate=task.get("gate"),
            state=state,
            pr=url,
        )
        return state

    def step_ci(self, task_id):
        """The unit's merge state, then its checks. A conflict, a branch behind its base or a merge state GitHub has
        not computed yet is handled before `gh pr ready`, so a pull request that cannot merge is never readied. Green
        and the last review passed: the pull request is marked ready and the unit waits for the merge."""
        task = self.task(task_id)
        try:
            url = self.pr_url(task_id)[0]
        except (contracts.ContractError, ValueError) as error:
            return self.request(task_id, "ci", str(error))
        view = getattr(self, "views", {}).pop(url, None) or self.pr_view(url)
        kind = merge_state(view)
        if kind == "merged":
            return False  # the next poll completes it (merged_early)
        if kind == "closed":
            return self.request(task_id, "merge", "%s was closed without a merge" % url)
        moved = self.merge_moved(task_id, url, view)
        if moved is not None:
            return moved
        state = task.get("ci_state")
        if state != "green":
            if state == "failed":
                report = contracts.load(
                    self.task(task_id)["report_path"], contracts.validate_report
                )
                return self.fail_gate(
                    task_id, report, "ci", self.ci_items(task_id, url, task.get("ci_rows") or [])
                )
            if state == "error":
                return self.request(task_id, "ci", "gh pr checks failed for %s" % url)
            event("%s: no checks yet, or they are still running; polling again" % task_id)
            return False  # no fix round: the unit is polled again on a later round
        before = task.get("updated_from")
        if before and task.get("ci_head") in (None, before):
            # green read before GitHub moved the head is the old head's green, not the update's
            event("%s: the checks are not yet those of the updated branch; polling again" % task_id)
            return False
        waiting = (task.get("answers") or {}).get("state") in ("working", "reported")
        if waiting:  # green, but the implementer's answers may still change the code: pr-ready waits for them
            return False
        if not task.get("pr_ready"):
            code, out, err = self.gh(["pr", "ready", url])
            if code != 0:
                return self.request(
                    task_id, "ci", "gh pr ready failed: %s" % (err or out).strip()[:200]
                )
            self.mark(task_id, pr_ready=True)
            event("%s: the pull request is ready for review (%s)" % (task_id, url))
        unit = self.unit_of(task_id)
        # an update-branch moves the head without a new report: the live head keys the wake, so it is sent again
        head = (
            view.get("headRefOid")
            or contracts.load(self.task(task_id)["report_path"], contracts.validate_report)["head"]
        )
        self.wake("pr-ready", unit, "pr-ready %s: %s" % (unit, url), key="%s:%s" % (unit, head))
        self.put_status(task_id, "IN PROGRESS · awaiting merge (%s)" % url)
        self.mark(task_id, gate="awaiting-merge", pr=url, updated_from=None)

    def merge_moved(self, task_id, url, view):
        """What an open pull request's merge state asks for: a conflict round, an update, or another poll. None when
        it can merge as it is and the caller goes on; True when the gate moved, False when nothing moves yet."""
        kind = merge_state(view)
        if kind == "conflict":
            self.conflict(task_id, url, view)
            return True
        if kind == "unknown":
            event(
                "%s: the merge state of %s is not known yet (%s); polling again"
                % (task_id, url, view.get("error") or "UNKNOWN")
            )
            return False
        if kind == "behind":
            return self.update_branch(task_id, url, view) is not False
        return None

    def update_branch(self, task_id, url, view):
        """The pull request is behind its base: `gh pr update-branch` brings it up to date. A clean update needs no
        review (nobody wrote code): the unit is back at ready-for-ci, its checks run again and the PR stays ready. A
        failed update is a conflict round only when the PR now conflicts; any other failure goes to the Lead, since no
        rebase fixes an access or network error."""
        before = view.get("headRefOid")
        code, out, err = self.gh(["pr", "update-branch", url])
        unit = self.unit_of(task_id)
        if code == 0:
            self.log("update-branch", unit=unit, task=task_id, pr=url, outcome="updated")
            event("%s: the branch was updated; no review needed" % unit)
            self.mark(
                task_id,
                gate="ready-for-ci",
                ci_state=None,
                ci_rows=None,
                ci_head=None,
                updated_from=before,
            )
            return False  # the new commits run CI again: polled on a later round
        excerpt = (err or out).strip()[:1500] or "gh pr update-branch exited %d" % code
        fresh = self.pr_view(url)
        if merge_state(fresh) == "conflict":
            self.log("update-branch", unit=unit, task=task_id, pr=url, outcome="conflict")
            evidence = ("update-branch", "gh pr update-branch %s" % url, code, excerpt)
            return self.conflict(task_id, url, fresh, evidence)
        self.log("update-branch", unit=unit, task=task_id, pr=url, outcome="failed")
        return self.request(
            task_id,
            "ci",
            "gh pr update-branch failed for %s and the pull request does not conflict: %s. Fix the access or "
            "update the branch by hand, then decide how the unit goes on." % (url, excerpt[:300]),
        )

    def conflict(self, task_id, url, view, evidence=None):
        """The pull request conflicts with its base: a fix round of its own, where a fresh worker rebases and the
        result goes to a delta re-review. The evidence is what `gh` said: the merge state, or the failed update."""
        unit = self.unit_of(task_id)
        status = "mergeStateStatus %s, mergeable %s" % (
            view.get("mergeStateStatus"),
            view.get("mergeable"),
        )
        check, command, code, excerpt = evidence or (
            "merge-state",
            "gh pr view %s --json mergeStateStatus,mergeable" % url,
            0,
            "the pull request conflicts with its base: %s" % status,
        )
        if evidence:
            excerpt = "%s (%s)" % (excerpt, status)
        log = next_path(self.run_dir / task_id, check, ".log")[0]
        log.write_text(excerpt + "\n", encoding="utf-8")
        self.log("conflict", unit=unit, task=task_id, pr=url, state=status)
        report = contracts.load(self.task(task_id)["report_path"], contracts.validate_report)
        item = {
            "id": "BR1",
            "kind": "blocking",
            "source": "conflict",
            "check": check,
            "command": command,
            "exit": code,
            "excerpt": excerpt,
            "log_path": str(log),
        }
        return self.fail_gate(task_id, report, "conflict", [item], rebase=True)

    def ci_items(self, task_id, url, rows):
        """The failing CI checks as evidence items. Each carries the capped tail of its own job's log (`gh run view
        --log-failed`, fetched once per run); the job's whole log is written to its `log_path`."""
        failing = [
            row
            for row in rows
            if str(row.get("bucket") or "").lower() in ("fail", "failing", "cancel", "cancelled")
        ]
        runs = {}  # run id -> its failed-step log: every failing job of a run shares one fetch
        items = []
        for number, row in enumerate(failing, 1):
            name = row.get("name") or "ci"
            text = str(row.get("description") or "").strip()
            run = re.search(r"/runs/(\d+)", str(row.get("link") or ""))
            if run:
                if run.group(1) not in runs:
                    _code, out, err = self.gh(["run", "view", run.group(1), "--log-failed"])
                    runs[run.group(1)] = (out or err).strip()
                text = job_log(runs[run.group(1)], name) or text
            items.append(self.ci_item(task_id, url, "CI%d" % number, name, text))
        if not items:
            items.append(self.ci_item(task_id, url, "CI1", "ci", "the checks failed"))
        return items

    def ci_item(self, task_id, url, item_id, check, text):
        """One CI finding: the job's full text at `log_path`, its capped tail as the excerpt."""
        log = next_path(self.run_dir / task_id, "ci", ".log")[0]
        log.write_text(text + "\n", encoding="utf-8")
        return {
            "id": item_id,
            "kind": "blocking",
            "source": "ci",
            "check": check,
            "command": "gh pr checks %s" % url,
            "exit": 1,
            "excerpt": log_tail(text) or "the check failed",
            "log_path": str(log),
        }

    def put_status(self, task_id, status):
        """The ticket's Status cell; the archive unit has no row."""
        if self.task(task_id)["packet"]["unit"] != ARCHIVE:
            if self.plan_path.name.endswith(".plan.yaml"):
                self.write_status(self.ticket_name(task_id), status)
            else:
                set_status(self.plan_path, self.ticket_name(task_id), status)

    def ticket_name(self, task_id):
        return self.task(task_id)["packet"]["unit"].upper()

    def step_merge(self, task_id):
        """The unit waits for the merge, and the merge itself is the user's. The driver watches the pull request
        while it waits: main moves on, so a PR that now conflicts, is behind or has red checks is acted on."""
        url = self.task(task_id).get("pr")
        if not url:
            return self.request(task_id, "merge", "no pull request to wait for")
        state = self.pr_view(url)
        kind = merge_state(state)
        if kind == "closed":
            return self.request(task_id, "merge", "%s was closed without a merge" % url)
        if kind != "merged":
            moved = self.merge_moved(task_id, url, state)
            if moved is not None:
                return moved
            task = self.task(task_id)
            if task.get("ci_state") == "failed":
                report = contracts.load(task["report_path"], contracts.validate_report)
                return self.fail_gate(
                    task_id, report, "ci", self.ci_items(task_id, url, task.get("ci_rows") or [])
                )
            return False
        stored = self.task(task_id)["report"]
        report = dict(
            stored,
            status="COMPLETE",
            remaining=[],
            acceptance_met=True,
            review_rounds=stored.get("review_rounds", 0),
        )
        if not self.complete_merged(task_id, report):
            return
        commit = (state.get("mergeCommit") or {}).get("oid", "")
        self.put_status(
            task_id,
            "COMPLETE %s · merged %s%s" % (now()[:10], url, " (%s)" % commit[:7] if commit else ""),
        )
        self.mark(task_id, gate="cleanup", merge_commit=commit)
        event("%s merged: %s" % (task_id, url))

    def complete_merged(self, task_id, report):
        """The ledger's COMPLETE for a merged unit. A merge is final, so a report that fails the COMPLETE rules does
        not hold it back: the unit completes with the checks as reported, deferred.md says what went unverified, and no
        Lead is asked. False when the ledger refuses for another reason (a closed task); the Lead is asked then."""
        unverified = None
        gone = self.task(task_id)["packet"]["checkout"]
        if not Path(gone).exists():
            self.log("merged-checkout-missing", task=task_id, checkout=gone)
            event("%s merged, but its checkout %s no longer exists" % (task_id, gone))
        try:
            with self.book() as data:
                try:
                    checkpoint(data, task_id, report, merged=True)
                except ValueError as error:
                    unverified = " ".join(str(error).split())
                    settle_merged(data, task_id, report)
        except ValueError as error:
            self.request(task_id, "merge", "merged, but the ledger rejected COMPLETE: %s" % error)
            return False
        if unverified:
            unit = self.unit_of(task_id)
            self.defer_unverified(task_id, unverified)
            self.log("merged-unverified", unit=unit, task=task_id, reason=unverified)
        return True

    def merged_early(self, task_id, state):
        """The pull request was merged while the unit was still working: a merge is final. The unit is complete, its
        agents stop, and the findings it never settled go to deferred.md tagged merged-early."""
        task = self.task(task_id)
        unit = task["packet"]["unit"]
        url = task.get("pr") or self.pr_of(task_id)
        items = self.open_findings(unit)
        if items:
            self.defer_merged(unit, items, self.run_dir / unit)
        self.log("merged-early", unit=unit, task=task_id, pr=url, findings=len(items))
        self.close_request(task_id, "the pull request was merged before the unit finished")
        if not self.complete_merged(task_id, self.merged_report(task_id, url)):
            return
        commit = (state.get("mergeCommit") or {}).get("oid", "")
        self.put_status(
            task_id,
            "COMPLETE %s · merged %s%s" % (now()[:10], url, " (%s)" % commit[:7] if commit else ""),
        )
        self.mark(task_id, gate="cleanup", pr=url, merge_commit=commit)
        self.stop_unit_agents(unit, "merged")
        event("%s merged early: %s" % (unit, url))

    def reported_checks(self, task_id):
        """The checks of the newest attempt of the unit that holds a report: a fix round's own attempt usually has
        none yet, and the merge must not erase the evidence its worker already stored. [] only when none reported."""
        with self.book() as data:
            unit = data["tasks"][task_id]["packet"].get("unit") or task_id
            attempts = [
                tid for tid, t in data["tasks"].items() if (t["packet"].get("unit") or tid) == unit
            ]
            reports = {tid: data["tasks"][tid].get("report") for tid in attempts}
        ordered = [task_id] + [tid for tid in reversed(attempts) if tid != task_id]
        for tid in ordered:
            if reports.get(tid):
                return list(reports[tid].get("checks") or [])
        return []

    def merged_report(self, task_id, url):
        """The report a unit that was merged before it finished completes with: the merge settled it."""
        task = self.task(task_id)
        try:
            head = head_of(task["packet"]["checkout"])
        except (ValueError, OSError, subprocess.TimeoutExpired):
            head = "unknown"
        return {
            "contract": contracts.VERSION,
            "task_id": task_id,
            "status": "COMPLETE",
            "summary": "the pull request was merged before the unit finished",
            "acceptance_met": True,
            "files": [],
            "decisions": [],
            "interfaces": [],
            "remaining": [],
            "checks": self.reported_checks(task_id),
            "review_rounds": 0,
            "usage": "unknown",
            "head": head,
            "pr": url,
        }

    def open_findings(self, unit):
        """The findings a unit has not settled: the newest fix round's items, or the ones a fix round is about to
        get."""
        _, findings, _ = self.unit_trail(unit)
        if findings:
            try:
                document = json.loads(Path(findings).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                document = None
            if isinstance(document, dict) and document.get("items"):
                return list(document["items"])
        with self.book() as data:
            tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
        for task in reversed(tasks):
            items = (task.get("fix") or {}).get("items") or []
            if items:
                return list(items)
        return []

    def deferred_file(self):
        """deferred.md, created with its header on first use."""
        deferred = self.run_dir / "deferred.md"
        if not deferred.exists():
            deferred.write_text(
                "# Deferred findings: %s (for the major review after the driver ships)\n\n"
                % self.slug,
                encoding="utf-8",
            )
        return deferred

    def defer_merged(self, unit, items, source):
        """The findings a merged-early unit never settled: deferred, tagged with the merge that ended it."""
        with self.deferred_file().open("a", encoding="utf-8") as stream:
            for item in items:
                stream.write(
                    "- %s %s [merged-early]: %s (%s)\n"
                    % (unit.upper(), item.get("id", "?"), item_text(item), source)
                )

    def defer_unverified(self, task_id, reason):
        """A unit that merged without meeting the COMPLETE rules: the human reads what went unverified."""
        with self.deferred_file().open("a", encoding="utf-8") as stream:
            stream.write("- [merged-unverified] %s: %s\n" % (task_id, reason))

    def step_cleanup(self, task_id):
        """A merge is final: stop the unit's implementer and reviewer panes, then remove the worktree only when nothing
        would be lost; else leave it and say why."""
        task = self.task(task_id)
        packet = task["packet"]
        self.stop_unit_agents(
            packet["unit"], "merged"
        )  # a merge is final: both panes stop whatever the worktree holds
        with self.book() as data:
            busy = [
                t["packet"]["task_id"]
                for t in data["tasks"].values()
                if (
                    t["packet"]["worker"] == packet["worker"]
                    or t["packet"]["checkout"] == packet["checkout"]
                )
                and t["status"] not in ("COMPLETE", "RELEASED")
            ]
        if busy:
            return self.mark(
                task_id, gate="done", cleanup="kept: worker still holds %s" % ", ".join(busy)
            )
        path, branch = Path(packet["checkout"]), task["checkout_at_claim"]["branch"]
        dirty = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if dirty.returncode != 0 or dirty.stdout.strip():
            return self.keep(task_id, "the checkout has uncommitted or untracked files")
        head = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        pushed = subprocess.run(
            ["git", "-C", str(path), "branch", "-r", "--contains", head],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()
        if not pushed:
            return self.keep(
                task_id, "branch %s has commits that are neither pushed nor merged" % branch
            )
        run(["git", "-C", str(self.repo), "worktree", "remove", str(path)], timeout=60)
        run(["git", "-C", str(self.repo), "worktree", "prune"], timeout=60)
        self.mark(task_id, gate="done", cleanup="panes stopped, worktree removed")
        event("%s panes stopped and worktree removed" % task_id)

    def keep(self, task_id, reason):
        self.mark(task_id, gate="done", cleanup="kept: " + reason)
        event("%s worktree kept: %s" % (task_id, reason))

    # ---- agents: one door for every spawn ---------------------------------------------------------------------
    # `ensure_agent` records the intent in the ledger (`agents[name]`) and the log, starts the agent under its stable
    # name, then binds the runtime's answer. An intent without a binding means the driver died in between; the next
    # `ensure_agent` for that name looks it up and re-attaches to it, and spawns only when no such agent exists.

    def agent_live(self, name):
        """True when the runtime knows a live agent of this name. A lookup that fails is an error, not `absent`: the
        caller must not spawn on a guess."""
        return runtime_call(self.lead, "status", {"name": name}).get("state") not in (
            None,
            "exited",
            "unknown",
        )

    def open_intent(self, name):
        with self.book() as data:
            known = (data.get("agents") or {}).get(name)
        return bool(known) and known.get("assignment_id") is None

    def spawn_args(self, spawn, role):
        """The spawn arguments, plus the run's role and profile when the Lead named them.

        Without a named role, implementation workers explicitly select implementer; review gates use no role.
        A named profile replaces the CLI family in the spawn: the runtime resolves it to its own CLI and env.
        A named brain backend wins over the profile for the gates that spawn on it."""
        agent = getattr(self.args, "agent", None) or ("implementer" if role == "worker" else None)
        profile = getattr(self.args, "profile", None)
        brain = getattr(self, "brain_cli", None)
        if profile and not (brain and spawn.get("cli") == brain):
            spawn = dict(spawn, cli=profile)
        return dict(spawn, agent=agent) if agent else spawn

    def gate_cli(self):
        """The backend the review gates spawn on: the brain backend when one was named, the worker backend
        otherwise."""
        return getattr(self, "brain_cli", None) or self.args.cli

    def load_routes(self):
        """The class -> route map this run uses: the typed route config when one was found, the legacy
        'Model classes' table otherwise. An invalid route config stops the run here, before any worker.

        The step routes come with it: the config's own `steps` list, then the command line's overrides,
        which replace the class or the step they name."""
        path = self.routes_file
        steps = {}
        if not path:
            event(routes.legacy_warning(self.args.models_file))
            resolved = routes.legacy(self.args.cli, self.models)
        else:
            try:
                config = routes.load(path)
                resolved = routes.resolve(config, self.args.cli, self.models)
                steps = {step.step: step for step in config.steps}
            except routes.RouteError as error:
                raise PlanError("invalid route config %s: %s" % (path, error)) from None
        try:
            resolved = routes.apply_overrides(
                resolved, routes.overrides(getattr(self.args, "route", None))
            )
        except routes.RouteError as error:
            raise PlanError("invalid route override: %s" % error) from None
        try:
            steps = routes.apply_step_overrides(
                steps, routes.step_overrides(getattr(self.args, "step", None))
            )
        except routes.RouteError as error:
            raise PlanError("invalid step override: %s" % error) from None
        return resolved, steps

    def step_route(self, step):
        """The step's own route, when the run names one."""
        return (getattr(self, "step_routes", None) or {}).get(step)

    def step_backend(self, step, default):
        """The backend one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return route.backend if route else default

    def step_model(self, step, default):
        """The model one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return route.model if route else default

    def step_effort(self, step, default):
        """The effort one step spawns on: its own route, or the run's answer."""
        route = self.step_route(step)
        return (route.effort or default) if route else default

    def gate_model(self, klass):
        """The model a review gate spawns on: the brain's medium model when a brain backend was named.

        The legacy path reads the brain backend's own column and fails when the table has none. A typed route
        config answers the brain model instead: its medium class must run on the brain backend."""
        brain = getattr(self, "brain_cli", None)
        if not brain:
            return self.routes[klass].model
        if self.routes_file:
            route = self.routes.get("medium")
            if route is None or route.backend != brain:
                raise PlanError(
                    "the route config has no medium route on the brain backend '%s'" % brain
                )
            return route.model
        return self.brain_models["medium"]

    def gate_step(self, step, klass):
        """The backend and model one gate spawns on: the step's own route, or the brain's answer.

        A step route wins over the brain: the reviewer may sit on another backend, and on another
        model, than the gaps reviewer."""
        route = self.step_route(step)
        if route:
            return route.backend, route.model
        return self.gate_cli(), self.gate_model(klass)

    def gate_label(self, step, klass):
        """The class a review gate runs on, for the event line: the step's own name when a step route names it."""
        if self.step_route(step):
            return step
        return "medium" if getattr(self, "brain_cli", None) else klass

    def ensure_agent(self, name, role, unit, spawn, fresh=False):
        """Put one agent under `name` and return (address, assignment_id).

        `spawn` is the runtime's spawn arguments. A name the ledger already knows is looked up first and, when live,
        resumed with the same task instead of spawned (that is how a restart re-attaches). `fresh` says the caller
        already stopped the old agent of this name, so a bound record needs no lookup; an open intent is always
        looked up."""
        with self.book() as data:
            agents = data.setdefault("agents", {})
            known = agents.get(name)
            recovering = bool(known) and known.get("assignment_id") is None
            if not recovering:
                agents[name] = {
                    "role": role,
                    "unit": unit,
                    "intent_at": now(),
                    "assignment_id": None,
                }
        self.log("intent", name=name, role=role, unit=unit, recovering=True if recovering else None)
        live = self.agent_live(name) if known and (recovering or not fresh) else False
        if live and not recovering and self.swap_due(name, role, unit, known.get("assignment_id")):
            self.stop_agent(name, unit, "swap")
            live, spawn = False, dict(spawn, task=spawn["task"] + self.handoff(name, unit))
        try:
            if live:
                result, how = (
                    runtime_call(self.lead, "resume", {"name": name, "task": spawn["task"]}),
                    "attach",
                )
            else:
                result, how = (
                    runtime_call(self.lead, "spawn", self.spawn_args(spawn, role)),
                    "spawn",
                )
        except subprocess.TimeoutExpired:
            raise  # it may have started: the open intent stays, and a restart looks the name up
        except (ValueError, OSError):
            if not recovering:  # the runtime refused: nothing started, so nothing is left to find
                with self.book() as data:
                    if known:
                        data["agents"][name] = known
                    else:
                        data["agents"].pop(name, None)
            raise
        assignment_id = assignment_of(result)
        address = result.get("address") or result.get("assignment_id") or name
        with self.book() as data:
            data["agents"][name].update(
                assignment_id=assignment_id, address=address, bound_at=now(), how=how
            )
        self.touched.add(name)
        self.log("agent", name=name, role=role, unit=unit, how=how, assignment_id=assignment_id)
        return address, assignment_id

    def swap_due(self, name, role, unit, assignment_id):
        """True when the agent is below the context reserve, judged from the usage the runtime last reported for its
        assignment (D5b). With no usable measurement it is resumed (D5c) and the log says `context: unknown`."""
        with self.book() as data:
            usage = (data.get("usage") or {}).get(assignment_id)
        sample = (
            usage.get("context")
            if isinstance(usage, dict) and isinstance(usage.get("context"), dict)
            else usage
        )
        verdict = (
            context_verdict(sample, self.args.context_reserve)
            if isinstance(sample, dict)
            else {"status": "unknown"}
        )
        if verdict["status"] == "unknown":
            self.log("context", name=name, role=role, unit=unit, context="unknown")
            return False
        if verdict["status"] != "checkpoint":
            return False
        self.log(
            "swap",
            name=name,
            role=role,
            unit=unit,
            remaining_tokens=verdict["remaining_tokens"],
            reserve=self.args.context_reserve,
            model=verdict["model"],
        )
        event(
            "%s is below the context reserve (%d tokens left): swapping in a fresh %s"
            % (name, verdict["remaining_tokens"], role)
        )
        return True

    def handoff(self, name, unit):
        """What a fresh agent that replaces `name` reads in place of the transcript: the unit's task, its last report and
        findings, and the commit range (the whole change, and what is new since the last review)."""
        reviewed, findings, report = self.unit_trail(unit)
        with self.book() as data:
            checkout = next(
                (
                    t["packet"]["checkout"]
                    for t in data["tasks"].values()
                    if t["packet"].get("unit") == unit
                ),
                "",
            )
        return "\n".join(
            [
                "",
                "You are a fresh agent replacing %s, which ran short of context. You have no earlier transcript; read:"
                % name,
                "- The unit's task: %s" % (self.run_dir / unit / "task.md"),
                "- Its last report: %s" % (report or "none yet"),
                "- Its last findings: %s" % (findings or "none yet"),
                "- Commit range: `git -C %s diff %s...HEAD`%s"
                % (
                    checkout,
                    self.args.base,
                    "; new since the last review: `%s..HEAD`" % reviewed if reviewed else "",
                ),
                "",
            ]
        )

    def stop_agent(self, name, unit, why):
        runtime_call(self.lead, "stop", {"name": name})
        self.log("stop", name=name, unit=unit, why=why)

    def stop_unit_agents(self, unit, why):
        """Stop the unit's implementer and reviewer panes (the ones still alive). A pane that will not stop is logged, not fatal."""
        with self.book() as data:
            names = [
                n
                for n, a in (data.get("agents") or {}).items()
                if a.get("unit") == unit and a.get("role") in ("worker", "reviewer")
            ]
        for name in names:
            if not self.pane_alive(name):
                continue
            try:
                self.stop_agent(name, unit, why)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                event("could not stop %s (%s): %s" % (name, why, error))
                self.log("error", unit=unit, name=name, gate="stop", error=str(error)[:500])

    def pane_alive(self, name):
        try:
            return runtime_call(self.lead, "status", {"name": name}).get("state") not in (
                None,
                "exited",
                "unknown",
            )
        except (ValueError, OSError, subprocess.TimeoutExpired):
            return False

    def step_fix(self, task_id):
        """Hand the failing items to the unit's worker in a new task directory; a gone pane gets a fresh worker."""
        task = self.task(task_id)
        packet, fix = task["packet"], task["fix"]
        if fix.get("draft"):
            if not self.undraft(task_id, fix["draft"]):
                return False  # a ready PR must not change under a reviewer's or a merger's eyes
            fix = dict(fix, draft=None)
            self.mark(task_id, fix=fix)
        unit, attempt = packet["unit"], fix["attempt"]
        new_id = "%sr%d" % (unit, attempt)
        new_dir = self.run_dir / new_id
        new_dir.mkdir(exist_ok=True, mode=0o700)
        findings_path = new_dir / "findings.json"
        try:
            findings = contracts.make_findings(
                fix["source"],
                attempt,
                fix["items"],
                fix.get("checks_run"),
                version=1 if contracts.legacy_items(fix["items"]) else contracts.VERSION,
            )
            if not findings_path.exists():
                findings_path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
            contracts.load(findings_path, contracts.validate_findings)
        except contracts.ContractError as error:
            return self.contract_error(task_id, "findings", error)
        task_md = new_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(self.fix_text(task, new_id, new_dir, findings), encoding="utf-8")
        successor = dict(
            packet,
            task_id=new_id,
            objective="Fix round %d for %s: %s findings" % (attempt, unit.upper(), fix["source"]),
        )
        if fix.get("up"):
            successor.update(self.class_up(packet))
        text = (
            "Read %s and follow it. Its work is already in this checkout; this is a fix round: read the findings at %s "
            "and the round's brief at %s, then fix only what the findings name."
            % (self.run_dir / unit / "task.md", findings_path, task_md)
        )
        if fix.get("rebase"):
            text = (
                "Read %s and follow it. The pull request conflicts with its base: this is a rebase round, not a fix round. Rebase the branch onto the base, resolve the "
                "conflicts, push the result to the same branch and open no new pull request. The round's brief is "
                "at %s." % (task_md, task_md)
            )
        self.hand_round(
            task,
            successor,
            task_md,
            text,
            fresh=fix.get("fresh") or fix.get("up") or fix.get("rebase"),
            answer=fix.get("answer"),
        )
        self.mark(new_id, answer_ids=list(fix.get("answer_ids") or []))
        self.mark(task_id, gate="superseded", fix_task=new_id, held=[])
        self.replay_held(task, new_id)
        event("fix round %s for %s from %s findings" % (new_id, packet["worker"], fix["source"]))

    def hand_round(self, task, successor, task_md, text, fresh=False, answer=None):
        """Close the unit's open task, claim its successor and put a worker on it: the live pane is resumed, a gone
        pane (or a `fresh` round) gets a new worker from `text`."""
        task_id, new_id = task["packet"]["task_id"], successor["task_id"]
        with self.book() as data:
            if data["tasks"][task_id]["status"] != "RELEASED":
                release(data, task_id, "superseded by " + new_id)
            claim(data, successor)
            bound = data["tasks"][new_id]["runtime_id"]
        if not bound:
            runtime_id, assignment_id = self.hand_over(
                task, successor, task_md, text, fresh, answer
            )
            with self.book() as data:
                bind(data, new_id, str(runtime_id))
                data["tasks"][new_id]["assignment_id"] = assignment_id

    def hand_over(self, task, successor, task_md, text, fresh=False, answer=None):
        name, unit = successor["worker"], successor["unit"]
        said = " The Lead's answer: %s" % answer if answer else ""
        text += said
        spawn = {
            "kind": "worker",
            "name": name,
            "cwd": successor["checkout"],
            "cli": successor["backend"],
            "model": successor["model"],
            "effort": successor["effort"],
            "task": text,
        }
        if self.open_intent(
            name
        ):  # the driver died while starting this worker: find it, do not start another
            return self.ensure_agent(name, "worker", unit, spawn, fresh=True)
        alive = self.pane_alive(name)
        if alive and not fresh:
            with self.book() as data:
                last = ((data.get("agents") or {}).get(name) or {}).get("assignment_id")
            if self.swap_due(name, "worker", unit, last):
                fresh, spawn["task"] = True, spawn["task"] + self.handoff(name, unit)
        if alive and not fresh:
            resumed = runtime_call(
                self.lead,
                "resume",
                {"name": name, "task": "Read %s and follow it.%s" % (task_md, said)},
            )
            assignment_id = assignment_of(resumed)
            with self.book() as data:
                if name in (data.get("agents") or {}):
                    data["agents"][name]["assignment_id"] = assignment_id
            self.touched.add(name)
            self.log(
                "agent",
                name=name,
                role="worker",
                unit=unit,
                how="resume",
                assignment_id=assignment_id,
            )
            return resumed.get("address") or task["runtime_id"], assignment_id
        if alive:
            try:
                self.stop_agent(name, unit, "replaced")
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                event("could not stop %s before replacing it: %s" % (name, error))
        address, assignment_id = self.ensure_agent(name, "worker", unit, spawn, fresh=True)
        event(
            "%s: spawned a fresh worker for %s (%s)"
            % (name, successor["task_id"], "replacing the pane" if alive else "pane is gone")
        )
        return address, assignment_id

    def class_up(self, packet):
        """The packet fields that move a retry one model class up; at the top class the effort goes up a notch."""
        classes, efforts = ROUTE["class"], ROUTE["effort"]
        at = classes.index(packet["class"])
        if at + 1 < len(classes):
            return {"class": classes[at + 1], "model": self.routes[classes[at + 1]].model}
        return {"effort": efforts[min(efforts.index(packet["effort"]) + 1, len(efforts) - 1)]}

    def step_decided(self, task_id):
        """Apply the Lead's decision. A `complete` that arrived while the request was open and whose report is on disk
        is acted on instead of handing the unit to a worker again: the worker had already finished."""
        task = self.task(task_id)
        try:
            decision = contracts.load(task["decision"]["path"], contracts.validate_decision)
        except contracts.ContractError as error:
            return self.request(task_id, "decision", str(error))
        action, reason, unit = decision["action"], decision["reason"], task["packet"]["unit"]
        event("%s decided %r: %s" % (task_id, action, reason))
        self.log("decision", unit=unit, task=task_id, action=action, reason=reason[:500])
        held = task.get("held") or []
        if action in ("mark blocked", "skip"):
            blocked = action == "mark blocked"
            with self.book() as data:
                if blocked:
                    data["tasks"][task_id].update(status="BLOCKED", blocker=reason)
                else:
                    release(data, task_id, "skipped by the Lead: " + reason)
                data["tasks"][task_id].update(
                    gate="blocked" if blocked else "skipped", updated_at=now(), held=[]
                )
            for message in held:
                self.log("held-dropped", unit=unit, task=task_id, held=message["kind"], why=action)
            self.stop_unit_agents(unit, "paused")
            return self.write_status(
                unit.upper(), ("BLOCKED" if blocked else "SKIPPED") + " · " + cell(reason)
            )
        self.write_status(unit.upper(), "IN PROGRESS")
        answers = task.get("answers") or {}
        if answers.get("state") == "working":
            # The Lead decided a unit whose implementer went silent on the answers: the hand-over below puts that
            # pane on a new assignment, so the old answers assignment can never report. Left `working`, pr-ready
            # would wait on it for ever; `failed` is what the old exit path recorded.
            self.mark(task_id, answers=dict(answers, state="failed"))
        finished = next((m for m in held if m["kind"] == "complete"), None)
        if finished and self.report_is_valid(task_id):
            self.log("held-acted", unit=unit, task=task_id, held="complete", acted="reported")
            return self.mark(task_id, gate="reported", held=[])
        if action == "answer":
            return self.step_answer(task_id, decision)
        flags = {
            "fresh": action == "fresh worker",
            "up": action == "retry one class up",
            "answer": decision.get("answer"),
        }
        if task.get("fix"):
            return self.mark(task_id, gate="fix-pending", fix=dict(task["fix"], **flags))
        self.mark(task_id, gate="redo-pending", redo=dict(flags, reason=reason))

    def step_answer(self, task_id, decision):
        """The Lead's `answer`: the unit's worker goes on under the same task id, so the id the brief names is the id
        its report must carry. Only `retry`, `fresh worker` and `retry one class up` start a new attempt."""
        task = self.task(task_id)
        packet, unit = task["packet"], task["packet"]["unit"]
        task_dir = self.run_dir / task_id
        task_dir.mkdir(exist_ok=True, mode=0o700)
        # named after the decision file, so a driver that died after the resume writes nothing twice
        suffix = Path(task["decision"]["path"]).name.removeprefix("decision").removesuffix(".json")
        brief = task_dir / ("answer%s.md" % suffix)
        if not brief.exists():
            brief.write_text(self.answer_text(task, decision), encoding="utf-8")
        text = (
            "Read %s and follow it. This is the task you were working on, answered by the Lead: it keeps its "
            "task id `%s`; the unit's brief is %s."
            % (brief, task_id, self.run_dir / unit / "task.md")
        )
        address, assignment_id = self.hand_over(
            task, packet, brief, text, answer=decision["answer"]
        )
        with self.book() as data:
            data["tasks"][task_id].update(
                runtime_id=str(address or task["runtime_id"]),
                assignment_id=assignment_id,
                status="ACTIVE",  # a report that failed its gate left PARTIAL; the worker is working again
                gate=None,
                held=[],
                answered_at=now(),
                updated_at=now(),
            )
        self.replay_held(task, task_id)
        event("answer for %s: %s resumed on the same task id" % (task_id, packet["worker"]))

    def answer_text(self, task, decision):
        packet, task_id, unit = task["packet"], task["packet"]["task_id"], task["packet"]["unit"]
        report = next_path(self.run_dir / task_id, "report", ".json")[0]
        items = (task.get("fix") or {}).get("items") or []
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % report,
                "",
                "# Answer to %s (task %s)" % (unit.upper(), task_id),
                "",
                "What happened: %s" % task["event"]["reason"],
                "The Lead's instruction: %s" % decision["reason"],
                "The Lead's answer: %s" % decision["answer"],
                *(
                    ["", "Findings still open:", *["- " + describe(item) for item in items], ""]
                    if items
                    else []
                ),
                "This is the same attempt, not a new one: keep using task_id `%s` in your report, and write it to "
                "the path above (files are write-once; an earlier report stays where it is)."
                % task_id,
                "Original task: %s. Same checkout and branch: %s on %s. Work only here; edit only: %s."
                % (
                    self.run_dir / unit / "task.md",
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    ", ".join(packet["owns"]),
                ),
                "Commit and push to the same branch. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    def report_landed(self, task_id):
        """The worker's report is on disk, it names this task, it says it finished and it belongs to this attempt:
        the turn ended after the report was written, so the driver reads the report instead of asking a human."""
        task = self.task(task_id)
        path = self.report_file(task_id)
        try:
            report = contracts.load(path, contracts.validate_report)
        except (contracts.ContractError, OSError, ValueError):
            return False
        if report["task_id"] != task_id or report.get("status") != "COMPLETE":
            return False
        if report.get("acceptance_met") is not True:  # the same gate the reported path applies
            return False
        return self.belongs_to_attempt(task, path)

    def belongs_to_attempt(self, task, path):
        """The report was written after the task was claimed: a report left over from an earlier attempt is not this
        worker's news."""
        claimed = max(filter(None, (task.get("started_at"), task.get("answered_at"))), default=None)
        if not claimed:
            return True
        try:
            moment = datetime.fromisoformat(claimed)
        except ValueError:
            return True
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        try:
            return path.stat().st_mtime >= moment.timestamp()
        except OSError:
            return False

    def report_is_valid(self, task_id):
        try:
            report = contracts.load(self.report_file(task_id), contracts.validate_report)
        except contracts.ContractError:
            return False
        return report["task_id"] == task_id

    def step_redo(self, task_id):
        """Hand the unit back to a worker after the Lead's `retry`: a new task directory with the event and the reason."""
        task = self.task(task_id)
        packet, redo, unit = task["packet"], task["redo"], task["packet"]["unit"]
        with self.book() as data:
            taken = set(data["tasks"])
        number = next(
            n
            for n in range(1, 1000)
            if "%sd%d" % (unit, n) not in taken or "%sd%d" % (unit, n) == task.get("redo_task")
        )
        new_id = "%sd%d" % (unit, number)
        new_dir = self.run_dir / new_id
        new_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = new_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(self.redo_text(task, new_id, new_dir, redo), encoding="utf-8")
        successor = dict(
            packet,
            task_id=new_id,
            objective="Retry %s after: %s" % (unit.upper(), task["event"]["kind"]),
        )
        if redo["up"]:
            successor.update(self.class_up(packet))
        self.mark(task_id, redo_task=new_id)
        text = (
            "Read %s and follow it. Its work is already in this checkout; this is a retry: read the retry brief at %s "
            "and continue from where the work stands." % (self.run_dir / unit / "task.md", task_md)
        )
        self.hand_round(
            task,
            successor,
            task_md,
            text,
            fresh=redo["fresh"] or redo["up"],
            answer=redo.get("answer"),
        )
        self.mark(task_id, gate="superseded", held=[])
        self.replay_held(task, new_id)
        event("retry %s for %s after %s" % (new_id, packet["worker"], task["event"]["kind"]))

    def redo_text(self, task, new_id, new_dir, redo):
        packet, unit = task["packet"], task["packet"]["unit"]
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (new_dir / "report.json"),
                "",
                "# Retry %s (task %s) after a decision" % (unit.upper(), new_id),
                "",
                "What happened: %s" % task["event"]["reason"],
                "The Lead's instruction: %s" % redo["reason"],
                *(["The Lead's answer: %s" % redo["answer"]] if redo.get("answer") else []),
                "Original task: %s. Use task_id `%s` in your report."
                % (self.run_dir / unit / "task.md", new_id),
                "Same checkout and branch: %s on %s. Work only here; edit only: %s."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    ", ".join(packet["owns"]),
                ),
                "Commit and push to the same branch. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    def fix_text(self, task, new_id, new_dir, findings):
        packet = task["packet"]
        if findings["source"] == "conflict":
            return self.rebase_text(task, new_id, new_dir, findings)
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (new_dir / "report.json"),
                "",
                "# Fix round %d for %s (task %s): %s findings"
                % (findings["attempt"], packet["unit"].upper(), new_id, findings["source"]),
                "",
                "Original task: %s. Use task_id `%s` in your report."
                % (self.run_dir / packet["unit"] / "task.md", new_id),
                "Same checkout and branch: %s on %s. Work only here; edit only: %s."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    ", ".join(packet["owns"]),
                ),
                "Findings, verbatim, also in %s. Fix only these:" % (new_dir / "findings.json"),
                "",
                *["- " + describe(item) for item in findings["items"]],
                "",
                *(
                    [
                        "The reviewer already ran these; re-run only what your fix touches, then the final gate:",
                        *[
                            "- `%s` exit %s at %s" % (c["command"], c["exit"], c["at_commit"])
                            for c in findings["checks_run"]
                        ],
                        "",
                    ]
                    if findings.get("checks_run")
                    else []
                ),
                *(
                    [
                        "Items marked answer_required need an answer: add `responses` to your report, one "
                        '`{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}` each. '
                        "Without them the driver asks you once more.",
                        "",
                    ]
                    if any(
                        i.get("answer_required") is True
                        for i in findings["items"]
                        if isinstance(i, dict)
                    )
                    else []
                ),
                *(
                    ["The Lead's answer: %s" % task["fix"]["answer"], ""]
                    if task["fix"].get("answer")
                    else []
                ),
                "Commit and push to the same branch; open no new pull request. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    def rebase_text(self, task, new_id, new_dir, findings):
        """The brief of a rebase round: the branch conflicts with its base, so the worker rebases it."""
        packet = task["packet"]
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (new_dir / "report.json"),
                "",
                "# Rebase %s (task %s): the branch conflicts with its base"
                % (packet["unit"].upper(), new_id),
                "",
                "This is a rebase round: the pull request of %s conflicts with its base. Rebase the branch onto the "
                "base, resolve the conflicts, and push the result to the same branch. What `gh` said (also in %s):"
                % (packet["unit"].upper(), new_dir / "findings.json"),
                "",
                *["- " + describe(item) for item in findings["items"]],
                "",
                "Original task: %s. Use task_id `%s` in your report."
                % (self.run_dir / packet["unit"] / "task.md", new_id),
                "Same checkout and branch: %s on %s. Work only here; edit only: %s."
                % (
                    packet["checkout"],
                    task["checkout_at_claim"]["branch"],
                    ", ".join(packet["owns"]),
                ),
                "Commit and push to the same branch; open no new pull request. Run before you report COMPLETE: %s."
                % ("; ".join(packet["checks"]) or "the repository's full CI gate"),
                "Report review_rounds 0: the driver counts failed rounds.",
                "",
            ]
        )

    # ---- archive: one more worker unit, a gaps review, then converge --final ----------------------------------
    # When every agent ticket is COMPLETE the driver claims unit `archive` (small, simple) like a ticket and starts
    # a medium gaps reviewer beside it. The archive's report is gated by `converge --final` alone (a failure is a
    # fix round), then the normal CI and merge gates. The gaps review is advisory: `<run>/gaps.md`, never a gate.

    def plan_rel(self):
        try:
            return self.plan_path.relative_to(self.repo).as_posix()
        except ValueError:
            return "docs/plans/" + self.plan_path.name

    def start_archive(self, plan):
        agents = [t for t in plan["tickets"] if is_agent(t)]
        if not agents or not all(is_done(t) for t in agents) or ARCHIVE in self.failed:
            return 0
        if not self.model and not self.outcome_logged:
            # A Markdown plan's outcome is never run: it completes unverified and cannot be accepted.
            self.outcome_logged = True
            self.log("plan-outcome", outcome=verify.legacy_outcome(True))
        with self.book() as data:
            held = data["tasks"].get(ARCHIVE)
        if held and held["runtime_id"]:
            return 0
        try:
            self.dispatch_archive(plan, agents)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.failed.add(ARCHIVE)
            self.errors.append("archive: %s" % error)
            event("archive not dispatched: %s" % error)
            return 0
        self.start_gaps(plan)
        return 1

    def dispatch_archive(self, plan, agents):
        name = self.slug[: 32 - len("-archive")].rstrip("-_") + "-archive"
        klass, effort = MATRIX[("small", "simple")]
        backend = self.step_backend("archive", self.args.cli)
        model = self.step_model("archive", self.routes[klass].model)
        effort = self.step_effort("archive", effort)
        checkout = self.worktree(name, "feat/" + name)
        owns = ["docs/specs/", "docs/plans/"]
        for ticket in agents:
            owns += [
                p.strip() for p in ticket["Paths"].split(",") if p.strip() and p.strip() not in owns
            ]
        packet = {
            "task_id": ARCHIVE,
            "worker": name,
            "plan": str(self.plan_path),
            "objective": "Archive the plan into the living spec",
            "checkout": str(checkout),
            "backend": backend,
            "owns": owns,
            "acceptance": ARCHIVE_ACCEPTANCE,
            "checks": list(self.args.gate),
            "depends_on": [],
            "unit": ARCHIVE,
            "size": "small",
            "difficulty": "simple",
            "class": klass,
            "effort": effort,
            "model": model,
        }
        with self.book() as data:
            claim(data, packet)
        task_dir = self.run_dir / ARCHIVE
        task_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = task_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(self.archive_text(plan, packet, task_dir), encoding="utf-8")
        address, assignment_id = self.ensure_agent(
            name,
            "worker",
            ARCHIVE,
            {
                "kind": "worker",
                "name": name,
                "task": "Read %s and follow it." % task_md,
                "cwd": str(checkout),
                "cli": backend,
                "model": model,
                "effort": effort,
            },
        )
        with self.book() as data:
            bind(data, ARCHIVE, str(address))
            data["tasks"][ARCHIVE]["assignment_id"] = assignment_id
        self.spawned.append(name)
        event("spawned %s (%s, %s, %s)" % (name, klass, model, effort))

    def archive_text(self, plan, packet, task_dir):
        text = self.plan_path.read_text(encoding="utf-8")
        found = re.search(r"Living spec:\s*`([^`]+)`", text)
        spec = found.group(1) if found else "docs/specs/%s.md" % self.slug
        area = Path(spec).stem
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (task_dir / "report.json"),
                "",
                "# Archive %s into its living spec" % self.slug,
                "",
                "Plan: %s (repo path `%s`)." % (self.plan_path, self.plan_rel()),
                "Checkout: %s on branch feat/%s (new, from %s). Work only here. If the plan file is not tracked there, "
                "copy it from the path above first."
                % (packet["checkout"], packet["worker"], self.args.base),
                "Owns (edit nothing else): %s." % ", ".join(packet["owns"]),
                "",
                "Steps:",
                "- Create the living spec `%s` if it is missing. Its first lines are `# %s` and `Paths: src/area/, tests/area/**`: "
                "the comma-separated code paths the area covers. Converge fails a spec without a parseable `Paths:` line."
                % (spec, area.title()),
                "- Append each scenario below as a requirement with the next free number and its provenance: "
                "`- **R4** WHEN ..., THEN ... (from %s/S3)`." % self.slug,
                "- Rename each test's `S<n>` to `%s/R<m>`: in the test name, or in a Python test's docstring first line."
                % area,
                "- Set the plan's `Status: ARCHIVED %s`." % now()[:10],
                "- Run before you report COMPLETE: %s. Then open a pull request against %s."
                % (
                    "; ".join(packet["checks"]) or "the repository's full CI gate",
                    self.args.base.partition("/")[2],
                ),
                "The driver then runs `converge.py --final` on this branch; a FAIL comes back as a fix round.",
                "",
                "Scenarios, verbatim:",
                "",
                *plan["scenarios"].values(),
                "",
            ]
        )

    def start_gaps(self, _plan):
        """Spawn the advisory gaps reviewer once. Its failure never blocks anything."""
        with self.book() as data:
            if data.get("gaps"):
                return
            merges = [
                (tid, t.get("pr"), t.get("merge_commit"))
                for tid, t in data["tasks"].items()
                if t.get("merge_commit")
            ]
        name = self.slug[: 32 - len("-gaps")].rstrip("-_") + "-gaps"
        path, task_md = self.run_dir / "gaps.md", self.run_dir / "gaps-task.md"
        task_md.write_text(
            "\n".join(
                [
                    "# Gaps review of %s: advisory, paths only" % self.slug,
                    "",
                    "Read these yourself; nothing is pasted here.",
                    "- Plan: %s (its Decisions, Scenarios, Risks and Out of scope)"
                    % self.plan_path,
                    "- Repository: %s; the built work is merged on %s"
                    % (self.repo, self.args.base),
                    *[
                        "- Merged %s: %s %s" % (tid, pr or "no PR url", (commit or "")[:12])
                        for tid, pr, commit in merges
                    ],
                    "",
                    "Look for what the plan promised and the final code lacks: a scenario with no real test, a decision not "
                    "reflected, a risk left open, behaviour nobody asked for. Findings become plan edits, so be specific.",
                    "Advisory only: it blocks nothing. Touch nothing in the repository.",
                    "",
                    "Write %s for the user: one line per gap (missing, partial, contradicts, unrequested) with the file or "
                    "scenario it concerns. If there are none, say so." % path,
                    "Then call the agent-runtime `report` tool with kind=complete and one line: `<n> gaps - %s`."
                    % path,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        try:
            backend, model = self.gate_step("gaps", "medium")
            effort = self.step_effort("gaps", "medium")
            _, assignment_id = self.ensure_agent(
                name,
                "gaps",
                "gaps",
                {
                    "kind": "worker",
                    "name": name,
                    "task": "Read %s and follow it." % task_md,
                    "cwd": str(self.repo),
                    "cli": backend,
                    "model": model,
                    "effort": effort,
                },
            )
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            event("gaps reviewer not started: %s" % error)
            gaps = {"name": name, "path": str(path), "ended": True, "error": str(error)}
        else:
            gaps = {"name": name, "path": str(path), "assignment_id": assignment_id, "ended": False}
            event(
                "spawned %s (%s, %s, %s)" % (name, self.gate_label("gaps", "medium"), model, effort)
            )
        with self.book() as data:
            data["gaps"] = gaps

    # ---- metrics: what a run cost, from the ledger and the runtime's own usage numbers ----------------------

    def record_usage(self, assignments):
        """Keep the `usage` the runtime attaches to an assignment; nothing is ever estimated."""
        seen = {
            a["assignment_id"]: a["usage"]
            for a in assignments
            if a.get("assignment_id") and a.get("usage") is not None
        }
        if seen:
            with self.book() as data:
                changed = {i: u for i, u in seen.items() if data.get("usage", {}).get(i) != u}
                data.setdefault("usage", {}).update(seen)
                owners = {}
                for task_id, task in data["tasks"].items():
                    for agent in (task, task.get("review") or {}, task.get("decision") or {}):
                        owners[agent.get("assignment_id")] = task["packet"].get("unit") or task_id
            for assignment_id, usage in changed.items():
                self.log(
                    "usage",
                    unit=owners.get(assignment_id),
                    assignment_id=assignment_id,
                    usage=usage,
                )

    def report_metrics(self):
        metrics = events.rollup(events.read(self.run_dir / "events.jsonl"), self.slug)
        (self.run_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
        )
        print(metrics_table(metrics), file=sys.stderr, flush=True)
        return metrics

    def report_evidence(self):
        """The run's attempt evidence: one schema-versioned record per attempt, written beside metrics.json (which
        keeps its own shape). A measurement that was not taken is null, never 0."""
        with ledger(self.ledger_path) as data:
            records = evidence.attempts(data, events.read(self.run_dir / "events.jsonl"), self.slug)
        return evidence.write(self.run_dir / "attempts.jsonl", records)

    def dispatch_ready(self, plan):
        with self.book() as data:
            tasks = data["tasks"]
            # a unit that waits for its merge holds no slot: only work that still needs an agent counts
            unfinished = sum(
                1
                for t in tasks.values()
                if t["status"] not in ("COMPLETE", "RELEASED")
                and t.get("gate") not in PARKED
                and t.get("gate") not in ("awaiting-merge", "cleanup", "done")
            )
        by_id = {t["#"]: t for t in plan["tickets"]}
        count = 0
        for ticket in plan["tickets"]:
            task_id = ticket["#"].lower()
            if not is_agent(ticket):
                continue
            if task_id in self.failed and ticket["Status"].strip().upper() == "TO-DO":
                self.failed.discard(task_id)  # the Lead's answer to the wake: try it again
            if task_id in self.failed:
                continue
            if not ready(ticket, by_id, self.restartable(ticket, tasks)):
                continue
            attempts = {k: t for k, t in tasks.items() if t["packet"].get("unit") == task_id}
            open_ = {
                k: t for k, t in attempts.items() if t["status"] not in ("COMPLETE", "RELEASED")
            }
            held, new_id = None, None
            if any(t["status"] == "COMPLETE" for t in attempts.values()) or any(
                t["runtime_id"] for t in open_.values()
            ):
                continue
            if open_:
                # a dispatch that failed after its claim left a reserved attempt: it is dispatched again under its
                # own id (claim is idempotent for it), not as a fresh one
                new_id, held = next(iter(open_.items()))
            elif attempts:
                # every attempt is closed, and a closed id is never dispatched again
                new_id = next(
                    "%sr%d" % (task_id, n)
                    for n in range(1, 1000)
                    if "%sr%d" % (task_id, n) not in tasks
                )
            if held and held["runtime_id"]:
                continue
            if not held and unfinished >= self.args.max_workers:
                continue
            try:
                self.dispatch(plan, ticket, new_id)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.not_dispatched(ticket, error)
                unfinished += 0 if held else 1
                continue
            unfinished += 0 if held else 1
            # a failure the Lead has answered no longer fails the run
            self.errors[:] = [e for e in self.errors if not e.startswith(ticket["#"] + ": ")]
            count += 1
        return count

    def restartable(self, ticket, tasks):
        """True when a ticket whose cell reads `IN PROGRESS` holds no live work: the ledger has an attempt of its
        unit, every one is RELEASED, and the runtime answers definitively that its worker is not live. A cell with
        no attempt in the ledger belongs to some other run and stays a lock; a failed lookup is not an answer: it
        strands the unit (a wake and a non-zero exit) and nothing is spawned."""
        if not ticket["Status"].strip().upper().startswith("IN PROGRESS"):
            return False
        unit = ticket["#"].lower()
        attempts = {k: t for k, t in tasks.items() if t["packet"].get("unit") == unit}
        if not attempts or any(t["status"] != "RELEASED" for t in attempts.values()):
            return False
        name = worker_name(self.slug, ticket["#"][1:])
        try:
            return self.agent_gone(name)[0]
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            self.strand(list(attempts)[-1], name, str(error))
            return False

    def not_dispatched(self, ticket, error, what="not dispatched"):
        """A dispatch that cannot happen asks the Lead, once per ticket and reason; the cell keeps the reason and
        the ticket stays out of this run's rounds until the Lead sets its Status to TO-DO (a restarted drive reads the cell
        as TO-DO and tries it once more)."""
        task_id = ticket["#"].lower()
        self.failed.add(task_id)
        self.errors.append("%s: %s" % (ticket["#"], error))
        event("%s %s: %s" % (ticket["#"], what, error))
        self.write_status(ticket["#"], "TO-DO · %s: %s" % (what, cell(error, 120)))
        self.wake(
            "not-dispatched",
            task_id,
            "%s %s: %s -> fix that, then set the ticket's Status to TO-DO and the next round tries it again"
            % (ticket["#"], what, cell(error, 300)),
            key="%s:%s" % (task_id, error),
        )

    # ---- reconciliation: an open task whose agent the runtime no longer lists -------------------------------------
    # A drive that restarts (or finds its worker gone) must not trust the ledger's `runtime_id`, and must not finish
    # over a unit nobody is working on. `status` answers `unknown` for a read that failed as well as for a terminal
    # the runtime has none of, so only the shape of the answer says "gone" (`exited`, or `unknown` with `not_found`)
    # and anything else is "cannot tell": nothing is released or spawned, and the Lead is told.

    def in_flight(self):
        """(task, role, agent name, assignment id) for every open, unparked task that has an agent working for it."""
        found = []
        with self.book() as data:
            for task_id, task in data["tasks"].items():
                gate, packet = task.get("gate"), task["packet"]
                if task["status"] in ("COMPLETE", "RELEASED") or gate in PARKED:
                    continue
                answers, review = task.get("answers") or {}, task.get("review") or {}
                if answers.get("state") == "working" and answers.get("assignment_id"):
                    found.append((task_id, "answerer", packet["worker"], answers["assignment_id"]))
                if gate == "reviewing" and review.get("assignment_id"):
                    found.append((task_id, "reviewer", review["name"], review["assignment_id"]))
                if not gate and task["status"] == "ACTIVE" and task.get("assignment_id"):
                    found.append((task_id, "worker", packet["worker"], task["assignment_id"]))
        return found

    def agent_gone(self, name):
        """(True, the answer) when the runtime says the agent is gone, (False, its state) when it is live. A `status`
        that fails, or answers `unknown` for any other reason, raises `Unreadable`: that is not an answer."""
        answer = runtime_call(self.lead, "status", {"name": name})
        state = answer.get("state")
        if state in ("exited", "finished"):
            return True, state
        if state != "unknown":
            return False, state
        error = (answer.get("native") or {}).get("error")
        if terminal_gone(error, name):
            return True, "unknown: not_found"
        raise Unreadable(
            "status answered unknown: %s" % (cell(error, 200) if error else "no reason given")
        )

    def bound_here(self, name):
        """True when this drive spawned or attached the agent: the runtime has had no `wait` round to list it yet."""
        return name in self.touched

    def strand(self, task_id, name, error):
        """The run cannot tell what became of this task's agent: the task stays open and the Lead hears of it."""
        unit = self.unit_of(task_id)
        self.stranded.add(task_id)
        text = (
            "stranded %s: the agent %s of task %s cannot be settled: %s -> nothing was released or spawned; "
            "look at the terminal, then restart the drive" % (unit, name, task_id, cell(error, 300))
        )
        self.wake("stranded", unit, text, key="%s:%s" % (task_id, error))  # once per task and error
        reason = "%s: %s" % (task_id, error)
        if (
            reason not in self.errors
        ):  # the exit code follows what is stranded now, not what the Lead was told first
            self.errors.append(reason)
        self.log("stranded", unit=unit, task=task_id, name=name, error=str(error)[:500])
        event("%s stranded: %s" % (task_id, error))

    def reconcile(self, finishing=False):
        """Settle every open task whose assignment `wait` does not list. Returns how many it acted on; a task it
        cannot settle is left in `self.stranded`. At startup a live terminal is left alone (its message may still
        come); when the run would finish, a terminal still at work that the runtime does not list, and that a
        dead drive left behind, is a unit nobody waits on."""
        self.stranded = set()
        acted = 0
        for task_id, role, name, assignment_id in self.in_flight():
            if assignment_id in self.unfinished:
                continue
            try:
                lost, said = self.agent_gone(name)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.strand(task_id, name, str(error))
                continue
            if not lost:
                if finishing and not self.bound_here(name):
                    # A live terminal that a dead drive left and that this run's `wait` does not list (its
                    # assignment belongs to the dead drive's team). A turn that ended with its report on disk goes to
                    # the gates; anything else nothing will ever settle, so the Lead is told.
                    ended = said in ("idle", "done")
                    if ended and role == "worker" and self.report_landed(task_id):
                        self.log(
                            "report-landed",
                            unit=self.unit_of(task_id),
                            task=task_id,
                            assignment_id=assignment_id,
                        )
                        self.mark(task_id, gate="reported")
                        acted += 1
                    else:
                        self.strand(
                            task_id,
                            name,
                            "turn ended (%s) with no report for this attempt" % said
                            if ended
                            else "live but not an assignment of this run: %s" % said,
                        )
                continue
            unit = self.unit_of(task_id)
            self.log(
                "orphaned",
                unit=unit,
                task=task_id,
                role=role,
                name=name,
                assignment_id=assignment_id,
                answer=said,
            )
            event("%s orphaned: %s (%s) answers %s" % (task_id, name, role, said))
            if role != "worker":  # the path an agent the runtime reported as exited takes
                self.on_agent_attention(
                    assignment_id, "exited", "the runtime answered " + said, quiet=True
                )
                acted += 1
            elif self.report_landed(task_id):
                self.log("report-landed", unit=unit, task=task_id, assignment_id=assignment_id)
                self.mark(task_id, gate="reported")
                acted += 1
            else:
                acted += self.redispatch(task_id, said)
        return acted

    def redispatch(self, task_id, said):
        """Release the orphaned attempt and put the unit's worker on a new one, in the same checkout and branch."""
        task = self.task(task_id)
        unit, name = task["packet"]["unit"], task["packet"]["worker"]
        plan = self.last_plan
        tickets = (
            plan["tickets"] if plan else []
        )  # a plan is read before the loop starts; the type allows none
        ticket = next((t for t in tickets if t["#"].lower() == unit), None)
        with self.book() as data:
            taken = set(data["tasks"])
            number = next(n for n in range(1, 1000) if "%so%d" % (unit, n) not in taken)
            release(data, task_id, "orphaned: " + said)
        new_id = "%so%d" % (unit, number)
        note = "\n".join(
            [
                "This is a new attempt (%s) of %s: the agent of the earlier attempt (%s) was lost "
                "(the runtime answered %s)." % (new_id, unit.upper(), task_id, said),
                "Its work is in this checkout and on this branch: read `git status` and `git log`, and continue "
                "from where it stands. Report with task_id `%s`." % new_id,
                "",
            ]
        )
        try:
            if ticket is None:
                raise ValueError("the plan no longer has ticket %s" % unit.upper())
            self.dispatch(plan, ticket, new_id, note=note)
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            if ticket is None:
                self.strand(task_id, name, "re-dispatch as %s failed: %s" % (new_id, error))
            else:
                self.not_dispatched(ticket, error, "not re-dispatched as " + new_id)
            return 0
        event("%s: dispatched again as %s after %s" % (unit.upper(), new_id, said))
        return 1

    def attend_people(self, plan):
        """A person-owned ticket that becomes ready reads `needs you: <what it needs>` and waits for the user."""
        by_id = {t["#"]: t for t in plan["tickets"]}
        count = 0
        for ticket in plan["tickets"]:
            if is_agent(ticket) or not ready(ticket, by_id):
                continue
            needs = cell(ticket["What it delivers"] or "your action")
            self.note_attention(ticket["#"], "person", needs)
            self.write_status(ticket["#"], "needs you: " + needs)
            event("%s needs you: %s" % (ticket["#"], needs))
            count += 1
        return count

    def reopen_parked(self, plan):
        """The user answers a pause through the plan (S4). A parked ticket whose Status is now TO-DO gets a fresh
        attempt of the same unit; one now COMPLETE (done by hand) is released so its dependents and the archive
        proceed. Any other Status leaves it parked. The held worker is stopped first, when its pane is live."""
        by_id = {t["#"].lower(): t for t in plan["tickets"]}
        with self.book() as data:
            parked = [
                (task_id, copy.deepcopy(task))
                for task_id, task in data["tasks"].items()
                if task.get("gate") in PARKED and task["status"] not in ("COMPLETE", "RELEASED")
            ]
        count = 0
        for task_id, task in parked:
            ticket = by_id.get(task["packet"]["unit"])
            if not ticket or not is_agent(ticket):
                continue
            status = ticket["Status"].strip().upper()
            if not status.startswith(("TO-DO", "COMPLETE")):
                continue
            again = status.startswith("TO-DO")
            if self.pane_alive(task["packet"]["worker"]):
                self.stop_agent(task["packet"]["worker"], task["packet"]["unit"], "reopened")
            with self.book() as data:
                release(
                    data,
                    task_id,
                    "the user set %s to %s"
                    % (ticket["#"], "TO-DO: retry" if again else "COMPLETE: done by hand"),
                )
            event(
                "%s %s"
                % (
                    ticket["#"],
                    "reopened by the user: fresh attempt" if again else "completed by hand",
                )
            )
            if not again:
                continue
            with self.book() as data:
                taken = set(data["tasks"])
            attempt = next(
                n for n in range(1, 1000) if "%sp%d" % (task["packet"]["unit"], n) not in taken
            )
            try:
                self.dispatch(plan, ticket, "%sp%d" % (task["packet"]["unit"], attempt))
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                self.not_dispatched(ticket, error, "not re-dispatched")
                continue
            count += 1
        return count

    def dispatch(self, plan, ticket, task_id=None, note=""):
        number = ticket["#"][1:]
        name = worker_name(plan["slug"], number)
        task_id = task_id or "t" + number
        with self.book() as data:
            closed = (data["tasks"].get(task_id) or {}).get("status")
        if closed in ("COMPLETE", "RELEASED"):
            # Before any spawn: the ledger would refuse the bind only after a worker had been started for it.
            raise ValueError(
                "task %s is %s in the ledger, and a closed task id is never dispatched again: "
                "dispatch the unit under a new attempt id" % (task_id, closed)
            )
        size, difficulty = ticket["Size"].strip(), ticket["Difficulty"].strip()
        klass, matrix_effort = MATRIX[(size, difficulty)]
        route = self.routes[klass]
        backend = self.step_backend("implementer", route.backend)
        model = self.step_model("implementer", route.model)
        effort = self.step_effort("implementer", route.effort or matrix_effort)
        checkout = self.worktree(name, "feat/" + name)
        scenarios = [plan["scenarios"][sid] for sid in satisfied(ticket, plan["scenarios"])[0]]
        packet = {
            "task_id": task_id,
            "worker": name,
            "plan": str(self.plan_path),
            "objective": ticket["What it delivers"],
            "checkout": str(checkout),
            "backend": backend,
            "owns": [p.strip() for p in ticket["Paths"].split(",") if p.strip()],
            "acceptance": [re.sub(r"^- \*\*(S\d+[a-z]?)\*\*", r"\1:", s) for s in scenarios],
            "checks": list(self.args.gate),
            "depends_on": [],
            "unit": "t" + number,
            "size": size,
            "difficulty": difficulty,
            "class": klass,
            "effort": effort,
            "model": model,
        }
        with self.book() as data:
            claim(data, packet)
        step = self.step_route("implementer")
        self.log(
            "route",
            unit=packet["unit"],
            task=task_id,
            **{
                "class": klass,
                "backend": backend,
                "model": model,
                "effort": effort,
                "effort_source": "step"
                if (step and step.effort)
                else ("config" if route.effort else "matrix"),
                "source": self.route_source or "legacy",
            },
        )
        task_dir = self.run_dir / task_id
        task_dir.mkdir(exist_ok=True, mode=0o700)
        task_md = task_dir / "task.md"
        if not task_md.exists():
            task_md.write_text(
                self.task_text(plan, ticket, packet, scenarios, task_dir) + note, encoding="utf-8"
            )
        runtime_id, assignment_id = self.ensure_agent(
            name,
            "worker",
            packet["unit"],
            {
                "kind": "worker",
                "name": name,
                "task": "Read %s and follow it." % task_md,
                "cwd": str(checkout),
                "cli": backend,
                "model": model,
                "effort": effort,
            },
        )
        with self.book() as data:
            bind(data, task_id, str(runtime_id))
            data["tasks"][task_id]["assignment_id"] = assignment_id
        if is_yaml(self.plan_path):
            self.write_status(ticket["#"], "IN PROGRESS")
        else:
            set_status(self.plan_path, ticket["#"], "IN PROGRESS")
        self.spawned.append(name)
        event("spawned %s (%s, %s, %s)" % (name, klass, packet["model"], effort))

    def worktree(self, name, branch):
        path = self.repo.parent / (self.repo.name + ".worktrees") / name
        remote, _, ref = self.args.base.partition("/")
        run(["git", "-C", str(self.repo), "fetch", "-q", remote, ref], timeout=120)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            exists = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(self.repo),
                        "rev-parse",
                        "--verify",
                        "-q",
                        "refs/heads/" + branch,
                    ],
                    capture_output=True,
                ).returncode
                == 0
            )
            run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "worktree",
                    "add",
                    *([] if exists else ["-b", branch]),
                    str(path),
                    *([branch] if exists else [self.args.base]),
                ],
                timeout=TREE_TIMEOUT,
            )
        return path

    def task_text(self, _plan, ticket, packet, scenarios, task_dir):
        gate = self.args.gate or ["the repository's full CI gate (see its AGENTS.md / CLAUDE.md)"]
        return "\n".join(
            [
                "Contract: %s" % (self.run_dir / "contract.md"),
                "Report: %s" % (task_dir / "report.json"),
                "",
                "# %s: %s" % (ticket["#"], ticket["What it delivers"]),
                "",
                "Report with task_id `%s`: the driver refuses a report whose task_id is not this one."
                % packet["task_id"],
                "",
                "Plan: %s (read its Decisions and Seams)." % self.plan_path,
                "Checkout: %s on branch feat/%s (new, from %s). Work only here."
                % (packet["checkout"], packet["worker"], self.args.base),
                "Owns (edit nothing else): %s." % ", ".join(packet["owns"]),
                "Shared databases are read-only; schema changes go through migrations in your own branch.",
                "",
                "Scenarios, verbatim. Work red-first: one test per scenario, named with its S-ID, seen failing before the code exists.",
                "",
                *scenarios,
                "",
                "Before you report COMPLETE, run: " + "; ".join(gate) + ".",
                "Then open a draft pull request against %s." % self.args.base.partition("/")[2],
                "",
            ]
        )


def live_terminals():
    """Names of the live Herdr terminals. `hc ls --json` is {"terminals": [{"name"?, "state", …}], …}."""
    result = subprocess.run(["hc", "ls", "--json"], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(
            "hc ls failed (exit %d): %s"
            % (result.returncode, (result.stderr or result.stdout).strip())
        )
    try:
        rows = json.loads(result.stdout or "{}").get("terminals", [])
    except ValueError as error:
        raise ValueError("hc ls printed no JSON: %s" % error) from None
    return {
        row["name"]
        for row in rows
        if isinstance(row, dict) and row.get("name") and row.get("state") == "live"
    }


def pane_name(slug):
    """hc names allow a-z 0-9 - _ (1-32), so the pane is `orchestrate-<slug>`, not `orchestrate:<slug>`."""
    return ("orchestrate-" + slug)[:32].rstrip("-")


def forwarded(argv, option):
    """The value of `option V` or `option=V` among arguments `launch` forwards to `run`, else None."""
    for index, item in enumerate(argv):
        if item == option and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(option + "="):
            return item[len(option) + 1 :]
    return None


def is_yaml(plan_path):
    return Path(plan_path).name.endswith(".plan.yaml")


def repo_root(plan_path, repo=None):
    """The repository a plan belongs to: the one named, else the one holding the plan file."""
    if repo:
        return Path(repo).resolve()
    return Path(
        run(["git", "-C", str(Path(plan_path).resolve().parent), "rev-parse", "--show-toplevel"])
    ).resolve()


def repo_key(repo):
    """The directory a repository's YAML plan state lives in under the state dir: its name for the eye, a hash of its
    git common dir for uniqueness. A worktree of the repository has the repository's key, and two clones that share
    a name do not share one."""
    common = Path(yaml_plan.identity(Path(repo).resolve()))
    home = common.parent if common.name == ".git" else common
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", home.name.removesuffix(".git")).strip("-.") or "repo"
    return "%s-%s" % (name[:40], hashlib.sha256(str(common).encode("utf-8")).hexdigest()[:12])


def state_layout(state_dir, plan_path, repo, slug):
    """(ledger path, run directory) of a plan. A YAML plan's are under `<state>/<repo-key>/`, so two repositories
    that run the same slug have two ledgers; a Markdown plan keeps `<state>/<slug>.json` until the format is gone."""
    state = Path(state_dir).expanduser()
    if not is_yaml(plan_path):
        return state / (slug + ".json"), state / slug
    keyed = state / repo_key(repo)
    return keyed / (slug + ".json"), keyed / slug


def owns_ledger(repo, data):
    """Whether a ledger written under the old `<state>/<slug>.json` key belongs to `repo`: one of its tasks names the
    plan file or the checkout inside the repository or its `.worktrees` directory. A ledger that names neither is
    nobody's."""
    repo = Path(os.path.realpath(repo))
    roots = (repo, repo.parent / (repo.name + ".worktrees"))
    for task in (data.get("tasks") or {}).values():
        packet = task.get("packet") if isinstance(task, dict) else None
        for field in ("plan", "checkout"):
            value = (packet or {}).get(field)
            if isinstance(value, str) and any(
                Path(os.path.realpath(value)).is_relative_to(root) for root in roots
            ):
                return True
    return False


def old_ledger_of(state_dir, repo, slug):
    """The ledger of the old `<state>/<slug>.json` key when `repo` owns it, else None."""
    path = Path(state_dir).expanduser() / (slug + ".json")
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return path if isinstance(data, dict) and owns_ledger(repo, data) else None


def import_old_ledger(state_dir, repo, slug, ledger_path, run_dir):
    """Move a ledger and a run directory written under the old `<state>/<slug>` key into the repository that owns
    them. Only a `run` does it, under the plan's run lock: a Lead command that moved them would pull the files out
    from under a live driver. Once the repository's own ledger exists nothing is imported over it, and a repository
    that does not own the old ledger leaves it alone for the one that does."""
    state = Path(state_dir).expanduser()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(state / ".import.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ledger_path.exists():
            return
        old = old_ledger_of(state, repo, slug)
        if old is None:
            return
        # The old ledger's own lock is what a driver holds while it updates that ledger.
        old_fd = os.open(
            old.with_name(old.name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(old_fd, "w") as old_lock:
            try:
                fcntl.flock(old_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PlanError(
                    "plan '%s': a live driver is updating its old ledger %s; the ledger moves to %s "
                    "when that driver has stopped — follow it with `await`, or stop it and run again"
                    % (slug, old, ledger_path)
                ) from None
            ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            old_run = state / slug
            if old_run.is_dir() and not run_dir.exists():
                os.replace(old_run, run_dir)
            os.replace(old, ledger_path)


def locate_state(state_dir, plan_path, repo, slug, importing=False):
    """(ledger path, run directory) of a plan. A YAML plan's old ledger is imported only when `importing`, which is
    `run` under the plan's run lock; every other caller reads the old key in place while the repository's own ledger
    is absent and the repository owns the old one."""
    ledger_path, run_dir = state_layout(state_dir, plan_path, repo, slug)
    if is_yaml(plan_path):
        if importing:
            import_old_ledger(state_dir, repo, slug, ledger_path, run_dir)
        elif not ledger_path.exists() and old_ledger_of(state_dir, repo, slug):
            return Path(state_dir).expanduser() / (slug + ".json"), Path(
                state_dir
            ).expanduser() / slug
    return ledger_path, run_dir


def plan_home(args):
    """(slug, ledger path, run directory) of the plan a Lead command names."""
    plan_path = Path(args.plan).resolve(strict=True)
    slug = plan_slug(plan_path)
    if not slug:
        raise Refused("plan has no Slug")
    repo = repo_root(plan_path, getattr(args, "repo", None)) if is_yaml(plan_path) else None
    return (slug, *locate_state(args.state_dir, plan_path, repo, slug))


def enter_plan_state(data, state, at=None):
    """The plan's state in the ledger, with its history: one entry per change, oldest first."""
    plan = data.setdefault("plan", {"state": None, "history": []})
    if plan["state"] != state:
        plan["state"] = state
        plan["history"].append({"state": state, "at": at or now()})


def split_status(text):
    """(state, reason) of a ticket Status: `IN PROGRESS · decision pending: why` is IN PROGRESS and its reason."""
    text = " ".join((text or "TO-DO").split())
    if text.lower().startswith("needs you:"):
        return "needs you", text.split(":", 1)[1].strip() or None
    head, _, reason = text.partition(" · ")
    return re.sub(r"\s+\d{4}-\d{2}-\d{2}$", "", head), reason.strip() or None


def plan_status(args):
    """`status <plan> --json`: the plan's state and history, and each ticket's state, reason and pull request, read
    from the ledger. It writes nothing, takes no lock and needs no run: before one, the plan is APPROVED (or DRAFT)
    and every ticket TO-DO."""
    plan_path = Path(args.plan).resolve(strict=True)
    if is_yaml(plan_path):
        repo = repo_root(plan_path, args.repo)
        model = yaml_plan.load(plan_path, repo=repo, state_dir=args.state_dir, verify=False)
        slug, ids, statuses = model.slug, [t.id for t in model.tickets], {}
        state = "APPROVED" if model.approval else "DRAFT"
        history = [{"state": state, "at": model.approval.at}] if model.approval else []
    else:
        repo = None
        plan = parse_plan(plan_path.read_text(encoding="utf-8"))
        slug, ids = plan["slug"], [t["#"] for t in plan["tickets"]]
        statuses = {t["#"]: t["Status"] for t in plan["tickets"]}
        state, history = plan.get("status") or "DRAFT", []
    ledger_path, run_dir = state_layout(args.state_dir, plan_path, repo, slug)
    if is_yaml(plan_path) and not ledger_path.exists():
        ledger_path = old_ledger_of(args.state_dir, repo, slug) or ledger_path
    data = {}
    if ledger_path.is_file():
        data = read_ledger_file(ledger_path)
        if is_yaml(plan_path):
            statuses = data.get("ticket_status") or {}
            moves = data.get("plan") or {}
            state, history = moves.get("state") or state, moves.get("history") or history
    tickets = []
    for ticket_id in ids:
        status, reason = split_status(statuses.get(ticket_id))
        prs = [
            task.get("pr")
            for task in (data.get("tasks") or {}).values()
            if (task.get("packet") or {}).get("unit") == ticket_id.lower() and task.get("pr")
        ]
        tickets.append(
            {"id": ticket_id, "state": status, "reason": reason, "pr": prs[-1] if prs else None}
        )
    return {
        "slug": slug,
        "plan": str(plan_path),
        "repo": str(repo) if repo else None,
        "run_dir": str(run_dir),
        "ledger": str(ledger_path),
        "state": state,
        "history": history,
        "tickets": tickets,
    }


def read_ledger_file(path):
    """A ledger read without its lock: every writer replaces the file whole, so a reader sees one version."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("cannot read the ledger %s: %s" % (path, error)) from None
    require(isinstance(data, dict), "the ledger %s is not an object" % path)
    return data


def render_status(status):
    lines = ["%s: %s" % (status["slug"], status["state"]), "run: " + status["run_dir"]]
    for ticket in status["tickets"]:
        lines.append(
            "%s  %s%s%s"
            % (
                ticket["id"],
                ticket["state"],
                " · " + ticket["reason"] if ticket["reason"] else "",
                "  " + ticket["pr"] if ticket["pr"] else "",
            )
        )
    return "\n".join(lines)


def plan_slug(plan_path):
    """The slug a Markdown or YAML plan names, read without validating it: a plan mid-edit still names its slug."""
    path = Path(plan_path).resolve(strict=True)
    if path.name.endswith(".plan.yaml"):
        data = yaml_plan.read_yaml(path)[0]
        slug = data.get("slug") if isinstance(data, dict) else None
        return slug if isinstance(slug, str) else None
    return parse_plan(path.read_text(encoding="utf-8"))["slug"]


def launch(args, run_args):
    """Check the plan, then start `run` in a new Herdr terminal. A live driver of the same name is left alone."""
    plan_path = Path(args.plan).resolve(strict=True)
    repo = (
        Path(args.repo).resolve()
        if args.repo
        else Path(
            run(["git", "-C", str(plan_path.parent), "rev-parse", "--show-toplevel"])
        ).resolve()
    )
    if plan_path.name.endswith(".plan.yaml"):
        # The same check `run` makes first, so an unapproved or drifted contract starts no pane.
        slug = yaml_plan.load(
            plan_path,
            repo=repo,
            state_dir=forwarded(run_args, "--state-dir") or yaml_plan.DEFAULT_STATE,
            require_approval=True,
        ).slug
    else:
        plan = parse_plan(plan_path.read_text(encoding="utf-8"))
        check_plan(plan)
        slug = plan["slug"]
    name = pane_name(slug)
    if name in live_terminals():
        return {"pane": name, "started": False}
    argv = [
        "hc",
        "run",
        "--bg",
        "--name",
        name,
        "--cwd",
        str(repo),
        "--json",
        "--",
        sys.executable,
        "-m",
        "agent_orchestrator",
        "run",
        str(plan_path),
        "--repo",
        str(repo),
        *run_args,
    ]
    parent = os.environ.get("HC_TERMINAL_ID")
    # The run's own HC_TERMINAL_ID is its pane: the Lead's terminal has to be handed over.
    if parent:
        argv += ["--notify-terminal", parent]
    result = subprocess.run(
        argv[:3] + ["--parent", parent] + argv[3:] if parent else argv,
        capture_output=True,
        text=True,
        timeout=60,
    )
    nesting_error = None
    if parent and result.returncode != 0:
        nesting_error = "hc run failed (exit %d): %s" % (
            result.returncode,
            (result.stderr or result.stdout).strip(),
        )
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise ValueError(
            "hc run failed (exit %d): %s"
            % (result.returncode, (result.stderr or result.stdout).strip())
        )
    out = {"pane": name, "started": True}
    if nesting_error is not None:
        out.update(nested=False, nesting_error=nesting_error)
    return out


def rebuild_metrics(args):
    """`metrics <plan>`: metrics.json rebuilt from the run's events.jsonl, which survives a crash. A run dir written
    before the log existed is read from its ledger instead."""
    slug, ledger_path, run_dir = plan_home(args)
    rows = events.read(run_dir / "events.jsonl")
    if not rows:
        require(
            ledger_path.exists(),
            "no events.jsonl or ledger for %s under %s" % (slug, run_dir.parent),
        )
        with ledger(ledger_path) as data:
            rows = [dict(row, kind="import") for row in legacy_imports(data)]
    result = events.rollup(rows, slug)
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (run_dir / "metrics.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def rebuild_evidence(args):
    """`evidence <plan>`: the run's attempts.jsonl rebuilt from its ledger and its events.jsonl, which survive a crash.
    The old metrics.json is left alone: the evidence is written beside it."""
    slug, ledger_path, run_dir = plan_home(args)
    require(ledger_path.exists(), "no ledger for %s under %s" % (slug, run_dir.parent))
    with ledger(ledger_path) as data:
        records = evidence.attempts(data, events.read(run_dir / "events.jsonl"), slug)
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    evidence.write(run_dir / "attempts.jsonl", records)
    return records


def write_atomic(path, text):
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".orchestrate-", dir=path.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


class Refused(PlanError):
    """A command of the Lead that cannot be honoured; the command exits 2 and writes nothing."""


def run_dir_of(args):
    """The run directory of the plan a Lead command names."""
    return plan_home(args)[2]


def numbered(directory, stem, suffix):
    """The existing write-once files <stem><suffix>, <stem>.2<suffix>, ... in order."""
    found = []
    for index in range(1, 1000):
        path = directory / (stem + ("" if index == 1 else ".%d" % index) + suffix)
        if not path.exists():
            break
        found.append(path)
    return found


def foreign_attempt(text, unit, expected):
    """The first attempt id of `unit` that `text` names and that is not `expected`, else None. An attempt id is the
    unit's own id or the unit followed by a round letter (fix `r`, retry `d`, orphan `o`) and a number; the worker
    name (`demo-t1`) and any longer word are not ids."""
    pattern = r"(?<![\w-])%s(?:[rdo]\d+)?(?!\w)" % re.escape(unit)
    return next((m for m in re.findall(pattern, text) if m != expected), None)


def decide(args):
    """`decide <plan> <unit> <action> --reason R [--answer A]`: answer the unit's open request. The decision file is
    written and validated first; only then is the request marked resolved, so the driver never sees a resolved request
    without its decision."""
    run_dir, unit = run_dir_of(args), args.unit.lower()
    unit_dir = run_dir / unit
    unit_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(unit_dir / ".decide.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        requests = numbered(unit_dir, "request", ".json")
        try:
            request = contracts.load(requests[-1], contracts.validate_request) if requests else None
        except contracts.ContractError as error:
            raise Refused(str(error)) from None
        if request is None or request["status"] != "open":
            raise Refused("%s has no open request" % unit)
        if args.action not in request["menu"]:
            raise Refused(
                "action %r is not on the menu: %s" % (args.action, ", ".join(request["menu"]))
            )
        if args.action == "answer" and args.answer:
            wrong = foreign_attempt(args.answer, unit, request["task"])
            if wrong:
                raise Refused(
                    "the answer names attempt %r, but the request is for %r and the worker resumes on %r: "
                    "name %r, or leave the id out"
                    % (wrong, request["task"], request["task"], request["task"])
                )
        try:
            decision = contracts.validate_decision(
                dict(
                    {"contract": contracts.VERSION, "action": args.action, "reason": args.reason},
                    **({"answer": args.answer} if args.answer else {}),
                )
            )
        except contracts.ContractError as error:
            raise Refused(str(error)) from None
        path = next_path(unit_dir, "decision", ".json")[0]
        write_atomic(path, json.dumps(decision, indent=2) + "\n")
        write_atomic(
            requests[-1],
            json.dumps(dict(request, status="resolved", decision=str(path)), indent=2) + "\n",
        )
    return {
        "unit": unit,
        "action": args.action,
        "decision": str(path),
        "request": str(requests[-1]),
    }


def take_wake(run_dir):
    """The oldest wake `await` has not printed (and mark it printed), or None. Concurrent awaits take turns."""
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(run_dir / "wakes.cursor", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+") as cursor:
        fcntl.flock(cursor, fcntl.LOCK_EX)
        raw = cursor.read().strip()
        seen = int(raw) if raw.isdigit() else 0
        wakes = events.read(run_dir / "wakes.jsonl")
        if seen >= len(wakes):
            return None
        cursor.seek(0)
        cursor.truncate()
        cursor.write(str(seen + 1))
        cursor.flush()
        os.fsync(cursor.fileno())
        return wakes[seen]["text"]


def run_finished(run_dir):
    """True when the newest of the driver's `start` and `finish` events is `finish`."""
    marks = [
        row["kind"]
        for row in events.read(run_dir / "events.jsonl")
        if row["kind"] in ("start", "finish")
    ]
    return bool(marks) and marks[-1] == "finish"


def await_wake(args):
    """`await <plan>`: print the next wake the Lead has not seen and return; `finished -> <run>/retro.md` once the run
    has ended and every wake is printed. Waits (polling, no tokens) while there is neither."""
    run_dir = run_dir_of(args)
    while True:
        line = take_wake(run_dir)
        if line:
            return line
        if run_finished(run_dir):
            return "finished -> %s" % (run_dir / "retro.md")
        time.sleep(args.poll_seconds)


@contextmanager
def run_lock(repo, slug, plan_path):
    """Hold the plan's lock for the whole run: one driver per plan and repository.

    It lives in the git common dir, not the state dir: two drivers of one plan collide on its worktrees, branches
    and plan file whatever `--state-dir` each names. `flock` dies with the process, so a crash leaves no stale lock.
    """
    common = Path(
        run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"])
    )
    directory = common / "agent-orchestrator"
    directory.mkdir(exist_ok=True, mode=0o700)
    fd = os.open(directory / (slug + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = lock.read().strip() or "another driver"
            raise PlanError(
                "plan '%s' is already being driven by %s in %s; follow it with `await %s`, "
                "or stop that driver before starting another" % (slug, holder, repo, plan_path)
            ) from None
        lock.seek(0)
        lock.truncate()
        lock.write(
            "pid %d since %s\n"
            % (os.getpid(), datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        )
        lock.flush()
        yield


def drive(args):
    require(args.max_workers >= 1, "--max-workers must be at least 1")
    require(args.max_fix_rounds >= 0, "--max-fix-rounds must not be negative")
    require(args.context_reserve >= 0, "--context-reserve must not be negative")
    require(args.idle_minutes >= 0, "--idle-minutes must not be negative")
    require(args.working_minutes >= 0, "--working-minutes must not be negative")
    driver = Driver(args)
    slug = (
        driver.read_plan()["slug"]
        if driver.plan_path.name.endswith(".plan.yaml")
        else parse_plan(driver.plan_path.read_text(encoding="utf-8"))["slug"]
    )
    with run_lock(driver.repo, slug, driver.plan_path):
        return drive_locked(driver)


def drive_locked(driver):
    driver.start()
    try:
        driver.loop()
        if not driver.stranded:  # a run that leaves a task it cannot settle has not finished
            driver.log("finish")
    finally:
        try:
            driver.report_metrics()
        finally:
            driver.report_evidence()
    return {
        "slug": driver.slug,
        "lead": driver.lead,
        "spawned": driver.spawned,
        "errors": driver.errors,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan_commands = commands.add_parser(
        "plan", help="Validate and approve plan/1 YAML contracts"
    ).add_subparsers(dest="plan_command", required=True)
    plan_commands.add_parser("schema", help="Print the plan/1 JSON Schema")
    plans = commands.add_parser(
        "plans", help="List the plans of a repository (or of an explicit plan's directory)"
    ).add_subparsers(dest="plans_command", required=True)
    listing = plans.add_parser("list")
    listing.add_argument("path", nargs="?", help="A plan file, or the directory holding plans")
    listing.add_argument("--repo", help="Repository root; default the current repository")
    listing.add_argument("--json", action="store_true", help="Print the list as a JSON array")
    for verb in ("check", "approve"):
        sub = plan_commands.add_parser(verb)
        sub.add_argument("plan")
        sub.add_argument("--repo", help="Repository root; default the plan file's repository")
        sub.add_argument("--state-dir", default=yaml_plan.DEFAULT_STATE)
        if verb == "approve":
            sub.add_argument("--base", default="origin/main")
    inspect = commands.add_parser(
        "inspect", help="Read checkout identity and installed command paths"
    )
    inspect.add_argument("--checkout", default=".")
    for name in ("claim", "bind", "checkpoint", "release", "show"):
        sub = commands.add_parser(name)
        sub.add_argument(
            "--state", required=True, help="Shared ledger path outside tracked product files"
        )
        if name in ("bind", "checkpoint", "release"):
            sub.add_argument("--task", required=True)
        if name == "show":
            sub.add_argument(
                "--task", help="Show the full record for one task; otherwise show a compact index"
            )
            sub.add_argument("--unit", help="Sum failed reviews and cutoffs over a unit's attempts")
        if name == "claim":
            sub.add_argument("--packet", required=True)
        if name == "bind":
            sub.add_argument("--runtime-id", required=True)
        if name == "checkpoint":
            sub.add_argument("--report", required=True)
        if name == "release":
            sub.add_argument("--reason", required=True)
            sub.add_argument(
                "--stopped", action="store_true", help="Assert runtime confirms the worker stopped"
            )
            sub.add_argument(
                "--cutoff", action="store_true", help="The worker was stopped at its cutoff"
            )
    sub = commands.add_parser("context")
    sub.add_argument("--sample", required=True)
    sub.add_argument("--reserve-tokens", type=int, required=True)
    sub.add_argument("--max-age-seconds", type=int, default=300)
    sub = commands.add_parser(
        "metrics", help="Rebuild a run's metrics.json from its events.jsonl, or normalize --samples"
    )
    sub.add_argument("plan", nargs="?", help="Plan file of the run to rebuild")
    sub.add_argument("--samples", help="A file of per-request usage records to normalize")
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )
    sub = commands.add_parser(
        "evidence", help="Rebuild a run's attempts.jsonl (the per-attempt evidence) from its ledger"
    )
    sub.add_argument("plan", help="Plan file of the run to rebuild")
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )
    sub = commands.add_parser(
        "run", help="Dispatch an approved plan's ready agent tickets and wait for them"
    )
    sub.add_argument("plan")
    sub.add_argument("--max-workers", type=int, default=3)
    sub.add_argument(
        "--max-fix-rounds",
        type=int,
        default=2,
        help="Fix rounds a unit may use before the next failed gate raises a request; a plan line "
        "`Max fix rounds: T3=3` sets it for one ticket",
    )
    sub.add_argument(
        "--context-reserve",
        type=int,
        default=40000,
        help="Tokens an implementer or reviewer must have left before a resume; below it the driver swaps in a fresh agent",
    )
    sub.add_argument("--cli", choices=("claude", "codex", "pi"), default="claude")
    sub.add_argument(
        "--brain-cli",
        choices=("claude", "codex", "pi"),
        help="The backend the review gates (the reviewer and the gaps reviewer) spawn on, with its "
        "medium model; the implementer and the archive worker stay on --cli",
    )
    sub.add_argument(
        "--agent",
        help="Role every worker runs under; 'none' runs them without one. "
        "Default: implementer for implementation workers, no role for review gates",
    )
    sub.add_argument(
        "--profile",
        help="Profile every worker runs under, as the runtime knows it "
        "(a configured profile such as 'minimax'); it replaces the CLI family "
        "in the spawn. Default: the CLI family alone",
    )
    sub.add_argument(
        "--base", default="origin/main", help="Remote branch each worktree starts from"
    )
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")
    sub.add_argument(
        "--gate", action="append", default=[], help="CI command a worker must pass (repeatable)"
    )
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--models-file",
        default="~/.claude/CLAUDE.md",
        help="File holding the 'Model classes' table",
    )
    sub.add_argument(
        "--routes-file",
        help="Typed route config (JSON, or YAML with PyYAML): what each model class "
        "runs on. Without it the legacy 'Model classes' table answers",
    )
    sub.add_argument(
        "--route",
        action="append",
        default=[],
        metavar="CLASS=BACKEND/MODEL[@EFFORT]",
        help="An explicit route for one model class: 'small=codex/gpt-s@high'. It wins over the route "
        "config, which wins over the legacy 'Model classes' table",
    )
    sub.add_argument(
        "--step",
        action="append",
        default=[],
        metavar="STEP=BACKEND/MODEL[@EFFORT]",
        help="An explicit route for one step of the run: 'reviewer=codex/gpt-l@xhigh'. The steps are "
        "implementer, reviewer, gaps and archive. It wins over the route config and over --brain-cli",
    )
    sub.add_argument(
        "--converge", default=str(CONVERGE), help="converge.py used to gate each report"
    )
    sub.add_argument(
        "--notify-terminal",
        default=None,
        help="hc terminal told about each new wake (`launch` passes its own); unset: wakes.jsonl only",
    )
    sub.add_argument(
        "--wait-seconds",
        type=int,
        default=60,
        help="Longest one wait blocks before the plan is re-read",
    )
    sub.add_argument(
        "--idle-interval",
        type=float,
        default=30,
        help="Seconds to sleep after an iteration that made no progress",
    )
    sub.add_argument(
        "--idle-minutes",
        type=float,
        default=10,
        help="Minutes between the idle ticks: a unit whose agent and CI show nothing new is logged idle, "
        "and at two ticks in a row the Lead is asked",
    )
    sub.add_argument(
        "--working-minutes",
        type=float,
        default=120,
        help="Minutes a unit's agent may be reported `working` by the runtime before the idle tick counts it",
    )
    sub = commands.add_parser(
        "await",
        help="Print the next wake the Lead has not seen, or `finished -> <retro>`; waits for one",
    )
    sub.add_argument("plan")
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )
    sub.add_argument(
        "--poll-seconds", type=float, default=2, help="How often wakes.jsonl is read while waiting"
    )
    sub = commands.add_parser(
        "decide", help="Answer a unit's open request with one action from its menu"
    )
    sub.add_argument("plan")
    sub.add_argument("unit")
    sub.add_argument("action", help="One of: " + ", ".join(contracts.ACTIONS))
    sub.add_argument("--reason", required=True)
    sub.add_argument(
        "--answer", help="The text the worker is resumed with; required for the answer action"
    )
    sub.add_argument(
        "--state-dir",
        default="~/.local/state/orchestrate",
        help="Holds <slug>.json and the <slug>/ run directory; a YAML plan's are under <repo-key>/",
    )
    sub.add_argument(
        "--repo", help="Repository root of a YAML plan; default the plan file's repository"
    )
    sub = commands.add_parser(
        "status",
        help="A plan's state and history and each ticket's state, reason and PR, from its ledger",
    )
    sub.add_argument("plan")
    sub.add_argument("--json", action="store_true", help="Print the answer as JSON")
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")
    sub.add_argument("--state-dir", default=yaml_plan.DEFAULT_STATE)
    sub = commands.add_parser(
        "launch", help="Check the plan and start `run` in a Herdr terminal named orchestrate-<slug>"
    )
    sub.add_argument("plan")
    sub.add_argument("--repo", help="Repository root; default the plan file's repository")
    args, extra = parser.parse_known_args(argv)
    if extra and args.command != "launch":
        parser.error("unrecognized arguments: " + " ".join(extra))
    if args.command == "launch":
        return launch(args, extra)  # unknown options are forwarded to `run`, which validates them
    if args.command == "plan":
        if args.plan_command == "schema":
            return yaml_plan.Plan.model_json_schema(by_alias=True)
        if args.plan_command == "approve":
            caller = resolve_runtime()
            if isinstance(caller, dict) and caller.get("role") == "worker":
                raise PlanError(
                    "approval: caller is a spawned worker, parent %s — approval belongs to the Lead "
                    "(the person in their own session); ask the Lead to run plan approve"
                    % caller.get("parent", "unknown")
                )
            return {
                "approval": yaml_plan.approve(
                    args.plan, repo=args.repo, state_dir=args.state_dir, base=args.base
                )
            }
        model = yaml_plan.load(args.plan, repo=args.repo, state_dir=args.state_dir)
        return {"valid": True, "slug": model.slug}
    if args.command == "plans":
        found = yaml_plan.discover(args.repo, args.path)
        if args.json:
            return found
        return "\n".join(entry["path"] for entry in found)
    if args.command == "status":
        status = plan_status(args)
        return status if args.json else render_status(status)
    if args.command == "run":
        return drive(args)
    if args.command == "await":
        return await_wake(args)
    if args.command == "decide":
        return decide(args)
    if args.command == "inspect":
        return {
            **checkout_snapshot(args.checkout),
            "executables": {c: shutil.which(c) for c in ("codex", "claude", "pi")},
            "agent_runtime": resolve_runtime(),
            "native_agent_capabilities": "inspect this session's tool schema",
            "context": "unknown; supply current child runtime telemetry",
        }
    if args.command == "context":
        return context_verdict(read_json(args.sample), args.reserve_tokens, args.max_age_seconds)
    if args.command == "evidence":
        return rebuild_evidence(args)
    if args.command == "metrics":
        if args.plan:
            return rebuild_metrics(args)
        require(args.samples, "metrics needs a plan file (rebuild its metrics.json) or --samples")
        return metrics(read_json(args.samples))
    with ledger(args.state) as data:
        if args.command == "claim":
            result = claim(data, read_json(args.packet))
            return {"action": result["action"], **task_summary(result["task"])}
        if args.command == "bind":
            return task_summary(bind(data, args.task, args.runtime_id))
        if args.command == "checkpoint":
            return task_summary(checkpoint(data, args.task, read_json(args.report)))
        if args.command == "release":
            require(args.stopped, "release requires --stopped")
            return task_summary(release(data, args.task, args.reason, args.cutoff))
        if args.unit:
            return unit_summary(data, args.unit)
        return (
            data["tasks"][args.task]
            if args.task
            else [task_summary(t) for t in data["tasks"].values()]
        )


def run_cli(argv=None):
    """The script's entry point: dispatch, print the answer, return the exit code.

    The tests call this in process, so a driver run starts no process of its own."""
    try:
        output = main(argv)
        print(output if isinstance(output, str) else json.dumps(output, separators=(",", ":")))
        return 1 if isinstance(output, dict) and output.get("errors") else 0
    except PlanError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run_cli())
