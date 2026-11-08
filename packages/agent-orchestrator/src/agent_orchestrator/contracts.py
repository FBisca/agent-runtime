"""The agent -> driver handoff files: versioned JSON, validated here with Pydantic v2.

The driver writes contract 2 and still reads contract 1 (`major[]` and `deferred[]` in review.json, text lines in
findings.json), so a run dir left by the previous version keeps working.
The driver reads these and nothing else from an agent: a missing or invalid file counts as no report.

Every model forbids unknown fields at its own level and keeps the fields it is documented to keep
verbatim: a review item's fields outside the core survive a round trip unchanged.

`report.json`   worker: the ledger checkpoint shape plus `head` (commit sha) and `pr` (URL or null); optional
                `responses[] {id, status fixed|disputed, commit, note, changes_code?}` (one per answer_required finding)
                and `clarifications[] {finding, asked_by, note}`.
`review.json`   reviewer: `verdict` pass|block, `items[]`, `checks_run[] {command, exit, at_commit}`, optional
                `clarifications[]`, `commit` reviewed (must equal the checkout's HEAD). An item has the core `id`, `kind`
                (blocking|concern|question|nit|deferred), `summary`, `answer_required`; a blocking item also `file`,
                `line`, `fix` and `evidence {command, exit, excerpt, log_path}`. Any other field is kept verbatim.
                Block iff there is a blocking item.
`findings.json` driver -> fix round: `source`, `attempt`, the blocking and answer_required `items` (converge, CI
                and conflict items carry `kind`, `source`, `check`, `command`, `exit`, `excerpt`, `log_path`) and the
                reviewer's `checks_run`.
`request.json`  driver -> Lead: why a unit needs a decision, the evidence paths and the action menu; `status` open|resolved.
`decision.json` Lead (through `agent-orchestrator decide`): one `action` from the menu, a `reason`, and for `answer` the `answer`.
"""

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

VERSION = 2  # what the driver writes
SUPPORTED = (1, 2)  # what it reads
LIMIT = 65536
STATUSES = ("PARTIAL", "BLOCKED", "COMPLETE")
SOURCES = ("converge", "review", "ci", "conflict")
ACTIONS = (
    "retry",
    "retry one class up",
    "fresh worker",
    "mark blocked",
    "skip",
    "answer",
)  # the menu of a request
MAJOR_FIELDS = ("id", "file", "line", "problem", "fix")  # contract 1
KINDS = ("blocking", "concern", "question", "nit", "deferred")
CORE_FIELDS = ("id", "kind", "summary", "answer_required")
EVIDENCE_FIELDS = ("command", "exit", "excerpt", "log_path")
GATE_ITEM_FIELDS = (
    "kind",
    "source",
    "check",
    "command",
    "exit",
    "excerpt",
    "log_path",
)  # converge and CI findings
RESPONSE_STATUSES = ("fixed", "disputed")

Contract = Literal[1, 2]
Status = Literal["PARTIAL", "BLOCKED", "COMPLETE"]
Kind = Literal["blocking", "concern", "question", "nit", "deferred"]
Source = Literal["converge", "review", "ci", "conflict"]
Action = Literal["retry", "retry one class up", "fresh worker", "mark blocked", "skip", "answer"]
ResponseStatus = Literal["fixed", "disputed"]


class ContractError(ValueError):
    """A handoff file is missing or breaks its contract."""


def need(condition, message):
    if not condition:
        raise ContractError(message)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def whole(value, least=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= least


def versioned(value, what):
    need(isinstance(value, dict), "%s must be a JSON object" % what)
    need(
        value.get("contract") in SUPPORTED,
        "%s: contract must be one of %s" % (what, ", ".join(map(str, SUPPORTED))),
    )


class Strict(BaseModel):
    """A boundary model: unknown fields are an error, and the error names the field."""

    model_config = ConfigDict(extra="forbid")


class Open(BaseModel):
    """A boundary model that keeps the fields it is documented to keep verbatim."""

    model_config = ConfigDict(extra="allow")


class Clarification(Strict):
    finding: str = Field(min_length=1)
    asked_by: str = Field(min_length=1)
    note: str = Field(min_length=1)


class Response(Strict):
    id: str = Field(min_length=1)
    status: ResponseStatus
    note: str = Field(min_length=1)
    commit: str | None = None
    changes_code: bool | None = None


class Check(Open):
    command: str = ""
    outcome: str = ""


class Report(Strict):
    """The worker's report: the ledger checkpoint shape plus the commit and the pull request."""

    contract: Contract
    task_id: str = Field(min_length=1)
    status: Status
    summary: str = Field(min_length=1)
    acceptance_met: bool
    files: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    remaining: list[str] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    head: str = Field(min_length=1)
    pr: str | None = None
    review_rounds: int = 0
    usage: str = "unknown"
    responses: list[Response] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)


