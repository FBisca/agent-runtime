"""The calibrated numbers decide an escalation: a value past its threshold, or a confidence below its band.

01-agent-review-smart-screening/S5 (D06, D08, D27, D48): a critical or high judgment escalates when a noul's
probability falls below its question's calibrated value or a score's position rises above it, or when the
confidence the provider answered beside the value falls below the question's band. The reason names the
rule, the question, the scope, the value and the confidence, so a person can read what fired and where. An
advisory judgment's uncertainty is recorded in its judgment and never escalates alone, and a noul's
probability is never read as a confidence: the band is only the confidence the provider answered beside
the value, and a question no calibration names is recorded without escalating on a number nobody chose
(D07, D48).
"""

import pytest
from agent_review import calibration
from agent_review.questions import Question, QuestionBank, Threshold

pytestmark = pytest.mark.unit

# One bank with every shape the rules read: noul, score, advisory and a question no calibration names.
BANK = QuestionBank(
    model="test-model-v1",
    questions=(
        Question(id="contract.test-evidence", priority="critical", ask="Would a test fail"),
        Question(
            id="contract.error-handling",
            priority="critical",
            ask="How severely does the change violate error handling",
            type="score",
            criteria=["none", "mild", "moderate", "major", "severe"],
        ),
        Question(id="repo.style", priority="advisory", ask="Does the style hold"),
        Question(id="repo.uncalibrated", priority="high", ask="Is the migration safe"),
    ),
    thresholds={
        "contract.test-evidence": Threshold(value=0.6, confidence=0.7),
        "contract.error-handling": Threshold(value=1.5, confidence=0.6),
        "repo.style": Threshold(value=1.5, confidence=0.9),
    },
)

REVISION = f"{'a' * 40}..{'b' * 40}"


def judged(question, value, *, confidence=None, scope="whole-diff"):
    """One judgment as `judgments.collected` emits it, with the confidence only when one was answered."""
    judgment = {
        "question": question,
        "priority": next(entry.priority for entry in BANK.questions if entry.id == question),
        "scope": scope,
        "revision": REVISION,
        "value": value,
    }
    if confidence is not None:
        judgment["confidence"] = confidence
    return judgment


@pytest.mark.spec("Calibrated escalation signals")
def test_S5_a_noul_below_its_threshold_escalates_with_its_question_scope_value_and_confidence():
    reasons = calibration.escalation((judged("contract.test-evidence", 0.2),), BANK)

    assert [reason.rule for reason in reasons] == ["value-below-threshold"]
    reason = reasons[0].as_dict()
    assert reason["question"] == "contract.test-evidence", "the signal names the question it judged"
    assert reason["scope"] == "whole-diff", "and the scope it was made over"
    assert reason["value"] == 0.2, "and the value that was below the threshold"
    assert reason["confidence"] is None, "a noul carries no confidence, so none is recorded"
    assert reason["source"] == "configuration", "the threshold is the run's own declaration"
    assert "contract.test-evidence" in reason["pattern"] and "0.6" in reason["pattern"], (
        "the pattern names the calibrated declaration that fired"
    )


@pytest.mark.parametrize(
    ("value", "rule"),
    [(2.0, "value-above-threshold"), (1.5, None), (0.0, None)],
    ids=["above", "at-the-threshold", "below"],
)
@pytest.mark.spec("Calibrated escalation signals")
def test_S5_a_score_escalates_only_above_its_threshold(value, rule):
    reasons = calibration.escalation(
        (judged("contract.error-handling", value, confidence=0.9),), BANK
    )

    assert [reason.rule for reason in reasons] == ([] if rule is None else [rule]), (
        "a score's ladder runs from best to worst, so its position escalating is above the value"
    )


@pytest.mark.spec("Calibrated escalation signals")
def test_S5_a_confidence_below_the_band_escalates_even_when_the_value_passes():
    reasons = calibration.escalation(
        (judged("contract.error-handling", 0.5, confidence=0.4),), BANK
    )

    assert [reason.rule for reason in reasons] == ["confidence-below-band"]
    reason = reasons[0].as_dict()
    assert (reason["value"], reason["confidence"]) == (0.5, 0.4), (
        "a confidence signal keeps the value that passed and the confidence that did not"
    )
    assert "0.6" in reason["pattern"], "the pattern names the band that fired"


@pytest.mark.spec("Calibrated escalation signals")
def test_S5_advisory_uncertainty_is_recorded_without_escalating():
    assert calibration.escalation((judged("repo.style", 9.0, confidence=0.0),), BANK) == (), (
        "an advisory judgment's uncertainty is recorded in its judgment, never escalated alone (D08)"
    )


@pytest.mark.spec("Calibrated escalation signals")
def test_S5_a_raw_probability_is_never_read_as_a_calibrated_confidence():
    # 0.65 clears the 0.6 value and sits below the 0.7 band, so only a confidence could fire it; a noul
    # answer carries no confidence and the probability is not one.
    assert calibration.escalation((judged("contract.test-evidence", 0.65),), BANK) == (), (
        "a noul's probability is a value, never a calibrated confidence (P1-D5)"
    )


@pytest.mark.spec("Calibrated escalation signals")
def test_S5_a_question_with_no_declared_calibration_escalates_on_nothing():
    assert calibration.escalation((judged("repo.uncalibrated", 0.01),), BANK) == (), (
        "a threshold nobody declared would escalate on a number the model chose (D07, D48)"
    )
