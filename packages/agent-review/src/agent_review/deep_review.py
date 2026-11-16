"""The one deep review an escalation asks for: its scope, its instructions and its read-only spawn (D12, D14).

An escalated change is read by one reviewer, never by a swarm (D14). The review is asked for with
`--deep-review <harness>`, and everything about it is configuration rather than the reviewer's choice: the
instruction sets it is given, and the rules and paths that make each set apply (D07, D29, D14).

The scope follows the reasons that fired, never a guess (P2-D3): a reason that names the question it judged
is a calibrated signal, which points where the reviewer should look, so a review of those alone is
`focused`; a reason about the change itself - a sensitive path, a safeguard, a coverage gap - is nothing a
question bounds, so the review is `full`. The rule is one sentence over the reasons: a review whose every
reason names its question is focused, and one reason about the change makes it full.

The instructions are composed the same way (P2-D5): a set that declares no `applies` joins every review, a
set matching a fired rule or a changed path joins too, and the report names the composed ids in declaration
order.

The reviewer is read-only (D18): the harness family answers with the arguments that hold it to reading and
searching, or refuses and nothing is spawned (`agent_runtime.read_only`). It is handed the frozen diff,
because a reviewer that may not run a command cannot ask git what changed.

What the reviewer answers is judged where the report is assembled: this stage spawns it once, hands the
answer to `review`, and records what it was asked, so a spawn is checked without a model (P2-D1, P2-D7).
A reviewer's answer becomes findings and one disposition per fired signal (D15, D16, D17): the disposition
carries what the signal was judged on, so a confirmed contract violation stays blocking, a dismissal cites
the evidence that answers it, and a signal nobody disposed of stays unresolved. A reviewer that stops - a
timeout, an invalid answer, a failure - leaves an incomplete review carrying what it did answer, and it is
never retried or fallen back from (D20, D34).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agent_review import readonly
from agent_review.errors import ReviewError
from agent_review.numbers import number
from agent_review.safeguards import EscalationReason, matches

if TYPE_CHECKING:
    # Asked here and imported for the type only: `questions` reads this module's settings when it loads, so a
    # runtime import of the bank would be the cycle this annotation avoids.
    from agent_review.questions import QuestionBank

FOCUSED = "focused"
FULL = "full"
# What a completed review concluded: nothing blocking, or a confirmed finding that is (D16, D39).
PASSED = "passed"
BLOCKED = "blocked"
# What a reviewer may do with one fired signal, and the only words a disposition may use (D16, D17).
CONFIRMED = "confirmed"
DISMISSED = "dismissed"
DOWNGRADED = "downgraded"
DISPOSITIONS = (CONFIRMED, DISMISSED, DOWNGRADED)
# The canned reviewer's record of what it was asked for, written beside its answers (P2-D1).
ANSWERS = "answers.json"
SPAWNS = "spawns.json"
# One review's own budget, when a configuration declares none: ten minutes for the reviewer to stop in.
DEFAULT_TIMEOUT_SECONDS = 600.0
SETTINGS_FIELDS = ("timeout_seconds", "instructions")
INSTRUCTION_FIELDS = ("id", "text", "applies")
APPLIES_FIELDS = ("rules", "paths")
ANSWER_FIELDS = ("findings", "dispositions", "failure")
FINDING_FIELDS = (
    "id",
    "classification",
    "path",
    "line",
    "requirement",
    "evidence",
    "correction",
    "blocking",
)
DISPOSITION_FIELDS = ("signal", "question", "disposition", "evidence", "finding")
# The priority whose question is the mandatory contract: a confirmed violation of one is blocking (D16).
MANDATORY = "critical"


class ReviewerStopped(Exception):
    """The reviewer was spawned and did not answer usably: it timed out, exited nonzero or never started.

    An answer no review can accept - one that is not a review of the change, disposes of a signal nobody
    raised, or dismisses a signal without the evidence that answers it - is a stop too (D15, D16, D20):
    what cannot be read cannot be trusted, and both leave an incomplete execution.

    A stop is the reviewer's own, so the review records it and the screening it was asked about survives
    (D20, R10). A caller's input that the invocation cannot use - a fixture directory it cannot write, or an
    answer file it does not hold - is not a stop: it is a `ReviewError` refusal, raised where it happens and
    never recorded as a review that ran.
    """


@dataclass(frozen=True)
class Instruction:
    """One specialized instruction set: what it says, and the rules or changed paths that make it join.

    A set that declares neither applies to every review, which is what makes `evidence-first` the set every
    invocation carries (P2-D5).
    """

    id: str
    text: str
    rules: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    def joins(self, fired: Iterable[str], changed_paths: Iterable[str]) -> bool:
        """Whether this set is composed for a review with these fired rules and these changed paths."""
        if not (self.rules or self.paths):
            return True
        if set(self.rules).intersection(fired):
            return True
        return any(matches(pattern, path) for pattern in self.paths for path in changed_paths)

    def as_dict(self) -> dict[str, str]:
        """The set as a reviewer is handed it: its id and its text."""
        return {"id": self.id, "text": self.text}


@dataclass(frozen=True)
class Settings:
    """What a configuration says a deep review is: how long it may take, and the sets it may be given."""

    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    instructions: tuple[Instruction, ...] = ()


@dataclass(frozen=True)
class Target:
    """What a review looks at: the frozen change, both revisions, the changed paths and the checkout.

    They travel together because a reviewer is handed all four or none: the diff because it may not run a
    command, the revisions so the report names what was reviewed, the paths for the instruction sets that
    match a changed path, and the checkout the reviewer reads.
    """

    diff: str
    base: str
    head: str
    paths: tuple[str, ...]
    root: Path


@dataclass(frozen=True)
class Spawn:
    """One reviewer asked to investigate: what it may do, where it looks, and what it is told."""

    harness: str
    # The arguments that hold this harness to reading and searching, as the report of an audit reads them.
    controls: tuple[str, ...]
    base: str
    head: str
    scope: str
    instructions: tuple[Instruction, ...]
    signals: tuple[EscalationReason, ...]
    paths: tuple[str, ...]
    timeout_seconds: float
    diff: str
    root: Path

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What the reviewer may do, which is what every read-only control leaves it."""
        return readonly.capabilities()

    @property
    def prompt(self) -> str:
        """What the reviewer is told: the change, its scope, the instructions, the signals and how to answer.

        The frozen diff travels in the prompt because the reviewer may not run a command, so it cannot ask
        git what changed; naming the change only would leave the review reading a checkout blind. The answer
        format travels with it for the same reason the instructions do: the shape of a review is decided
        here, and a reviewer left to invent one answers something no review can read (D15, D17, D20).
        """
        lines = [
            f"Review the change {self.base}..{self.head} read-only: read and search {self.root}, and "
            "modify nothing, run no command and run no test.",
            f"Scope: {self._scope_instruction()}",
            "Instructions:",
        ]
        lines += [f"- {entry.id}: {entry.text}" for entry in self.instructions] or ["- none"]
        lines.append("Signals that fired:")
        lines += [f"- {signal.rule}{self._signal_subject(signal)}" for signal in self.signals]
        lines += [
            "Answer with one JSON object and nothing else:",
            '  {"findings": [...], "dispositions": [...], "failure": null}',
            "A finding carries id, classification, path, line, requirement, evidence, correction and "
            "blocking: the line you read, the requirement it violates, the evidence and the smallest "
            "correction that answers it.",
            "One disposition per signal above, each on its own: signal, question, disposition (confirmed, "
            "dismissed or downgraded), the evidence that decision rests on, and the finding it rests on "
            "when it rests on one.",
            "A failure is the reason you stopped, beside what you did answer.",
        ]
        lines += ["Change:", self.diff]
        return "\n".join(lines)

    @staticmethod
    def _signal_subject(signal: EscalationReason) -> str:
        """The question or path a signal is about, with its detail, as one line."""
        subject = signal.question or signal.path
        if subject and signal.detail:
            return f" on {subject}: {signal.detail}"
        if subject:
            return f" on {subject}: {signal.pattern}"
        return f": {signal.detail or signal.pattern}"

    def _scope_instruction(self) -> str:
        if self.scope == FULL:
            return "the whole change, not only where the signals point."
        return "the flagged areas below, and the code they touch."

    def as_dict(self) -> dict[str, Any]:
        """The request as a canned reviewer records it: everything the report says, and the controls it ran."""
        return {
            "harness": self.harness,
            "controls": list(self.controls),
            "base": self.base,
            "head": self.head,
            "scope": self.scope,
            "capabilities": list(self.capabilities),
            "instructions": [entry.as_dict() for entry in self.instructions],
            "signals": [signal.as_dict() for signal in self.signals],
            "paths": list(self.paths),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class Finding:
    """One finding a reviewer reports: what it is, where, the requirement it violates and what fixes it.

    D15 asks for exactly this: a classification, a location, the violated requirement or principle, the
    evidence and the smallest correction that answers it. `blocking` is the reviewer's own claim, and a
    review promotes it to `True` for a confirmed finding on a critical-priority question (D16, P2-D6).
    """

    id: str
    classification: str
    path: str
    requirement: str
    evidence: str
    correction: str
    line: int | None = None
    blocking: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The finding as `report.json` holds it, in the contract's field order (D15)."""
        return {
            "id": self.id,
            "classification": self.classification,
            "path": self.path,
            "line": self.line,
            "requirement": self.requirement,
            "evidence": self.evidence,
            "correction": self.correction,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class Disposition:
    """What the reviewer decided about one fired signal, and what decided it (D16, D17).

    The reviewer names the signal and the evidence; the review adds what the signal was judged on - the
    value the screening answered, the confidence it came with, the calibrated declaration that fired - so
    one entry carries its own basis and nobody has to join it back to the escalation (P2-D6).
    """

    signal: str
    disposition: str
    evidence: str
    # The question the reviewer was answering, so two signals carrying the same rule are told apart (D17).
    question: str = ""
    # The finding this disposition rests on, when it rests on one.
    finding: str | None = None
    # What the signal was judged on, filled in by the review from the signal itself (P2-D6).
    path: str = ""
    value: Any = None
    confidence: Any = None
    threshold: str = ""

    def judged(self, signal: EscalationReason) -> "Disposition":
        """This disposition with the fired signal's own facts, never the reviewer's retelling of them."""
        return replace(
            self,
            question=signal.question,
            path=signal.path,
            value=signal.value,
            confidence=signal.confidence,
            threshold=signal.pattern,
        )

    def as_dict(self) -> dict[str, Any]:
        """The disposition as `report.json` holds it: the decision, its evidence, and what judged it."""
        return {
            "signal": self.signal,
            "question": self.question,
            "path": self.path,
            "disposition": self.disposition,
            "evidence": self.evidence,
            "finding": self.finding,
            "value": self.value,
            "confidence": self.confidence,
            "threshold": self.threshold,
        }


@dataclass(frozen=True)
class Answer:
    """What one reviewer answered: its findings, its dispositions of the signals, and its own stop (D20).

    A failure travels with the answer rather than instead of it: a reviewer that read half the change and
    then timed out has evidence worth keeping, and the execution is incomplete either way (D20, D34).
    """

    findings: tuple[Finding, ...] = ()
    dispositions: tuple[Disposition, ...] = ()
    failure: str | None = None


@dataclass(frozen=True)
class Review:
    """What the review asked for and what it was answered: the `deep_review` section of the one report."""

    harness: str
    scope: str
    instructions: tuple[Instruction, ...]
    signals: tuple[EscalationReason, ...]
    findings: tuple[Finding, ...] = ()
    dispositions: tuple[Disposition, ...] = ()
    # The fired signals nobody disposed of, which leave the review unresolved (D17, P2-D6).
    unresolved: tuple[EscalationReason, ...] = ()
    spawns: int = 1
    # Why the reviewer never finished, when it did not: the review records its own stop (D20, R10).
    failure: str | None = None

    @property
    def capabilities(self) -> tuple[str, ...]:
        """What the reviewer was allowed to do, as the read-only answer gave it."""
        return readonly.capabilities()

    @property
    def verdict(self) -> str | None:
        """What a completed review concluded: `blocked` on one blocking finding, `passed` on none.

        An incomplete review concluded nothing, so it has no verdict (D34, D39): a reviewer that stopped is
        never read as a clean review.
        """
        if self.failure is not None:
            return None
        return BLOCKED if any(finding.blocking for finding in self.findings) else PASSED

    def as_dict(self) -> dict[str, Any]:
        """The section as `report.json` holds it, in the contract's field order (additive, D26, D39)."""
        return {
            "harness": self.harness,
            "scope": self.scope,
            "capabilities": list(self.capabilities),
            "instructions": [entry.id for entry in self.instructions],
            "spawns": self.spawns,
            "signals": [signal.as_dict() for signal in self.signals],
            "dispositions": [entry.as_dict() for entry in self.dispositions],
            "findings": [finding.as_dict() for finding in self.findings],
            "unresolved": [
                {"signal": signal.rule, "question": signal.question} for signal in self.unresolved
            ],
            "failure": self.failure,
        }


class Reviewer(Protocol):
    """The one seam a deep review reaches: the harness's own CLI, or a canned reviewer replacing it.

    `spawn` answers what the reviewer said and raises `ReviewerStopped` when it did not answer usably; it
    raises `ReviewError` when the caller's input cannot be used. The two are told apart by who stopped the
    run. What the answer means is judged by `review`, never by the adapter (P2-D7).
    """

    # The family this reviewer is spawned through, named in the report beside what it was asked.
    harness: str
    # The arguments that hold that family to reading and searching, recorded so an audit reads them.
    controls: tuple[str, ...]

    # `...` is a protocol member's body: it carries the signature and no caller runs it.
    def spawn(self, request: Spawn) -> Any: ...


class FixtureReviewer:
    """A canned reviewer behind the same seam, so every check stays offline and repeats exactly (P2-D1)."""

    def __init__(self, harness: str, controls: Sequence[str], directory: Path | str) -> None:
        self.harness = harness
        self.controls = tuple(controls)
        self.directory = Path(directory)
        self.recorded: list[dict[str, Any]] = []
        if not self.directory.is_dir():
            raise ReviewError(
                f"the deep-review fixture {self.directory} is not a directory; point "
                f"--deep-review-fixture at the directory the canned reviewer records {SPAWNS} in"
            )

    def spawn(self, request: Spawn) -> Any:
        """Record what the reviewer was asked, then answer what the canned reviewer says it did.

        The record is written first: a check reads the spawn even when the answer file is missing, and a
        directory this invocation cannot write is refused before anything else is claimed about the run.
        A missing or unreadable answer file is the caller's fixture, not the reviewer's stop.
        """
        self.recorded.append(request.as_dict())
        path = self.directory / SPAWNS
        try:
            path.write_text(json.dumps(self.recorded, indent=2) + "\n", encoding="utf-8")
        except OSError as error:
            raise ReviewError(
                f"cannot record the deep review in {path}: {error.strerror or error}; point "
                "--deep-review-fixture at a directory this invocation may write"
            ) from None
        return _canned(self.directory, len(self.recorded))


class HarnessReviewer:
    """One read/search-only reviewer, run through its harness's own CLI with the arguments that hold it."""

    def __init__(self, harness: str, invocation: readonly.Invocation) -> None:
        self.harness = harness
        self.invocation = invocation
        self.controls = tuple(invocation.controls)

    def spawn(self, request: Spawn) -> Any:
        """Ask the family one question read-only, with the prompt on stdin and the answer left on stdout.

        The prompt never travels as a positional: a family's options can be variadic, and a prompt after them
        is read as another value of theirs. A harness that cannot start, times out or exits nonzero stops the
        review, and the report records the stop rather than throwing the screening away (D20, R10). The
        answer on stdout is read as JSON here and judged by `review`: output that is not JSON is one more way
        a reviewer fails to answer (D20, S5).
        """
        argv = [self.harness, *self.invocation.argv]
        try:
            done = subprocess.run(
                argv,
                input=request.prompt,
                cwd=request.root,
                capture_output=True,
                text=True,
                timeout=request.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ReviewerStopped(
                f"the {self.harness} reviewer did not answer within {request.timeout_seconds} s"
            ) from None
        except OSError as error:
            raise ReviewerStopped(
                f"cannot start the {self.harness} reviewer: {error.strerror or error}; install the "
                "harness or name one that this checkout can run"
            ) from None
        if done.returncode != 0:
            raise ReviewerStopped(
                f"the {self.harness} reviewer exited {done.returncode}: "
                f"{(done.stderr or done.stdout).strip()[-2000:]}"
            )
        try:
            return json.loads(done.stdout)
        except ValueError:
            raise ReviewerStopped(
                f"the {self.harness} reviewer did not answer with a review: "
                f"{done.stdout.strip()[:2000] or 'nothing'}"
            ) from None


def settings(declared: Any, path: Path | str) -> Settings:
    """The `deep_review` block as the settings a review runs with, or a refusal naming the field by path.

    A declaration no stage can read is refused before anything is screened (D29, D30): a timeout of zero
    would stop the reviewer before it read anything, and an instruction set with an `applies` that names
    neither a rule nor a path would never join a review.
    """
    if declared is None:
        return Settings()
    if not isinstance(declared, Mapping):
        raise ReviewError(
            f"{path}: deep_review must be a mapping of settings, not {type(declared).__name__}"
        )
    unknown = sorted(set(declared) - set(SETTINGS_FIELDS))
    if unknown:
        raise ReviewError(
            f"{path}: deep_review has an unknown field {unknown[0]!r}; it takes "
            f"{', '.join(SETTINGS_FIELDS)}"
        )
    return Settings(
        timeout_seconds=_timeout(declared.get("timeout_seconds"), path),
        instructions=_instructions(declared.get("instructions"), path),
    )


def scope_of(reasons: Iterable[EscalationReason]) -> str:
    """`focused` when every reason names the question it judged, `full` when one is about the change (P2-D3)."""
    return FULL if any(not reason.question for reason in reasons) else FOCUSED


def composed(
    settings: Settings, reasons: Iterable[EscalationReason], paths: Iterable[str]
) -> tuple[Instruction, ...]:
    """The instruction sets this review is given, in the configuration's declaration order (P2-D5)."""
    fired = {reason.rule for reason in reasons}
    changed = tuple(paths)
    return tuple(entry for entry in settings.instructions if entry.joins(fired, changed))


def review(
    *,
    settings: Settings,
    escalation: Iterable[EscalationReason],
    target: Target,
    reviewer: Reviewer,
    bank: QuestionBank,
) -> Review:
    """Spawn the one reviewer this escalation asks for, and judge the answer it gave (D14, D19, P2-D3).

    The scope and the instructions are decided here, from the reasons the tool recorded, so neither is the
    reviewer's choice; the reviewer is spawned once, with the frozen change in hand (D18, D19). A reviewer
    that cannot start, times out, exits nonzero or answers something no review can accept is recorded here
    as the review's own stop and not raised: the screening it was asked about already happened, and what the
    execution then is belongs to the report that keeps it (D20, R10). A caller's input that cannot be used is
    refused instead - a `ReviewError` from the seam propagates, so a broken fixture never becomes a review
    that is recorded as having happened.

    The answer is judged here, once (P2-D7): every fired signal gets its own disposition or stays
    unresolved, a confirmed finding on a critical-priority question is blocking whatever the reviewer's flag
    said, and the run is never retried or fallen back from - the spawn count is the evidence (D16, D17, D20).
    """
    signals = tuple(escalation)
    request = Spawn(
        harness=reviewer.harness,
        controls=reviewer.controls,
        base=target.base,
        head=target.head,
        scope=scope_of(signals),
        instructions=composed(settings, signals, target.paths),
        signals=signals,
        paths=target.paths,
        timeout_seconds=settings.timeout_seconds,
        diff=target.diff,
        root=target.root,
    )
    # One spawn, and its answer is the only one this review will judge: a retry or a fallback would show as a
    # second request, which is exactly what D20 forbids.
    judgement, stop = _judged(reviewer, request, signals, bank)
    return Review(
        harness=request.harness,
        scope=request.scope,
        instructions=request.instructions,
        signals=signals,
        findings=judgement.findings,
        dispositions=judgement.dispositions,
        unresolved=judgement.unresolved,
        failure=stop or judgement.failure,
    )


def _judged(
    reviewer: Reviewer, request: Spawn, signals: Sequence[EscalationReason], bank: QuestionBank
) -> tuple[Judgement, str | None]:
    """Spawn the reviewer once and judge its answer, or record why no one could read what it answered."""
    try:
        return judged(signals, answer_of(reviewer.spawn(request)), bank), None
    except ReviewerStopped as ended:
        # The screening already answered: the review records what stopped it, and the report keeps the
        # judgments and usage it paid for instead of the invocation refusing with no report (D20, R10).
        # A caller's broken fixture is not a stop: it raises `ReviewError` and refuses the run untouched.
        # Nothing it answered is kept - what cannot be read is not evidence - and every signal it left is
        # unresolved, so no signal is silently read as dismissed (D15, D17).
        return judged(signals, Answer(), bank), str(ended)


def answer_of(declared: Any) -> Answer:
    """The answer the reviewer gave, refused as a stop when no review can accept it (D15, D16, D17, D20).

    Everything an answer must carry is checked here, once: a finding names its classification, its location,
    the requirement it violates, its evidence and its correction, and a disposition names a fired signal, one
    of the three decisions and the evidence that decision rests on. What cannot be read cannot be trusted, so
    it is a stop, never a review with the unreadable parts quietly dropped.
    """
    if not isinstance(declared, Mapping):
        raise ReviewerStopped(
            f"the reviewer answered {type(declared).__name__}, not a review of the change"
        )
    unknown = sorted(set(declared) - set(ANSWER_FIELDS))
    if unknown:
        raise ReviewerStopped(
            f"the reviewer's answer has an unknown field {unknown[0]!r}; it takes "
            f"{', '.join(ANSWER_FIELDS)}"
        )
    findings = tuple(
        _finding(entry, index)
        for index, entry in enumerate(_listed(declared.get("findings"), "findings"))
    )
    repeated = _repeated([finding.id for finding in findings])
    if repeated:
        raise ReviewerStopped(
            f"the reviewer's findings repeat {repeated!r}; a disposition cites a finding by id, so each "
            "is reported once"
        )
    dispositions = tuple(
        _disposition(entry, index)
        for index, entry in enumerate(_listed(declared.get("dispositions"), "dispositions"))
    )
    return Answer(
        findings=findings,
        dispositions=dispositions,
        failure=_failure(declared.get("failure")),
    )


@dataclass(frozen=True)
class Judgement:
    """What one answer means for a review: a disposition per signal, the ones left, and the findings."""

    dispositions: tuple[Disposition, ...] = ()
    unresolved: tuple[EscalationReason, ...] = ()
    findings: tuple[Finding, ...] = ()
    # Why the review is incomplete, when it is: the reviewer's own stop, or a signal left unanswered.
    failure: str | None = None


def judged(
    signals: Sequence[EscalationReason], answer: Answer, bank: QuestionBank
) -> Judgement:
    """The dispositions of the fired signals, the ones nobody answered, and the findings with blocking fixed.

    Each fired signal gets the disposition that names it - with its question when the rule alone matches more
    than one signal - so a reviewer cannot answer two signals with one sentence, and one it never answered is
    unresolved rather than read as dismissed (D17, P2-D6). A finding on a critical-priority question is
    blocking unless the reviewer dismissed or downgraded it with evidence: a confirmed mandatory-contract
    violation is never downgraded by the reviewer's own flag (D16).
    """
    matched = _matched(signals, answer.dispositions)
    confirmed = {
        entry.finding
        for entry in answer.dispositions
        if entry.finding is not None and entry.disposition == CONFIRMED
    }
    cleared = {
        entry.finding
        for entry in answer.dispositions
        if entry.finding is not None and entry.disposition != CONFIRMED
    } - confirmed
    mandatory = {question.id for question in bank.questions if question.priority == MANDATORY}
    findings = tuple(
        replace(
            finding,
            blocking=finding.blocking
            or (finding.requirement in mandatory and finding.id not in cleared),
        )
        for finding in answer.findings
    )
    dispositions = tuple(
        entry.judged(signal) for signal, entry in zip(signals, matched, strict=True) if entry
    )
    unresolved = tuple(
        signal for signal, entry in zip(signals, matched, strict=True) if entry is None
    )
    return Judgement(
        dispositions=dispositions,
        unresolved=unresolved,
        findings=findings,
        failure=answer.failure or _unanswered(unresolved),
    )


def _matched(
    signals: Sequence[EscalationReason], answered: Sequence[Disposition]
) -> tuple[Disposition | None, ...]:
    """The disposition of each fired signal, or nothing where the reviewer left it unanswered (D17)."""
    taken: list[Disposition | None] = [None] * len(signals)
    for entry in answered:
        index = _signal_index(signals, taken, entry)
        if index is None:
            raise ReviewerStopped(
                f"the reviewer disposed of {entry.signal!r}, which is not one of the signals it was given"
                + (f" for {entry.question!r}" if entry.question else "")
                + "; answer the signals that fired, each on its own"
            )
        taken[index] = entry
    return tuple(taken)


def _signal_index(
    signals: Sequence[EscalationReason], taken: Sequence[Disposition | None], entry: Disposition
) -> int | None:
    """Where one answered disposition belongs among the fired signals, or nothing when it names none."""
    for index, signal in enumerate(signals):
        if taken[index] is not None or signal.rule != entry.signal:
            continue
        if entry.question and entry.question != signal.question:
            continue
        return index
    return None


def _unanswered(unresolved: Sequence[EscalationReason]) -> str | None:
    """Why a review that left signals unanswered is unresolved, or nothing when every signal was answered."""
    if not unresolved:
        return None
    named = ", ".join(signal.rule for signal in unresolved)
    return (
        f"the reviewer left {len(unresolved)} fired signal"
        f"{'s' if len(unresolved) != 1 else ''} without a disposition: {named}; an unanswered signal is "
        "unresolved, never a dismissal"
    )


def _finding(entry: Any, index: int) -> Finding:
    """One reported finding, refused as a stop when it does not carry what D15 asks a finding to carry."""
    name = f"the reviewer's finding {index}"
    declared = _mapping(entry, name, FINDING_FIELDS)
    for field_name in ("id", "classification", "path", "requirement", "evidence", "correction"):
        _text(declared.get(field_name), f"{name}.{field_name}")
    line = declared.get("line")
    if line is not None and (isinstance(line, bool) or not isinstance(line, int) or line < 1):
        raise ReviewerStopped(f"{name}.line must be a line number, not {line!r}")
    blocking = declared.get("blocking", False)
    if not isinstance(blocking, bool):
        raise ReviewerStopped(f"{name}.blocking must be true or false, not {blocking!r}")
    return Finding(
        id=declared["id"],
        classification=declared["classification"],
        path=declared["path"],
        requirement=declared["requirement"],
        evidence=declared["evidence"],
        correction=declared["correction"],
        line=line,
        blocking=blocking,
    )


def _disposition(entry: Any, index: int) -> Disposition:
    """One disposition of a fired signal, refused as a stop when it decides nothing or cites no evidence."""
    name = f"the reviewer's disposition {index}"
    declared = _mapping(entry, name, DISPOSITION_FIELDS)
    signal = _text(declared.get("signal"), f"{name}.signal")
    decision = declared.get("disposition")
    if decision not in DISPOSITIONS:
        raise ReviewerStopped(
            f"{name}.disposition must be one of {', '.join(DISPOSITIONS)}, not {decision!r}"
        )
    evidence = _text(declared.get("evidence"), f"{name}.evidence")
    question = declared.get("question")
    if question is not None and not isinstance(question, str):
        raise ReviewerStopped(f"{name}.question must be a question id, not {question!r}")
    finding = declared.get("finding")
    if finding is not None and not isinstance(finding, str):
        raise ReviewerStopped(f"{name}.finding must be a finding id, not {finding!r}")
    return Disposition(
        signal=signal,
        disposition=decision,
        evidence=evidence,
        question=question or "",
        finding=finding,
    )


def _mapping(declared: Any, name: str, fields: Sequence[str]) -> Mapping[str, Any]:
    """One object of an answer, refused as a stop when it is not an object or carries a field it cannot."""
    if not isinstance(declared, Mapping):
        raise ReviewerStopped(f"{name} must be an object, not {type(declared).__name__}")
    unknown = sorted(set(declared) - set(fields))
    if unknown:
        raise ReviewerStopped(
            f"{name} has an unknown field {unknown[0]!r}; it takes {', '.join(fields)}"
        )
    return declared


def _listed(declared: Any, name: str) -> Sequence[Any]:
    """The entries of one part of an answer, refused as a stop when it is not a list."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ReviewerStopped(
            f"the reviewer's answer has {name} that is not a list, but {type(declared).__name__}"
        )
    return declared


def _text(value: Any, name: str) -> str:
    """A field an answer must spell out, refused as a stop when it is missing or empty."""
    if not isinstance(value, str) or not value.strip():
        raise ReviewerStopped(f"{name} must be a non-empty string, not {value!r}")
    return value.strip()


def _failure(declared: Any) -> str | None:
    """The reviewer's own reason for stopping, when its answer carries one."""
    if declared is None:
        return None
    return _text(declared, "the reviewer's answer.failure")


def _repeated(identifiers: Sequence[str]) -> str | None:
    """The first identifier declared twice, or nothing when each is declared once."""
    seen: set[str] = set()
    for identifier in identifiers:
        if identifier in seen:
            return identifier
        seen.add(identifier)
    return None


def _canned(directory: Path, asked: int) -> Any:
    """The answer a canned reviewer gives its `asked`-th spawn, refused by path when the fixture cannot give it."""
    path = directory / ANSWERS
    try:
        declared = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ReviewError(
            f"the deep-review fixture {directory} holds no {ANSWERS}; point --deep-review-fixture at the "
            f"directory the canned reviewer answers from, or drop it to spawn the harness"
        ) from None
    except (OSError, ValueError) as error:
        raise ReviewError(
            f"the deep-review fixture {path} is not readable JSON: {error}; the canned reviewer answers "
            f"from {ANSWERS} in that directory"
        ) from None
    answers = declared.get("answers") if isinstance(declared, Mapping) else None
    if not isinstance(answers, list):
        raise ReviewError(
            f"the deep-review fixture {path} must hold an `answers` list, one answer per spawn"
        )
    if len(answers) < asked:
        raise ReviewError(
            f"the deep-review fixture {path} holds {len(answers)} answer"
            f"{'s' if len(answers) != 1 else ''} for {asked} spawn"
            f"{'s' if asked != 1 else ''}; a review is spawned once and answered once"
        )
    return answers[asked - 1]


def _timeout(declared: Any, path: Path | str) -> float:
    """How long the reviewer may take, refused when a zero or unreadable number would stop it at once."""
    if declared is None:
        return DEFAULT_TIMEOUT_SECONDS
    seconds = number(declared, "deep_review.timeout_seconds", path)
    if seconds == 0:
        raise ReviewError(
            f"{path}: deep_review.timeout_seconds must be a positive number of seconds, not "
            f"{declared!r}; a reviewer given no time reads nothing"
        )
    return seconds


def _instructions(declared: Any, path: Path | str) -> tuple[Instruction, ...]:
    """The specialized sets a review may be given, in declaration order, refused by path when unreadable."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ReviewError(
            f"{path}: deep_review.instructions must be a list of instruction sets, not "
            f"{type(declared).__name__}"
        )
    sets: list[Instruction] = []
    seen: dict[str, int] = {}
    for index, entry in enumerate(declared):
        instruction = _instruction(entry, f"deep_review.instructions[{index}]", path)
        if instruction.id in seen:
            raise ReviewError(
                f"{path}: deep_review.instructions[{index}].id repeats {instruction.id!r}, already "
                f"declared by deep_review.instructions[{seen[instruction.id]}]; the report names the "
                "composed sets by id, so each is declared once"
            )
        seen[instruction.id] = index
        sets.append(instruction)
    return tuple(sets)


def _instruction(entry: Any, name: str, path: Path | str) -> Instruction:
    """One instruction set, refused by field path when a stage cannot read it."""
    if not isinstance(entry, Mapping):
        raise ReviewError(
            f"{path}: {name} must be a mapping with {', '.join(INSTRUCTION_FIELDS)}, not "
            f"{type(entry).__name__}"
        )
    unknown = sorted(set(entry) - set(INSTRUCTION_FIELDS))
    if unknown:
        raise ReviewError(f"{path}: {name} has an unknown field {unknown[0]!r}")
    for required in ("id", "text"):
        if required not in entry:
            raise ReviewError(f"{path}: {name}.{required} is missing")
    identifier, text = entry["id"], entry["text"]
    for field_name, value in (("id", identifier), ("text", text)):
        if not isinstance(value, str) or not value.strip():
            raise ReviewError(
                f"{path}: {name}.{field_name} must be a non-empty string, not {value!r}"
            )
    rules, paths = _applies(entry.get("applies"), identifier, name, path)
    return Instruction(id=identifier, text=text, rules=rules, paths=paths)


def _applies(
    declared: Any, identifier: str, name: str, path: Path | str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The rules and changed paths that make one set join, or a refusal naming the field that is unreadable."""
    if declared is None:
        return (), ()
    if not isinstance(declared, Mapping):
        raise ReviewError(
            f"{path}: {name}.applies must be a mapping of rules and paths, not "
            f"{type(declared).__name__}"
        )
    unknown = sorted(set(declared) - set(APPLIES_FIELDS))
    if unknown:
        raise ReviewError(
            f"{path}: {name}.applies has an unknown field {unknown[0]!r}; it takes "
            f"{', '.join(APPLIES_FIELDS)}"
        )
    if not declared:
        raise ReviewError(
            f"{path}: {name}.applies declares nothing, so the set {identifier!r} would never join a "
            "review; name the rules or the paths that make it apply, or drop `applies` to join every one"
        )
    return (
        _names(declared.get("rules"), f"{name}.applies.rules", identifier, path),
        _names(declared.get("paths"), f"{name}.applies.paths", identifier, path),
    )


def _names(declared: Any, field_name: str, identifier: str, path: Path | str) -> tuple[str, ...]:
    """A list of rules or path patterns a set applies to, refused by path when it is not one."""
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ReviewError(
            f"{path}: {field_name} must be a list for the instruction set {identifier!r}, not "
            f"{type(declared).__name__}"
        )
    for index, entry in enumerate(declared):
        if not isinstance(entry, str) or not entry.strip():
            raise ReviewError(
                f"{path}: {field_name}[{index}] must be a non-empty string for the instruction set "
                f"{identifier!r}, not {entry!r}"
            )
    return tuple(entry.strip() for entry in declared)
