"""The driver's reporting: the tables and the rendered text a run is read by.

The usage figures beside a route, the lines a `status` command prints, and the few words a report or
a fix round shows for a finding. Each takes the record it is given and returns text, so a collaborator
the package is split into prints what the driver prints.

They live beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because a rendering its callers share has one home.
"""

import json

from .pointers import collection

# The usage figures a metrics table prints, in the order it prints them.
USAGE_COLUMNS = (
    ("input", "input_tokens"),
    ("output", "output_tokens"),
    ("cached", "cached_input_tokens"),
    ("cache_write", "cache_write_tokens"),
    ("reasoning", "reasoning_tokens"),
    ("model_calls", "model_calls"),
    ("tool_calls", "tool_calls"),
    ("cost_usd", "cost_usd"),
)


def metrics_table(metrics):
    rows = [
        ("unit", "route", "rounds", "escalations", "deciders", *(name for name, _ in USAGE_COLUMNS))
    ]
    for unit, row in metrics["tickets"].items():
        route = "%s·%s" % (row["route"]["size"], row["route"]["difficulty"])
        usage = row["usage"]
        # A figure nobody reported prints as null, never as 0 or as a blank.
        figures = [
            usage
            if isinstance(usage, str)
            else json.dumps((usage if isinstance(usage, dict) else {}).get(key))
            for _, key in USAGE_COLUMNS
        ]
        rows.append(
            (
                unit,
                route,
                str(row["review_rounds"]),
                str(row["escalations"]),
                str(row["decider_calls"]),
                *figures,
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]
    return "\n".join(
        [
            "metrics for %s" % metrics["slug"],
            *lines,
            "lead model turns: %d" % metrics["lead"]["model_turns"],
        ]
    )


def render_status(status):
    lines = ["%s: %s" % (status["slug"], status["state"]), "run: " + collection(status["run_dir"])]
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


def cell(text, limit=160):
    """Text safe inside one plan table cell: one line, no pipes, cut at `limit`."""
    flat = " ".join(str(text).replace("|", "/").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def show(value):
    """A plan field as one line: a list joined, anything else as it reads."""
    return ", ".join(value) if isinstance(value, list) else str(value)


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
    # Only a blocking item must carry a line; a deferred item may name just its file.
    place = "%s:%s" % (item["file"], item["line"]) if item.get("line") else item.get("file")
    where = place + " " if place else ""
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
            " ".join(item["excerpt"].splitlines()[:2])
            if item["excerpt"]
            else "%s %s failed" % (item["source"], item["check"])
        )
    return "%s: %s" % (item.get("id"), item_text(item))
