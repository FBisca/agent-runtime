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
from pathlib import Path
from typing import Any

from .. import contracts, narrate
from ..invariants import next_path
from ..timeutil import iso as now
from .book import ARCHIVE, checkpoint
from .handoff import read_hand_in, read_verdict
from .report import cell
from .state import JsonRefusal, Refused, numbered, write_atomic
from .watch import (
    PAYLOAD_WARNINGS,
    archive_retry_allowed,
    archive_retry_refusal,
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


def _applying(task, gate, drive):
    """What a step was applying, or None when it was not applying a decision and nothing is owed."""
    if task.get("gate") != gate:
        return None
    applying = (task.get("decision") or {}).get("applying")
    if gate == "decided":
        return applying or drive.decided_action(task)
    if gate in ("redo-pending", "fix-pending") and applying:
        return applying
    return None


def _handover_target(task, gate, applying):
    """(worker, from_gate, menu, the way out) the new request names, after a hand-over the pane refused."""
    worker, from_gate, menu = task["packet"]["worker"], None, None
    way_out = "retry it once the pane works, or fresh worker, mark blocked or skip"
    if (
        gate == "decided"
        and applying == "answer"
        and (task.get("request") or {}).get("from_gate") == "reviewing"
        and task.get("review")
    ):
        # The answer was for the reviewer: the new request names it and keeps the review routing, so the Lead's next
        # `answer` reaches the reviewer again, not the implementer.
        worker, from_gate = task["review"]["name"], "reviewing"
        # `retry` and `fresh worker` on this request resume the implementer, not the reviewer: name and offer only
        # the actions that reach the reviewer.
        way_out = "answer again once the pane works, or mark blocked or skip"
        menu = ("answer", "mark blocked", "skip")
    return worker, from_gate, menu, way_out


class Answers:
    """The Lead's requests and the answers they are given, mixed into the Driver.

    A member writes one record - a request, a report, a decision's answer - and asks the driver it
    belongs to for everything else, so a refusal lives where the record it refuses lives.
    """

    def __getattr__(self, name) -> Any:
        """The engine behind this mixin: the record, the plan and the processes are the
        Driver's own, and only what this module owns lives here."""
        raise AttributeError(name)

    def decision_unapplied(self, task_id, gate, error):
        """A step that was applying the Lead's decision failed (the hand-over was refused, e.g. the pane is blocked on
        a login): the unit gets a new request naming the failure and the way out, instead of sitting stuck for the rest
        of the run. A worker message that lands while it is open is held, and the next decision acts on it. False when
        the step was not applying a decision."""
        task = self.task(task_id)
        applying = _applying(task, gate, self)
        if applying is None:
            return False
        worker, from_gate, menu, way_out = _handover_target(task, gate, applying)
        # the wake cuts a reason at 200 characters: the way out comes first, the runtime's words (all in the file) after
        reason = "the Lead's decision (%s) could not reach %s; %s: %s" % (
            applying or "decision",
            worker,
            way_out,
            " ".join(str(error).split())[:600],
        )
        with self.book() as data:
            if "decision" in data["tasks"][task_id]:
                data["tasks"][task_id]["decision"].pop("applying", None)
        self.log(
            "error",
            unit=task["packet"]["unit"],
            task=task_id,
            gate=gate,
            error=str(error)[:500],
        )
        self.request(task_id, "handover", reason, menu=menu, from_gate=from_gate)
        return True

    def decided_action(self, task):
        try:
            return contracts.load(task["decision"]["path"], contracts.validate_decision)["action"]
        except (contracts.ContractError, KeyError, OSError, ValueError):
            return None

    def request(self, task_id, kind, reason, **asks):
        """The unit needs the Lead. A unit that already has an open request keeps it. `menu` narrows
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
        }
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
        with self.book() as data:
            self.book_report(data, task_id, report, **changes)

    @staticmethod
    def book_report(data, task_id, report, **changes):
        # review_rounds is the driver's own count: the worker's value is never trusted.
        checkpoint(
            data,
            task_id,
            dict(report, status="PARTIAL", **dict({"review_rounds": 0}, **changes)),
        )

    def take_report(self, task_id, text):
        """The hand-in one worker's `report` call carried, read the moment it arrives: accepted, or refused back to
        that worker with the exact errors. The Lead is asked only for a BLOCKED report, or when the same worker has
        been refused `PAYLOAD_WARNINGS` times already."""
        report, error = read_hand_in(self.task(task_id), task_id, text)
        unit = self.unit_of(task_id)
        if error:
            self.log("report-refused", unit=unit, task=task_id, reason=error, payload=text)
            return self.refuse_report(task_id, error)
        self.log("report", unit=unit, task=task_id, report=report)
        if report["status"] == "BLOCKED":
            return self.request(
                task_id, "blocked", "%s: %s" % (report["summary"], report["blocker"])
            )
        self.mark(task_id, gate="reported", handed_in=report)

    def refuse_report(self, task_id, error):
        """Tell the worker what its hand-in got wrong, in its own session; a third refusal is the Lead's."""
        refused = int(self.task(task_id).get("refused") or 0)
        if refused >= PAYLOAD_WARNINGS:
            return self.contract_error(
                task_id, "report", "refused %d times: %s" % (refused + 1, error)
            )
        self.mark(task_id, refused=refused + 1)
        return self.nudge(
            task_id,
            "Your hand-in was refused: %s. Fix it and hand it in again: your agent-runtime `report` call "
            "carries the whole report JSON as its text." % error,
        )

    def nudge(self, task_id, text):
        """Put `text` to the task's worker in its own session: the live pane is resumed, a gone one replaced. The
        new assignment is the one whose report the task then waits for."""
        task = self.task(task_id)
        packet = task["packet"]
        name = packet["worker"]
        _, assignment_id = self.ensure_agent(
            name,
            "worker",
            self.unit_of(task_id),
            {
                "kind": "worker",
                "name": name,
                "task": text,
                "cwd": packet["checkout"],
                "cli": packet.get("backend") or self.args.cli,
                "model": packet.get("model"),
                "effort": packet.get("effort"),
            },
            attempt="%s-nudge%d" % (task_id, int(task.get("nudges") or 0)),
        )
        self.nudged = True
        self.mark(task_id, nudges=int(task.get("nudges") or 0) + 1)
        self.mark(task_id, assignment_id=assignment_id, gate=None)

    def take_verdict(self, task_id, text):
        """The verdict one reviewer's `report` call carried, read the moment it arrives: accepted and booked on the
        task, or refused back to that reviewer with the exact errors. The Lead is asked only after
        `PAYLOAD_WARNINGS` refusals. A verdict is the reviewer's reading of the commit the worker reported."""
        task, unit = self.task(task_id), self.unit_of(task_id)
        verdict, error = read_verdict(text)
        if error:
            self.log("review-refused", unit=unit, task=task_id, reason=error, payload=text)
            return self.refuse_verdict(task_id, error)
        self.log("review", unit=unit, task=task_id, commit=task["report"]["head"], **verdict)
        self.mark(task_id, gate="reviewed", verdict=verdict, reviewed_commit=task["report"]["head"])

    def refuse_verdict(self, task_id, error):
        """Tell the reviewer what its verdict got wrong, in its own session; a third refusal is the Lead's."""
        review = self.task(task_id)["review"]
        refused = int(review.get("refused") or 0)
        if refused >= PAYLOAD_WARNINGS:
            return self.contract_error(
                task_id, "review", "verdict refused %d times: %s" % (refused + 1, error)
            )
        self.mark(task_id, review=dict(review, refused=refused + 1))
        return self.resume_reviewer(
            task_id,
            "Your verdict was refused: %s. Fix it and hand it in again: your agent-runtime `report` call "
            "carries the whole verdict JSON as its text." % error,
        )

    def review_ended(self, task_id, reason):
        """The reviewer's turn ended with no verdict: it is told so once, and the Lead is asked when it happens again."""
        review = self.task(task_id)["review"]
        self.log("review-ended", unit=self.unit_of(task_id), task=task_id, reason=reason)
        if review.get("ended"):
            return self.request(task_id, "review", reason)
        self.mark(task_id, review=dict(review, ended=True))
        return self.resume_reviewer(
            task_id,
            "You ended without a verdict; hand it in: your agent-runtime `report` call carries the verdict JSON as "
            "its text.",
        )

    def resume_reviewer(self, task_id, text):
        """Put `text` to the unit's reviewer in its own session (a gone pane is replaced, and told where its brief
        is); the new assignment is the one whose verdict the task then waits for."""
        task = self.task(task_id)
        packet, review = task["packet"], task["review"]
        backend, model = self.gate_step("reviewer", "large")
        sent = int(review.get("sent") or 0) + 1
        _, assignment_id = self.ensure_agent(
            review["name"],
            "reviewer",
            packet["unit"],
            {
                "kind": "worker",
                "name": review["name"],
                "task": "%s Your brief: %s." % (text, Path(review["dir"]) / "task.md"),
                "cwd": packet["checkout"],
                "cli": backend,
                "model": model,
                "effort": self.step_effort("reviewer", "high"),
            },
            attempt="%s-%d" % (Path(review["dir"]).name, sent),
        )
        self.nudged = True
        self.mark(
            task_id,
            gate="reviewing",
            review=dict(self.task(task_id)["review"], assignment_id=assignment_id, sent=sent),
        )

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
        key = str(path) + json.dumps(document.get("clarifications") or [], sort_keys=True)
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
