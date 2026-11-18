"""verify: the ticket gate's invariants (scope, integrity, covers, red) and the legacy path for Markdown plans."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import plan as yaml_plan
from agent_orchestrator import verify
from fixtures import added

pytestmark = pytest.mark.integration

IDENTITY = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
PLAN_REL = "docs/plans/2026-11-10-demo.plan.yaml"

OLD_TESTS = "def test_old():\n    assert True\n"


def seen_red(covers, scenarios=("S1", "S2", "S3")):
    """A `red` for every scenario: the first covers node failing, or a failing line for a scenario left unmapped
    (the covers check names that one; the red check only asks that an excerpt name a node it was given)."""
    return {
        sid: "FAILED %s - AssertionError" % ((covers.get(sid) or ["tests/test_x.py::test_x"])[0])
        for sid in scenarios
    }


def model(others=(), **ticket):
    return yaml_plan.Plan.model_validate(
        {
            "schema": "plan/2",
            "slug": "demo",
            "goal": "deliver",
            "spec": "docs/specs/demo.md",
            "requirements": added(
                [
                    {"id": "S1", "when": "a", "then": "b"},
                    {"id": "S2", "when": "c", "then": "d"},
                    {"id": "S3", "when": "e", "then": "f"},
                ]
            ),
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
                ),
                *others,
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

    def gate(self, covers, red=None, others=(), **ticket):
        report = {
            "contract": 3,
            "task_id": "t1",
            "covers": covers,
            "red": seen_red(covers) if red is None else red,
        }
        return verify.invariants(
            model(others, **ticket),
            "T1",
            report,
            repo=self.repo,
            base="main",
            layout=yaml_plan.PlanLayout(self.repo / PLAN_REL, self.repo),
        )

    def found(self, findings):
        return [(f["check"], f["excerpt"]) for f in findings]


class CoversTests(VerifyFixture):
    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_satisfied_scenario_mapped_to_changed_nodes_passes(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        self.assertEqual(
            self.gate({"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}),
            [],
        )

    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_node_in_a_file_that_does_not_exist_names_scenario_node_and_reason(self):
        findings = self.gate({"S1": ["tests/test_gone.py::test_x"], "S2": ["tests/test_a.py::t"]})
        text = " | ".join(e for c, e in self.found(findings) if c == "covers")
        self.assertIn("S1", text)
        self.assertIn("tests/test_gone.py::test_x", text)
        self.assertIn("does not exist", text)

    @pytest.mark.spec("Scenario evidence")
    def test_a_node_whose_name_the_red_excerpt_lacks_is_refused_in_any_language(self):
        """The one rule for every language: the worker copies the name from the runner's failing output."""
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        self.write("web/a.test.ts", "it('shows the total', () => {})\n")
        findings = self.gate(
            {"S1": ["tests/test_a.py::test_new"], "S2": ["web/a.test.ts::shows the total"]},
            red={
                "S1": "FAILED tests/test_a.py::test_other",
                "S2": "FAIL a.test.ts > shows the total",
            },
            paths=["web/", "tests/"],
        )
        self.assertEqual(
            self.found(findings),
            [
                (
                    "covers",
                    "S1: test node tests/test_a.py::test_new: the red excerpt does not contain test_new;"
                    " copy the name from the runner's failing output",
                )
            ],
        )

    @pytest.mark.spec("Scenario evidence")
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

    @pytest.mark.spec("Scenario evidence")
    def test_one_counting_node_is_enough_for_a_scenario(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        findings = self.gate(
            {
                "S1": ["tests/test_a.py::test_new", "tests/test_old.py::test_old"],
                "S2": ["tests/test_a.py::test_new"],
            }
        )
        self.assertEqual(findings, [])

    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_class_method_node_and_a_committed_change_count(self):
        self.write(
            "tests/test_a.py",
            "class TestA:\n    def test_one(self):\n        assert 1\n    def test_two(self):\n        assert 1\n",
        )
        self.commit("tests")
        nodes = ["tests/test_a.py::TestA::test_two"]
        self.assertEqual(self.gate({"S1": nodes, "S2": nodes}), [])

    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_satisfied_scenario_left_unmapped_is_refused_and_named(self):
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")
        findings = self.gate({"S1": ["tests/test_a.py::test_new"]})
        self.assertEqual(
            self.found(findings),
            [("covers", "S2: no test node in covers; map the scenario to the tests that prove it")],
        )

    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_report_without_covers_names_every_satisfied_scenario_left_unmapped(self):
        # An implementation provides its evidence: no scenario is exempt from needing a covering node.
        findings = self.gate({}, satisfies=["S1", "S2", "S3"])
        self.assertEqual(
            [e.split(":")[0] for c, e in self.found(findings) if c == "covers"], ["S1", "S2", "S3"]
        )


class ScopeAndIntegrityTests(VerifyFixture):
    covers = {"S1": ["tests/test_a.py::test_new"], "S2": ["tests/test_a.py::test_new"]}

    def setUp(self):
        super().setUp()
        self.write("tests/test_a.py", "def test_new():\n    assert 1\n")

    def second(self, paths=("src/b/",), **extra):
        return [
            dict(
                {
                    "id": "T2",
                    "delivers": "y",
                    "satisfies": ["S3"],
                    "paths": list(paths),
                    "size": "small",
                    "difficulty": "simple",
                },
                **extra,
            )
        ]

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_a_file_outside_the_ticket_paths_that_no_ticket_owns_passes(self):
        self.write("src/c/other.py", "x = 1\n")
        self.assertEqual(self.gate(self.covers, others=self.second()), [])

    def overlaps(self, others=(), done=()):
        return verify.overlaps(model(others), "T1", verify.changed(self.repo, "main"), done=done)

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_a_file_an_unfinished_ticket_owns_passes_the_gate(self):
        """A file another ticket lists is never the worker's to revert: the gate passes."""
        self.write("src/b/other.py", "x = 1\n")
        self.assertEqual(self.gate(self.covers, others=self.second()), [])

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_a_file_an_unfinished_ticket_owns_is_a_warning_naming_file_ticket_and_what_to_expect(
        self,
    ):
        self.write("src/b/other.py", "x = 1\n")
        (warning,) = self.overlaps(others=self.second())
        self.assertEqual((warning["file"], warning["ticket"]), ("src/b/other.py", "T2"))
        self.assertIn("src/b/other.py", warning["excerpt"])
        self.assertIn("T2", warning["excerpt"])
        self.assertIn("order", warning["excerpt"])
        self.assertIn("merge conflict", warning["excerpt"])
        self.assertNotIn("revert", warning["excerpt"])

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_a_downstream_ticket_that_lists_the_file_is_a_warning_too(self):
        """A ticket that waits on this one can never run first: a block would deadlock the plan."""
        self.write("src/b/other.py", "x = 1\n")
        (warning,) = self.overlaps(others=self.second(blocked_by=["T1"]))
        self.assertEqual(warning["ticket"], "T2")

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_no_warning_for_a_finished_owner_or_a_file_inside_the_ticket_paths(self):
        self.write("src/b/other.py", "x = 1\n")
        self.write("src/a/x.py", "x = 1\n")
        self.assertEqual(self.overlaps(others=self.second(), done=("T2",)), [])
        self.assertEqual(self.overlaps(others=self.second(paths=("src/a/",))), [])

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_a_path_prefix_is_a_directory_not_a_string_prefix(self):
        self.write("src/bb/x.py", "x = 1\n")
        self.assertEqual(self.overlaps(others=self.second()), [])

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_touching_the_plan_is_an_integrity_failure_even_inside_the_paths(self):
        self.write(PLAN_REL, "schema: plan/2\n")
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

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_every_failing_rule_is_reported_in_one_gate(self):
        self.write("src/b/other.py", "x = 1\n")
        self.write(PLAN_REL, "schema: plan/2\n")
        found = self.gate({"S1": ["tests/test_a.py::test_new"]}, others=self.second())
        self.assertEqual(
            sorted(c for c, _ in self.found(found)),
            ["covers", "integrity"],
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
        report["red"] = seen_red(self.covers)
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

    @pytest.mark.spec("Ticket scope and plan integrity")
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
