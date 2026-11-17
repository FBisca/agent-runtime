"""The reviewer's verdict contract and the rulebook a repository names: pure decisions, no process."""

import json

import pytest
from agent_orchestrator import contracts
from agent_orchestrator.driver.handoff import read_verdict

pytestmark = pytest.mark.unit

FINDING = {"file": "src/a.py", "line": 7, "quote": "x = 1", "claim": "x is never read"}


@pytest.mark.spec("A verdict is handed in through the runtime")
@pytest.mark.parametrize(
    "verdict",
    [
        {"verdict": "pass"},
        {"verdict": "pass", "findings": []},
        {"verdict": "block", "findings": [FINDING]},
        {
            "verdict": "block",
            "findings": [dict(FINDING, rule="Every changed behaviour has a test")],
        },
    ],
)
def test_a_verdict_is_accepted_whether_or_not_a_finding_names_a_rule(verdict):
    assert read_verdict(json.dumps(verdict)) == (verdict, None)


@pytest.mark.spec("A verdict is handed in through the runtime")
def test_a_verdict_in_a_code_fence_is_read():
    verdict, error = read_verdict("```json\n%s\n```" % json.dumps({"verdict": "pass"}))
    assert (verdict, error) == ({"verdict": "pass"}, None)


@pytest.mark.spec("A refused verdict goes back to its reviewer")
@pytest.mark.parametrize(
    ("text", "named"),
    [
        ("pass, looks fine", "not the verdict JSON"),
        (json.dumps({"verdict": "block"}), "block needs at least one finding"),
        (json.dumps({"verdict": "pass", "findings": [FINDING]}), "pass has none"),
        (json.dumps({"verdict": "maybe"}), "verdict"),
        (json.dumps({"verdict": "pass", "note": "x"}), "note"),
        (json.dumps({"verdict": "block", "findings": [dict(FINDING, line=0)]}), "findings.0.line"),
        (json.dumps({"verdict": "block", "findings": [{"file": "a", "line": 1}]}), "quote"),
        ('["block"]', "must be a JSON object"),
    ],
)
def test_a_wrong_verdict_is_refused_naming_what_is_wrong(text, named):
    verdict, error = read_verdict(text)
    assert verdict is None
    assert named in error


@pytest.mark.spec("A refused verdict goes back to its reviewer")
def test_every_error_of_a_wrong_verdict_is_named_at_once():
    _, error = read_verdict(
        json.dumps({"verdict": "block", "findings": [{"file": "a", "line": 0}]})
    )
    for field in ("findings.0.line", "findings.0.quote", "findings.0.claim"):
        assert field in error


def test_review_findings_become_numbered_blocking_items_a_fix_round_can_carry():
    verdict = contracts.validate_verdict(
        {"verdict": "block", "findings": [FINDING, dict(FINDING, rule="A rule")]}
    )
    items = contracts.review_items(verdict)
    assert [(i["id"], i["kind"], i["source"]) for i in items] == [
        ("R1", "blocking", "review"),
        ("R2", "blocking", "review"),
    ]
    assert contracts.make_findings("review", 1, items)["items"] == items
