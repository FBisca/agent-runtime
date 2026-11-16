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

from .records import newest_attempts


def _written_at(path):
    """When a file was written, to the second, in UTC; "" when it is not there."""
    try:
        stamp = Path(path).stat().st_mtime
    except OSError:
        return ""
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")


def _reported(driver, packet):
    """The report of the attempt under review: the path the record holds, when the file was written and the head it
    claims. No glob over the unit's directory, and no file read under another name as a fallback for it."""
    task = driver.book.task(packet["task_id"])
    path = task.get("report_path")
    if not path:
        return "- What it reported: no report recorded for this attempt yet"
    head = (task.get("report") or {}).get("head")
    return (
        "- What it reported: %s, written %s, claiming head %s (its status, files, covers, checks), beside report.md"
        % (
            path,
            _written_at(path) or "not on disk",
            head or "none",
        )
    )


def review_brief(driver, packet, review_path, delta=None, open_items=None):
    """The reviewer's brief: the paths it works from, the pass in one order, and the schema the verdict is written
    in. Every path is named exactly, so no step sends the reviewer to a file the brief left it to find."""
    checkout = Path(packet["checkout"])
    rules = [str(checkout / n) for n in ("AGENTS.md", "CLAUDE.md") if (checkout / n).exists()]
    return "\n".join(
        [
            "# Review %s: the pass, then one verdict" % packet["unit"].upper(),
            "",
            "Read these yourself; nothing is pasted here.",
            "- The task the implementer was given: %s (its What it delivers, Paths, the scenarios verbatim, "
            "the checks it owed)" % (driver.run_dir / packet["unit"] / "task.md"),
            _reported(driver, packet),
            "- The rules every task in this run follows: %s" % (driver.run_dir / "contract.md"),
            "- Plan: %s (its Decisions, Scenarios and Seams)" % driver.plan_path,
            *(
                ["- Commit range: run `git -C %s diff %s...HEAD`" % (checkout, driver.args.base)]
                if not delta
                else [
                    "- This is a re-review. Only `git -C %s diff %s..HEAD` is new; the whole change is `%s...HEAD`."
                    % (checkout, delta["from"], driver.args.base),
                    "- The earlier findings and the implementer's answers: %s."
                    % (
                        delta.get("findings")
                        or "none; the first review's file is in %s"
                        % (driver.run_dir / packet["unit"])
                    ),
                ]
            ),
            *(
                [
                    "- Open blocking findings, not closed yet: %s. Each stays open until this review closes it "
                    "with a `resolutions` entry, whatever the delta shows; a passing review that leaves one open "
                    "is refused. Their payloads are in %s."
                    % (
                        ", ".join(item["id"] for item in open_items),
                        driver.run_dir / packet["unit"] / "open-findings.json",
                    )
                ]
                if open_items
                else []
            ),
            "- Evidence already in the run: %s (the driver's own runs; do not repeat them)"
            % (driver.run_dir / "receipts"),
            "- Repo rules: %s"
            % (", ".join(rules) or "none found; follow the code's own conventions"),
            "",
            "## The pass, in this order",
            "1. Read the whole diff (`git -C %s diff %s...HEAD`) first: every file, every test it adds or "
            "changes, the commit messages. Nothing is skimmed, and nothing is judged before the last file is "
            "read." % (checkout, driver.args.base),
            *(
                [
                    "   This is a re-review, so read the delta `%s..HEAD` first and the whole change beside it. "
                    "Check each earlier finding (resolved or not, with evidence) and raise new blocking items "
                    "only inside the delta. A blocking item outside the files the delta changes is deferred, not "
                    "gated, unless it is about an earlier finding: reuse that finding's `id`, or set `finding` "
                    "to it." % delta["from"]
                ]
                if delta
                else []
            ),
            "2. The ticket delivers what it says: `What it delivers` in the task file is in the diff, and each "
            "scenario the ticket satisfies is proved by a test the diff adds or changes. A promise the code does "
            "not keep, or a scenario with no test of its own, is `blocking`.",
            "3. Each test proves its scenario: it asserts the behaviour the scenario's THEN promises, on the real "
            "code path rather than on a mock of it, and it names its scenario id. Reverting the change makes it "
            "fail. A test that would pass without the change, or that asserts a call happened instead of what the "
            "behaviour promises, is `blocking`: that is the scenario not really tested.",
            "4. Test cost: the report's `test_cost` is the measurement, not the claim. Run its command (or "
            "`uv run pytest <the diff's test files> --durations=0 -q`) and compare each new or changed test's "
            "seconds and each touched module's seconds with the run before this change. A new or changed test "
            "over 2 s, a module whose seconds grew materially without a new boundary, or a test that re-proves a "
            "guarantee at a costlier level is `blocking`: it slows every later run. Name the node, its seconds "
            "and the cheaper level that keeps the guarantee.",
            "5. The report matches the code: every `covers` node is a test that exists at the commit you review, "
            "every `checks` entry is a command with the outcome the log shows, `head` is the commit you are "
            "reviewing, and `files` covers the diff. A claim the code contradicts is `blocking`.",
            "6. The numbered requirements it touches still hold: read the spec the plan names, and raise the "
            "`requirement_violation` `blocking` item described below when one does not.",
            "7. The code follows the repository's rules: the ones above, its own conventions, no secret in the "
            "diff or a log, and no behaviour nobody asked for (that last one is a `concern`, not a block).",
            "8. Only then judge severity and write the review: everything the pass found, each item once.",
            "",
            "Block only on MAJOR defects: wrong behaviour, data loss, a scenario that is not really tested, or a "
            "test that materially slows the suite. Everything else is a `concern`, `question`, `nit` or "
            "`deferred` item; it never blocks.",
            "A defect that breaks a numbered requirement is a `blocking` item that also asserts "
            "`requirement_violation` {`source`, `id`, `expected`, `observed`}: the spec or plan file, the requirement "
            "id, what it promises and what the code does instead, each nonempty, beside the item's own file, line, "
            "fix and evidence. A rare or post-launch failure is still a violation: rarity does not make it an "
            "observation, and the driver refuses a requirement_violation on a nonblocking item.",
            "Touch nothing in the repository.",
            "Run targeted tests only: name the test files or node ids the change touches. The implementer's checks "
            "and CI already ran the suites; read their results in the report and the pull request's checks.",
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
            'To close a blocking finding an earlier review raised, add it to `resolutions`: `{"id", "status": '
            '"fixed|disproven", "commit" (the commit you reviewed), "note" (what changed, or why the finding was '
            'wrong: nonempty), "evidence" {command, exit 0, excerpt, log_path}}`, and list that same command, its '
            "exit 0 and that commit in `checks_run`. One resolution per id. A resolution for an id the unit never "
            "had, a commit other than the one you reviewed, or evidence that did not pass leaves the finding open.",
            "List in `checks_run` every check you ran, so the implementer does not repeat them. List in `clarifications` "
            "each question you asked the implementer directly.",
            "Then call the agent-runtime `report` tool with kind=complete and one line: `<n> blocking - <the "
            "review file you wrote>`. The file to write is %s." % review_path,
            "",
        ]
    )


def attempt_trail(driver, unit):
    """(last reviewed commit, newest findings.json, newest report.json) over the unit's attempts, read newest first by
    the order the record holds; "" when none yet."""
    reviewed = findings = report = ""
    with driver.book() as data:
        tasks = [t for t in data["tasks"].values() if t["packet"].get("unit") == unit]
    for task in newest_attempts(tasks):
        reviewed = reviewed or task.get("reviewed_commit") or ""
        report = report or task.get("report_path") or ""
        candidate = driver.run_dir / task["packet"]["task_id"] / "findings.json"
        findings = findings or (str(candidate) if candidate.exists() else "")
    return reviewed, findings, report
