"""`workbench totals`: the last seven days of every plan of a repository, summed for the home cards, with each card's
dashboard detail. Read-only, like the rest of the workbench: it reads each plan's `events.jsonl` (and the ledger's pull
request links) and writes nothing.

A ticket counts when it merged in the window: its gate reached `cleanup` (or `done`) at `merged_at`. Everything else
about it (the steps it spent time in, its fix rounds, its tokens) comes from the same metrics `workbench plan` shows.
An escalation counts by its own time, merged ticket or not, and only its cause and ticket are printed, never its
reason. A figure nothing measured is null, never 0 or a guess. Stdlib only."""

import statistics
from datetime import timedelta
from pathlib import Path

from . import events, evidence
from . import workbench_metrics as metrics

WINDOW_DAYS = 7
SLOWEST = 5
LATEST = 10
TOP = 10


class Source:
    """One plan's inputs: its slug, run directory, ticket ids and the ledger's tasks (for pull request links)."""

    def __init__(self, slug, run_dir, ids, tasks=None):
        self.slug = slug
        self.run_dir = Path(run_dir)
        self.ids = list(ids)
        self.tasks = tasks or {}

    def pr_of(self, ticket):
        """The pull request a ledger task of the ticket holds, the newest task's; None when none does."""
        found = None
        for task in self.tasks.values():
            unit = ((task or {}).get("packet") or {}).get("unit")
            pr = (task or {}).get("pr")
            if isinstance(unit, str) and unit.lower() == ticket.lower() and isinstance(pr, str):
                found = pr
        return found


def median_of(values):
    """The median (the mean of the middle two of an even count), or None for no values."""
    return round(statistics.median(values), 3) if values else None


def sum_of(values):
    """The sum of the figures that were measured; None when none was."""
    measured = [v for v in values if v is not None]
    return sum(measured) if measured else None


def in_window(when, start):
    return when is not None and when >= start


def merged_in(source, start, rows):
    """[merged ticket dict] of the plan's tickets merged in the window, each with its metrics."""
    attempts = evidence.read(source.run_dir / "attempts.jsonl")
    shown = metrics.compute_rows(rows, attempts, source.ids)
    found = []
    for ticket, figures in shown["tickets"].items():
        merged_at = metrics.moment({"at": figures["merged_at"]})
        if not in_window(merged_at, start):
            continue
        began = metrics.moment({"at": figures["dispatched_at"]}) or metrics.moment(
            {"at": figures["merged_at"]}
        )
        found.append(
            {
                "plan": source.slug,
                "ticket": ticket,
                "pr": source.pr_of(ticket),
                "merged_at": merged_at,
                "seconds": metrics.seconds(began, merged_at),
                "phases": figures["phases"],
                "fix_rounds": figures["fix_rounds"],
                "findings": figures["findings"],
                "tokens": figures["tokens"],
                "model": figures["model"],
            }
        )
    return found


def escalations_in(source, start, rows):
    """[{plan, ticket, cause, at}] of the plan's escalation events in the window; the reason is never read."""
    known = {ticket.lower(): ticket for ticket in source.ids}
    found = []
    for row in rows:
        if row["kind"] != "escalation":
            continue
        when = metrics.moment(row)
        if in_window(when, start):
            found.append(
                {
                    "plan": source.slug,
                    "ticket": known.get(str(row.get("unit") or "").lower()),
                    "cause": row.get("cause") or "unknown",
                    "at": when,
                }
            )
    return found


def readable(source, start):
    """The plan's events when it ran inside the window, else []: a log untouched since before the window holds nothing
    of it, so it is not read."""
    path = source.run_dir / "events.jsonl"
    try:
        if path.stat().st_mtime < start.timestamp():
            return []
    except OSError:
        return []
    return events.read(path)


def count_by(items, key):
    counted = {}
    for item in items:
        counted[item[key]] = counted.get(item[key], 0) + 1
    return dict(sorted(counted.items(), key=lambda pair: (-pair[1], str(pair[0]))))


