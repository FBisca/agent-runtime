"""The driver's record helpers: one reading of a record, defined once.

A record is what the engine reads between two steps — a worker's packet, its report, a plan ticket's
row, the runtime's reply, a review receipt. The helpers here answer a question about one without
reading a file, starting a process or changing a ledger, so a collaborator the package is split into
can ask the same question the driver does and get the same answer.

They live beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines, and because a rule its callers share has one home.
"""

import json
import re
from datetime import datetime

from .. import contracts, timeutil
from ..report_rules import require

ROUTE = {
    "size": ("small", "regular", "big"),
    "difficulty": ("simple", "complex"),
    "class": ("small", "medium", "large"),
    "effort": ("low", "medium", "high", "xhigh", "max"),
}


def routed(packet):
    """A packet opts into routing with a cell key; model or effort alone stay legacy metadata."""
    return any(key in packet for key in ("size", "difficulty", "class"))


def whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# A report names each required check as the brief spelled it: the driver briefs workers in any repository and
# behind any shell, so it keeps no list of launchers, wrappers or proxies to strip. Only whitespace is not part
# of a command's spelling.
SPELLING_INSTRUCTION = "Report each required check with its command exactly as written above, whatever your shell runs it through."

# A worker that ran a test directory by hand (`pytest pkg/tests -m integration`) held a ticket for twenty minutes:
# the gate is the cheap tiers by design, and verify and CI own the rest. The driver briefs workers in any
# repository, so the rule names no runner, flag or file of its own: the repository's instructions supply those.
TEST_SCOPE_INSTRUCTION = (
    "Run the checks above, then run only the test files you wrote or changed; leave every other test to verify "
    "and CI. Run each command in the foreground under a timeout, read its failures from that run's output or "
    "log, and re-run only the tests that failed."
)


# What every brief that carries required checks says about running them, after its required-check line.
CHECK_INSTRUCTIONS = "\n".join((SPELLING_INSTRUCTION, TEST_SCOPE_INSTRUCTION))


def normalise_check(command):
    """The command as a report must spell it: its words, whitespace collapsed."""
    return " ".join(command.split())


def unique_checks(commands):
    """Each required command once, in the spelling it was declared: tiers that render the same gate add no second
    requirement, so a worker is never asked to run one command twice, nor told not to run one it must pass."""
    seen, unique = set(), []
    for command in commands:
        key = normalise_check(command)
        if key not in seen:
            seen.add(key)
            unique.append(command)
    return unique


def passed_checks(packet, checks):
    """The required commands one report passed, by their normalised spelling.

    The rendered `affected` check may be `not_run` with a reason: the selection found no test to run. A command
    another tier also requires is never that, so the allowance is only for a tier that stands alone.
    """
    passed = {
        normalise_check(c["command"])
        for c in checks
        if c.get("outcome") == "passed" and isinstance(c.get("command"), str)
    }
    affected = packet.get("affected")
    alone = (
        isinstance(affected, str)
        and [normalise_check(c) for c in packet["checks"]].count(normalise_check(affected)) == 1
    )
    if alone:
        passed |= {
            normalise_check(c["command"])
            for c in checks
            if c.get("outcome") == "not_run"
            and isinstance(c.get("command"), str)
            and isinstance(c.get("reason"), str)
            and c["reason"].strip()
            and normalise_check(c["command"]) == normalise_check(affected)
        }
    return passed


def require_completable(task, report):
    """The rules a report meets to be COMPLETE, apart from the ledger's own state. Raises ValueError naming the first
    that fails; `checkpoint` and the report gate share it, so a report is refused for the same reason in both."""
    # Total over a schema-valid report: the report gate applies `require_report` first, so a malformed `remaining`
    # or check is already refused to its sender and never reaches these rules; `checkpoint`'s own ValueError is
    # still the Lead request `record_report` makes.
    checks = [c for c in report.get("checks") or [] if isinstance(c, dict)]
    require(report.get("acceptance_met") is True, "acceptance criteria are not confirmed")
    require(not report.get("remaining"), "complete result has remaining work")
    failed = [str(c.get("command")) for c in checks if c.get("outcome") == "failed"]
    require(not failed, "complete result has failed checks: " + ", ".join(failed))
    required = {normalise_check(c): c for c in task["packet"]["checks"]}
    passed = passed_checks(task["packet"], checks)
    missing = [command for key, command in required.items() if key not in passed]
    require(
        not missing,
        "required checks are not passed: " + ", ".join(sorted(missing)),
    )
    if routed(task["packet"]):
        require("review_rounds" in report, "a routed task needs review_rounds")
        require(
            isinstance(report.get("usage"), str) and report["usage"].strip(),
            "a routed task needs usage (the runtime's metric line, or unknown)",
        )


