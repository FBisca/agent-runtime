"""Structured requirement violations cannot masquerade as optional observations."""

import pytest

pytestmark = pytest.mark.unit

VIOLATION = {
    "source": "docs/spec.md",
    "id": "R2",
    "expected": "exit 2 means no command ran",
    "observed": "state save failed after the command ran, and exit 2 was returned",
}
EVIDENCE = {
    "command": "pytest -k post_launch_save",
    "exit": 1,
    "excerpt": "exit 2 after execution",
    "log_path": "/run/probe.log",
}


def review(item, verdict="pass"):
    return {
        "contract": 2,
        "commit": "abc1234",
        "verdict": verdict,
        "items": [item],
        "checks_run": [],
    }


@pytest.mark.parametrize("kind", ["concern", "nit", "question", "deferred"])
def test_S3_requirement_violation_requires_blocking_classification(kind):
    from agent_orchestrator import contracts

    item = {
        "id": "B1",
        "kind": kind,
        "summary": "rare post-launch save failure",
        "answer_required": False,
        "requirement_violation": VIOLATION,
        "file": "src/cli.py",
        "line": 42,
        "fix": "honor exit contract",
        "evidence": EVIDENCE,
    }
    for validate in (contracts.validate_review, contracts.ReviewV2.model_validate):
        with pytest.raises(ValueError, match="B1"):
            validate(review(item))


@pytest.mark.parametrize("violation", [{}, dict(VIOLATION, expected=""), "R2"])
def test_S3_requirement_metadata_must_name_the_contract_and_observed_breach(violation):
    from agent_orchestrator import contracts

    item = {
        "id": "B1",
        "kind": "blocking",
        "summary": "exit contract breached",
        "answer_required": False,
        "requirement_violation": violation,
        "file": "src/cli.py",
        "line": 42,
        "fix": "honor exit contract",
        "evidence": EVIDENCE,
    }
    for validate in (contracts.validate_review, contracts.ReviewV2.model_validate):
        with pytest.raises(ValueError, match="B1"):
            validate(review(item, "block"))


def test_control_optional_concern_and_legacy_clean_reviews_remain_valid():
    from agent_orchestrator import contracts

    concern = {
        "id": "C1",
        "kind": "concern",
        "summary": "optional refactor",
        "answer_required": False,
    }
    payload = review(concern)
    assert contracts.validate_review(payload) == payload
    assert contracts.ReviewV2.model_validate(payload).model_dump(exclude_unset=True) == payload
    contracts.validate_review(
        {"contract": 1, "commit": "abc1234", "verdict": "pass", "major": [], "deferred": []}
    )


def test_control_well_formed_requirement_blocker_is_accepted():
    from agent_orchestrator import contracts

    item = {
        "id": "B1",
        "kind": "blocking",
        "summary": "exit contract breached",
        "answer_required": False,
        "requirement_violation": VIOLATION,
        "file": "src/cli.py",
        "line": 42,
        "fix": "honor exit contract",
        "evidence": EVIDENCE,
    }
    contracts.validate_review(review(item, "block"))


@pytest.mark.parametrize("field", ["source", "id", "expected", "observed"])
@pytest.mark.parametrize("bad", [None, "", " ", 7])
def test_S3_every_violation_field_requires_nonempty_text(field, bad):
    from agent_orchestrator import contracts

    violation = dict(VIOLATION)
    if bad is None:
        violation.pop(field)
    else:
        violation[field] = bad
    item = {
        "id": "B1",
        "kind": "blocking",
        "summary": "requirement breached",
        "answer_required": False,
        "requirement_violation": violation,
        "file": "src/cli.py",
        "line": 42,
        "fix": "honor contract",
        "evidence": EVIDENCE,
    }
    for validate in (contracts.validate_review, contracts.ReviewV2.model_validate):
        with pytest.raises(ValueError, match="B1"):
            validate(review(item, "block"))