def token_card(tickets):
    """{input, output, measured, tickets} over the tickets: the sums are of the measured ones, null when none was."""
    measured = [t["tokens"] for t in tickets if t["tokens"]]
    return {
        "input": sum_of([m.get("input") for m in measured]),
        "output": sum_of([m.get("output") for m in measured]),
        "measured": len(measured),
        "tickets": len(tickets),
    }


def steps_of(tickets):
    shown = {}
    for phase in metrics.PHASES:
        values = [t["phases"][phase] for t in tickets if t["phases"].get(phase) is not None]
        shown[phase] = {"median": median_of(values), "max": max(values) if values else None}
    return shown


def total_of(ticket):
    return (ticket["tokens"].get("input") or 0) + (ticket["tokens"].get("output") or 0)


def totals(sources, now):
    """The answer of `workbench totals` over the plans' sources, for the window that ends at `now`."""
    start = now - timedelta(days=WINDOW_DAYS)
    merged, raised = [], []
    for source in sources:
        rows = readable(source, start)
        if not rows:
            continue
        merged += merged_in(source, start, rows)
        raised += escalations_in(source, start, rows)
    merged.sort(key=lambda t: (t["merged_at"], t["plan"], t["ticket"]), reverse=True)
    raised.sort(key=lambda e: e["at"], reverse=True)
    first_pass = [t for t in merged if t["fix_rounds"] == 0]
    by_day = {}
    for ticket in merged:
        day = ticket["merged_at"].date().isoformat()
        by_day[day] = by_day.get(day, 0) + 1
    plans = {}
    for ticket in merged:
        plans.setdefault(ticket["plan"], []).append(ticket)
    measured = [t for t in merged if t["tokens"]]
    models = {}
    for ticket in measured:
        models.setdefault(ticket["model"] or "unknown", []).append(ticket)
    seconds = [t["seconds"] for t in merged]
    return {
        "schema_version": 1,
        "window": {"days": WINDOW_DAYS, "start": start.isoformat(), "end": now.isoformat()},
        "cards": {
            "merged": len(merged),
            "first_pass_rate": len(first_pass) / len(merged) if merged else None,
            "escalations": len(raised),
            "tokens": token_card(merged),
            "median_ticket_seconds": median_of(seconds),
        },
        "time": {
            "steps": steps_of(merged),
            "slowest": [
                {k: t[k] for k in ("plan", "ticket", "pr", "seconds")}
                for t in sorted(merged, key=lambda t: -t["seconds"])[:SLOWEST]
            ],
        },
        "merged": {
            "by_day": [{"day": day, "count": by_day[day]} for day in sorted(by_day)],
            "tickets": [
                {
                    "plan": t["plan"],
                    "ticket": t["ticket"],
                    "pr": t["pr"],
                    "merged_at": t["merged_at"].isoformat(),
                }
                for t in merged
            ],
        },
        "first_pass": {
            "by_plan": {
                plan: sum(1 for t in tickets if t["fix_rounds"] == 0) / len(tickets)
                for plan, tickets in sorted(plans.items())
            },
            "fixed": [
                {k: t[k] for k in ("plan", "ticket", "fix_rounds", "findings")}
                for t in merged
                if t["fix_rounds"] > 0
            ],
        },
        "escalations": {
            "by_cause": count_by(raised, "cause"),
            "by_plan": count_by(raised, "plan"),
            "latest": [
                {
                    "plan": e["plan"],
                    "ticket": e["ticket"],
                    "cause": e["cause"],
                    "at": e["at"].isoformat(),
                }
                for e in raised[:LATEST]
            ],
        },
        "tokens": {
            "by_plan": {plan: token_card(tickets) for plan, tickets in sorted(plans.items())},
            "by_model": {
                model: {
                    "input": sum_of([t["tokens"].get("input") for t in tickets]),
                    "output": sum_of([t["tokens"].get("output") for t in tickets]),
                    "tickets": len(tickets),
                }
                for model, tickets in sorted(models.items())
            },
            "top": [
                {
                    "plan": t["plan"],
                    "ticket": t["ticket"],
                    "input": t["tokens"].get("input"),
                    "output": t["tokens"].get("output"),
                    "model": t["model"],
                }
                for t in sorted(measured, key=total_of, reverse=True)[:TOP]
            ],
        },
    }