def report_reader(task_id):
    """The report one attempt sent, refused when it names another task or is not the complete it claims:
    the words go back to the worker that wrote it, as every payload refusal does."""

    def read(where):
        report = contracts.load(where, contracts.validate_report)
        contracts.need(
            report["task_id"] == task_id,
            "report task_id %r is not %s" % (report["task_id"], task_id),
        )
        contracts.need(
            report["status"] == "COMPLETE" and report["acceptance_met"],
            "the worker said complete but report.json says %s" % report["status"],
        )
        return report

    return read


def context_verdict(sample, reserve, max_age=300):
    """Evaluate reported occupancy only; relevance remains an orchestrator decision."""
    require(isinstance(sample, dict), "context sample must be an object")
    require(reserve >= 0 and max_age > 0, "reserve must be nonnegative; max age must be positive")
    unknown = {"status": "unknown", "reason": "missing, stale, or unsupported context measurement"}
    if (
        sample.get("kind") != "current_context"
        or not sample.get("source")
        or not sample.get("model")
    ):
        return unknown
    try:
        observed = datetime.fromisoformat(sample["observed_at"])
        age = (timeutil.now() - observed).total_seconds()
    except (KeyError, TypeError, ValueError):
        return unknown
    used, window = sample.get("context_tokens"), sample.get("effective_window")
    if (
        type(used) is not int
        or type(window) is not int
        or not 0 <= used <= window
        or window <= 0
        or not 0 <= age <= max_age
    ):
        return unknown
    available = window - used
    return {
        "status": "checkpoint" if reserve >= available else "room_available",
        "remaining_tokens": available,
        "next_task_reserve": reserve,
        "model": sample["model"],
        "source": sample["source"],
        "note": "capacity check only; verify relevance and checkout before reuse",
    }


def split_row(line):
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return None
    return [cell.strip() for cell in line[1:-1].split("|")]


def is_done(ticket):
    return ticket["Status"].strip().upper().startswith("COMPLETE")


def is_agent(ticket):
    return ticket["Owner"].strip().lower() == "agent"


def ready(ticket, by_id, restartable=False):
    """A TO-DO ticket (or an IN PROGRESS one with nothing alive behind it: `restartable`) whose blockers are all
    COMPLETE."""
    return (restartable or ticket["Status"].strip().upper().startswith("TO-DO")) and all(
        is_done(by_id[b])
        for b in re.split(r"[,\s]+", ticket["Blocked by"].strip())
        if b and b.lower() != "none"
    )


def scenario_number(sid):
    """The number in a scenario id (S12 -> 12); 0 when the id carries none."""
    found = re.match(r"S(\d+)", sid)
    return int(found.group(1)) if found else 0


def satisfied(ticket, scenarios):
    """Scenario IDs a ticket makes true; a range covers every ID numbered inside it."""
    found, missing = [], []
    for token in re.split(r"[,\s]+", ticket["Satisfies"].strip()):
        if not token or token.lower() == "none":
            continue
        ends = re.fullmatch(r"S(\d+)[a-z]?[–-]S?(\d+)[a-z]?", token)
        if ends:
            low, high = int(ends.group(1)), int(ends.group(2))
            ids = [sid for sid in scenarios if low <= scenario_number(sid) <= high]
        else:
            ids = [token] if token in scenarios else []
        (found if ids else missing).extend(ids or [token])
    return found, missing


def terminal_gone(error, name):
    """True when the runtime's `unknown` says the terminal does not exist: hc's error kind `not_found`, or its
    `no terminal "<name>"`. Any other error is a read that failed, and says nothing about the terminal."""
    text = str(error or "")
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return body["error"].get("kind") == "not_found"
    return text.strip() == 'no terminal "%s"' % name or "not_found" in text


