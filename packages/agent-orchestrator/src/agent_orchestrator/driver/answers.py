"""The Lead's request, the worker's report and the decision that answers it.

A unit's life with a person is three records: a `request.json` the driver writes once, the report a
worker sends back, and a decision the Lead writes naming an action. This module keeps the three of
them - the one wake per escalation, the payload a report is read from, the answer a decision leaves
alone and the attempt ids it may name - so the refusal a bad request gets and the refusal a foreign
answer gets are read in one place rather than six thousand lines apart.

It lives beside `__init__.py` rather than in it because no module of the package may pass a thousand
lines and no class twenty-five public methods.
"""

import fcntl
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import contracts, narrate
from ..invariants import next_path
from ..timeutil import iso as now
from .book import ARCHIVE, checkpoint
from .report import cell
from .state import JsonRefusal, Refused, numbered, write_atomic
from .watch import (
    PAYLOAD_WARNINGS,
    archive_retry_allowed,
    archive_retry_refusal,
    payload_waits,
    plan_state_of,
)


def _engine():
    """The package this module is split from, asked when a report's run directory is located.

    A test answers `plan_home` by patching `driver.plan_home`; a name bound here
    at import time would resolve the real plan and ignore the seam.
    """
    return sys.modules[__name__.rpartition(".")[0]]


class Unreadable(ValueError):
    """`status` could not say whether an agent is gone."""


class IntentUnsettled(ValueError):
    """A spawn or resume the driver will not send on its own: it may or may not have reached the runtime, or the
    runtime cannot key it. `operations` are the intents that stay open until the Lead decides."""

    def __init__(self, message, operations=()):
        super().__init__(message)
        self.operations = list(operations)


ARCHIVE_MENU = ("retry", "skip")  # the archive has no worker to fix, answer or replace


ARCHIVE_REBUILDS = 2


NO_TEST_MENU = ("amend",) + tuple(action for action in contracts.ACTIONS if action != "amend")


def readable_request(path):
    """The request on disk, or None when it is missing or does not validate: the driver owns the file, so either way
    it writes the file again rather than leave the unit waiting on a decision no `decide` can record."""
    try:
        return contracts.load(path, contracts.validate_request)
    except (OSError, contracts.ContractError):
        return None


def decision_version(path):
    """The contract version a decision file names, or None when it names none or cannot be read."""
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value.get("contract") if isinstance(value, dict) else None


def unreadable_decision(path, error):
    """Why the driver cannot read a decision. A decision in a contract version this driver does not read names the
    reason and the way out: the Lead is asked again, and the restart on the current code is the way out. Any other
    break keeps the raw error."""
    version = decision_version(path)
    if version is not None and version not in contracts.LEAD_SUPPORTED:
        return (
            "the decision is contract %s, which this driver does not read; restart the driver on the current code"
            % version
        )
    return str(error)


FAILURE_LINE = re.compile(r"^(FAILED |ERROR |E\s+\S)|Error\b")