class Evidence(Strict):
    command: str = Field(min_length=1)
    exit: int
    excerpt: str
    log_path: str = Field(min_length=1)


class ReviewItem(Open):
    """One review item (contract 2). The core fields are required; the rest is kept verbatim."""

    id: str = Field(min_length=1)
    kind: Kind
    summary: str = Field(min_length=1)
    answer_required: bool


class BlockingReviewItem(Open):
    """A blocking review item: the core fields plus where the fix goes and the evidence."""

    id: str = Field(min_length=1)
    kind: Literal["blocking"]
    summary: str = Field(min_length=1)
    answer_required: bool
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    fix: str = Field(min_length=1)
    evidence: Evidence


class ChecksRun(Strict):
    command: str = Field(min_length=1)
    exit: int
    at_commit: str = Field(min_length=1)


class ReviewV2(Strict):
    contract: Literal[2]
    verdict: Literal["pass", "block"]
    commit: str = Field(min_length=1)
    items: list[ReviewItem] = Field(default_factory=list)
    checks_run: list[ChecksRun] = Field(default_factory=list)
    clarifications: list[Clarification] = Field(default_factory=list)


class MajorItem(Strict):
    """A contract 1 major item: the blocking item of the previous version."""

    id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    problem: str = Field(min_length=1)
    fix: str = Field(min_length=1)


class DeferredItem(Strict):
    id: str = Field(min_length=1)
    note: str = Field(min_length=1)


class ReviewV1(Strict):
    contract: Literal[1]
    verdict: Literal["pass", "block"]
    commit: str = Field(min_length=1)
    major: list[MajorItem] = Field(default_factory=list)
    deferred: list[DeferredItem] = Field(default_factory=list)


class GateItem(Strict):
    """A converge, CI or conflict finding: the evidence the driver filled in."""

    kind: Literal["blocking"]
    source: Source
    check: str = Field(min_length=1)
    command: str = Field(min_length=1)
    exit: int
    excerpt: str
    log_path: str = Field(min_length=1)


class FindingsV2(Strict):
    contract: Literal[2]
    source: Source
    attempt: int = Field(ge=1)
    items: list[GateItem | ReviewItem] = Field(min_length=1)
    checks_run: list[ChecksRun] = Field(default_factory=list)


class FindingsV1(Strict):
    contract: Literal[1]
    source: Source
    attempt: int = Field(ge=1)
    items: list[Any] = Field(min_length=1)


class Decision(Strict):
    contract: Contract
    action: Action
    reason: str = Field(min_length=1)
    answer: str | None = None


class Request(Strict):
    contract: Contract
    unit: str = Field(min_length=1)
    task: str = Field(min_length=1)
    event: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    menu: list[Action] = Field(min_length=1)
    status: Literal["open", "resolved"]
    decision: str | None = None


def _problem(error: ValidationError, what: str) -> str:
    """One deterministic message from a validation failure: the field, then what is wrong with it."""
    first = error.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ())) or what
    kind = first.get("type", "")
    if kind == "missing":
        return "%s: %s is required" % (what, field)
    message = str(first.get("msg", "is invalid"))
    return "%s: %s %s" % (
        what,
        field,
        message[0].lower() + message[1:] if message else "is invalid",
    )


def _check(model, value, what):
    try:
        model.model_validate(value)
    except ValidationError as error:
        raise ContractError(_problem(error, what)) from None
    return value


def validate_report(report):
    versioned(report, "report")
    _check(Report, report, "report")
    for response in report.get("responses", []):
        validate_response(response)
    validate_clarifications(report, "report")
    return report


def validate_response(response):
    need(
        isinstance(response, dict) and text(response.get("id")), "report: each response needs an id"
    )
    where = "report: response %s" % response["id"]
    need(
        response.get("status") in RESPONSE_STATUSES,
        "%s: status must be one of %s" % (where, ", ".join(RESPONSE_STATUSES)),
    )
    need(text(response.get("note")), "%s: note is required" % where)
    need(
        response.get("commit") is None or text(response["commit"]),
        "%s: commit must be a sha or null" % where,
    )
    need(
        "changes_code" not in response or isinstance(response["changes_code"], bool),
        "%s: changes_code must be true or false" % where,
    )
    need(
        response.get("status") != "fixed" or text(response.get("commit")),
        "%s: a fixed response names its commit" % where,
    )


def missing_responses(report, ids):
    """The answer_required ids (in order) that the report has no response for."""
    answered = {r["id"] for r in report.get("responses") or []}
    return [i for i in ids if i not in answered]


