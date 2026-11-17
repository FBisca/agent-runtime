"""The agent -> driver handoff files: versioned JSON, validated here with Pydantic v2.

The driver writes contract 2 (a request and a decision are contract 3).
The driver reads these and nothing else from an agent: a missing or invalid file counts as no report.

Every model forbids unknown fields at its own level and keeps the fields it is documented to keep
verbatim.

The report   worker: the text of its agent-runtime `report` call, no file: the ledger checkpoint shape plus `head` (commit sha) and `pr` (URL or null); optional
                `clarifications[] {finding, asked_by, note}`. Contract 3 is a YAML plan ticket's report: it adds
                `covers {S<n>: [test node ids]}`, the scenarios the ticket satisfies mapped to the tests that prove
                them, and `red {S<n>: excerpt}`, each covering test's failing output from before the fix; verify
                counts a node when its file exists, the ticket changed that file and the scenario's excerpt
                contains the test's name. `status` is COMPLETE (optional `notes[]`, kept and never blocking) or
                BLOCKED (`blocker`, the reason, goes to the Lead).
The verdict    reviewer: the text of its agent-runtime `report` call, no file: `{"verdict": "pass"|"block", "findings": [{file,
                line, quote, claim, rule?}]}`. `rule` is the bold title of the rulebook rule the finding breaks, absent for
                the reviewer's own judgment; the driver only records it. Block iff there is a finding.
`findings.json` driver -> fix round: `source`, `attempt`, and the `items` the worker fixes: a review finding (`id`, `kind`,
                `source` review, `file`, `line`, `quote`, `claim`, `rule?`) or a converge, verify, CI or conflict item (`kind`,
                `source`, `check`, `command`, `exit`, `excerpt`, `log_path`).
`request.json`  driver -> Lead: why a unit needs a decision, the evidence paths and the action menu; `status` open|resolved.
                Contract 3: each menu option is `{action, summary, inputs}`, `inputs` mapping an input name to
                `{type, required}`, so an agent Lead reads what it may do and what each action takes. Contracts 1 and 2
                list the action names as strings; they are still read.
`decision.json` Lead (through `agent-orchestrator decide`): one `action` from the menu, a `reason`, and for `answer` the
                `answer`. It carries the contract version of the request it answers, so the driver that wrote the
                request reads it; contracts 1 and 2 are still read.
"""

import json
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

VERSION = 2  # what the driver writes
SUPPORTED = (2,)  # what it reads
LIMIT = 65536
STATUSES = ("PARTIAL", "BLOCKED", "COMPLETE")
REPORT_SUPPORTED = (1, 2, 3)  # a report may also be contract 3 (`covers`); no other file is
SOURCES = ("converge", "verify", "review", "ci", "conflict")
ACTIONS = (
    "retry",
    "retry one class up",
    "fresh worker",
    "mark blocked",
    "skip",
    "answer",
)  # the menu of a request
LEAD_VERSION = 3  # what the driver writes for a request and a decision
LEAD_SUPPORTED = (1, 2, 3)  # what it reads of them
SUMMARIES = {
    "retry": "Hand the unit back to its worker for another attempt in the same checkout.",
    "retry one class up": "Retry on the next model class up; at the top class, with more effort.",
    "fresh worker": "Retry with a new worker instead of resuming the old pane.",
    "mark blocked": "Park the unit as blocked until a person sets its Status to TO-DO.",
    "skip": "Release the unit without building it.",
    "answer": "Answer what the worker asked; the same worker resumes with your answer.",
}  # what each action does, as the typed menu says it
GATE_ITEM_FIELDS = (
    "kind",
    "source",
    "check",
    "command",
    "exit",
    "excerpt",
    "log_path",
)  # converge, verify and CI findings

Contract = Literal[1, 2]
LeadContract = Literal[1, 2, 3]
Status = Literal[
    "PARTIAL", "BLOCKED", "COMPLETE"
]  # the ledger's: a reported unit is PARTIAL until it merges
ReportStatus = Literal["BLOCKED", "COMPLETE"]  # a hand-in's: finished, or stuck with a reason
Source = Literal["converge", "verify", "review", "ci", "conflict"]
ReportContract = Literal[1, 2, 3]
Action = Literal["retry", "retry one class up", "fresh worker", "mark blocked", "skip", "answer"]


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


class Check(Open):
    command: str = ""
    outcome: str = ""


