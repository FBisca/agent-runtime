"""verify's covers invariant: an implementation must provide its evidence.

Every scenario a ticket satisfies maps, in the report's `covers`, to a test node that exists and changed in the
ticket. Nothing a plan says about a scenario exempts it. Whether a node exists and changed is `node_problem`'s,
which `test_verify.py` drives against a real checkout; here it is answered in process.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import pytest
from agent_orchestrator import verify

pytestmark = pytest.mark.unit

NODE = "tests/test_a.py::test_s1"
TICKET = SimpleNamespace(id="T1", satisfies=["S1"], paths=["src/", "tests/test_a.py"])


def gate(covers):
    report = {"contract": 3, "task_id": "t1", "covers": covers}
    return verify.covers(TICKET, report, "/repo", "main", {"tests/test_a.py"})


class ImplementationProvidesItsEvidence(unittest.TestCase):
    @pytest.mark.spec("Scenario evidence")
    def test_a_satisfied_scenario_without_a_changed_covering_node_fails_naming_it(self):
        self.assertEqual(
            gate({}),
            [
                {
                    "check": "covers",
                    "excerpt": "S1: no test node in covers; map the scenario to the tests that prove it",
                }
            ],
        )

    @pytest.mark.spec("Scenario evidence")
    def test_the_same_report_with_a_changed_covering_node_passes(self):
        with mock.patch.object(verify, "node_problem", return_value=None) as problem:
            self.assertEqual(gate({"S1": [NODE]}), [])
        problem.assert_called_once_with("/repo", NODE, "main", {"tests/test_a.py"}, "")
