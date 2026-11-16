"""What a deep review decides from configuration and the reasons that fired (02-agent-review-deep-review/S2).

The scope is decided by one sentence over the reasons: a reason that names the question it judged is a
calibrated signal, which points where the reviewer should look, and a reason about the change itself - a
sensitive path, a safeguard hit, a coverage gap - is nothing a question bounds, so the review is full
(D12, D43, P2-D3). The instruction sets are composed from the same reasons and the changed paths (P2-D5).

A declaration no stage can read is refused by its field path, before anything is screened: a `deep_review`
block with an unknown key, a reviewer given no time, or an instruction set whose `applies` names neither a
rule nor a path would otherwise be accepted and silently honoured nowhere (D29, D30).
"""

import pytest
from agent_review.deep_review import FOCUSED, FULL, scope_of, settings
from agent_review.errors import ReviewError
from agent_review.safeguards import EscalationReason

pytestmark = pytest.mark.unit

QUESTION = "repo.migration-safety"
MIGRATION = "db/migrations/002_index.sql"


def signal(rule, question=""):
    """One escalation reason, about the question it judged or about the change itself."""
    return EscalationReason(
        rule=rule,
        path="" if question else MIGRATION,
        pattern="thresholds[%s].value=1.5" % question if question else "db/migrations/**",
        question=question,
    )


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        pytest.param(
            (signal("value-above-threshold", QUESTION), signal("confidence-below-band", QUESTION)),
            FOCUSED,
            id="every-reason-names-its-question",
        ),
        pytest.param(
            (signal("value-above-threshold", QUESTION), signal("protected-path")),
            FULL,
            id="sensitive-path",
        ),
        pytest.param((signal("stdlib-only"),), FULL, id="critical-safeguard"),
        pytest.param((signal("coverage-gap"),), FULL, id="cross-file-context-gap"),
    ],
)
def test_S2_a_reason_about_the_change_makes_the_review_full(reasons, expected):
    assert scope_of(reasons) == expected


@pytest.mark.parametrize(
    ("declared", "field"),
    [
        pytest.param({"timeout_second": 600}, "unknown field 'timeout_second'", id="unknown-field"),
        pytest.param({"timeout_seconds": 0}, "timeout_seconds", id="no-time"),
        pytest.param({"timeout_seconds": "600"}, "timeout_seconds", id="timeout-text"),
        pytest.param(
            {"instructions": [{"id": "one"}]}, "instructions[0].text", id="instruction-without-text"
        ),
        pytest.param(
            {"instructions": [{"id": "one", "text": "a", "apply": {}}]},
            "instructions[0] has an unknown field 'apply'",
            id="instruction-unknown-field",
        ),
        pytest.param(
            {"instructions": [{"id": "one", "text": "a", "applies": {}}]},
            "applies declares nothing",
            id="applies-nothing",
        ),
        pytest.param(
            {
                "instructions": [
                    {"id": "one", "text": "a"},
                    {"id": "one", "text": "b"},
                ]
            },
            "repeats 'one'",
            id="repeated-id",
        ),
        pytest.param(
            {"instructions": [{"id": "one", "text": "a", "applies": {"rules": [""]}}]},
            "applies.rules[0]",
            id="empty-rule",
        ),
    ],
)
def test_a_deep_review_declaration_no_stage_can_read_is_refused_by_path(declared, field):
    with pytest.raises(ReviewError) as refused:
        settings(declared, "config.yaml")
    assert field in str(refused.value), "the refusal names the field a person must fix"


def test_a_configuration_that_declares_no_deep_review_takes_the_defaults():
    declared = settings(None, "config.yaml")
    assert declared.instructions == (), "a review with no declared set is still one review"
    assert declared.timeout_seconds > 0, "the reviewer is given time to stop in"