class Report(Strict):
    """The worker's report: the ledger checkpoint shape plus the commit and the pull request."""

    contract: ReportContract
    task_id: str = Field(min_length=1)
    status: ReportStatus
    summary: str = Field(min_length=1)
    acceptance_met: bool
    files: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    # What a COMPLETE report records without blocking: the driver keeps it and lists it at plan close.
    notes: list[str] = Field(default_factory=list)
    # Why a BLOCKED report stopped; it goes to the Lead.
    blocker: str | None = None
    checks: list[Check] = Field(default_factory=list)
    head: str = Field(min_length=1)
    pr: str | None = None
    review_rounds: int = 0
    usage: str = "unknown"
    clarifications: list[Clarification] = Field(default_factory=list)
    # CONTRACT asks for it: the worker's context telemetry, `{"status": "unknown"}` when it has none.
    context: dict[str, Any] | None = None
    # Contract 3: each scenario the ticket satisfies, mapped to the test nodes (`path::name`) that prove it.
    covers: dict[str, list[str]] | None = None
    # Contract 3: each scenario's covering test seen failing before the fix, as the runner printed it.
    red: dict[str, str] | None = None

    @model_validator(mode="after")
    def blocked_names_its_blocker(self):
        if self.status == "BLOCKED" and not (self.blocker and self.blocker.strip()):
            raise ValueError("a BLOCKED report needs a blocker: why it stopped")
        return self


class ReviewFinding(Strict):
    """One finding of a reviewer's verdict: where, the line it quotes, what is wrong, and the rulebook rule it breaks."""

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    quote: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    rule: str | None = None


