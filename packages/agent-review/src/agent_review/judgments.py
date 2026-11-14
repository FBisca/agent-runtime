"""The provider's native judgments, checked against the questions they answer and given their provenance.

The port returns values as the provider answered them (D44); this is where a value the question's type
does not allow is refused, naming the question and the way out, so the invocation reports an incomplete
execution instead of a verdict (D40). A judgment is emitted in configuration order whatever order it was
answered in, and carries its question's priority, its scope and the revision it was made on (D07, D44,
D46). A question the screening left out is simply unanswered: nothing is guessed at or invented.

A bounded yes/no question is a noul, and the provider answers it with the probability that the change
satisfies it: the value is that probability as answered, never a `true` this tool invented by comparing
it with a threshold. A graded question is a score, and the provider answers the level it graded with the
confidence it graded it with, both kept as answered: a level its ladder does not declare - 0.5 between a
five-level ladder's rungs is no level - and a confidence outside zero and one are refused, and so is a
level with no confidence at all, because a score without one is a number with no meaning (S3).
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agent_review.errors import ScreeningError
from agent_review.questions import Question

# P0 screens the change between two revisions as one unit, so every judgment's scope is that whole diff
# (D09, D44). A later phase's file and hunk scopes replace it where they apply.
SCOPE = "whole-diff"

NOUL_VALUE = "the probability between 0 and 1 that the change satisfies it"
NO_LEVELS_VALUE = "one of its declared levels, and it declares none: add a ladder of levels"
CONFIDENCE_VALUE = "the confidence between 0 and 1 that comes beside its level"
CHOICE_VALUE = "one of its declared options"


def attributed(
    questions: Sequence[Question],
    native: Iterable[Any],
    *,
    base: str,
    head: str,
) -> tuple[dict[str, Any], ...]:
    """Each answered question as a judgment with its provenance, in configuration order."""
    asked = {question.id: question for question in questions}
    answered = _answered(native, asked)
    revision = f"{base}..{head}"
    return tuple(
        {
            "question": question.id,
            "priority": question.priority,
            "scope": SCOPE,
            "revision": revision,
            **answered[question.id],
        }
        for question in questions
        if question.id in answered
    )


def _answered(native: Iterable[Any], asked: Mapping[str, Question]) -> dict[str, dict[str, Any]]:
    """The provider's native answers by question id, refusing what the run cannot attribute."""
    values: dict[str, dict[str, Any]] = {}
    for index, judgment in enumerate(native):
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
    return values


def _fields(question: Question, judgment: Mapping[str, Any]) -> dict[str, Any]:
    """A judgment's own fields: the value, and the confidence a score answers beside its level.

    A score without its confidence is a number with no meaning, so it is refused rather than recorded:
    the confidence is the provider's own abstention signal (S3).
    """
    fields = {"value": _value(question, judgment["value"])}
    if "confidence" in judgment:
        fields["confidence"] = _confidence(question, judgment["confidence"])
    elif question.type == "score":
        raise ScreeningError(
            f"the screening answered question {question.id!r} with a level and no confidence: "
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
        if not _is_level(question, value):
            raise _refused(question, value)
        return value
    if not isinstance(value, str) or not value.strip():
        raise _refused(question, value)
    return value


def _levels(question: Question) -> int:
    """How many levels a score question declares; its rungs are the numbers 0 up to one less (D44).

    The provider answers a score with the level it graded, so a value between two rungs names no level:
    a five-level ladder declares 0..4, and 0.5 is no more a level than 7 is (S3).
    """
    criteria = question.criteria
    return len(criteria) if isinstance(criteria, (list, tuple)) else 0


def _is_level(question: Question, value: Any) -> bool:
    """Whether `value` is one of the levels the question declares, never a number between them."""
    levels = _levels(question)
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and float(value).is_integer()
        and 0 <= value <= levels - 1
    )


def _allowed(question: Question) -> str:
    """What a judgment of this question may hold, in the question's own terms (D50)."""
    if question.type == "noul":
        return NOUL_VALUE
    if question.type == "score":
        levels = _levels(question)
        if not levels:
            return NO_LEVELS_VALUE
        return f"one of its {levels} declared levels, 0 to {levels - 1}"
    return CHOICE_VALUE


def _refused(question: Question, value: Any) -> ScreeningError:
    return ScreeningError(
        f"the screening answered question {question.id!r} with {value!r}, which a {question.type} "
        f"question does not allow: answer {_allowed(question)}"
    )
