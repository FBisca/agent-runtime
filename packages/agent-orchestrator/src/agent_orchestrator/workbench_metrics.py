"""The run's metrics, computed read-only from `events.jsonl` (and `attempts.jsonl` when it is there) for `workbench plan`.

Nothing is written and no lock is taken: `metrics.json` is written only when a run ends, so a pane could never show a
live run from it. Every figure here is a pure function of the lines on disk. A figure nothing measured is None, never
0; an open phase has a null end and a null duration.

Phases come from a unit's gate transitions: `work` runs from the unit's first RESERVED to `reported`, `review` from
`reviewing` until the gate leaves it, `ci` from `ready-for-ci` until the gate leaves it. Wall time sums each drive
(`start` to its `finish`, else its last event): the driver is sometimes down for an hour, and that hour is nobody's
time. Stdlib only.
"""

import re
from datetime import datetime
from pathlib import Path

from . import events, evidence

LAST_ERROR_LIMIT = 120
PHASES = ("work", "review", "ci")


def moment(row):
    """The row's `at` as a datetime, or None when it is missing or unreadable."""
    try:
        return datetime.fromisoformat(row["at"])
    except (KeyError, TypeError, ValueError):
        return None


def seconds(start, end):
    return round((end - start).total_seconds(), 3)


def drives_of(rows):
    """[(start, end)] of each drive: from a `start` event to the first `finish` after it, else the last event before
    the next `start` (or the last event of the log)."""
    marks = [(index, moment(row)) for index, row in enumerate(rows) if row["kind"] == "start"]
    drives = []
    for number, (index, begun) in enumerate(marks):
        if begun is None:
            continue
        stop = marks[number + 1][0] if number + 1 < len(marks) else len(rows)
        end = None
        for row in rows[index + 1 : stop]:
            when = moment(row)
            if when is None:
                continue
            end = when
            if row["kind"] == "finish":
                break
        drives.append((begun, max(end, begun) if end else begun))
    return drives


def covered(start, end, drives):
    """Seconds of [start, end] that fall inside a drive."""
    total = 0.0
    for begun, stopped in drives:
        low, high = max(start, begun), min(end, stopped)
        if high > low:
            total += (high - low).total_seconds()
    return round(total, 3)


