"""`workbench totals`: the last seven days of every plan of a repository, summed for the home cards, with each card's
dashboard detail. Read-only, like the rest of the workbench: it reads each plan's `events.jsonl` (and the ledger's pull
request links) and writes nothing.

A ticket counts when it merged in the window: its gate reached `cleanup` (or `done`) at `merged_at`. Everything else
about it (the steps it spent time in, its fix rounds, its tokens) comes from the same metrics `workbench plan` shows.
An escalation counts by its own time, merged ticket or not, and only its cause and unit are printed, never its
reason. The same cards are summed over the seven days before the window (`previous`) and compared (`change`), and each
card carries its per-day series. A figure nothing measured is null, never 0 or a guess. Stdlib only."""

import statistics
from datetime import timedelta, timezone
from pathlib import Path

from . import events, evidence
from . import workbench_metrics as metrics

WINDOW_DAYS = 7
SLOWEST = 5
LATEST = 10
TOP = 10
TICKETS = 12
ROUNDS = ("0", "1", "2", "3+")


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


def percentile_of(values, share):
    """The value `share` of the way up the sorted values, interpolated between neighbours; None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    spot = (len(ordered) - 1) * share
    low = int(spot)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (spot - low), 3)


def median_of(values):
    """The median (the mean of the middle two of an even count), or None for no values."""
    return round(statistics.median(values), 3) if values else None


def sum_of(values):
    """The sum of the figures that were measured; None when none was."""
    measured = [v for v in values if v is not None]
    return sum(measured) if measured else None


def merged_of(source, rows):
    """[merged ticket dict] of every ticket of the plan that merged, each with its metrics, whenever it did."""
    attempts = evidence.read(source.run_dir / "attempts.jsonl")
    shown = metrics.compute_rows(rows, attempts, source.ids)
    found = []
    for ticket, figures in shown["tickets"].items():
        merged_at = metrics.moment({"at": figures["merged_at"]})
        if merged_at is None:
            continue
        began = metrics.moment({"at": figures["dispatched_at"]}) or merged_at
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
                "reasons": figures["reasons"],
                "tokens": figures["tokens"],
                "model": figures["model"],
            }
        )
    return found


def escalations_of(source, rows):
    """[{plan, unit, ticket, cause, at}] of the plan's escalation events; the reason is never read. A unit that is no
    ticket (the archive) keeps its own name beside a null ticket."""
    known = {ticket.lower(): ticket for ticket in source.ids}
    found = []
    for row in rows:
        if row["kind"] != "escalation":
            continue
        when = metrics.moment(row)
        if when is None:
            continue
        name = str(row.get("unit") or "")
        ticket = known.get(name.lower())
        found.append(
            {
                "plan": source.slug,
                "unit": ticket or name or "plan",
                "ticket": ticket,
                "cause": row.get("cause") or "unknown",
                "at": when,
            }
        )
    return found


def readable(source, floor):
    """The plan's events when it was written since `floor`, else []: a log untouched since before it holds nothing
    of the window."""
    path = source.run_dir / "events.jsonl"
    try:
        if path.stat().st_mtime < floor.timestamp():
            return []
    except OSError:
        return []
    return events.read(path)


def between(items, key, low, high):
    """The items whose `key` moment is in [low, high)."""
    return [item for item in items if low <= item[key] < high]


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
        shown[phase] = {
            "median": median_of(values),
            "p90": percentile_of(values, 0.9),
            "max": max(values) if values else None,
        }
    return shown


def total_of(ticket):
    return (ticket["tokens"].get("input") or 0) + (ticket["tokens"].get("output") or 0)


def rate_of(tickets):
    return sum(1 for t in tickets if t["fix_rounds"] == 0) / len(tickets) if tickets else None


def cards_of(merged, raised):
    """The five home cards over the merged tickets and the escalations of one window."""
    return {
        "merged": len(merged),
        "first_pass_rate": rate_of(merged),
        "escalations": len(raised),
        "tokens": token_card(merged),
        "median_ticket_seconds": median_of([t["seconds"] for t in merged]),
    }


def change_of(now, before):
    """(now - before) / before; None when either is unknown or last week's was 0."""
    if now is None or not before:
        return None
    return (now - before) / before


def changes_of(cards, previous):
    """Each card's change against last week; the tokens card compares its output."""
    return {
        key: change_of(
            cards[key]["output"] if key == "tokens" else cards[key],
            previous[key]["output"] if key == "tokens" else previous[key],
        )
        for key in cards
    }


def dates_of(start, now):
    """Every UTC date the window touches, oldest first: eight when it starts mid-day."""
    first = start.astimezone(timezone.utc).date()
    last = now.astimezone(timezone.utc).date()
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def day_of(moment):
    return moment.astimezone(timezone.utc).date().isoformat()


