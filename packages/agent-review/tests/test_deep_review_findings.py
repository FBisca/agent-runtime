"""What a deep review judges from the reviewer's answer (02-agent-review-deep-review/S4, S5, S6).

Every fired critical/high signal is answered on its own, in the order it fired, and the disposition carries
what the signal was judged on: the value the screening answered, the confidence it came with and the
calibrated declaration that fired (D16, D17, P2-D6). A confirmed finding on a critical-priority question is
blocking whatever the reviewer's own flag said, a reviewer that stops leaves an incomplete review carrying
the evidence it had and is never retried, and a signal nobody disposed of stays unresolved (D20, D34, D39).

This is the seam the spawn hands its answer to: the reviewer itself is checked where it runs in
`test_deep_review_spawn`.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from agent_review.deep_review import FOCUSED, Finding, Review, Settings, Target, review
from agent_review.questions import Question, QuestionBank
from agent_review.report import Report
from agent_review.safeguards import EscalationReason

pytestmark = pytest.mark.unit

QUESTION = "repo.migration-safety"
MIGRATION = "db/migrations/002_index.sql"

TARGET = Target(
    diff="diff --git a/store.py b/store.py\n+    with open(path) as stream:\n",
    base="0" * 40,
    head="1" * 40,
    paths=("store.py",),
    root=Path("/tmp/checkout"),
)

BLOCKING_FINDING = Finding(
    id="F1",
    classification="critical",
    path=MIGRATION,
    line=1,
    requirement=QUESTION,
    evidence="the index is created without a way back",
    correction="add the down migration beside it",
    blocking=True,
)

BANK = QuestionBank(
    model="jev-latest",
    questions=(Question(id=QUESTION, priority="critical", ask="How severe is the violation"),),
)


def signal(rule, question=QUESTION, value=None, confidence=None, pattern=""):
    """One fired reason, carrying what its threshold or band judged."""
    return EscalationReason(
        rule=rule,
        path="" if question else MIGRATION,
        pattern=pattern or f"thresholds[{question}].value=1.5",
        question=question,
        value=value,
        confidence=confidence,
    )


def reported_finding(blocking=False, requirement=QUESTION):
    """The reviewer's finding on the critical question, blocking only if the reviewer says so (D15).

    `requirement` is whatever the reviewer named as the violated requirement: a question id, or the spec
    requirement or principle that question's contract comes from (D15), which the review may not have heard of.
    """
    return {
        "id": "F1",
        "classification": "critical",
        "path": MIGRATION,
        "line": 1,
        "requirement": requirement,
        "evidence": "the index is created without a way back",
        "correction": "add the down migration beside it",
        "blocking": blocking,
    }


def confirmation(rule):
    """The reviewer's answer to one fired signal: it confirmed it, resting on the finding it reported."""
    return {
        "signal": rule,
        "question": QUESTION,
        "disposition": "confirmed",
        "evidence": "db/migrations/002_index.sql:1 answers it",
        "finding": "F1",
    }


def dismissal(rule):
    """The reviewer's answer to one fired signal: it dismissed it, citing where the change answers it."""
    return {
        "signal": rule,
        "question": QUESTION,
        "disposition": "dismissed",
        "evidence": "store.py:2 reads the path it was given",
    }


class Reviewer:
    """The one seam a review reaches, canned: what it answers, or the stop it reports instead."""

    harness: str = "pi"
    controls: tuple[str, ...] = ("--permission-mode", "plan")

    def __init__(self, answer=None):
        self.answer = {} if answer is None else answer
        self.spawned = []

    def spawn(self, request):
        self.spawned.append(request)
        return self.answer


def test_S4_each_fired_signal_carries_its_own_disposition_and_what_judged_it():
    """Two signals fired on one answer, and each disposition restates its own number and declaration."""
    value = signal(
        "value-above-threshold",
        value=2.5,
        confidence=0.3,
        pattern="thresholds[repo.migration-safety].value=1.5",
    )
    band = signal(
        "confidence-below-band",
        value=2.5,
        confidence=0.3,
        pattern="thresholds[repo.migration-safety].confidence=0.6",
    )
    reviewer = Reviewer(
        {
            "dispositions": [
                dismissal("value-above-threshold"),
                dismissal("confidence-below-band"),
            ]
        }
    )

    reviewed = review(
        settings=Settings(),
        escalation=(value, band),
        target=TARGET,
        reviewer=reviewer,
        bank=BANK,
    )

    answered = reviewed.as_dict()["dispositions"]
    assert [entry["signal"] for entry in answered] == [
        "value-above-threshold",
        "confidence-below-band",
    ], "one disposition per fired signal, in the order they fired"
    assert [entry["value"] for entry in answered] == [2.5, 2.5]
    assert [entry["confidence"] for entry in answered] == [0.3, 0.3]
    assert [entry["threshold"] for entry in answered] == [
        "thresholds[repo.migration-safety].value=1.5",
        "thresholds[repo.migration-safety].confidence=0.6",
    ], "each disposition carries the declaration it was judged against"
    assert reviewed.as_dict()["unresolved"] == []
    assert reviewed.failure is None


