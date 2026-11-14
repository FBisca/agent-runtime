"""The provider's native judgments, validated against the questions they answer (D44, D50).

The port returns judgments as the provider answered them; the run is what attaches the question's
priority, scope and revision, and refuses a value the question's type does not allow. That refusal is
the reason an invocation reports an incomplete execution (D40). A score question is answered with a
position on its ladder and carries the confidence that came beside it: a position off the ladder, a
confidence outside zero and one, or a score with no confidence at all is refused naming the question
(S1, S3).
"""

import pytest
from agent_review.errors import ScreeningError
from agent_review.judgments import attributed
from agent_review.questions import Question

pytestmark = pytest.mark.unit

BASE = "a" * 40
HEAD = "b" * 40

# A four-level ladder, best first, so the levels it declares are 0..3; and two declared options.
LEVELS = [
    "the change keeps one reason to change",
    "the change implies a second reason",
    "the change mixes two reasons into one module",
    "the change has no single reason a reader could name",
]
OPTIONS = {"compatible": "callers keep working", "breaking": "a caller must change"}

QUESTIONS = (
    Question(id="contract.error-handling", priority="critical", ask="Errors handled", type="noul"),
    Question(id="design.naming", priority="advisory", ask="Names say what they mean", type="noul"),
    Question(
        id="design.one-reason",
        priority="high",
        ask="One reason to change",
        type="score",
        criteria=LEVELS,
    ),
    Question(
        id="contract.shape", priority="high", ask="Shape kept", type="choice", criteria=OPTIONS
    ),
)


def test_each_native_answer_is_kept_as_answered():
    judgments = attributed(
        QUESTIONS,
        [
            {"question": "contract.error-handling", "value": 0.9},
            {"question": "design.one-reason", "value": 2.0, "confidence": 0.8},
            {"question": "contract.shape", "value": "compatible"},
        ],
        base=BASE,
        head=HEAD,
    )
    assert [(judgment["question"], judgment.get("value")) for judgment in judgments] == [
        ("contract.error-handling", 0.9),
        ("design.one-reason", 2.0),
        ("contract.shape", "compatible"),
    ]


def test_a_judgment_carries_its_question_priority_scope_and_revision():
    (judgment,) = attributed(
        QUESTIONS, [{"question": "design.naming", "value": 0.2}], base=BASE, head=HEAD
    )
    assert judgment == {
        "question": "design.naming",
        "priority": "advisory",
        "scope": "whole-diff",
        "revision": f"{BASE}..{HEAD}",
        "value": 0.2,
    }


def test_the_report_keeps_the_configuration_order_whatever_order_it_was_answered_in():
    answered = [
        {"question": "contract.shape", "value": "compatible"},
        {"question": "contract.error-handling", "value": 0.9},
    ]
    assert [j["question"] for j in attributed(QUESTIONS, answered, base=BASE, head=HEAD)] == [
        "contract.error-handling",
        "contract.shape",
    ]


def test_a_question_the_screening_could_not_assess_is_simply_absent():
    assert attributed(QUESTIONS, [], base=BASE, head=HEAD) == ()


def test_S3_a_score_judgment_keeps_its_confidence_beside_the_value():
    (judgment,) = attributed(
        QUESTIONS,
        [{"question": "design.one-reason", "value": 1.0, "confidence": 0.42}],
        base=BASE,
        head=HEAD,
    )
    assert judgment["value"] == 1.0, (
        "the level is the value the provider answered, never a threshold"
    )
    assert judgment["confidence"] == pytest.approx(0.42), (
        "a score without its confidence is a number with no meaning"
    )


def test_S1_a_score_between_two_rungs_is_the_providers_own_number():
    (judgment,) = attributed(
        QUESTIONS,
        [{"question": "design.one-reason", "value": 1.22, "confidence": 0.9}],
        base=BASE,
        head=HEAD,
    )
    assert judgment["value"] == 1.22, (
        "1.22 sits on a four-level ladder (0..3) and is carried as the provider answered it"
    )
    assert judgment["confidence"] == pytest.approx(0.9)


@pytest.mark.parametrize(
    "value",
    [
        4.0,
        3.5,
        -0.4,
        -1.0,
        int("1" + "0" * 400),
        float("inf"),
        float("nan"),
        "2",
        True,
    ],
    ids=[
        "past-the-last",
        "past-the-last-rung",
        "below-the-first",
        "far-below-the-first",
        "integer-too-large-for-a-float",
        "infinite",
        "not-a-number",
        "text",
        "boolean",
    ],
)
def test_S1_a_score_outside_its_ladder_is_refused_naming_the_question_and_the_range(value):
    with pytest.raises(ScreeningError) as refused:
        attributed(
            QUESTIONS,
            [{"question": "design.one-reason", "value": value, "confidence": 0.9}],
            base=BASE,
            head=HEAD,
        )
    assert "design.one-reason" in str(refused.value)
    assert "on its ladder of 4 declared levels" in str(refused.value), (
        "a refusal names the range the ladder allows, in the wording this rule introduced"
    )


@pytest.mark.parametrize(
    "confidence",
    [1.4, -0.1, "0.8", True],
    ids=["above-one", "below-zero", "text", "boolean"],
)
def test_S3_a_confidence_outside_zero_and_one_is_refused_naming_the_question(confidence):
    with pytest.raises(ScreeningError) as refused:
        attributed(
            QUESTIONS,
            [{"question": "design.one-reason", "value": 1.0, "confidence": confidence}],
            base=BASE,
            head=HEAD,
        )
    assert "design.one-reason" in str(refused.value)
    assert "confidence" in str(refused.value).lower(), "a refusal names the way out"


def test_S3_a_score_with_no_confidence_is_refused_naming_the_question():
    with pytest.raises(ScreeningError) as refused:
        attributed(
            QUESTIONS, [{"question": "design.one-reason", "value": 1.0}], base=BASE, head=HEAD
        )
    assert "design.one-reason" in str(refused.value)
    assert "confidence" in str(refused.value).lower(), "a refusal names the way out"


@pytest.mark.parametrize(
    "question, value, allowed",
    [
        ("contract.error-handling", "true", "the probability between 0 and 1"),
        ("contract.error-handling", True, "the probability between 0 and 1"),
        ("contract.error-handling", 1.5, "the probability between 0 and 1"),
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
            QUESTIONS, [{"question": "contract.not-asked", "value": 0.9}], base=BASE, head=HEAD
        )


def test_a_question_answered_twice_is_refused():
    with pytest.raises(ScreeningError, match="design.naming"):
        attributed(
            QUESTIONS,
            [
                {"question": "design.naming", "value": 0.9},
                {"question": "design.naming", "value": 0.1},
            ],
            base=BASE,
            head=HEAD,
        )


def test_a_judgment_that_is_not_an_object_is_refused():
    with pytest.raises(ScreeningError, match="judgment 0"):
        attributed(QUESTIONS, ["design.naming"], base=BASE, head=HEAD)
