"""The provider's native judgments, checked against the questions they answer and given their provenance.

The port returns values as the provider answered them (D44); this is where a value the question's type
does not allow is refused, naming the question and the way out, so the invocation reports an incomplete
execution instead of a verdict (D40). A judgment is emitted in configuration order whatever order it was
answered in, and carries its question's priority, its scope and the revision it was made on (D07, D44,
D46). A question the screening left out is simply unanswered: nothing is guessed at or invented.

A screening of a change larger than one request's budget asks the whole bank of each chunk, so the same
question is answered once per chunk. That is why a judgment's scope names what it was made over - the
whole diff when the change fitted one request, the chunk and its paths otherwise - and never a constant
that would make two different answers to one question indistinguishable in a frozen report (D41, D44).

A bounded yes/no question is a noul, and the provider answers it with the probability that the change
satisfies it: the value is that probability as answered, never a `true` this tool invented by comparing
it with a threshold. A graded question is a score, and the provider answers a position on its ladder
with the confidence it graded it with, both kept as answered: the ladder bounds the answer at 0 and at
one less than its declared levels, and any finite number between them - 1.22 on a five-level ladder -
is the provider's own answer, never snapped to a rung. An answer off that range and a confidence
outside zero and one are refused, and so is a score with no confidence at all, because a score without
one is a number with no meaning (S1, S3).
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agent_review.errors import ScreeningError
from agent_review.questions import Question

# What a judgment was made over when the change is one screening: the whole diff (D09, D44). A later stage
# splits a change into chunks, and each chunk's judgments name the chunk instead.
SCOPE = "whole-diff"

NOUL_VALUE = "the probability between 0 and 1 that the change satisfies it"
NO_LEVELS_VALUE = "one of its declared levels, and it declares none: add a ladder of levels"
CONFIDENCE_VALUE = "the confidence between 0 and 1 that comes beside its score"
CHOICE_VALUE = "one of its declared options"


@dataclass(frozen=True)
class Judged:
    """What a reply yielded: the judgments it made, and why it stopped when one was refused (S6)."""

    judgments: tuple[dict[str, Any], ...] = ()
    error: ScreeningError | None = None


def chunk_scope(index: int, total: int, files: Sequence[str]) -> str:
    """What one chunk's judgments were made over, so a report can tell two answers to one question apart.

    A change that fits one chunk is screened as the whole diff it is, and says so; a chunked change names
    its place in the order and the paths it carries, so a reader can find the region an answer is about.
    """
    if total == 1:
        return SCOPE
    return f"chunk {index}/{total}: {', '.join(files)}"


def attributed(
    questions: Sequence[Question],
    native: Iterable[Any],
    *,
    base: str,
    head: str,
    scope: str = SCOPE,
) -> tuple[dict[str, Any], ...]:
    """Each answered question as a judgment with its provenance, in configuration order.

    The strict form: one answer the run cannot attribute raises, so a caller that wants no partial
    verdict uses this, while a caller that keeps what a stopped run learned uses `collected` (S6).
    """
    held = collected(questions, native, base=base, head=head, scope=scope)
    if held.error is not None:
        raise held.error
    return held.judgments


def collected(
    questions: Sequence[Question],
    native: Iterable[Any],
    *,
    base: str,
    head: str,
    scope: str = SCOPE,
) -> Judged:
    """The judgments a reply earned and the reason it stopped, keeping what it learned before a refusal.

    A run that stops part-way keeps the judgments it already made and the usage it paid for: the report is
    the only record of a screening that happened, and discarding it hides both the cost and the judgement
    (S6, D47). The refused answer and everything after it are not learned, and nothing is guessed.
    """
    asked = {question.id: question for question in questions}
    answered: dict[str, dict[str, Any]] = {}
    for index, judgment in enumerate(native):
        try:
            _learn(index, judgment, asked, answered)
        except ScreeningError as error:
            return Judged(
                judgments=_emitted(questions, answered, base=base, head=head, scope=scope),
                error=error,
            )
    return Judged(judgments=_emitted(questions, answered, base=base, head=head, scope=scope))


def _emitted(
    questions: Sequence[Question],
    answered: Mapping[str, dict[str, Any]],
    *,
    base: str,
    head: str,
    scope: str,
) -> tuple[dict[str, Any], ...]:
    """The learned answers as judgments, in configuration order whatever order they were answered in."""
    revision = f"{base}..{head}"
    return tuple(
        {
            "question": question.id,
            "priority": question.priority,
            "scope": scope,
            "revision": revision,
            **answered[question.id],
        }
        for question in questions
        if question.id in answered
    )


def _learn(
    index: int,
    judgment: Any,
    asked: Mapping[str, Question],
    values: dict[str, dict[str, Any]],
) -> None:
    """Take one native answer into `values`, or refuse it naming what it should have been (D50)."""
    if not isinstance(judgment, Mapping):
        raise ScreeningError(
            f"the screening answered judgment {index} with {judgment!r}, which is not an object; "
            'a judgment is {"question": "<id>", "value": <native value>}'
        )
    identifier = judgment.get("question")
    if not isinstance(identifier, str) or identifier not in asked:
        raise ScreeningError(
            f"the screening answered {identifier!r}, which the question configuration does not "
            "ask; a judgment names a configured question id"
        )
    if identifier in values:
        raise ScreeningError(
            f"the screening answered question {identifier!r} more than once; "
            "answer each question once"
        )
    if "value" not in judgment:
        raise ScreeningError(
            f"the screening answered question {identifier!r} with no value: "
            f"answer it {_allowed(asked[identifier])}"
        )
    values[identifier] = _fields(asked[identifier], judgment)


def _fields(question: Question, judgment: Mapping[str, Any]) -> dict[str, Any]:
    """A judgment's own fields: the value, and the confidence that came beside a score.

    A score without its confidence is a number with no meaning, so it is refused rather than recorded:
    the confidence is the provider's own abstention signal (S3).
    """
    fields = {"value": _value(question, judgment["value"])}
    if "confidence" in judgment:
        fields["confidence"] = _confidence(question, judgment["confidence"])
    elif question.type == "score":
        raise ScreeningError(
            f"the screening answered question {question.id!r} with a score and no confidence: "
            f"answer {CONFIDENCE_VALUE}"
        )
    return fields


def _confidence(question: Question, confidence: Any) -> Any:
    """The confidence as answered, or a refusal naming the question and what it allows (S3)."""
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ScreeningError(
            f"the screening answered question {question.id!r} with confidence {confidence!r}, "
            f"which a {question.type} question does not allow: answer {CONFIDENCE_VALUE}"
        )
    return confidence


def _value(question: Question, value: Any) -> Any:
    """The value as the provider answered it, or a refusal naming what the question allows (D50)."""
    if question.type == "noul":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise _refused(question, value)
        return value
    if question.type == "score":
        if not _on_ladder(question, value):
            raise _refused(question, value)
        return value
    if not isinstance(value, str) or not value.strip():
        raise _refused(question, value)
    return value


def _levels(question: Question) -> int:
    """How many levels a score question declares; its ladder spans 0 up to one less (D44).

    A score is a position on that ladder, so a five-level ladder bounds the answer at 0 and 4: 1.22 is
    the provider's own answer, while 4.6 is off the ladder (S1).
    """
    criteria = question.criteria
    return len(criteria) if isinstance(criteria, (list, tuple)) else 0


def _on_ladder(question: Question, value: Any) -> bool:
    """Whether `value` is a finite number the ladder bounds: 0 up to one less than its levels.

    The ladder bounds the answer; it does not snap it to a rung, so a fractional position is allowed. The
    check is total over the values it admits: an `int` is compared as the integer it is, so a JSON integer
    too large for a float is refused rather than raised on, and only a `float` is asked whether it is finite.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    return 0 <= value <= _levels(question) - 1


def _allowed(question: Question) -> str:
    """What a judgment of this question may hold, in the question's own terms (D50)."""
    if question.type == "noul":
        return NOUL_VALUE
    if question.type == "score":
        levels = _levels(question)
        if not levels:
            return NO_LEVELS_VALUE
        return f"a number from 0 to {levels - 1} on its ladder of {levels} declared levels"
    return CHOICE_VALUE


def _refused(question: Question, value: Any) -> ScreeningError:
    return ScreeningError(
        f"the screening answered question {question.id!r} with {value!r}, which a {question.type} "
        f"question does not allow: answer {_allowed(question)}"
    )
