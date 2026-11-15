"""`report.json`: the review's verdict kept apart from the execution's own health.

One invocation writes exactly one report under the caller's directory (D53, D54), whether the screening
completed or not: `status` says whether the execution finished, `verdict` says what the review concluded
and is null when nothing was concluded, and `failure.reason` names why an incomplete run stopped (D34,
D35, D39, D40). The report also freezes what the run was made of - both revisions, the question
configuration, the model and the usage - so the same frozen input can be recognised again (D26, D44).

`usage.requests` is how many times the provider was asked. One screening of one chunk is one request (D40):
a retry or a model fallback would show as a second one, and a change larger than one request's budget is
asked chunk by chunk, so the count is the number of chunks (D41). A safeguard's hit is `usage.requests: 0`:
the change was decided from the diff, so nothing was asked and `escalation.reasons[]` is the verdict's whole
basis (SG-D2, SG-D10), and no coverage is claimed for a change nothing screened.

A report says what produced it, so a frozen one can be recognised and trusted later: the provider endpoint
it was screened through, the model the provider served beside the alias the bank asked for, the input
limit in force, whether long context was requested, and a digest of the bank it screened (S4). The model
the provider served is read from its own answer, never inferred from the alias.
"""

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_review.chunks import Chunk, Coverage
from agent_review.questions import Question, QuestionBank, Threshold
from agent_review.safeguards import EscalationReason, recommends_full_review, way_out
from agent_review.screening import limits

REPORT = "report.json"
COMPLETED = "completed"
INCOMPLETE = "incomplete"
SCREENED = "screened"
ESCALATED = "escalated"