def tokens_of(records):
    """The summed input/output/cached figures of a unit's attempt records, a label nobody measured None; None when
    no label was measured."""
    counted = dict.fromkeys(evidence.TOKEN_KEYS)
    for record in records:
        for label in counted:
            value = (record.get("tokens") or {}).get(label) if isinstance(record, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                counted[label] = (counted[label] or 0) + value
    return counted if any(v is not None for v in counted.values()) else None


def usage_tokens(rows, unit):
    """The same figures from the log's `usage` events, for a run that has no attempts file: a usage event names an
    assignment, and an `agent` event of the unit says which assignments are the unit's."""
    mine = {r.get("assignment_id") for r in rows if r["kind"] == "agent" and r.get("unit") == unit}
    mine.discard(None)
    return tokens_of(
        [
            {"tokens": evidence.tokens(r.get("usage"))}
            for r in rows
            if r["kind"] == "usage" and r.get("assignment_id") in mine
        ]
    )


def fix_task(task, unit):
    """Whether the task is one of the unit's fix rounds: its id is the unit's, then `r`, then the round."""
    return re.fullmatch(r"%sr\d+" % re.escape(unit), str(task).lower()) is not None


class Lane:
    """One unit's measurements, filled event by event."""

    def __init__(self, unit):
        self.unit = unit
        self.first = None
        self.last = None
        self.done = None
        self.model = None
        self.fix_tasks = set()
        self.reviews = 0
        self.findings = 0
        self.ci_failed = set()
        self.escalations = {}
        self.segments = []  # [phase, start, end or None]

    def open(self, phase):
        return next((s for s in self.segments if s[0] == phase and s[2] is None), None)

    def begin(self, phase, when):
        if when is not None and self.open(phase) is None:
            self.segments.append([phase, when, None])

    def close(self, phase, when):
        segment = self.open(phase)
        if segment is not None and when is not None:
            segment[2] = when

    def state(self, row, when):
        old, new = (list(row.get("gate") or [None, None]) + [None, None])[:2]
        status = (list(row.get("status") or [None, None]) + [None, None])[:2]
        if status[1] == "RESERVED":
            self.begin("work", when)
        if new == "reported":
            self.close("work", when)
        if new == "reviewing" and old != "reviewing":
            self.reviews += 1
            self.begin("review", when)
        if old == "reviewing" and new != "reviewing":
            self.close("review", when)
        if new == "ready-for-ci" and old != "ready-for-ci":
            self.begin("ci", when)
        if old == "ready-for-ci" and new != "ready-for-ci":
            self.close("ci", when)
        if new == "done" and self.done is None:
            self.done = when
        route = row.get("route")
        if isinstance(route, dict) and isinstance(route.get("model"), str):
            self.model = self.model or route["model"]

    def take(self, row):
        when = moment(row)
        if when is not None:
            self.first = min(self.first, when) if self.first else when
            self.last = max(self.last, when) if self.last else when
        kind = row["kind"]
        task = row.get("task")
        if task and fix_task(task, self.unit):
            self.fix_tasks.add(str(task).lower())
        if kind == "state":
            self.state(row, when)
        elif kind == "route" and isinstance(row.get("model"), str):
            self.model = self.model or row["model"]
        elif kind == "finding":
            self.findings += 1
        elif kind == "ci" and row.get("state") == "failed":
            self.ci_failed.add((task, row.get("pr"), row.get("gate")))
        elif kind == "escalation":
            cause = row.get("cause") or "unknown"
            self.escalations[cause] = self.escalations.get(cause, 0) + 1

    def phases(self):
        shown = dict.fromkeys(PHASES)
        for phase, start, end in self.segments:
            if end is not None:
                shown[phase] = (shown[phase] or 0) + seconds(start, end)
        return shown

    def timeline(self):
        return [
            {"phase": phase, "start": start.isoformat(), "end": end.isoformat() if end else None}
            for phase, start, end in self.segments
        ]


def errors_of(rows):
    """Driver errors by the gate that raised them (`unknown` for an error with no gate), and the last one's text on
    one line, cut."""
    by_gate = {}
    last = None
    for row in rows:
        if row["kind"] != "error":
            continue
        gate = row.get("gate") or "unknown"
        by_gate[gate] = by_gate.get(gate, 0) + 1
        last = " ".join(str(row.get("error") or "").split())[:LAST_ERROR_LIMIT]
    return {"by_gate": dict(sorted(by_gate.items())), "last": last}


def compute(run_dir, ticket_ids):
    """The metrics of the run in `run_dir` for the plan's tickets, or None when the run has no events."""
    rows = events.read(Path(run_dir) / "events.jsonl")
    if not rows:
        return None
    attempts = evidence.read(Path(run_dir) / "attempts.jsonl")
    drives = drives_of(rows)
    stamps = [t for t in map(moment, rows) if t]
    run_start = drives[0][0] if drives else (min(stamps) if stamps else None)
    run_end = max([end for _, end in drives] + stamps) if stamps else None
    known = {ticket.lower(): ticket for ticket in ticket_ids}
    lanes = {}
    for row in rows:
        unit = str(row.get("unit") or "").lower()
        if unit in known:
            lanes.setdefault(unit, Lane(unit)).take(row)
    tickets, timeline = {}, {}
    for unit, lane in lanes.items():
        ticket = known[unit]
        mine = [a for a in attempts if str(a.get("unit") or "").lower() == unit]
        end = lane.done or run_end
        elapsed = covered(lane.first, end, drives) if lane.first and end and drives else None
        if elapsed is None and lane.first and end:
            elapsed = seconds(lane.first, end)
        tickets[ticket] = {
            "elapsed_seconds": elapsed,
            "phases": lane.phases(),
            "fix_rounds": len(lane.fix_tasks),
            "review_rounds": lane.reviews,
            "findings": lane.findings,
            "ci_failures": len(lane.ci_failed),
            "escalations": dict(sorted(lane.escalations.items())),
            "model": lane.model
            or next((a["provider"] for a in mine if isinstance(a.get("provider"), str)), None),
            "tokens": tokens_of(mine) or (None if attempts else usage_tokens(rows, unit)),
        }
        timeline[ticket] = lane.timeline()
    first_pass = [t for t in tickets.values() if t["fix_rounds"] == 0]
    return {
        "wall_seconds": round(sum(seconds(s, e) for s, e in drives), 3) if drives else None,
        "span": {
            "start": run_start.isoformat() if run_start else None,
            "end": run_end.isoformat() if run_end else None,
        },
        "first_pass_rate": len(first_pass) / len(tickets) if tickets else None,
        "errors": errors_of(rows),
        "tickets": dict(sorted(tickets.items())),
        "timeline": dict(sorted(timeline.items())),
    }
