"""verify's red-first proof: each satisfied scenario's covering test was seen failing before the fix.

The report carries, under `red`, the failing output of each scenario's covering test from before the fix. The gate
checks only that the excerpt is there and names a node the scenario's `covers` maps; whether it is a genuine
assertion failure of the behaviour is the reviewer's judgement.
"""

import unittest
from types import SimpleNamespace

import pytest
from agent_orchestrator import verify

pytestmark = pytest.mark.unit

NODE = "tests/test_a.py::test_S1_refuses_the_stale_plan"
TICKET = SimpleNamespace(id="T1", satisfies=["S1"], paths=["src/", "tests/test_a.py"])
FAILED = (
    "FAILED tests/test_a.py::test_S1_refuses_the_stale_plan - AssertionError: "
    "expected a refusal, got None"
)


def gate(red):
    report = {"contract": 3, "task_id": "t1", "covers": {"S1": [NODE]}}
    if red is not None:
        report["red"] = red
    return verify.red_first(TICKET, report)


class RedFirstProof(unittest.TestCase):
    @pytest.mark.spec("Scenario evidence")
    def test_a_satisfied_scenario_without_a_red_excerpt_fails_naming_it(self):
        findings = gate(None)
        self.assertEqual([f["check"] for f in findings], ["red"])
        self.assertIn("S1: no red excerpt", findings[0]["excerpt"])

    @pytest.mark.spec("Scenario evidence")
    def test_a_red_excerpt_that_names_another_node_fails_naming_the_scenario_and_its_nodes(self):
        findings = gate({"S1": "FAILED tests/test_b.py::test_other - AssertionError"})
        self.assertEqual([f["check"] for f in findings], ["red"])
        self.assertIn("S1", findings[0]["excerpt"])
        self.assertIn(NODE, findings[0]["excerpt"])

    @pytest.mark.spec("Scenario evidence")
    def test_a_red_excerpt_that_names_the_covering_node_passes(self):
        self.assertEqual(gate({"S1": FAILED}), [])