@dataclass(frozen=True)
class Report:
    """One invocation's report: the execution's status, the review's verdict, and what both rest on.

    `bank` is the configuration the run screened - its questions, the model it asked for and the numbers it
    was decided with - so a frozen report names its calibration as well as its questions (D26).
    """

    bank: QuestionBank
    base: str
    head: str
    # Where the screening came from: the provider's endpoint, or the fixture a canned one answered from.
    provider: str = ""
    # The model the provider's answer named; the configured alias stays in `model` beside it.
    resolved_model: str | None = None
    judgments: tuple[Mapping[str, Any], ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    # One screening is one request: the run never retries, so anything else would be a defect (D40). A
    # chunked change asks once per chunk, so this is the number of requests the run actually made.
    requests: int = 1
    # The chunks the change was split into, in the order they were asked, and what they covered (D41).
    chunks: tuple[Chunk, ...] = ()
    # Nothing for a change a safeguard decided before the model was asked: nothing was screened, so the
    # report claims no coverage of it.
    coverage: Coverage | None = None
    # Why a person must read this change: a safeguard decided it before the model was asked (SG-D2, SG-D3),
    # or the screening could not cover it (D43).
    escalation: tuple[EscalationReason, ...] = ()
    # Long context is the caller's opt-in, so the report says whether the larger budget was asked for.
    long_context: bool = False
    failure: str | None = field(default=None)

    @property
    def model(self) -> str:
        """The model the bank asked for, which the provider may answer with a resolved name beside it."""
        return self.bank.model

    @property
    def questions(self) -> Sequence[Question]:
        """The questions this run asked, in configuration order."""
        return self.bank.questions

    @property
    def status(self) -> str:
        """Whether the execution finished; never what the review concluded (D34)."""
        return COMPLETED if self.failure is None else INCOMPLETE

    @property
    def verdict(self) -> str | None:
        """What the review concluded: `escalated` when a safeguard fired, `screened` once screened, and

        nothing when the execution did not complete.
        """
        if self.failure is not None:
            return None
        return ESCALATED if self.escalation else SCREENED

    @property
    def bank_digest(self) -> str:
        """A digest of the bank exactly as configured, so a frozen report names what it screened (D26)."""
        return bank_digest(
            self.bank.model, self.bank.questions, self.bank.weights, self.bank.thresholds
        )

    @property
    def recommended(self) -> bool:
        """Whether the outcome sends this change to a full deep review rather than a screen (D12, D43)."""
        return recommends_full_review(self.escalation)

    def as_dict(self) -> dict[str, Any]:
        """The report as `report.json` holds it, in the contract's field order (P0-D3)."""
        return {
            "status": self.status,
            "verdict": self.verdict,
            "input": {"base": self.base, "head": self.head},
            "provider": self.provider,
            "model": self.model,
            "resolved_model": self.resolved_model,
            "limits": {
                "long_context": self.long_context,
                "per_question": limits(self.long_context).per_question,
                "per_request": limits(self.long_context).per_request,
            },
            "usage": {
                "requests": self.requests,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
            },
            "questions": [
                {"id": question.id, "priority": question.priority, "type": question.type}
                for question in self.questions
            ],
            "chunks": [chunk.as_dict() for chunk in self.chunks],
            "coverage": None if self.coverage is None else self.coverage.as_dict(),
            "bank_digest": self.bank_digest,
            "judgments": [dict(judgment) for judgment in self.judgments],
            # Additive in a frozen report: a report written before the safeguards read it as no reasons (D26).
            "escalation": {
                "reasons": [reason.as_dict() for reason in self.escalation],
                "recommended": self.recommended,
            },
            "failure": None if self.failure is None else {"reason": self.failure},
        }

    def write(self, directory: Path) -> Path:
        """Write the report atomically under `directory`, so no reader sees a half-written one (D54)."""
        path = directory / REPORT
        temporary = path.with_name(f"{REPORT}.tmp")
        temporary.write_text(json.dumps(self.as_dict(), indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return path

    def summary(self, path: Path | None = None) -> str:
        """What a person reads on the terminal: the verdict, the judgments behind it and where the report is.

        Without `path` the report line is left out: a summary printed because the report could not be
        written must not claim a file that is not there (S6).
        """
        change = f"{self.base}..{self.head}"
        if self.failure is not None:
            lines = [f"screened nothing: {change} was not screened with {self.model}"]
            lines.append(f"execution: {self.status}")
            lines.append(f"reason: {self.failure}")
        elif self.escalation:
            # A person reads the change instead of trusting the screen: the rule, the path it matched and the
            # way out are what they can act on (S8). A coverage gap is decided after the screen, so the
            # report says the screening happened rather than claiming no model was asked (D43).
            asked = f"screened with {self.model}" if self.provider else "no model was asked"
            lines = [f"escalated {change}: {asked}"]
            for reason in self.escalation:
                lines.append(
                    f"  {reason.rule} at {reason.path}" if reason.path else f"  {reason.rule}"
                )
                lines.append(f"    {reason.pattern}")
                lines.append(f"    {way_out(reason)}")
            lines.append(f"verdict: {self.verdict}")
        else:
            lines = [f"screened {change} with {self.model}"]
            lines.extend(
                f"  {judgment['question']} [{judgment['priority']}] {json.dumps(judgment['value'])}"
                for judgment in self.judgments
            )
            lines.append(f"verdict: {self.verdict}")
        lines.append(
            f"usage: {self.requests} request{'s' if self.requests != 1 else ''}, "
            f"{self.input_tokens} input tokens, {self.output_tokens} output tokens"
        )
        if self.provider:
            lines.append(f"provider: {self.provider}")
        elif self.escalation:
            lines.append("screened by: no provider was asked; a safeguard decided the change")
        if self.coverage is not None and self.coverage.gap is not None:
            lines.append(f"coverage: {self.coverage.gap.reason}")
        if self.resolved_model is not None:
            lines.append(f"served: {self.resolved_model}")
        if path is not None:
            lines.append(f"report: {path}")
        return "\n".join(lines)


def bank_digest(
    model: str,
    questions: Sequence[Question],
    weights: Mapping[str, float] | None = None,
    thresholds: Mapping[str, Threshold] | None = None,
) -> str:
    """The sha256 of the bank as configured: its model, its questions and the numbers it is decided with.

    The digest covers what the run was made of and never the file's path, so the same bank screened from
    another checkout digests the same, a reworded level is a different bank, and a recalibrated threshold
    is a different screening of it (S4, D26).
    """
    canonical = json.dumps(
        {
            "model": model,
            "questions": [
                {
                    "id": question.id,
                    "priority": question.priority,
                    "type": question.type,
                    "ask": question.ask,
                    "criteria": question.criteria,
                }
                for question in questions
            ],
            "weights": dict(weights or {}),
            "thresholds": {
                identifier: threshold.as_dict()
                for identifier, threshold in sorted((thresholds or {}).items())
            },
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
