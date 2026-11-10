"""Per-attempt evidence: one schema-versioned record per attempt, and the old metrics.json it does not replace.

`<run>/attempts.jsonl` holds one JSON object per attempt: what the attempt was (task, unit, harness, provider, source
revision), when it ran (started, finished, elapsed), how it ended (outcome, claimed completion, gate outcomes) and
what it cost (retries, tool loops, interventions, human rescues, input/output/cached tokens).

Two rules hold everywhere. A measurement that was not taken is `null`, never `0`: an unknown cost is not a free
attempt. And every record names the schema it was written under, so a reader can tell what it is looking at.

The old `metrics.json` keeps its shape: the evidence is written beside it, never folded into it, so the consumers
that read `metrics.json` today keep working. Stdlib only.
"""

import json
import os
from datetime import datetime

# 2: `verified` became `claimed`. A worker's report claims a completion; only the driver's receipt verifies one.
SCHEMA_VERSION = 2

FIELDS = (
    "schema_version",
    "task_id",
    "unit",
    "attempt",
    "harness",
    "provider",
    "source_revision",
    "started_at",
    "finished_at",
    "elapsed_seconds",
    "outcome",
    "claimed",
    "counters",
    "tokens",
    "gates",
    "checks",
)

COUNTERS = ("tool_calls", "model_calls", "tool_loops", "retries", "interventions", "human_rescues")

TOKEN_KEYS = {
    "input": ("input_tokens", "input"),
    "output": ("output_tokens", "output"),
    "cached": (
        "cached_input_tokens",
        "cache_read_input_tokens",
        "cached_tokens",
        "cache_read_tokens",
    ),
}

TERMINAL = ("COMPLETE", "RELEASED", "BLOCKED")


def tokens(usage):
    """One usage report as labelled input/output/cached figures. A figure the runtime did not report is `None`."""
    counted = {}
    for label, keys in TOKEN_KEYS.items():
        value = None
        if isinstance(usage, dict):
            for key in keys:
                candidate = usage.get(key)
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                    value = candidate
                    break
        counted[label] = value
    return counted


def sum_tokens(usages):
    """The usage of every assignment of an attempt, summed per label; a label nobody reported is `None`."""
    counted = {}
    for label in TOKEN_KEYS:
        numbers = [tokens(usage)[label] for usage in usages]
        numbers = [number for number in numbers if number is not None]
        counted[label] = sum(numbers) if numbers else None
    return counted


def elapsed(started, finished):
    """The seconds between two ISO timestamps, or `None` when one of them is missing or unreadable."""
    if not started or not finished:
        return None
    try:
        return round(
            (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds(), 3
        )
    except (TypeError, ValueError):
        return None


def claimed(task):
    """Whether the attempt claims a completion: a complete report that says it met its acceptance and passed every
    check. That is the worker's word, not evidence (a receipt is). An attempt that ended without such a report claims
    nothing; an attempt still running is unknown (`None`)."""
    report = task.get("report")
    if report:
        checks = report.get("checks") or []
        return bool(
            report.get("status") == "COMPLETE"
            and report.get("acceptance_met") is True
            and all(check.get("outcome") == "passed" for check in checks)
        )
    if task.get("status") in TERMINAL:
        return False
    return None


def assignments(task):
    """The assignment ids an attempt used: its worker, its reviewer, its decider and its answerer."""
    found = [task.get("assignment_id")]
    for key in ("review", "decision", "answers"):
        part = task.get(key)
        if isinstance(part, dict):
            found.append(part.get("assignment_id"))
    return {value for value in found if value}


def record(task_id, task, rows, number):
    """The evidence of one attempt."""
    mine = [row for row in rows if row.get("task") == task_id]
    report = task.get("report") or {}
    packet = task.get("packet") or {}
    started = task.get("started_at")
    finished = task.get("finished_at")
    if not finished and task.get("status") in TERMINAL:
        finished = task.get("updated_at")
    ids = assignments(task)
    usages = [
        row.get("usage")
        for row in rows
        if row.get("kind") == "usage" and row.get("assignment_id") in ids
    ]
    counters = {
        "tool_calls": None,
        "model_calls": None,
        "tool_loops": sum(1 for row in mine if row.get("kind") == "idle"),
        "retries": sum(1 for row in mine if row.get("kind") == "round"),
        "interventions": sum(1 for row in mine if row.get("kind") == "escalation"),
        "human_rescues": sum(1 for row in mine if row.get("kind") == "decision"),
    }
    gates = [
        {"source": row.get("source"), "outcome": "failed"}
        for row in mine
        if row.get("kind") == "round"
    ]
    gates += [
        {"source": "ci", "outcome": row.get("state")} for row in mine if row.get("kind") == "ci"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "unit": packet.get("unit") or task_id,
        "attempt": number,
        "harness": packet.get("backend"),
        "provider": packet.get("model"),
        "source_revision": report.get("head"),
        "started_at": started,
        "finished_at": finished,
        "elapsed_seconds": elapsed(started, finished),
        "outcome": task.get("status"),
        "claimed": claimed(task),
        "counters": counters,
        "tokens": sum_tokens(usages),
        "gates": gates,
        "checks": report.get("checks") or [],
    }


def attempts(data, rows, slug=None):
    """The evidence of every attempt of a run, oldest first. The slug is carried for the reader, not for the counts."""
    tasks = (data or {}).get("tasks") or {}
    ordered = sorted(tasks.items(), key=lambda item: (item[1].get("started_at") or "", item[0]))
    numbers = {}
    records = []
    for task_id, task in ordered:
        unit = (task.get("packet") or {}).get("unit") or task_id
        numbers[unit] = numbers.get(unit, 0) + 1
        records.append(record(task_id, task, rows, numbers[unit]))
    return records


def write(path, records):
    """The records as one JSON object per line. The file is replaced whole, so a rebuild is idempotent."""
    text = "".join(json.dumps(item, sort_keys=True) + "\n" for item in records)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return records


def read(path):
    """The records of an evidence file, oldest first. A line that does not parse is skipped."""
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            records.append(row)
    return records