def series_of(days, merged, raised):
    """Each card's `by_day`: every date present, a quiet day's rate, output and median null."""
    on = {day: [] for day in days}
    for ticket in merged:
        on[day_of(ticket["merged_at"])].append(ticket)
    escalated = {day: [] for day in days}
    for escalation in raised:
        escalated[day_of(escalation["at"])].append(escalation)
    return {
        "merged": [
            {"day": day, "count": len(on[day]), "by_plan": count_by(on[day], "plan")}
            for day in days
        ],
        "first_pass": [{"day": day, "rate": rate_of(on[day])} for day in days],
        "escalations": [
            {
                "day": day,
                "count": len(escalated[day]),
                "by_cause": count_by(escalated[day], "cause"),
            }
            for day in days
        ],
        "tokens": [{"day": day, "output": token_card(on[day])["output"]} for day in days],
        "time": [
            {"day": day, "median": median_of([t["seconds"] for t in on[day]])} for day in days
        ],
    }


def rounds_of(merged):
    """Every merged ticket counted by its fix rounds: 0, 1, 2 or 3 and more."""
    counted = dict.fromkeys(ROUNDS, 0)
    for ticket in merged:
        counted[str(ticket["fix_rounds"]) if ticket["fix_rounds"] < 3 else "3+"] += 1
    return counted


def reasons_by(tickets):
    summed = {}
    for ticket in tickets:
        for reason, count in ticket["reasons"].items():
            summed[reason] = summed.get(reason, 0) + count
    return dict(sorted(summed.items(), key=lambda pair: (-pair[1], pair[0])))


def by_plan_cause(raised):
    matrix = {}
    for escalation in raised:
        causes = matrix.setdefault(escalation["plan"], {})
        causes[escalation["cause"]] = causes.get(escalation["cause"], 0) + 1
    return {
        plan: dict(sorted(causes.items(), key=lambda pair: (-pair[1], pair[0])))
        for plan, causes in sorted(
            matrix.items(), key=lambda pair: (-sum(pair[1].values()), pair[0])
        )
    }


def totals(sources, now):
    """The answer of `workbench totals` over the plans' sources, for the window that ends at `now`."""
    start = now - timedelta(days=WINDOW_DAYS)
    before = start - timedelta(days=WINDOW_DAYS)
    # The window's upper bound is `now` itself: an event after it is clock skew and would sit on a date the series
    # does not list.
    end = now + timedelta(microseconds=1)
    every_merged, every_raised = [], []
    for source in sources:
        # A plan last driven before this window is still read: last week's cards may hold its merges.
        rows = readable(source, before)
        if not rows:
            continue
        every_merged += merged_of(source, rows)
        every_raised += escalations_of(source, rows)
    merged = between(every_merged, "merged_at", start, end)
    raised = between(every_raised, "at", start, end)
    merged.sort(key=lambda t: (t["merged_at"], t["plan"], t["ticket"]), reverse=True)
    raised.sort(key=lambda e: e["at"], reverse=True)
    last_merged = between(every_merged, "merged_at", before, start)
    last_raised = between(every_raised, "at", before, start)
    cards = cards_of(merged, raised)
    previous = cards_of(last_merged, last_raised)
    series = series_of(dates_of(start, now), merged, raised)
    plans = {}
    for ticket in merged:
        plans.setdefault(ticket["plan"], []).append(ticket)
    measured = [t for t in merged if t["tokens"]]
    models = {}
    for ticket in measured:
        models.setdefault(ticket["model"] or "unknown", []).append(ticket)
    slowest = sorted(merged, key=lambda t: -t["seconds"])
    fixed = [t for t in merged if t["fix_rounds"] > 0]
    return {
        "schema_version": 1,
        "window": {
            "days": WINDOW_DAYS,
            "start": start.isoformat(),
            "end": now.isoformat(),
            "previous": {"start": before.isoformat(), "end": start.isoformat()},
        },
        "cards": cards,
        "previous": previous,
        "change": changes_of(cards, previous),
        "time": {
            "steps": steps_of(merged),
            "slowest": [
                {k: t[k] for k in ("plan", "ticket", "pr", "seconds")} for t in slowest[:SLOWEST]
            ],
            "tickets": [
                {k: t[k] for k in ("plan", "ticket", "pr", "seconds", "phases")}
                for t in slowest[:TICKETS]
            ],
            "by_day": series["time"],
        },
        "merged": {
            "by_day": series["merged"],
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
            "by_plan": {plan: rate_of(tickets) for plan, tickets in sorted(plans.items())},
            "by_day": series["first_pass"],
            "by_reason": reasons_by(fixed),
            "rounds": rounds_of(merged),
            "fixed": [
                {k: t[k] for k in ("plan", "ticket", "fix_rounds", "findings", "reasons")}
                for t in fixed
            ],
        },
        "escalations": {
            "by_cause": count_by(raised, "cause"),
            "by_plan": count_by(raised, "plan"),
            "by_plan_cause": by_plan_cause(raised),
            "by_day": series["escalations"],
            "latest": [
                {
                    "plan": e["plan"],
                    "unit": e["unit"],
                    "ticket": e["ticket"],
                    "cause": e["cause"],
                    "at": e["at"].isoformat(),
                }
                for e in raised[:LATEST]
            ],
        },
        "tokens": {
            "by_plan": {plan: token_card(tickets) for plan, tickets in sorted(plans.items())},
            "by_day": series["tokens"],
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