def failure_line(text):
    """The log line that names the failure, so a one-line reason is not just a header: pytest's last
    FAILED/ERROR summary, else its last `E` line, else the last line mentioning an Error."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for prefix in ("FAILED ", "ERROR "):
        found = [line for line in lines if line.startswith(prefix)]
        if found:
            return found[-1]
    found = [line for line in lines if FAILURE_LINE.search(line)]
    return found[-1] if found else ""


ATTEMPT_LETTERS = "rdopa"


def attempt_pattern(unit):
    """The regex of one attempt id of `unit` in free text: the unit's own id, or the unit, a round letter and a
    number. Neither a longer word (`t1apple`) nor a worker name (`demo-t1`) matches."""
    return r"(?<![\w-])%s(?:[%s]\d+)?(?!\w)" % (re.escape(unit), ATTEMPT_LETTERS)


def foreign_attempt(text, unit, expected):
    """The first attempt id of `unit` that `text` names and that is not `expected`, else None, in any case: a worker
    that writes `T1R2` has named the same attempt as `t1r2`."""
    return next(
        (
            m
            for m in re.findall(attempt_pattern(unit), text, re.IGNORECASE)
            if m.lower() != expected.lower()
        ),
        None,
    )


DECISION_KEYS = {"action", "reason", "answer", "contract"}


def read_decision(source):
    """The decision object `decide --json <file|->` was handed: refused, naming what is wrong with it, when it cannot
    be read or is not the object a decision is. The `contract` it names is read with the rest: the request it answers
    decides which version is the right one."""
    try:
        raw = (
            sys.stdin.read(contracts.LIMIT + 1)
            if source == "-"
            else Path(source).read_text(encoding="utf-8")
        )
    except OSError as error:
        raise Refused(
            "cannot read the decision from %s (%s)" % (source, error.strerror or error),
            "give --json a readable file, or - to read the decision from stdin",
        ) from None
    shape = 'a JSON object {"action": ..., "reason": ..., "answer": ...}, `answer` for the answer action only'
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise Refused("the decision is not valid JSON (%s)" % error, "write " + shape) from None
    if not isinstance(value, dict):
        raise Refused("the decision is not a JSON object", "write " + shape)
    unknown = sorted(set(value) - DECISION_KEYS)
    if unknown:
        raise Refused("the decision has unknown fields: %s" % ", ".join(unknown), "write " + shape)
    named = value.get("contract")
    if named is not None and (not isinstance(named, int) or isinstance(named, bool)):
        raise Refused(
            "the decision's contract is not a version",
            "write `contract` as the number of the request's contract version, or leave it out",
        )
    for key in ("action", "reason", "answer"):
        if value.get(key) is not None and not isinstance(value[key], str):
            raise Refused("the decision's %s is not a string" % key, "write " + shape)
    return value


def decide(args):
    """`decide <plan> <unit> <action> --reason R [--answer A]` or `decide <plan> <unit> --json <file|->`: answer the
    unit's open request. The decision file is written and validated first; only then is the request marked resolved, so
    the driver never sees a resolved request without its decision. The JSON form writes what the flag form writes, and
    its refusals are JSON (`JsonRefusal`)."""
    source = getattr(args, "json", None)
    try:
        return decided(args, source)
    except Refused as refused:
        if source:
            raise JsonRefusal(refused) from None
        raise


def _decision_fields(args, source):
    """(action, reason, answer, named contract) a Lead's decision names, refused when it names too little."""
    if not source:
        action, reason, answer = args.action, args.reason, args.answer
        if not action or not reason:
            raise Refused(
                "the decision needs an action and a --reason",
                "run `decide <plan> <unit> <action> --reason <why>`, or `decide <plan> <unit> --json <file|->`",
            )
        return action, reason, answer, None
    if args.action or args.reason or args.answer:
        raise Refused(
            "--json carries the whole decision, and the command line named more",
            "put the action, reason and answer in the JSON, or drop --json and use the flags",
        )
    value = read_decision(source)
    return value.get("action"), value.get("reason"), value.get("answer"), value.get("contract")


def _open_request(args, unit, unit_dir):
    """(requests, request) the unit's newest request, refused when it is not open."""
    requests = numbered(unit_dir, "request", ".json")
    try:
        request = contracts.load(requests[-1], contracts.validate_request) if requests else None
    except contracts.ContractError as error:
        raise Refused(
            str(error), "the driver writes the request again on its next round; decide then"
        ) from None
    if request is None or request["status"] != "open":
        raise Refused(
            "%s has no open request" % unit,
            "a unit has a request only while the driver waits for the Lead: `await %s` names the next one"
            % args.plan,
        )
    return requests, request


def _contract_version(request, named):
    """The contract version the decision is written in, refused when the request does not hold it."""
    try:
        return contracts.answer_version(request, named)
    except contracts.ContractError as error:
        raise Refused(
            str(error),
            "write the decision in the request's contract version (%s), or leave `contract` out"
            % request["contract"],
        ) from None


def _retry_refused(state):
    """The archive's retry: a plan that is not accepted is refused here, and the request stays open."""
    if archive_retry_allowed(state):
        return
    reason, way_out = archive_retry_refusal(state)
    raise Refused(reason, way_out)