class Verdict(Strict):
    """The reviewer's hand-in: it blocks exactly when it names a finding."""

    verdict: Literal["pass", "block"]
    findings: list[ReviewFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def blocks_exactly_when_it_finds(self):
        if (self.verdict == "block") != bool(self.findings):
            raise ValueError("verdict block needs at least one finding, and pass has none")
        return self


class ReviewItem(ReviewFinding):
    """A review finding as a fix round carries it, with the id the worker answers to."""

    id: str = Field(min_length=1)
    kind: Literal["blocking"]
    source: Literal["review"]


class GateItem(Strict):
    """A converge, verify, CI or conflict finding: the evidence the driver filled in."""

    id: str = Field(min_length=1)
    kind: Literal["blocking"]
    source: Literal["converge", "verify", "ci", "conflict"]
    check: str = Field(min_length=1)
    command: str = Field(min_length=1)
    exit: int
    excerpt: str
    log_path: str = Field(min_length=1)


class Findings(Strict):
    contract: Literal[2]
    source: Source
    attempt: int = Field(ge=1)
    items: list[Annotated[GateItem | ReviewItem, Field(discriminator="source")]] = Field(
        min_length=1
    )


class Decision(Strict):
    contract: LeadContract
    action: Action
    reason: str = Field(min_length=1)
    answer: str | None = None


class Input(Strict):
    type: Literal["string"]
    required: bool


class MenuOption(Strict):
    action: Action
    summary: str = Field(min_length=1)
    inputs: dict[str, Input]


class Request(Strict):
    contract: LeadContract
    unit: str = Field(min_length=1)
    task: str = Field(min_length=1)
    event: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    menu: list[Action] | list[MenuOption] = Field(min_length=1)
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


def validate_covers(report):
    """Contract 3 carries `covers {S<n>: [node, ...]}` and may carry `red {S<n>: excerpt}`; no other contract does.
    Whether `red` is there for every scenario is verify's to say, as a fix round."""
    covers = report.get("covers")
    if report["contract"] != 3:
        for field in ("covers", "red"):
            need(
                report.get(field) is None,
                "report: %s needs contract 3, this report is contract %s"
                % (field, report["contract"]),
            )
        return
    red = report.get("red")
    need(
        red is None or isinstance(red, dict),
        "report: red must map each scenario id to its failing output",
    )
    for scenario in red or {}:
        need(
            isinstance(scenario, str) and re.fullmatch(r"S\d+[a-z]?", scenario),
            "report: red key %r is not a scenario id like S3" % (scenario,),
        )
    need(isinstance(covers, dict), "report: contract 3 needs covers {S<n>: [test node ids]}")
    for scenario, nodes in covers.items():
        need(
            isinstance(scenario, str) and re.fullmatch(r"S\d+[a-z]?", scenario),
            "report: covers key %r is not a scenario id like S3" % (scenario,),
        )
        need(
            isinstance(nodes, list) and nodes and all(text(node) for node in nodes),
            "report: covers %s must list at least one test node id, each a non-empty string"
            % scenario,
        )


def validate_report(report):
    need(isinstance(report, dict), "report must be a JSON object")
    need(
        report.get("contract") in REPORT_SUPPORTED,
        "report: contract must be one of %s" % ", ".join(map(str, REPORT_SUPPORTED)),
    )
    validate_covers(report)
    _check(Report, report, "report")
    validate_clarifications(report, "report")
    return report


def validate_clarifications(value, what):
    clarifications = value.get("clarifications", [])
    need(isinstance(clarifications, list), "%s: clarifications must be a list" % what)
    for entry in clarifications:
        need(
            isinstance(entry, dict)
            and all(text(entry.get(k)) for k in ("finding", "asked_by", "note")),
            "%s: each clarification needs finding, asked_by and note" % what,
        )


def validate_verdict(verdict):
    """A verdict the reviewer may hand in. Every error is named, so one refusal tells the reviewer all it must fix."""
    need(isinstance(verdict, dict), "verdict must be a JSON object")
    try:
        Verdict.model_validate(verdict)
    except ValidationError as error:
        raise ContractError(
            "verdict: "
            + "; ".join(
                "%s %s"
                % (".".join(map(str, e["loc"])) or "value", e["msg"].removeprefix("Value error, "))
                for e in error.errors()
            )
        ) from None
    return verdict


def review_items(verdict):
    """A verdict's findings as the items a fix round carries, numbered R1... in the order the reviewer wrote them."""
    return [
        dict(finding, id="R%d" % number, kind="blocking", source="review")
        for number, finding in enumerate(verdict["findings"], 1)
    ]


def validate_findings(findings):
    versioned(findings, "findings")
    return _check(Findings, findings, "findings")


def validate_decision(decision):
    need(isinstance(decision, dict), "decision must be a JSON object")
    need(
        decision.get("contract") in LEAD_SUPPORTED,
        "decision: contract must be one of %s" % ", ".join(map(str, LEAD_SUPPORTED)),
    )
    need(decision.get("action") in ACTIONS, "decision: action must be one of " + ", ".join(ACTIONS))
    need(text(decision.get("reason")), "decision: reason is required")
    need(
        text(decision.get("answer"))
        if decision["action"] == "answer"
        else decision.get("answer") is None,
        "decision: answer is required for the answer action and for no other",
    )
    return decision


def answer_version(request, named=None):
    """The contract version a decision answers a request in: the request's own version, so the driver that wrote the
    request reads the decision. A decision that names another version contradicts the request it answers, and is
    refused naming both versions."""
    version = request["contract"]
    if named is None or named == version:
        return version
    raise ContractError(
        "the decision names contract %s, but the request is contract %s" % (named, version)
    )


def menu_option(action):
    """One option of a contract 3 menu: what the action does and the inputs it takes."""
    inputs = {"reason": {"type": "string", "required": True}}
    if action == "answer":
        inputs["answer"] = {"type": "string", "required": True}
    return {"action": action, "summary": SUMMARIES[action], "inputs": inputs}


def menu_actions(request):
    """The actions a request offers, in its order, whichever contract wrote its menu."""
    return [o["action"] if isinstance(o, dict) else o for o in request["menu"]]


def valid_option(option):
    """A contract 3 menu option: a known action, its summary and inputs that include a required `reason`."""
    if not (
        isinstance(option, dict)
        and set(option) == {"action", "summary", "inputs"}
        and option["action"] in ACTIONS
        and text(option["summary"])
        and isinstance(option["inputs"], dict)
    ):
        return False
    inputs = option["inputs"]
    return (
        all(
            isinstance(spec, dict)
            and set(spec) == {"type", "required"}
            and spec["type"] == "string"
            and isinstance(spec["required"], bool)
            for spec in inputs.values()
        )
        and (inputs.get("reason") or {}).get("required") is True
    )


def validate_request(request):
    need(isinstance(request, dict), "request must be a JSON object")
    need(
        request.get("contract") in LEAD_SUPPORTED,
        "request: contract must be one of %s" % ", ".join(map(str, LEAD_SUPPORTED)),
    )
    for key in ("unit", "task", "event", "reason", "created_at"):
        need(text(request.get(key)), "request: %s is required" % key)
    need(
        isinstance(request.get("evidence"), list)
        and all(text(path) for path in request["evidence"]),
        "request: evidence must be a list of paths",
    )
    menu = request.get("menu")
    typed = request["contract"] >= 3  # earlier contracts list the action names
    need(
        isinstance(menu, list)
        and menu
        and all(valid_option(o) if typed else o in ACTIONS for o in menu),
        "request: menu must list %s from %s"
        % (
            "options {action, summary, inputs}, with a required reason input, of the actions"
            if typed
            else "actions",
            ", ".join(ACTIONS),
        ),
    )
    need(request.get("status") in ("open", "resolved"), "request: status must be open or resolved")
    need(
        (request["status"] == "resolved") == text(request.get("decision")),
        "request: a resolved request names its decision file, an open one has none",
    )
    return request


def make_request(unit, task, event, reason, evidence, created_at=None, menu=None, typed=True):  # noqa: PLR0913
    """A request for the Lead. `typed=False` writes contract 2, whose menu lists the action names: a request kind
    whose readers still compare the string menu."""
    return validate_request(
        {
            "contract": LEAD_VERSION if typed else 2,
            "unit": unit,
            "task": task,
            "event": event,
            "reason": reason,
            "evidence": list(evidence),
            "menu": [menu_option(a) if typed else a for a in (menu or ACTIONS)],
            "status": "open",
            "decision": None,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
    )


def make_findings(source, attempt, items):
    """The findings.json of a fix round."""
    return validate_findings(
        {"contract": VERSION, "source": source, "attempt": attempt, "items": list(items)}
    )


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
