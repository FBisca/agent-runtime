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
        return "- What it reported: no report recorded for this attempt yet"
    try:
        path = reference(driver.run_dir, path, "read")
    except ValueError:
        raise ValueError(
            "%s: expected the recorded report file; restore it and retry the review" % path
        ) from None
    head = (task.get("report") or {}).get("head")
    return (
        "- What it reported: %s, written %s, claiming head %s (its status, files, covers, checks)"
        % (
            path,
            _written_at(path) or "not on disk",
            head or "none",
        )
    )


def _paths(driver, packet, delta, open_items):
    """The paths the brief works from, each named exactly: the task, its attempt's report, the run's contract, the
    plan, the evidence already gathered, the repo's own rules — and, on a re-review, the earlier findings."""
    checkout = Path(packet["checkout"])
    rules = [str(checkout / n) for n in ("AGENTS.md", "CLAUDE.md") if (checkout / n).exists()]
    unit_dir = driver.run_dir / packet["unit"]
    return [
        "- The task the implementer was given: %s (its What it delivers, Paths, the scenarios verbatim, "
        "the checks it owed)" % (unit_dir / "task.md"),
        _reported(driver, packet),
        "- Checkout contents: %s" % collection(checkout),
        "- The rules every task in this run follows: %s" % (driver.run_dir / "contract.md"),
        "- Plan: %s (its Decisions, Scenarios and Seams)" % driver.plan_path,
        *(
            ["- Commit range: run `git -C %s diff %s...HEAD`" % (checkout, driver.args.base)]
            if not delta
            else [
                "- This is a re-review. Only `git -C %s diff %s..HEAD` is new; the whole change is `%s...HEAD`."
                % (checkout, delta["from"], driver.args.base),
                "- The earlier findings and the implementer's answers: %s."
                % (delta.get("findings") or "none"),
            ]
        ),
        *(
            [
                "- Open blocking findings, not closed yet: %s. Each stays open until this review closes it "
                "with a `resolutions` entry, whatever the delta shows; a passing review that leaves one open "
                "is refused. Their payloads are in %s."
                % (", ".join(item["id"] for item in open_items), unit_dir / "open-findings.json")
            ]
            if open_items
            else []
        ),
        "- Repo rules: %s" % (", ".join(rules) or "none found; follow the code's own conventions"),
    ]


def _pass(driver, packet, delta):
    """The pass, in one order: the diff, the ticket, each test proved, the report, the requirements, and only
    then the verdict."""
    checkout = Path(packet["checkout"])
    return [
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
        "behaviour promises, is `blocking`: that is the scenario not really tested. The report's `red` holds, per "
        "scenario, the covering test's failing output from before the fix: judge whether it is a genuine "
        "assertion failure of that behaviour. An import or collection error, a missing fixture, or a test "
        "that cannot fail is no red, and is `blocking`.",
        "4. The report matches the code: every `covers` node is a test that exists at the commit you review, "
        "every `checks` entry is a command with the outcome the log shows, `head` is the commit you are "
        "reviewing, and `files` covers the diff. A claim the code contradicts is `blocking`.",
        "5. The numbered requirements it touches still hold: read the spec the plan names, and raise the "
        "`requirement_violation` `blocking` item described below when one does not.",
        "6. Only then write the review: each correctness bug the pass found, once.",
    ]


def _limits():
    """What may block, and what the reviewer may touch: the severity line, the requirement violation, and the
    standing orders that keep the review to the change."""
    return [
        "Report only what a user or caller would hit: wrong behaviour, data loss, a lost wake, a plan or run that cannot finish or restart, a scenario that is not really tested, or a test that would still pass without the change. Each is `blocking`. Style, naming, questions, suggestions and inputs no caller sends (huge numbers, deeply nested data) are left out of the review.",
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
    ]


def _verdict(review_path):
    """The schema the review is written in: contract 2, its items, the resolutions that close an earlier finding,
    the checks it ran, and the one line it reports."""
    return [
        "",
        "Write %s as JSON, contract 2:" % review_path,
        '{"contract":2,"verdict":"pass|block","items":[...],"checks_run":[{"command","exit","at_commit"}],'
        '"clarifications":[{"finding","asked_by":"reviewer","note"}],"commit":"<git rev-parse HEAD of the checkout>"}',
        "Every item has `id`, `kind` (`blocking`), `summary` and `answer_required` "
        "(true only when the implementer must reply). A blocking item also needs `file`, `line`, `fix` and "
        "`evidence` {command, exit, excerpt, log_path}: the command that shows the defect, its exit code, an excerpt "
        "and the path of the full output you saved. `scenario`, `why` and `verify` are optional; any other field you "
        "add is passed on verbatim.",
        'The verdict is "block" exactly when the review has an item; a review with none is "pass". Write optional prose to %s.'
        % Path(review_path).with_name("review.md"),
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


def review_brief(driver, packet, review_path, delta=None, open_items=None):
    """The reviewer's brief: the paths it works from, the pass in one order, and the schema the verdict is written
    in. Every path is named exactly, so no step sends the reviewer to a file the brief left it to find."""
    return "\n".join(
        [
            "# Review %s: the pass, then one verdict" % packet["unit"].upper(),
            "",
            "Read these yourself; nothing is pasted here.",
            *_paths(driver, packet, delta, open_items),
            "",
            "## The pass, in this order",
            *_pass(driver, packet, delta),
            *_limits(),
            *_verdict(review_path),
        ]
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
