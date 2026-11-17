"""The reviewer's packet: the brief a review is read from, and the trail it is given.

A review is opened on one attempt's report, so the brief carries that report's content instead of a glob
over the unit's directory, which a stale attempt answers once the unit has been re-dispatched. The trail behind the review is read in the order
the record holds, so the newest attempt is the one the record says was made last.

They live beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because the one reading of "which report is this" has one home.
"""

import json
from pathlib import Path

from .pointers import collection, rulebook
from .prompts import block, render
from .records import reported_attempt, unit_attempts


def _reported(driver, packet):
    """The report of the attempt under review, in full: the worker handed it in through the runtime, so there is no
    file to name. The driver's own checkpoint of it is the record (its status reads PARTIAL until the merge)."""
    report = driver.book.task(packet["task_id"]).get("report")
    if not report:
        return "no report recorded for this attempt yet"
    return "```json\n%s\n```" % json.dumps(report, indent=2)


def review_brief(driver, packet):
    """The first review's brief: what the reviewer reads, the pass in one order and the verdict it hands in. Every
    path is named exactly, so no step sends the reviewer to a file the brief left it to find."""
    checkout = Path(packet["checkout"])
    rules = [str(checkout / n) for n in ("AGENTS.md", "CLAUDE.md") if (checkout / n).exists()]
    book = rulebook(driver.repo, checkout)
    return render(
        "review",
        unit=packet["unit"].upper(),
        task_md=driver.run_dir / packet["unit"] / "task.md",
        reported=_reported(driver, packet),
        checkout=checkout,
        checkout_contents=collection(checkout),
        plan=driver.plan_path,
        base=driver.args.base,
        head=driver.book.task(packet["task_id"])["report"]["head"],
        rulebook=block("review-rulebook", rulebook=book) if book else "",
        rules=", ".join(rules) or "none found; follow the code's own conventions",
    )


def rereview_text(driver, packet, delta, brief):
    """What the reviewer that still holds the change is told: the fix diff, the worker's report and what the fix was
    asked to answer. A re-review never re-reads the whole change."""
    task = driver.book.task(packet["task_id"])
    return render(
        "rereview",
        unit=packet["unit"].upper(),
        checkout=packet["checkout"],
        since=delta["from"],
        head=task["report"]["head"],
        reported=_reported(driver, packet),
        findings=delta.get("findings") or "none",
        earlier=block("rereview-earlier") if delta.get("blocked") else "",
        brief=brief,
    )


def attempt_trail(driver, unit):
    """(last reviewed commit, newest findings.json, newest report) over the unit's attempts, read newest first by
    the order the record holds; each answer is the newest attempt that has that thing, and "" (a report: None) when none has it.

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
    return reviewed, str(findings) if findings else "", (reported or {}).get("report")
