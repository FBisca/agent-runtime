"""The reviewer's packet: one home for the brief a review is read from and the trail it is given.

A review is opened on one attempt's report, so the brief names that report — its exact path, when it
was written and the head it claims — instead of a glob over the unit's directory, which a stale
attempt answers once the unit has been re-dispatched. The trail behind the review is read in the order
the record holds, so the newest attempt is the one the record says was made last.

They live beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because the one reading of "which report is this" has one home.
"""

from datetime import datetime, timezone
from pathlib import Path

from .pointers import collection, reference
from .prompts import block, render
from .records import reported_attempt, unit_attempts


def _written_at(path):
    """When a file was written, to the second, in UTC; "" when it is not there."""
    try:
        stamp = Path(path).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reported(driver, packet):
    """The report of the attempt under review: the path the record holds, when the file was written and the head it
    claims. No glob over the unit's directory, and no file read under another name as a fallback for it."""
    task = driver.book.task(packet["task_id"])
    path = task.get("report_path")
    if not path:
        return "no report recorded for this attempt yet"
    try:
        path = reference(driver.run_dir, path, "read")
    except ValueError:
        raise ValueError(
            "%s: expected the recorded report file; restore it and retry the review" % path
        ) from None
    head = (task.get("report") or {}).get("head")
    return "%s, written %s, claiming head %s (its status, files, covers, checks)" % (
        path,
        _written_at(path) or "not on disk",
        head or "none",
    )


def review_brief(driver, packet, review_path, delta=None, open_items=None):
    """The reviewer's brief: the paths it works from, the pass in one order, and the schema the verdict is written
    in. Every path is named exactly, so no step sends the reviewer to a file the brief left it to find."""
    checkout = Path(packet["checkout"])
    rules = [str(checkout / n) for n in ("AGENTS.md", "CLAUDE.md") if (checkout / n).exists()]
    unit_dir = driver.run_dir / packet["unit"]
    base = driver.args.base
    if delta:
        range_ = block(
            "review-delta-range",
            checkout=checkout,
            base=base,
            findings=delta.get("findings") or "none",
            since=delta["from"],
        )
        rereview = block("review-rereview", since=delta["from"])
    else:
        range_, rereview = block("review-range", checkout=checkout, base=base), ""
    return render(
        "review",
        unit=packet["unit"].upper(),
        task_md=unit_dir / "task.md",
        reported=_reported(driver, packet),
        checkout=checkout,
        checkout_contents=collection(checkout),
        contract=driver.run_dir / "contract.md",
        plan=driver.plan_path,
        base=base,
        range=range_,
        open_findings=block(
            "review-open-findings",
            ids=", ".join(item["id"] for item in open_items),
            payloads=unit_dir / "open-findings.json",
        )
        if open_items
        else "",
        rules=", ".join(rules) or "none found; follow the code's own conventions",
        rereview=rereview,
        review_path=review_path,
        review_prose=Path(review_path).with_name("review.md"),
    )


def attempt_trail(driver, unit):
    """(last reviewed commit, newest findings.json, newest report.json) over the unit's attempts, read newest first by
    the order the record holds; each answer is the newest attempt that has that thing, and "" when none has it.

    Three unit-scoped questions with one order: the newest attempt that was reviewed, the newest attempt that
    left findings on disk, the newest attempt that reported. An attempt that lacks one of the three does not
    erase it for the unit, and no answer is reconstructed by searching the unit's directory."""
    with driver.book() as data:
        tasks = unit_attempts(data, unit)
        reported = reported_attempt(data, unit)
    reviewed = next((t.get("reviewed_commit") for t in tasks if t.get("reviewed_commit")), "")
    findings = next(
        (
            driver.run_dir / task["packet"]["task_id"] / "findings.json"
            for task in tasks
            if (driver.run_dir / task["packet"]["task_id"] / "findings.json").exists()
        ),
        None,
    )
    return reviewed, str(findings) if findings else "", (reported or {}).get("report_path") or ""