@pytest.mark.parametrize(
    "requirement",
    [
        pytest.param(QUESTION, id="the-question-id"),
        pytest.param("R15", id="a-spec-requirement"),
        pytest.param("evidence before opinion", id="a-principle"),
    ],
)
def test_S4_a_confirmed_finding_on_a_mandatory_question_blocks_whatever_the_reviewer_said(
    requirement,
):
    """The reviewer reported a contract violation but called it advisory: the review keeps it blocking.

    Whichever name it gave the requirement: what decides is the confirmed disposition of a signal that fired
    on a critical-priority question, so the guarantee never rests on the reviewer spelling that question's id.
    """
    value = signal("value-above-threshold", value=2.5, confidence=0.3)
    reviewer = Reviewer(
        {
            "findings": [reported_finding(requirement=requirement)],
            "dispositions": [confirmation("value-above-threshold")],
        }
    )

    reviewed = review(
        settings=Settings(),
        escalation=(value,),
        target=TARGET,
        reviewer=reviewer,
        bank=BANK,
    )

    assert reviewed.as_dict()["findings"][0]["blocking"] is True, (
        "a confirmed mandatory-contract violation cannot be downgraded (D16)"
    )
    assert reviewed.verdict == "blocked"
    assert len(reviewer.spawned) == 1, "one reviewer, never a retry"


def test_S5_a_reviewer_that_stops_is_incomplete_keeping_the_evidence_it_had_and_never_retried():
    """A reviewer that timed out still answered something: the review keeps it and says it stopped."""
    value = signal("value-above-threshold", value=2.5, confidence=0.3)
    reviewer = Reviewer(
        {
            "findings": [reported_finding()],
            "dispositions": [confirmation("value-above-threshold")],
            "failure": "the reviewer timed out after 600 s",
        }
    )

    reviewed = review(
        settings=Settings(),
        escalation=(value,),
        target=TARGET,
        reviewer=reviewer,
        bank=BANK,
    )

    section = reviewed.as_dict()
    assert "timed out" in section["failure"]
    assert [finding["id"] for finding in section["findings"]] == ["F1"], (
        "the evidence it had is kept, never thrown away with the stop"
    )
    assert [entry["signal"] for entry in section["dispositions"]] == ["value-above-threshold"]
    assert section["spawns"] == 1, "no retry and no model fallback (D20)"
    assert reviewed.verdict is None, "an incomplete review concluded nothing"


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        pytest.param("no findings to report", "str, not a review", id="prose-instead-of-a-review"),
        pytest.param(
            {"dispositions": [{"signal": "value-above-threshold", "disposition": "dismissed"}]},
            "evidence",
            id="dismissed-without-evidence",
        ),
        pytest.param(
            {
                "dispositions": [
                    {
                        "signal": "protected-path",
                        "disposition": "confirmed",
                        "evidence": "db/migrations/002_index.sql:1 answers it",
                    }
                ]
            },
            "protected-path",
            id="disposes-of-a-signal-nobody-raised",
        ),
    ],
)
def test_S5_an_answer_a_review_cannot_accept_is_a_stop_naming_what_it_could_not_read(answer, named):
    """An unreadable answer is an incomplete execution naming it, never a review that silently drops it."""
    value = signal("value-above-threshold", value=2.5, confidence=0.3)
    reviewer = Reviewer(answer)

    reviewed = review(
        settings=Settings(),
        escalation=(value,),
        target=TARGET,
        reviewer=reviewer,
        bank=BANK,
    )

    assert reviewed.failure is not None, "an answer no review can accept is a stop (D20, S5)"
    assert named in reviewed.failure, "the stop names what could not be read"
    assert reviewed.as_dict()["findings"] == [], "nothing unreadable becomes a finding"
    assert len(reviewer.spawned) == 1, "a rejected answer is never re-asked (D20)"


def test_S5_a_signal_nobody_disposed_of_stays_unresolved_and_leaves_the_review_incomplete():
    """An empty answer resolves nothing: every fired signal is named, and the run is incomplete (D17)."""
    value = signal("value-above-threshold", value=2.5, confidence=0.3)
    band = signal("confidence-below-band", value=2.5, confidence=0.3)

    reviewed = review(
        settings=Settings(),
        escalation=(value, band),
        target=TARGET,
        reviewer=Reviewer({}),
        bank=BANK,
    )

    section = reviewed.as_dict()
    assert [entry["signal"] for entry in section["unresolved"]] == [
        "value-above-threshold",
        "confidence-below-band",
    ], "every fired signal is named as unanswered, not read as dismissed"
    assert reviewed.failure and "without a disposition" in reviewed.failure
    assert reviewed.verdict is None


def test_S6_the_verdict_tells_a_screen_from_a_clean_review_and_a_blocked_one():
    """`screened` is a screen nobody escalated; `passed` is a review that completed with nothing blocking."""
    value = signal("value-above-threshold", value=1.0, confidence=0.9)
    screened = Report(bank=BANK, base=TARGET.base, head=TARGET.head)
    reviewed = Review(
        harness="pi", scope=FOCUSED, instructions=(), signals=(value,), dispositions=()
    )

    assert screened.as_dict()["verdict"] == "screened"
    assert (
        replace(screened, escalation=(value,), deep_review=reviewed).as_dict()["verdict"]
        == "passed"
    ), "a clean deep review is not merely screened"
    assert (
        replace(
            screened,
            escalation=(value,),
            deep_review=replace(reviewed, findings=(BLOCKING_FINDING,)),
        ).as_dict()["verdict"]
        == "blocked"
    )
