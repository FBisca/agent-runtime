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
            "scenarios": [
                {"id": "S1", "when": "a", "then": "b"},
                {"id": "S2", "when": "c", "then": "d"},
                {"id": "S3", "when": "e", "then": "f"},
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

    def measured(self, covers):
        """A `test_cost` that accounts for every test file and covered node this ticket changed."""
        files, added = verify.changed(self.repo, "main")
        patterns = yaml_plan.repo_config(self.repo).test_patterns
        modules = {
            name: 0.25
            for name in files
            if yaml_plan.is_test_path(name, patterns) and added.get(name)
        }
        return {
            "command": "uv run pytest tests/ --durations=0 -q",
            "modules": modules,
            "tests": {node: 0.25 for nodes in covers.values() for node in nodes},
        }

    AUTO = object()

    def gate(self, covers, test_cost=AUTO, **ticket):
        report = {"contract": 3, "task_id": "t1", "covers": covers}
        cost = self.measured(covers) if test_cost is self.AUTO else test_cost
        if cost is not None:
            report["test_cost"] = cost
        return verify.invariants(
            model(**ticket),
            "T1",
            report,
            repo=self.repo,
            base="main",
            layout=yaml_plan.PlanLayout(self.repo / PLAN_REL, self.repo),
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

    def test_S9_a_report_without_covers_names_every_satisfied_scenario_left_unmapped(self):
        # An implementation provides its evidence: no scenario is exempt from needing a covering node.
        findings = self.gate({}, satisfies=["S1", "S2", "S3"])
        self.assertEqual(
            [e.split(":")[0] for c, e in self.found(findings) if c == "covers"], ["S1", "S2", "S3"]
        )


class TestCostTests(VerifyFixture):
    """A ticket that changes a test names what those tests cost, timed by whatever runner the repository uses."""

    def changed_test(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        return {"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}

    def test_S1_a_changed_test_without_test_cost_is_refused_naming_the_file_and_the_fix(self):
        findings = self.gate(self.changed_test(), test_cost=None)
        (finding,) = [f for f in self.found(findings) if f[0] == "test_cost"]
        self.assertIn("no test_cost", finding[1])
        self.assertIn("the command that timed the touched tests", finding[1])

    def test_S1_a_test_cost_without_a_command_is_refused(self):
        covers = self.changed_test()
        cost = self.measured(covers)
        cost["command"] = " "
        findings = self.gate(covers, test_cost=cost)
        self.assertEqual(
            [f for f in self.found(findings) if f[0] == "test_cost"],
            [
                (
                    "test_cost",
                    "test_cost.command is empty: time the touched tests and name the command that did",
                )
            ],
        )

    def test_S1_any_runners_timing_command_is_accepted(self):
        """The repository may use any runner: verify names no runner or flag of its own."""
        covers = self.changed_test()
        for command in ("npx vitest run --reporter=verbose tests/a.test.ts", "go test -v ./..."):
            with self.subTest(command=command):
                cost = self.measured(covers)
                cost["command"] = command
                findings = self.gate(covers, test_cost=cost)
                self.assertEqual([f for f in self.found(findings) if f[0] == "test_cost"], [])

    def test_S1_a_missing_module_row_names_the_file(self):
        covers = self.changed_test()
        cost = self.measured(covers)
        cost["modules"] = {}
        (finding,) = [
            f for f in self.found(self.gate(covers, test_cost=cost)) if f[0] == "test_cost"
        ]
        self.assertIn("tests/test_a.py", finding[1])

    def test_S1_a_missing_node_row_names_the_node(self):
        covers = self.changed_test()
        cost = self.measured(covers)
        cost["tests"] = {}
        (finding,) = [
            f for f in self.found(self.gate(covers, test_cost=cost)) if f[0] == "test_cost"
        ]
        self.assertIn("tests/test_a.py::test_new", finding[1])

    def test_S1_a_negative_or_boolean_second_is_not_a_measurement(self):
        covers = self.changed_test()
        for value in (-1, True):
            cost = self.measured(covers)
            cost["modules"] = {"tests/test_a.py": value}
            findings = self.gate(covers, test_cost=cost)
            self.assertTrue([f for f in self.found(findings) if f[0] == "test_cost"], value)

    def test_S1_a_change_with_no_test_needs_no_test_cost(self):
        self.write("src/a/x.py", "x = 1\n")
        files, added = verify.changed(self.repo, "main")
        self.assertEqual(verify.test_cost({}, self.repo, "main", files, added), [])


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
            found,
            [
                (
                    "integrity",
                    "%s is in the plan; a ticket never edits the plan that gates it" % PLAN_REL,
                )
            ],
        )

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
    """A plan in its own folder."""
    return model(**ticket)


class FolderPlanIntegrityTests(VerifyFixture):
    covers = {"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}

    def setUp(self):
        super().setUp()
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")

    def folder_gate(self, plan_rel=FOLDER_PLAN_REL, **ticket):
        outside = plan_rel is None  # a plan kept outside the repository
        plan_path = Path("/elsewhere/plan.yaml") if outside else self.repo / plan_rel
        report = {"contract": 3, "task_id": "t1", "covers": self.covers}
        report["test_cost"] = self.measured(self.covers)
        return self.found(
            verify.invariants(
                folder_model(**ticket),
                "T1",
                report,
                repo=self.repo,
                base="main",
                layout=yaml_plan.PlanLayout(plan_path, self.repo),
            )
        )

    def test_S7_touching_a_file_of_the_plan_folder_is_an_integrity_failure_naming_it(self):
        for name in ("plan.yaml", "plan.md", "brief.md", "notes/design.md"):
            with self.subTest(name):
                self.write("%s/%s" % (FOLDER, name), "x\n")
                found = self.folder_gate(paths=["docs/", "tests/test_a.py"])
                self.assertEqual(
                    found,
                    [
                        (
                            "integrity",
                            "%s/%s is in the plan; a ticket never edits the plan that gates it"
                            % (FOLDER, name),
                        )
                    ],
                )
                (self.repo / FOLDER / name).unlink()

    def test_S7_a_plan_outside_the_repository_protects_no_folder(self):
        self.write("docs/plans/plan.yaml", "x: 1\n")
        found = self.folder_gate(plan_rel=None, paths=["docs/", "tests/test_a.py"])
        self.assertEqual(found, [])

    def test_S7_a_file_beside_the_plan_folder_is_only_a_scope_matter(self):
        self.write("docs/plans/2026-11-12-demo-two/plan.yaml", "x: 1\n")
        found = self.folder_gate(paths=["docs/", "tests/test_a.py"])
        self.assertEqual(found, [])


class DriverPlanPathTests(unittest.TestCase):
    """What the driver's layout says of the plan's place in the repository."""

    def rel(self, plan_path, repo="/repo"):
        from agent_orchestrator import driver

        stub = driver.Driver.__new__(driver.Driver)
        stub.plan_path, stub.repo = Path(plan_path), Path(repo)
        return stub.layout.plan_place

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