def validate_clarifications(value, what):
    clarifications = value.get("clarifications", [])
    need(isinstance(clarifications, list), "%s: clarifications must be a list" % what)
    for entry in clarifications:
        need(
            isinstance(entry, dict)
            and all(text(entry.get(k)) for k in ("finding", "asked_by", "note")),
            "%s: each clarification needs finding, asked_by and note" % what,
        )


def validate_major(item, where):
    need(isinstance(item, dict), "%s: each major item (in items) must be an object" % where)
    for key in MAJOR_FIELDS:
        need(key in item and item[key] not in (None, ""), "%s: major item lacks %s" % (where, key))
    need(
        text(item["id"]) and text(item["file"]) and text(item["problem"]) and text(item["fix"]),
        "%s: major id, file, problem and fix must be text" % where,
    )
    need(whole(item["line"], 1), "%s: major line must be a positive whole number" % where)


def validate_item(item, where, position=0):
    """One review item (contract 2). Every failure names the item and the field."""
    need(isinstance(item, dict), "%s: item #%d must be an object" % (where, position))
    label = "item %s" % item["id"] if text(item.get("id")) else "item #%d" % position
    for key in CORE_FIELDS:
        need(key in item and item[key] not in (None, ""), "%s: %s lacks %s" % (where, label, key))
    need(item["kind"] in KINDS, "%s: %s kind must be one of %s" % (where, label, ", ".join(KINDS)))
    need(
        text(item["id"]) and text(item["summary"]),
        "%s: %s id and summary must be text" % (where, label),
    )
    need(
        isinstance(item["answer_required"], bool),
        "%s: %s answer_required must be true or false" % (where, label),
    )
    if item["kind"] == "blocking":
        for key in ("file", "line", "fix", "evidence"):
            need(
                key in item and item[key] not in (None, ""), "%s: %s lacks %s" % (where, label, key)
            )
        need(
            text(item["file"]) and text(item["fix"]),
            "%s: %s file and fix must be text" % (where, label),
        )
        need(whole(item["line"], 1), "%s: %s line must be a positive whole number" % (where, label))
        evidence = item["evidence"]
        need(isinstance(evidence, dict), "%s: %s evidence must be an object" % (where, label))
        for key in EVIDENCE_FIELDS:
            need(
                key in evidence and evidence[key] is not None,
                "%s: %s evidence lacks %s" % (where, label, key),
            )
        need(
            text(evidence["command"])
            and text(evidence["log_path"])
            and isinstance(evidence["excerpt"], str)
            and isinstance(evidence["exit"], int)
            and not isinstance(evidence["exit"], bool),
            "%s: %s evidence needs command and log_path text, an excerpt and a whole exit"
            % (where, label),
        )


def validate_checks_run(value, what):
    checks = value.get("checks_run", [])
    need(isinstance(checks, list), "%s: checks_run must be a list" % what)
    for run in checks:
        need(isinstance(run, dict), "%s: each checks_run entry must be an object" % what)
        for key in ("command", "exit", "at_commit"):
            need(
                run.get(key) is not None and run.get(key) != "",
                "%s: checks_run entry lacks %s" % (what, key),
            )
        need(
            text(run["command"])
            and text(run["at_commit"])
            and isinstance(run["exit"], int)
            and not isinstance(run["exit"], bool),
            "%s: checks_run needs command, at_commit text and a whole exit" % what,
        )


def validate_review(review):
    versioned(review, "review")
    need(review.get("verdict") in ("pass", "block"), "review: verdict must be pass or block")
    need(text(review.get("commit")), "review: commit (the sha reviewed) is required")
    if review["contract"] == 1:
        return validate_review_v1(review)
    need(isinstance(review.get("items"), list), "review: items must be a list")
    for position, item in enumerate(review["items"], 1):
        validate_item(item, "review", position)
    validate_checks_run(review, "review")
    validate_clarifications(review, "review")
    need(
        (review["verdict"] == "block") == any(i["kind"] == "blocking" for i in review["items"]),
        "review: verdict block needs a blocking item, and pass must have none",
    )
    return review


def validate_review_v1(review):
    need(isinstance(review.get("major"), list), "review: major must be a list")
    need(isinstance(review.get("deferred"), list), "review: deferred must be a list")
    for item in review["major"]:
        validate_major(item, "review")
    for item in review["deferred"]:
        need(
            isinstance(item, dict) and text(item.get("id")) and text(item.get("note")),
            "review: each deferred item needs an id and a note",
        )
    need(
        (review["verdict"] == "block") == bool(review["major"]),
        "review: verdict block needs major items, and pass must have none",
    )
    return review


