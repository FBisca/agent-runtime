"""The run's append-only event log, and the metrics rolled up from it.

`<run>/events.jsonl` holds one JSON object per line: `at`, `kind`, and the fields that kind carries. A line is written
(one `write`, then `fsync`) before the driver takes its next step, so a crash loses at most the line being written.
`rollup` is a pure function of the lines: `metrics.json` can always be rebuilt from the log. Stdlib only.

Kinds the driver writes today: `start`, `import` (a run dir written by the previous version), `state` (a unit's gate or
ledger status changed), `route` (the route a worker was dispatched on: `unit`, `task`, `class`, `backend`, `model`,
`effort`, `effort_source`, `source`), `intent` and `agent` (an agent is about to be started / is bound), `stop`, `finding`,
`round` (a failed gate), `escalation` (a unit raised a request for the Lead), `attention` (a unit paused), `decision`, `usage`,
`idle` (a loop iteration that made no progress), `error`, `wake` (a line for the Lead was written to `wakes.jsonl`),
`held`, `held-acted` and `held-dropped` (a worker message that arrived while its unit had an open request), `finish`
(the run ended; `orchestrate.py await` reads it), `contract-error` (a handoff file broke its contract: `where`, `error`),
`clarification` (an agent listed a question in `clarifications[]`: `finding`, `asked_by`, `note`, `source`), `answers`
(the implementer was resumed for the answer_required items of a passing review), `response` (one per answered finding),
`scope` (a re-review's blocking item outside the delta, about no earlier finding, was deferred: `id`, `file`),
`swap` (an agent below the context reserve was replaced by a fresh one: `name`, `role`, `remaining_tokens`) and
`context` (`context: unknown`: no usable usage measurement, so the agent was resumed).
`ci` (a pull request's checks were polled: `unit`, `task`, `gate`, `state` green|pending|none|failed, `pr`),
`draft` (a pull request was put back to a draft: `unit`, `task`, `pr`, `outcome`),
`update-branch` (a pull request behind its base was updated, or it conflicted: `unit`, `task`, `pr`, `outcome`),
`merged-early` (a pull request was merged while its unit was still working: `unit`, `task`, `pr`, `findings`) and
`idle` (a unit that showed nothing new at an idle tick: `unit`, `task`, `waiting` agent|ci, `ticks`),
`drain` (the plan was edited since its approval, or no longer loads, and the run starts no new worker: `state`
draining|ended, `cause` edited|unloadable, `error`, `digest` of the approval the run holds; `ended` when the file is
back to its approved bytes),
`amended` (the run adopted an amendment: `prev_digest`, `digest`, and the amendment's `reason` and `tickets`) and
`repacket` (an open attempt whose ticket the amendment changed was given its next packet: `unit`, `task`, `packet`
`task.v<n>.md`, `resumed`, `digest`)."""

import json
import os
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def append(path, kind, **fields):
    """Append one event as one line; the line is on disk when this returns."""
    line = (
        json.dumps(
            dict({"at": now(), "kind": kind}, **{k: v for k, v in fields.items() if v is not None}),
            separators=(",", ":"),
            default=str,
        )
        + "\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def read(path):
    """The events in a log, oldest first. A line that does not parse (the one a crash cut short) is skipped."""
    try:
        with open(path, encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("kind"), str):
            events.append(row)
    return events


def reported(value):
    return type(value) in (int, float)


def sum_usage(values):
    """One ticket's usage from the runtime's per-assignment values: each key is the sum over the assignments when every
    one reported it as a number, and `None` when any did not (a ticket total never hides a missing figure); the values
    as listed when they are not numeric dicts; `unknown` when the runtime reported none."""
    if not values:
        return "unknown"
    if all(
        isinstance(v, dict) and all(reported(x) or x is None for x in v.values()) for v in values
    ):
        total = {}
        for value in values:
            for key in value:
                total[key] = (
                    sum(v[key] for v in values)
                    if all(reported(v.get(key)) for v in values)
                    else None
                )
        return total
    return values if len(values) > 1 else values[0]


def rollup(events, slug):
    """metrics.json from the log: per unit the route, failed gates, escalations (deciders called plus pauses), decider
    calls, findings by kind and worker usage. The lead's own model turns are 0 by construction (the driver is code)."""
    units = {}

    def unit_of(row):
        return units.setdefault(
            row.get("unit") or "-",
            {
                "route": None,
                "review_rounds": 0,
                "escalations": 0,
                "decider_calls": 0,
                "findings": {},
                "usage": {},
            },
        )

    counts = {}
    for row in events:
        kind = row["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        if not row.get("unit") and kind != "import":
            continue
        entry = unit_of(row)
        if kind == "import":
            entry["route"] = entry["route"] or row.get("route")
            for key in ("review_rounds", "escalations", "decider_calls"):
                entry[key] += row.get(key, 0)
            entry["usage"].update(row.get("assignments") or {})
        elif kind == "state" and row.get("route"):
            entry["route"] = entry["route"] or row["route"]
        elif kind == "round":
            entry["review_rounds"] += 1
        elif kind in ("escalation", "attention"):
            entry["escalations"] += 1
        elif kind == "agent" and row.get("role") == "decider":
            entry["decider_calls"] += 1
        elif kind == "finding":
            found = row.get("finding_kind") or "unknown"
            entry["findings"][found] = entry["findings"].get(found, 0) + 1
        elif kind == "usage" and row.get("assignment_id"):
            entry["usage"][row["assignment_id"]] = row.get("usage")
    tickets = {}
    for unit, entry in units.items():
        if unit == "-" or not entry["route"]:
            continue
        values = [
            entry["usage"][key] for key in sorted(entry["usage"]) if entry["usage"][key] is not None
        ]
        tickets[unit] = {
            "route": entry["route"],
            "review_rounds": entry["review_rounds"],
            "escalations": entry["escalations"],
            "decider_calls": entry["decider_calls"],
            "findings": entry["findings"],
            "usage": sum_usage(values),
        }
    return {"slug": slug, "lead": {"model_turns": 0}, "tickets": tickets, "events": counts}