def _answer_refused(request, action, answer, unit):
    """An answer that names another attempt than the one the request holds is refused, naming both."""
    if action != "answer" or not answer:
        return
    wrong = foreign_attempt(answer, unit, request["task"])
    if wrong:
        raise Refused(
            "the answer names attempt %r, but the request is for %r and the worker resumes on %r"
            % (wrong, request["task"], request["task"]),
            "name %r, or leave the id out" % request["task"],
        )


def _decision_document(action, reason, answer, version):
    """The decision contract, refused when it does not validate."""
    try:
        return contracts.validate_decision(
            dict(
                {"contract": version, "action": action, "reason": reason},
                **({"answer": answer} if answer else {}),
            )
        )
    except contracts.ContractError as error:
        raise Refused(
            str(error),
            "give a reason, and an answer for the answer action only",
        ) from None


def decided(args, source):
    """`decide`: the Lead's decision written beside the request it answers, under the unit's own lock."""
    action, reason, answer, named = _decision_fields(args, source)
    _, ledger_path, run_dir = _engine().plan_home(args)
    unit = args.unit.lower()
    unit_dir = run_dir / unit
    unit_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(unit_dir / ".decide.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        requests, request = _open_request(args, unit, unit_dir)
        version = _contract_version(request, named)
        menu = contracts.menu_actions(request)
        if action not in menu:
            raise Refused(
                "action %r is not on the menu" % (action,),
                "choose one of: %s" % ", ".join(menu),
            )
        if unit == ARCHIVE and action == "retry":
            _retry_refused(plan_state_of(ledger_path))
        _answer_refused(request, action, answer, unit)
        decision = _decision_document(action, reason, answer, version)
        path = next_path(unit_dir, "decision", ".json")[0]
        write_atomic(path, json.dumps(decision, indent=2) + "\n")
        write_atomic(
            requests[-1],
            json.dumps(dict(request, status="resolved", decision=str(path)), indent=2) + "\n",
        )
    return {
        "unit": unit,
        "action": action,
        "decision": str(path),
        "request": str(requests[-1]),
    }


def _payload_sender(task, where):
    """The record of whoever sent the payload: the reviewer's, or the worker's packet."""
    record = task.get(where) or {}
    if where == "review":
        return record
    return dict(
        task["packet"],
        name=task["packet"]["worker"],
        role="worker",
        assignment_id=task.get("assignment_id"),
    )


def _payload_warnings(task, where):
    """How many times the driver already asked this sender for this payload."""
    return int((task.get("payload_warnings") or {}).get(where) or 0)


class Answers:
    """The Lead's requests and the answers they are given, mixed into the Driver.

    A member writes one record - a request, a report, a decision's answer - and asks the driver it
    belongs to for everything else, so a refusal lives where the record it refuses lives.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def request(self, task_id, kind, reason, **asks):
        """The unit needs the Lead. A unit that already has an open request keeps it. `answerer` is the assignment of
        a busy answerer the request names: only a decision on such a request replaces that assignment. `menu` narrows
        the actions the Lead may take, and `intent` names the intents the decision settles."""
        with self.book() as data:
            unit = self.book_request(data, task_id, kind, reason, **asks)
        if unit is not None:
            self.announce_request(task_id, kind, reason, unit)

    def book_request(self, data, task_id, kind, reason, **asks):
        """The ledger half of `request`, for a caller that books it with other changes in one write: the unit's
        id, or None when the unit already has an open request."""
        task = data["tasks"][task_id]
        if task.get("gate") == "requested":
            return None
        unit = task["packet"]["unit"]
        path, _ = next_path(self.run_dir / unit, "request", ".json")
        asked = {
            "path": str(path),
            "kind": kind,
            "from_gate": asks.get("from_gate") or task.get("gate"),
            # The approval the request was raised under. A park names this one, not whatever the drive holds when the
            # Lead decides `amend`: an amendment the drive adopts between the two must still release the unit, and a
            # park of the held approval can never be released.
            "digest": self.approval.digest if self.approval else None,
        }
        if asks.get("answerer"):
            asked["answerer"] = asks["answerer"]
        if asks.get("menu"):
            asked["menu"] = list(asks["menu"])
        if asks.get("intent"):
            asked["intent"] = list(asks["intent"])
        task.update(
            event={"at": now(), "kind": kind, "reason": reason},
            gate="requested",
            updated_at=now(),
            request=asked,
        )
        if asks.get("fix"):
            task["fix"] = asks["fix"]
        return unit

    def announce_request(self, task_id, kind, reason, unit):
        """What follows the booking of a request: the pane, the log, the ticket's status, the file and the wake. A
        crash before the file is on disk is repaired by `step_requested`."""
        narrate.say("ask", "decide", "Lead decision needed (%s): %s" % (kind, reason), task=task_id)
        self.log("escalation", unit=unit, task=task_id, cause=kind, reason=reason[:500])
        self.write_status(unit.upper(), "IN PROGRESS · decision pending: " + cell(reason))
        self.write_request(task_id)

    def write_request(self, task_id):
        """Write the request file of a `requested` task and its wake. It is written again when it is missing (the driver
        died before it was on disk) or unreadable (a crash left it empty), so the Lead can always `decide`."""
        task = self.task(task_id)
        unit, path = task["packet"]["unit"], Path(task["request"]["path"])
        if readable_request(path) is None:
            candidates = [
                self.run_dir / unit / "task.md",
                self.run_dir / task_id / "report.json",
                Path((task.get("review") or {}).get("path") or "/nonexistent"),
                self.run_dir / task_id / "findings.json",
                self.run_dir / "events.jsonl",
            ]
            row = contracts.make_request(
                unit,
                task_id,
                task["event"]["kind"],
                task["event"]["reason"],
                [str(c) for c in candidates if c.exists()],
                menu=task["request"].get("menu") or (ARCHIVE_MENU if unit == ARCHIVE else None),
                # An intent request keeps the string menu (contract 2): its readers compare the two action names.
                typed=task["event"]["kind"] != "intent",
            )
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_atomic(path, json.dumps(row, indent=2) + "\n")
        self.wake(
            "decision",
            unit,
            "decision %s: %s -> %s" % (unit, cell(task["event"]["reason"], 200), path),
            key=str(path),
        )

    def close_request(self, task_id, reason):
        """The unit no longer needs the Lead: its open request is resolved, with the decision that says why."""
        task = self.task(task_id)
        request = task.get("request")
        if not request:
            return
        path = Path(request["path"])
        if not path.exists():
            return
        document = contracts.load(path, contracts.validate_request)
        if document["status"] != "open":
            return
        decision_path = next_path(path.parent, "decision", ".json")[0]
        decision = contracts.validate_decision(
            {"contract": document["contract"], "action": "skip", "reason": reason}
        )
        write_atomic(decision_path, json.dumps(decision, indent=2) + "\n")
        write_atomic(
            path,
            json.dumps(dict(document, status="resolved", decision=str(decision_path)), indent=2)
            + "\n",
        )

    def hold(self, task_id, assignment_id, kind, text):
        """A worker message that arrives while its unit has an open request is kept on the task and acted on after the
        decision. The event that caused the request, and a repeat of a held message, are not held."""
        with self.book() as data:
            task = data["tasks"][task_id]
            held = task.setdefault("held", [])
            if kind == task["event"]["kind"] or any(
                h["assignment_id"] == assignment_id and h["kind"] == kind for h in held
            ):
                return
            held.append({"at": now(), "kind": kind, "assignment_id": assignment_id, "text": text})
            unit = task["packet"]["unit"]
        self.log(
            "held",
            unit=unit,
            task=task_id,
            held=kind,
            assignment_id=assignment_id,
            text=cell(text, 200),
        )

    def replay_held(self, task, successor_id):
        """After a decision handed the unit on: a held blocked or decision message that the Lead has not seen becomes
        a request on the unit's new task. A held complete never gets here (`step_decided` takes it first)."""
        for held in task.get("held") or []:
            if held["kind"] in ("blocked", "decision"):
                self.log(
                    "held-acted",
                    unit=task["packet"]["unit"],
                    task=successor_id,
                    held=held["kind"],
                    acted="request",
                )
                self.request(
                    successor_id,
                    held["kind"],
                    "%s (arrived while the earlier request was open)"
                    % (held["text"] or "worker " + held["kind"]),
                )

    def record_report(self, task_id, report, **changes):
        """Checkpoint the worker's report as PARTIAL: the unit is not COMPLETE until CI and the merge."""
        path = self.report_file(task_id)
        with self.book() as data:
            self.book_report(data, task_id, report, path, **changes)

    @staticmethod
    def book_report(data, task_id, report, path, **changes):
        # review_rounds is the driver's own count: the worker's value is never trusted.
        checkpoint(
            data,
            task_id,
            dict(report, status="PARTIAL", **dict({"review_rounds": 0}, **changes)),
        )
        data["tasks"][task_id]["report_path"] = str(path)

    def report_file(self, task_id):
        """The newest report in a task directory. Files are write-once: a later attempt writes report.2.json."""
        directory, newest = self.run_dir / task_id, "report.json"
        for index in range(2, 1000):
            if not (directory / ("report.%d.json" % index)).exists():
                break
            newest = "report.%d.json" % index
        return directory / newest

    def next_report(self, task_id):
        """The file a worker owes next: the first write-once report name the task directory does not hold yet. A
        worker asked again writes report.2.json; the newest file on disk is one it already wrote."""
        return next_path(self.run_dir / task_id, "report", ".json")[0]

    def read_payload(self, task_id, where, path, load, alternates=()):
        """The payload a report named, read and validated; None when the agent that sent it still owes it.

        A worker's report can reach the driver before the file it announces, and a file can be half written when
        it does. The report is the news and the file is the payload, so a payload that is missing or unusable is
        not a verdict: the sending agent is told once or twice, and only then the Lead is asked. A file that never
        arrives while nobody is left to write it goes straight to the Lead: warning a dead agent buys nothing.

        The named path is authoritative. Alternatives cannot substitute for it, and multiple files are
        ambiguous: ask the sender to resolve the names instead of selecting one by existence or order.
        """
        candidates = [path, *[a for a in alternates if a]]
        payload_waits(candidates)
        present = [p for p in candidates if Path(p).is_file()]
        if len(present) > 1:
            self.warn_sender(
                task_id,
                where,
                path,
                "multiple files match %s; keep only the expected file and report again"
                % ", ".join(map(str, present)),
            )
            return None
        if not Path(path).is_file():
            self.warn_sender(task_id, where, path, "expected this file; write it and report again")
            return None
        try:
            return load(path)
        except (contracts.ContractError, ValueError) as error:
            self.warn_sender(task_id, where, path, str(error))
            return None

    def warn_sender(self, task_id, where, path, reason, accept=False):
        """Tell the agent that sent the report what the driver still needs from it. False when the Lead must ask.

        A payload that agent can still fix is its own news; a payload from an agent that is gone, or one it was
        already warned about `PAYLOAD_WARNINGS` times, belongs to the Lead. `accept=True` is a file that reads but
        cannot be accepted as it stands, so the warning says so rather than calling it unreadable.
        """
        verb = "accept" if accept else "read"
        task, unit = self.task(task_id), self.unit_of(task_id)
        sender = _payload_sender(task, where)
        warned = _payload_warnings(task, where)
        if warned >= PAYLOAD_WARNINGS or not sender.get("assignment_id"):
            return self._payload_given_up(task_id, where, path, verb, reason, warned)
        name = sender.get("name") or sender.get("worker")
        if not name or not self.agent_live(name):
            return self._payload_orphaned(task_id, where, path, verb, reason)
        asked = {"where": where, "path": path, "verb": verb, "reason": reason, "name": name}
        return self._payload_warned(task_id, unit, asked, sender, warned, task)

    def _payload_given_up(self, task_id, where, path, verb, reason, warned):
        """The agent was asked `PAYLOAD_WARNINGS` times or holds no assignment: the Lead is asked instead."""
        self.contract_error(
            task_id,
            where,
            "cannot %s %s: %s, after %d warning(s) to the agent that sent the report"
            % (verb, path, reason, warned),
        )
        return False

    def _payload_orphaned(self, task_id, where, path, verb, reason):
        """No named agent is live to write the file: the unit's request names the file instead."""
        self.contract_error(
            task_id,
            where,
            "cannot %s %s: %s, and no agent left to write it" % (verb, path, reason),
        )
        return False

    def _payload_warned(self, task_id, unit, asked, sender, warned, task):
        """Ask the agent that sent the payload for it again, as a new assignment, and count the warning."""
        where, path, name, reason = (
            asked["where"],
            asked["path"],
            asked["name"],
            asked["reason"],
        )
        assignment_id = self._warn_spawn(task_id, unit, asked, sender, task)
        # The warned agent comes back as a new assignment: the record the next report is matched against must be
        # the one that will speak, or the report lands on no gate at all.
        if where == "review" and assignment_id:
            self.mark(task_id, review=dict(task["review"], assignment_id=assignment_id))
        elif assignment_id:
            self.mark(task_id, assignment_id=assignment_id)
        self.mark(
            task_id,
            payload_warnings=dict(task.get("payload_warnings") or {}, **{where: warned + 1}),
        )
        self.mark(task_id, gate="reviewing" if where == "review" else None)
        self.log(
            "payload-warning",
            unit=unit,
            task=task_id,
            where=where,
            warnings=warned + 1,
            reason=reason[:200],
        )
        narrate.say(
            "warn",
            "review" if where == "review" else "build",
            "asked %s to fix %s: %s" % (name, path, reason),
            task=task_id,
        )
        return True

    def _warn_spawn(self, task_id, unit, asked, sender, task):
        """The warned agent's new assignment, in the checkout and on the route it was already running."""
        where, path, name = asked["where"], asked["path"], asked["name"]
        text = (
            "The driver cannot %s %s: %s. Write or repair that file, then report complete under this assignment."
            % (asked["verb"], path, asked["reason"])
        )
        _, assignment_id = self.ensure_agent(
            name,
            sender.get("role") or ("reviewer" if where == "review" else "worker"),
            unit,
            {
                "kind": "worker",
                "name": name,
                "task": text,
                "cwd": sender.get("checkout") or task["packet"]["checkout"],
                "cli": sender.get("backend") or self.args.cli,
                "model": sender.get("model") or task["packet"].get("model"),
                "effort": sender.get("effort") or task["packet"].get("effort"),
            },
            attempt=task_id,
        )
        return assignment_id

    def contract_error(self, task_id, where, error):
        """A handoff file broke its contract: log it, and the Lead decides."""
        self.log(
            "contract-error",
            unit=self.unit_of(task_id),
            task=task_id,
            where=where,
            error=str(error)[:500],
        )
        return self.request(task_id, where, str(error))

    def log_clarifications(self, task_id, source, document, path):
        """One `clarification` event per entry the agent listed; a file is read once, however often its step runs."""
        key = str(path)
        if key in (self.task(task_id).get("clarified") or []):
            return
        for entry in document.get("clarifications") or []:
            self.log(
                "clarification",
                unit=self.unit_of(task_id),
                task=task_id,
                source=source,
                finding=entry["finding"],
                asked_by=entry["asked_by"],
                note=cell(entry["note"], 300),
            )
        with self.book() as data:
            data["tasks"][task_id].setdefault("clarified", []).append(key)

    def resume_for_responses(self, task, directory_id, report_path, missing):
        """Resume the task's worker once with the ids it left unanswered; the new report goes to the next report file."""
        directory = self.run_dir / directory_id
        brief = next_path(directory, "missing", ".md")[0]
        brief.write_text(
            "\n".join(
                [
                    "# Missing responses for %s" % directory_id,
                    "",
                    "Your report %s has no response for the answer_required finding(s): %s."
                    % (report_path, ", ".join(missing)),
                    "Write %s: a full report (same shape, task_id `%s`) whose `responses` answer them, each "
                    '`{"id", "status": "fixed|disputed", "commit", "note", "changes_code"?}`.'
                    % (next_path(directory, "report", ".json")[0], directory_id),
                    "Change nothing else.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        text = "Read %s and follow it." % brief
        return self.hand_over(task, dict(task["packet"], task_id=directory_id), brief, text)[1]

    def chase_responses(self, task_id, path, missing):
        """A fix-round report lacks a response the reviewer asked for. The first time, resume the worker with the
        missing ids at no round cost; the next report that still lacks them is a request."""
        task = self.task(task_id)
        message = "%s has no response for answer_required finding(s) %s" % (
            path,
            ", ".join(missing),
        )
        if task.get("chased"):
            return self.contract_error(
                task_id, "responses", "the next report still lacks it: " + message
            )
        self.log(
            "contract-error",
            unit=self.unit_of(task_id),
            task=task_id,
            where="responses",
            error=message[:500],
            missing=missing,
        )
        assignment_id = self.resume_for_responses(task, task_id, path, missing)
        self.mark(task_id, gate=None, assignment_id=assignment_id, chased=missing)

    def report_landed(self, task_id):
        """The worker's report is on disk, it names this task, it says it finished and it belongs to this attempt:
        the turn ended after the report was written, so the driver reads the report instead of asking a human."""
        task = self.task(task_id)
        path = self.report_file(task_id)
        try:
            report = contracts.load(path, contracts.validate_report)
        except (contracts.ContractError, OSError, ValueError):
            return False
        if report["task_id"] != task_id or report.get("status") != "COMPLETE":
            return False
        if report.get("acceptance_met") is not True:  # the same gate the reported path applies
            return False
        return self.belongs_to_attempt(task, path)

    def belongs_to_attempt(self, task, path):
        """The report was written after the task was claimed: a report left over from an earlier attempt is not this
        worker's news."""
        claimed = max(filter(None, (task.get("started_at"), task.get("answered_at"))), default=None)
        if not claimed:
            return True
        try:
            moment = datetime.fromisoformat(claimed)
        except ValueError:
            return True
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        try:
            return path.stat().st_mtime >= moment.timestamp()
        except OSError:
            return False

    def report_is_valid(self, task_id):
        try:
            report = contracts.load(self.report_file(task_id), contracts.validate_report)
        except contracts.ContractError:
            return False
        return report["task_id"] == task_id

    def start_answers(self, task_id, asks, review):
        task = self.task(task_id)
        packet, unit = task["packet"], task["packet"]["unit"]
        if (task.get("answers") or {}).get("review") == task["review"]["path"]:
            return  # the driver died after starting them
        number = next(
            n for n in range(1, 1000) if not (self.run_dir / ("%sa%d" % (unit, n))).exists()
        )
        answer_id = "%sa%d" % (unit, number)
        directory = self.run_dir / answer_id
        directory.mkdir(exist_ok=True, mode=0o700)
        findings_path = directory / "findings.json"
        write_atomic(
            findings_path,
            json.dumps(
                contracts.make_findings("review", number, asks, review.get("checks_run") or []),
                indent=2,
            )
            + "\n",
        )
        task_md = directory / "task.md"
        task_md.write_text(
            self._answers_brief(task, answer_id, asks, findings_path), encoding="utf-8"
        )
        text = (
            "Read %s and follow it. Answers only: the review passed; nothing else changes."
            % task_md
        )
        _, assignment_id = self.hand_over(task, dict(packet, task_id=answer_id), task_md, text)
        self.log(
            "answers", unit=unit, task=task_id, answer_task=answer_id, ids=[i["id"] for i in asks]
        )
        self.mark(
            task_id,
            answers={
                "id": answer_id,
                "dir": str(directory),
                "assignment_id": assignment_id,
                "ids": [i["id"] for i in asks],
                "state": "working",
                "tries": 0,
                "review": task["review"]["path"],
                "findings": str(findings_path),
            },
        )
