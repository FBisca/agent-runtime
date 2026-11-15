"""The calibrated numbers decide an escalation: a value past its threshold, or a confidence below its band.

A judgment is escalated by the tool, never by the model (D06, D07): the bank declares a per-question
threshold - the value an answer must clear - and a confidence band, and this reads them against the
judgments the provider answered. A noul's probability escalates below its value and a score's position
above it, because a noul says how likely the change satisfies the question while a score's ladder runs from
best to worst (D44, D48). The band is the confidence the provider answered beside the value and never the
value itself: a noul's probability is not a confidence, so an answer that brought none can only escalate on
its value (P1-D5).

Only a critical or high judgment escalates here: advisory uncertainty is recorded in the judgment and never
escalates alone (D08, D27). A question the bank does not calibrate escalates on nothing, because a threshold
nobody declared would escalate on a number the model chose; the frozen calibration is the only number that
decides (D26, D48).

Each reason keeps what a person needs to read it: the rule that fired, the question it judged, the scope the
judgment was made over, the value and the confidence, and the calibrated declaration they crossed.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from agent_review.questions import Question, QuestionBank, Threshold
from agent_review.safeguards import (
    CONFIDENCE_BELOW_BAND,
    CONFIGURATION,
    VALUE_ABOVE_THRESHOLD,
    VALUE_BELOW_THRESHOLD,
    EscalationReason,
)

# The judgments whose calibrated numbers escalate: advisory uncertainty is recorded and never escalates alone.
ESCALATING = ("critical", "high")


def escalation(
    judgments: Sequence[Mapping[str, Any]], bank: QuestionBank
) -> tuple[EscalationReason, ...]:
    """The reasons the bank's calibrated numbers give each critical or high judgment (D06, D08, D48).

    `judgments` are the attributed judgments a screening emitted, so the scope and the value are read from
    what the provider answered and the threshold from what the configuration declared.
    """
    asked = {question.id: question for question in bank.questions}
    reasons: list[EscalationReason] = []
    for judgment in judgments:
        identifier = judgment.get("question")
        if not isinstance(identifier, str):
            continue
        question = asked.get(identifier)
        if question is None or question.priority not in ESCALATING:
            continue
        threshold = bank.threshold(question)
        if threshold is None:
            continue
        reasons.extend(_value(judgment, question, threshold))
        reasons.extend(_band(judgment, question, threshold))
    return tuple(reasons)


def _value(
    judgment: Mapping[str, Any], question: Question, threshold: Threshold
) -> tuple[EscalationReason, ...]:
    """The value rule that fired for `judgment`, in its question's own direction, or nothing."""
    value = judgment.get("value")
    if threshold.value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return ()
    if question.type == "noul" and value < threshold.value:
        rule = VALUE_BELOW_THRESHOLD
        detail = (
            f"the noul answered {value} for {question.id}, below its calibrated threshold "
            f"{threshold.value}"
        )
    elif question.type == "score" and value > threshold.value:
        rule = VALUE_ABOVE_THRESHOLD
        detail = (
            f"the score answered {value} for {question.id}, above its calibrated threshold "
            f"{threshold.value}"
        )
    else:
        return ()
    return (
        _reason(
            rule,
            judgment,
            question,
            f"thresholds[{question.id}].value={threshold.value}",
            detail,
        ),
    )


def _band(
    judgment: Mapping[str, Any], question: Question, threshold: Threshold
) -> tuple[EscalationReason, ...]:
    """The confidence rule that fired for `judgment`, or nothing when it answered no confidence."""
    confidence = judgment.get("confidence")
    if (
        threshold.confidence is None
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or confidence >= threshold.confidence
    ):
        return ()
    detail = (
        f"the answer to {question.id} came with confidence {confidence}, below its calibrated band "
        f"{threshold.confidence}"
    )
    return (
        _reason(
            CONFIDENCE_BELOW_BAND,
            judgment,
            question,
            f"thresholds[{question.id}].confidence={threshold.confidence}",
            detail,
        ),
    )


def _reason(
    rule: str,
    judgment: Mapping[str, Any],
    question: Question,
    pattern: str,
    detail: str,
) -> EscalationReason:
    """One escalation signal: the rule, what it judged, and the calibrated declaration it crossed."""
    return EscalationReason(
        rule=rule,
        path="",
        pattern=pattern,
        source=CONFIGURATION,
        question=question.id,
        scope=str(judgment.get("scope", "")),
        value=judgment.get("value"),
        confidence=judgment.get("confidence"),
        detail=detail,
    )
