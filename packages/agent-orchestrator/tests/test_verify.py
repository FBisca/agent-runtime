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


FOLDER = "docs/plans/2026-11-12-demo"
FOLDER_PLAN_REL = FOLDER + "/plan.yaml"


def folder_model(**ticket):
    """A plan in its own folder: its acceptance file is placed at pkg/acceptance/billing/test_probe.py."""
    data = model(**ticket).model_dump(by_alias=True, exclude_none=True)
    evidence = data["scenarios"][0]["evidence"]
    for key in ("oracle", "place"):
        evidence.pop(key, None)
    evidence.update(acceptance=["test_probe.py"], package="pkg")
    data["domain"] = "billing"
    return yaml_plan.Plan.model_validate(data)


class FolderPlanIntegrityTests(VerifyFixture):
    covers = {"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}

    def setUp(self):
        super().setUp()
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")

    def folder_gate(self, plan_rel=FOLDER_PLAN_REL, **ticket):
        report = {"contract": 3, "task_id": "t1", "covers": self.covers}
        return self.found(
            verify.invariants(
                folder_model(**ticket),
                "T1",
                report,
                repo=self.repo,
                base="main",
                plan_rel=plan_rel,
            )
        )

    def test_S7_touching_a_file_of_the_plan_folder_is_an_integrity_failure_naming_it(self):
        for name in ("plan.yaml", "plan.md", "acceptance/test_probe.py", "negatives/probe.patch"):
            with self.subTest(name):
                self.write("%s/%s" % (FOLDER, name), "x\n")
                found = self.folder_gate(paths=["docs/", "tests/test_a.py"])
                self.assertEqual(
                    found,
                    [
                        (
                            "integrity",
                            "%s/%s is in the plan folder; a ticket never edits the plan or its "
                            "acceptance files" % (FOLDER, name),
                        )
                    ],
                )
                (self.repo / FOLDER / name).unlink()

    def test_S7_touching_an_acceptance_destination_is_an_integrity_failure_naming_it(self):
        self.write("pkg/acceptance/billing/test_probe.py", "def test_p(): pass\n")
        found = self.folder_gate(paths=["pkg/", "tests/test_a.py"])
        self.assertEqual(
            found,
            [
                (
                    "integrity",
                    "pkg/acceptance/billing/test_probe.py is an acceptance destination; the driver "
                    "places the acceptance file there, a ticket never does",
                )
            ],
        )

    def test_S7_the_destination_follows_the_repo_config_not_the_default(self):
        (self.repo / ".orchestrate").mkdir()
        self.write(".orchestrate/config.yaml", "acceptance:\n  dir: accept\n")
        self.write("pkg/acceptance/billing/test_probe.py", "def test_p(): pass\n")
        self.write("pkg/accept/billing/test_probe.py", "def test_p(): pass\n")
        found = self.folder_gate(paths=["pkg/", ".orchestrate/", "tests/test_a.py"])
        self.assertEqual(
            [(c, e.split(" ")[0]) for c, e in found],
            [("integrity", "pkg/accept/billing/test_probe.py")],
        )

    def test_S7_a_plan_outside_the_repository_protects_no_folder_and_still_protects_destinations(
        self,
    ):
        self.write("docs/plans/plan.yaml", "x: 1\n")
        self.write("pkg/acceptance/billing/test_probe.py", "def test_p(): pass\n")
        found = self.folder_gate(plan_rel=None, paths=["docs/", "pkg/", "tests/test_a.py"])
        self.assertEqual(
            [(c, e.split(" ")[0]) for c, e in found],
            [("integrity", "pkg/acceptance/billing/test_probe.py")],
        )

    def test_S7_a_file_beside_the_plan_folder_is_only_a_scope_matter(self):
        self.write("docs/plans/2026-11-12-demo-two/plan.yaml", "x: 1\n")
        found = self.folder_gate(paths=["docs/", "tests/test_a.py"])
        self.assertEqual(found, [])


class DriverPlanPathTests(unittest.TestCase):
    """What the driver hands verify as the plan's repo path."""

    def rel(self, plan_path, repo="/repo"):
        from agent_orchestrator import driver

        stub = driver.Driver.__new__(driver.Driver)
        stub.plan_path, stub.repo = Path(plan_path), Path(repo)
        return stub.plan_rel_for_integrity()

    def test_S7_a_folder_plan_in_the_repository_is_named_by_its_repo_path(self):
        self.assertEqual(
            self.rel("/repo/docs/plans/2026-11-12-demo/plan.yaml"),
            "docs/plans/2026-11-12-demo/plan.yaml",
        )

    def test_S7_a_folder_plan_outside_the_repository_has_no_repo_path(self):
        self.assertIsNone(self.rel("/elsewhere/plans/2026-11-12-demo/plan.yaml"))

    def test_S7_a_legacy_plan_outside_the_repository_keeps_its_docs_plans_name(self):
        self.assertEqual(
            self.rel("/elsewhere/plans/2026-11-10-demo.plan.yaml"),
            "docs/plans/2026-11-10-demo.plan.yaml",
        )
