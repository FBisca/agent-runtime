"""verify: the ticket gate's invariants (scope, integrity, covers) and the legacy path for Markdown plans."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import plan as yaml_plan
from agent_orchestrator import verify

pytestmark = pytest.mark.integration

IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
PLAN_REL = "docs/plans/2026-11-10-demo.plan.yaml"

OLD_TESTS = "def test_old():\n    assert True\n"


def model(**ticket):
    return yaml_plan.Plan.model_validate(
        {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "deliver",
            "spec": "docs/specs/demo.md",
            "outcome": ["S1"],
            "scenarios": [
                {
                    "id": "S1",
                    "when": "a",
                    "then": "b",
                    "evidence": {
                        "check": "pytest",
                        "baseline": "fail",
                        "oracle": ["test_oracle.py.oracle"],
                        "place": "tests/",
                    },
                },
                {"id": "S2", "when": "c", "then": "d"},
            ],
            "tickets": [
                dict(
                    {
                        "id": "T1",
                        "delivers": "x",
                        "satisfies": ["S1", "S2"],
                        "paths": ["src/a/", "tests/test_a.py"],
                        "size": "small",
                        "difficulty": "simple",
                    },
                    **ticket,
                )
            ],
        }
    )


class VerifyFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.write("tests/test_old.py", OLD_TESTS)
        self.write("README.md", "demo\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "ticket")

    def git(self, *args):
        done = subprocess.run(
            ["git", "-C", str(self.repo), *IDENTITY, *args], capture_output=True, text=True
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def gate(self, covers, **ticket):
        report = {"contract": 3, "task_id": "t1", "covers": covers}
        return verify.invariants(
            model(**ticket), "T1", report, repo=self.repo, base="main", plan_rel=PLAN_REL
        )

    def found(self, findings):
        return [(f["check"], f["excerpt"]) for f in findings]


class CoversTests(VerifyFixture):
    def test_S9_a_satisfied_scenario_mapped_to_changed_nodes_passes(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        self.assertEqual(
            self.gate({"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}),
            [],
        )

    def test_S9_a_node_in_a_file_that_does_not_exist_names_scenario_node_and_reason(self):
        findings = self.gate({"S1": ["tests/test_gone.py::test_x"], "S2": ["tests/test_a.py::t"]})
        text = " | ".join(e for c, e in self.found(findings) if c == "covers")
        self.assertIn("S1", text)
        self.assertIn("tests/test_gone.py::test_x", text)
        self.assertIn("does not exist", text)

    def test_S9_a_node_missing_from_an_existing_file_is_refused(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        findings = self.gate(
            {"S1": ["tests/test_a.py::test_nope"], "S2": ["tests/test_a.py::test_new"]}
        )
        self.assertEqual(
            self.found(findings),
            [
                (
                    "covers",
                    "S1: test node tests/test_a.py::test_nope does not exist (no test_nope in tests/test_a.py)",
                )
            ],
        )

    def test_S9_a_node_that_did_not_change_in_the_ticket_is_refused(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        findings = self.gate(
            {"S1": ["tests/test_old.py::test_old"], "S2": ["tests/test_a.py::test_new"]}
        )
        self.assertEqual(
            self.found(findings),
            [
                (
                    "covers",
                    "S1: test node tests/test_old.py::test_old did not change in this ticket"
                    " (base is main)",
                )
            ],
        )

    def test_S9_an_untouched_test_beside_a_changed_one_in_the_same_file_did_not_change(self):
        self.write("tests/test_old.py", OLD_TESTS + "\ndef test_added():\n    assert 1\n")
        findings = self.gate(
            {"S1": ["tests/test_old.py::test_added"], "S2": ["tests/test_old.py::test_old"]},
            paths=["tests/test_old.py"],
        )
        self.assertEqual(
            [c for c, _ in self.found(findings)],
            ["covers"],
        )
        self.assertIn(
            "S2: test node tests/test_old.py::test_old did not change", findings[0]["excerpt"]
        )

    def test_S9_a_class_method_node_and_a_committed_change_count(self):
        self.write(
            "tests/test_a.py",
            "class TestA:\n    def test_one(self):\n        assert 1\n    def test_two(self):\n        assert 1\n",
        )
        self.commit("tests")
        nodes = ["tests/test_a.py::TestA::test_two"]
        self.assertEqual(self.gate({"S1": nodes, "S2": nodes}), [])

    def test_S9_a_satisfied_scenario_left_unmapped_is_refused_and_named(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        findings = self.gate({"S1": ["tests/test_a.py::test_new"]})
        self.assertEqual(
            self.found(findings),
            [("covers", "S2: no test node in covers; map the scenario to the tests that prove it")],
        )

    def test_S9_a_report_without_covers_leaves_every_satisfied_scenario_unmapped(self):
        findings = self.gate({})
        self.assertEqual(
            [e.split(":")[0] for c, e in self.found(findings) if c == "covers"], ["S1", "S2"]
        )


class ScopeAndIntegrityTests(VerifyFixture):
    covers = {"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}

    def setUp(self):
        super().setUp()
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")

    def test_S10_a_file_outside_the_ticket_paths_fails_naming_file_and_scope(self):
        self.write("src/b/other.py", "x = 1\n")
        self.assertEqual(
            self.found(self.gate(self.covers)),
            [("scope", "src/b/other.py is outside the ticket paths (src/a/, tests/test_a.py)")],
        )

    def test_S10_a_file_inside_the_ticket_paths_passes_committed_or_not(self):
        self.write("src/a/x.py", "x = 1\n")
        self.commit("work")
        self.write("src/a/y.py", "y = 1\n")
        self.assertEqual(self.gate(self.covers), [])

    def test_S10_a_path_prefix_is_a_directory_not_a_string_prefix(self):
        self.write("src/ab/x.py", "x = 1\n")
        self.assertEqual([c for c, _ in self.found(self.gate(self.covers))], ["scope"])

    def test_S10_touching_the_plan_is_an_integrity_failure_even_inside_the_paths(self):
        self.write(PLAN_REL, "schema: plan/1\n")
        found = self.found(self.gate(self.covers, paths=["docs/", "tests/test_a.py"]))
        self.assertEqual(
            found, [("integrity", "%s is the plan; a ticket never edits its contract" % PLAN_REL)]
        )

    def test_S10_touching_an_oracle_file_is_an_integrity_failure(self):
        oracle = "docs/plans/2026-11-10-demo.oracle/test_oracle.py.oracle"
        self.write(oracle, "def test_o(): pass\n")
        found = self.found(self.gate(self.covers, paths=["docs/", "tests/test_a.py"]))
        self.assertEqual(
            found, [("integrity", "%s is an oracle file; a ticket never edits one" % oracle)]
        )

    def test_S10_touching_an_oracle_place_is_an_integrity_failure(self):
        self.write("tests/test_oracle.py", "def test_o(): pass\n")
        found = self.found(self.gate(self.covers, paths=["tests/"]))
        self.assertEqual(
            found,
            [
                (
                    "integrity",
                    "tests/test_oracle.py is an oracle place; the driver places the oracle there, a ticket never does",
                )
            ],
        )

    def test_S10_a_deleted_or_renamed_oracle_place_counts_as_touching_it(self):
        self.git("stash", "-q", "-u")
        self.git("checkout", "-q", "main")
        self.write("tests/test_oracle.py", "def test_o(): pass\n")
        self.commit("placed on base")
        self.git("checkout", "-q", "-b", "other")
        self.git("stash", "pop", "-q")
        (self.repo / "tests" / "test_oracle.py").unlink()
        found = self.found(self.gate(self.covers, paths=["tests/"]))
        self.assertEqual([c for c, _ in found], ["integrity"])

    def test_S10_every_failing_rule_is_reported_in_one_gate(self):
        self.write("src/b/other.py", "x = 1\n")
        self.write(PLAN_REL, "schema: plan/1\n")
        self.assertEqual(
            sorted(c for c, _ in self.found(self.gate({"S1": ["tests/test_a.py::test_new"]}))),
            ["covers", "integrity", "scope"],
        )
