"""The provider's native judgments, validated against the questions they answer (D44, D50).

The port returns judgments as the provider answered them; the run is what attaches the question's
priority, scope and revision, and refuses a value the question's type does not allow. That refusal is
the reason an invocation reports an incomplete execution (D40).
"""

import pytest
from agent_review.errors import ScreeningError
from agent_review.judgments import attributed
from agent_review.questions import Question

pytestmark = pytest.mark.unit

BASE = "a" * 40
HEAD = "b" * 40

QUESTIONS = (
    Question(
        id="contract.error-handling", priority="critical", ask="Errors handled", type="boolean"
    ),
    Question(
        id="design.naming", priority="advisory", ask="Names say what they mean", type="boolean"
    ),
    Question(id="design.one-reason", priority="high", ask="One reason to change", type="score"),
    Question(id="contract.shape", priority="high", ask="Shape kept", type="choice"),
)


def test_each_native_value_is_kept_as_answered():
    judgments = attributed(
        QUESTIONS,
        [
            {"question": "contract.error-handling", "value": True},
            {"question": "design.one-reason", "value": 0.5},
            {"question": "contract.shape", "value": "compatible"},
        ],
        base=BASE,
        head=HEAD,
    )
    assert [(judgment["question"], judgment["value"]) for judgment in judgments] == [
        ("contract.error-handling", True),
        ("design.one-reason", 0.5),
        ("contract.shape", "compatible"),
    ]


def test_a_judgment_carries_its_question_priority_scope_and_revision():
    (judgment,) = attributed(
        QUESTIONS, [{"question": "design.naming", "value": False}], base=BASE, head=HEAD
    )
    assert judgment == {
        "question": "design.naming",
        "priority": "advisory",
        "scope": "whole-diff",
        "revision": f"{BASE}..{HEAD}",
        "value": False,
    }


def test_the_report_keeps_the_configuration_order_whatever_order_it_was_answered_in():
    answered = [
        {"question": "contract.shape", "value": "compatible"},
        {"question": "contract.error-handling", "value": True},
    ]
    assert [j["question"] for j in attributed(QUESTIONS, answered, base=BASE, head=HEAD)] == [
        "contract.error-handling",
        "contract.shape",
    ]


def test_a_question_the_screening_could_not_assess_is_simply_absent():
    assert attributed(QUESTIONS, [], base=BASE, head=HEAD) == ()


@pytest.mark.parametrize(
    "question, value, allowed",
    [
        ("contract.error-handling", "true", "true or false"),
        ("contract.error-handling", 1, "true or false"),
        ("design.one-reason", 1.5, "a number between 0 and 1"),
        ("design.one-reason", "0.5", "a number between 0 and 1"),
        ("contract.shape", "", "one of its declared options"),
    ],
)
def test_a_value_the_type_does_not_allow_is_refused_naming_the_question(question, value, allowed):
    with pytest.raises(ScreeningError) as refused:
        attributed(QUESTIONS, [{"question": question, "value": value}], base=BASE, head=HEAD)
    assert question in str(refused.value)
    assert allowed in str(refused.value), "a refusal names the way out"


def test_a_judgment_with_no_value_is_refused_naming_the_question():
    with pytest.raises(ScreeningError, match="design.naming"):
        attributed(QUESTIONS, [{"question": "design.naming"}], base=BASE, head=HEAD)


def test_a_question_the_configuration_does_not_ask_is_refused():
    with pytest.raises(ScreeningError, match="contract.not-asked"):
        attributed(
            QUESTIONS, [{"question": "contract.not-asked", "value": True}], base=BASE, head=HEAD
        )


def test_a_question_answered_twice_is_refused():
    with pytest.raises(ScreeningError, match="design.naming"):
        attributed(
            QUESTIONS,
            [
                {"question": "design.naming", "value": True},
                {"question": "design.naming", "value": False},
            ],
            base=BASE,
            head=HEAD,
        )


def test_a_judgment_that_is_not_an_object_is_refused():
    with pytest.raises(ScreeningError, match="judgment 0"):
        attributed(QUESTIONS, ["design.naming"], base=BASE, head=HEAD)