def assignment_of(result):
    """The assignment id a `spawn` or `resume` answered with: how `wait` names this agent from then on."""
    found = result.get("assignment_id")
    if not isinstance(found, str) or not found:
        raise ValueError(
            "the runtime answered without an assignment_id: %s" % json.dumps(result)[:200]
        )
    return found


def merge_state(view):
    """One word for what a pull request's merge state asks of the driver, in precedence order: merged, closed,
    conflict, unknown, behind, open. A conflict wins over red checks (it can keep CI from starting). A draft reports
    mergeStateStatus DRAFT, which hides BEHIND but not `mergeable: CONFLICTING`: it is open until it is readied."""
    if view.get("error"):
        return "unknown"
    state = str(view.get("state") or "").upper()
    if state in ("MERGED", "CLOSED"):
        return state.lower()
    status = str(view.get("mergeStateStatus") or "").upper()
    mergeable = str(view.get("mergeable") or "").upper()
    if status == "DIRTY" or mergeable == "CONFLICTING":
        return "conflict"
    if status in ("", "UNKNOWN") or mergeable == "UNKNOWN":
        return "unknown"
    if status == "BEHIND":
        return "behind"
    return "open"


def is_flaky(receipt, prior):
    """A FAIL, then a PASS on the same tree, is not a pass (D16): the check is flaky, so nobody can rely on it. Only
    under the same approval digest: an amended contract runs other oracle bytes, and a FAIL under the old ones says
    nothing about the new."""
    return receipt["result"] == "PASS" and any(
        r.get("tree") == receipt["tree"]
        and r.get("digest") == receipt.get("digest")
        and r.get("result") == "FAIL"
        for r in prior
    )


def check_commit(claimed, head, what="review.json"):
    """A claimed commit is at least 7 hex characters and is the checkout's HEAD (or an abbreviation of it)."""
    contracts.need(
        re.fullmatch(r"[0-9a-f]{7,40}", claimed or "") is not None,
        "%s: commit %r is not a sha of at least 7 hex characters" % (what, claimed),
    )
    contracts.need(
        head.startswith(claimed),
        "%s: commit %s is not the checkout's HEAD (%s)" % (what, claimed, head[:12]),
    )


def newest_attempts(tasks):
    """The attempts at one unit, newest first, by the order the record holds: the claim that wrote each one's
    `started_at`.

    Never by the order a dictionary carries them. A ledger read back is a dict of task ids, and that file's key
    order is not a fact a reader may lean on: it survives a re-serialised, merged or hand-edited ledger, where the
    attempts may be inserted in any order at all. `started_at` is the record's own claim of when an attempt was
    made, so the newest attempt is the one the record says was made last, and a stale attempt's report cannot
    stand in for the change under review.
    """
    return sorted(tasks, key=lambda task: str(task.get("started_at") or ""), reverse=True)


def unit_attempts(data, unit):
    """The unit's attempts newest first, breaking equal timestamps by ledger position."""
    tasks = [
        (index, task)
        for index, (tid, task) in enumerate(data["tasks"].items())
        if (task["packet"].get("unit") or tid) == unit
    ]
    tasks.sort(key=lambda pair: (str(pair[1].get("started_at") or ""), pair[0]), reverse=True)
    return [task for _, task in tasks]


def unit_attempt(data, unit):
    """The unit's newest attempt, whatever its ledger status."""
    return next(iter(unit_attempts(data, unit)), None)


def open_attempt(data, unit):
    """The attempt the record's status says is still open: the newest one that is not COMPLETE or RELEASED."""
    return next(
        (
            task
            for task in unit_attempts(data, unit)
            if task.get("status") not in ("COMPLETE", "RELEASED")
        ),
        None,
    )


def reported_attempt(data, unit):
    """The unit's newest report: the newest attempt that has one.

    A unit-scoped question, and the answer is the attempt the record says was made last among those that
    reported. It is not a search for a file: an attempt that never reported contributes nothing, and an
    attempt-scoped reader (which report is this attempt's?) still answers only for its own attempt.
    """
    return next(
        (
            task
            for task in unit_attempts(data, unit)
            if task.get("report_path") or task.get("report")
        ),
        None,
    )