def review_items(review):
    """A validated review as (blocking items, the other items), verbatim, whatever contract wrote it. In contract 1
    `major[]` are the blocking items and `deferred[]` the others, so a run dir left by the previous version reads the same."""
    if review["contract"] == 1:
        return list(review["major"]), list(review["deferred"])
    return (
        [i for i in review["items"] if i["kind"] == "blocking"],
        [i for i in review["items"] if i["kind"] != "blocking"],
    )


def validate_findings(findings):
    versioned(findings, "findings")
    need(findings.get("source") in SOURCES, "findings: source must be one of " + ", ".join(SOURCES))
    need(whole(findings.get("attempt"), 1), "findings: attempt must be a positive whole number")
    items = findings.get("items")
    need(isinstance(items, list) and items, "findings: items must be a non-empty list")
    if findings["contract"] == 1:
        if findings["source"] == "review":
            for item in items:
                validate_major(item, "findings")
        else:
            need(
                all(text(item) for item in items),
                "findings: items must be non-empty lines of text for " + findings["source"],
            )
        return findings
    validate_checks_run(findings, "findings")
    for position, item in enumerate(items, 1):
        if findings["source"] == "review":
            validate_item(item, "findings", position)
            need(
                item["kind"] == "blocking" or item["answer_required"],
                "findings: item %s is neither blocking nor answer_required" % item["id"],
            )
        else:
            need(isinstance(item, dict), "findings: item #%d must be an object" % position)
            for key in GATE_ITEM_FIELDS:
                need(
                    key in item and item[key] is not None,
                    "findings: item #%d lacks %s" % (position, key),
                )
            need(
                item["kind"] == "blocking" and item["source"] == findings["source"],
                "findings: item #%d must be a blocking item of source %s"
                % (position, findings["source"]),
            )
            need(
                text(item["check"])
                and text(item["command"])
                and text(item["log_path"])
                and isinstance(item["excerpt"], str)
                and isinstance(item["exit"], int),
                "findings: item #%d has a malformed check, command, exit, excerpt or log_path"
                % position,
            )
    return findings


def validate_decision(decision):
    versioned(decision, "decision")
    need(decision.get("action") in ACTIONS, "decision: action must be one of " + ", ".join(ACTIONS))
    need(text(decision.get("reason")), "decision: reason is required")
    need(
        text(decision.get("answer"))
        if decision["action"] == "answer"
        else decision.get("answer") is None,
        "decision: answer is required for the answer action and for no other",
    )
    return decision


def validate_request(request):
    versioned(request, "request")
    for key in ("unit", "task", "event", "reason", "created_at"):
        need(text(request.get(key)), "request: %s is required" % key)
    need(
        isinstance(request.get("evidence"), list)
        and all(text(path) for path in request["evidence"]),
        "request: evidence must be a list of paths",
    )
    need(
        isinstance(request.get("menu"), list)
        and request["menu"]
        and set(request["menu"]) <= set(ACTIONS),
        "request: menu must list actions from " + ", ".join(ACTIONS),
    )
    need(request.get("status") in ("open", "resolved"), "request: status must be open or resolved")
    need(
        (request["status"] == "resolved") == text(request.get("decision")),
        "request: a resolved request names its decision file, an open one has none",
    )
    return request


def make_request(unit, task, event, reason, evidence, created_at=None):
    return validate_request(
        {
            "contract": VERSION,
            "unit": unit,
            "task": task,
            "event": event,
            "reason": reason,
            "evidence": list(evidence),
            "menu": list(ACTIONS),
            "status": "open",
            "decision": None,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
    )


def make_findings(source, attempt, items, checks_run=None, version=VERSION):
    """The findings.json of a fix round. `version=1` writes the previous shape (text lines, or review items without a
    `kind`), for a retry of a round the previous version left in a ledger."""
    row = {"contract": version, "source": source, "attempt": attempt, "items": list(items)}
    if version != 1:
        row["checks_run"] = list(checks_run or [])
    return validate_findings(row)


def legacy_items(items):
    """True for findings items written by contract 1: text lines, or review items without a `kind`."""
    return any(not isinstance(i, dict) or "kind" not in i for i in items)


def load(path, validate):
    """Read one handoff file and validate it; every failure names the file."""
    try:
        with open(path, encoding="utf-8") as stream:
            raw = stream.read(LIMIT + 1)
    except OSError as error:
        raise ContractError("%s: cannot read (%s)" % (path, error.strerror or error)) from None
    need(len(raw) <= LIMIT, "%s: exceeds 64 KiB; keep the handoff compact" % path)
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ContractError("%s: not valid JSON (%s)" % (path, error)) from None
    try:
        return validate(value)
    except ContractError as error:
        raise ContractError("%s: %s" % (path, error)) from None
