"""The provider's native judgments, checked against the questions they answer and given their provenance.

The port returns values as the provider answered them (D44); this is where a value the question's type
does not allow is refused, naming the question and the way out, so the invocation reports an incomplete
execution instead of a verdict (D40). A judgment is emitted in configuration order whatever order it was
answered in, and carries its question's priority, its scope and the revision it was made on (D07, D44,
D46). A question the screening left out is simply unanswered: nothing is guessed at or invented.
"""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from agent_review.errors import ScreeningError
from agent_review.questions import Question

# P0 screens the change between two revisions as one unit, so every judgment's scope is that whole diff
# (D09, D44). A later phase's file and hunk scopes replace it where they apply.
SCOPE = "whole-diff"

BOOLEAN_VALUE = "true or false"
SCORE_VALUE = "a number between 0 and 1"
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
            "value": answered[question.id],
        }
        for question in questions
        if question.id in answered
    )


def _answered(native: Iterable[Any], asked: Mapping[str, Question]) -> dict[str, Any]:
    """The provider's values by question id, refusing what the run cannot attribute to a question."""
    values: dict[str, Any] = {}
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
                f"answer it {_allowed(asked[identifier].type)}"
            )
        values[identifier] = _value(asked[identifier], judgment["value"])
    return values


def _value(question: Question, value: Any) -> Any:
    """The value as the provider answered it, or a refusal naming what the question allows (D50)."""
    if question.type == "boolean":
        if not isinstance(value, bool):
            raise _refused(question, value)
        return value
    if question.type == "score":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise _refused(question, value)
        return value
    if not isinstance(value, str) or not value.strip():
        raise _refused(question, value)
    return value


def _allowed(kind: str) -> str:
    return {"boolean": BOOLEAN_VALUE, "score": SCORE_VALUE}.get(kind, CHOICE_VALUE)


def _refused(question: Question, value: Any) -> ScreeningError:
    return ScreeningError(
        f"the screening answered question {question.id!r} with {value!r}, which a {question.type} "
        f"question does not allow: answer {_allowed(question.type)}"
    )
