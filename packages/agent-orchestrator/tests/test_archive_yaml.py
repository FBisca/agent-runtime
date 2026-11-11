"""The archive brief for a YAML plan: its spec, its plan files and oracles copied byte-identical, no Status, no renames."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fixtures import load_script
from test_archive import ARCHIVE, ArchiveFixture

pytestmark = pytest.mark.integration


def owns_of(task_md):
    line = next(line for line in task_md.splitlines() if line.startswith("Owns"))
    return [item.strip() for item in line.split(": ", 1)[1].rstrip(".").split(",")]


def commit_spec(case, text):
    """Land `docs/specs/demo.md` on origin/main, as a merged ticket that wrote requirements would have."""
    spec = case.repo / "docs" / "specs" / "demo.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(text, encoding="utf-8")
    case.git(case.repo, "add", "docs/specs/demo.md")
    case.git(case.repo, "commit", "-q", "-m", "spec")
    case.git(case.repo, "push", "-q", "origin", "main")


class YamlArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_S29_yaml_archive_brief_copies_plan_files_and_places_oracles(self):
        narrative = self.plan.parent / "demo.plan.md"
        narrative.write_text("# demo\n", encoding="utf-8")
        plan_before = self.plan.read_bytes()
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        self.assertIn(ARCHIVE, self.spawned())
        self.assertEqual(self.plan.read_bytes(), plan_before)

        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        owns = owns_of(task_md)
        oracle_dir = self.plan.parent / "demo.oracle"
        for needle in (
            "docs/specs/demo.md",
            str(self.plan),
            str(narrative),
            str(oracle_dir),
            "byte-identical",
        ):
            self.assertIn(needle, task_md)
        # The driver places the oracle under its promoted name before the worker starts.
        self.assertIn("Already placed by the driver", task_md)
        self.assertIn("`src/a/test_oracle.py`", task_md)
        self.assertIn("src/a/test_oracle.py", owns)
        self.assertIn("docs/specs/demo.md", owns)
        self.assertNotIn("docs/specs/,", owns)
        self.assertNotIn("src/a/test_oracle.py.oracle", task_md)
        self.assertNotIn("Status: ARCHIVED", task_md)
        self.assertNotIn("Rename each test", task_md)
        self.assertNotIn("Create the living spec", task_md)

    def test_S29_a_scenario_the_spec_already_holds_is_not_appended_again(self):
        """A ticket that wrote its scenario into the spec leaves the archive nothing to append for it."""
        commit_spec(
            self,
            "# Demo\nPaths: src/a/\n\n- **R7** WHEN a, THEN b. (from demo/S1)\n- **R8** WHEN x. (from other/S1)\n",
        )
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        self.assertIn("keep as they are: S1 (R7).", task_md)
        self.assertIn("append none", task_md)
        self.assertNotIn("Append each scenario", task_md)
        self.assertNotIn("Scenarios, verbatim", task_md)

    def test_S29_no_narrative_step_when_plan_md_is_missing(self):
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        self.assertNotIn(".plan.md", task_md)
        self.assertNotIn(".plan.md", " ".join(owns_of(task_md)))


class YamlArchiveDestinationTests(unittest.TestCase):
    """The destinations the brief names, computed from the plan's location and its approved scenarios."""

    def driver_for(self, plan_path, repo):
        driver = load_script().Driver.__new__(load_script().Driver)
        driver.plan_path = Path(plan_path)
        driver.repo = Path(repo)
        return driver

    def model(self):
        evidence = SimpleNamespace(promote=True, place="src/a/", oracle=["test_oracle.py.oracle"])
        return SimpleNamespace(scenarios=[SimpleNamespace(evidence=evidence)])

    def test_S29_placed_oracle_drops_the_oracle_suffix(self):
        driver = self.driver_for("/repo/docs/plans/demo.plan.yaml", "/repo")
        places = driver.yaml_places(self.model())
        self.assertEqual(
            places,
            [
                (
                    "src/a/test_oracle.py",
                    Path("/repo/docs/plans/demo.oracle/test_oracle.py.oracle"),
                )
            ],
        )

    def test_S29_plan_outside_repo_lands_inside_the_checkout(self):
        with tempfile.TemporaryDirectory() as outside, tempfile.TemporaryDirectory() as repo:
            plans = Path(outside) / "plans"
            plans.mkdir()
            (plans / "demo.plan.yaml").write_text("schema: plan/1\n", encoding="utf-8")
            (plans / "demo.plan.md").write_text("# demo\n", encoding="utf-8")
            driver = self.driver_for(plans / "demo.plan.yaml", repo)
            destinations = [rel for rel, _ in driver.yaml_copies()]
            destinations += [rel for rel, _ in driver.yaml_places(self.model())]
        for rel in destinations:
            self.assertFalse(Path(rel).is_absolute(), rel)
            self.assertNotIn("..", Path(rel).parts, rel)
        self.assertEqual(
            destinations,
            [
                "docs/plans/demo.plan.yaml",
                "docs/plans/demo.plan.md",
                "docs/plans/demo.oracle/",
                "src/a/test_oracle.py",
            ],
        )


class SnapshotLocationTests(unittest.TestCase):
    """The approval snapshot is found by digest wherever `plan approve` wrote it."""

    def setUp(self):
        self.driver = load_script()
        self.default = Path(self.driver.yaml_plan.DEFAULT_STATE).expanduser()

    def fake_load(self, present_in):
        def load(path, *, repo, state_dir, **kwargs):
            if Path(state_dir).expanduser() != present_in:
                raise self.driver.PlanError(f"approval: snapshot missing at {state_dir} — approve")
            return "model"

        return load

    def test_S23_a_snapshot_approved_under_the_default_state_dir_loads_for_another_state_dir(self):
        with mock.patch.object(self.driver.yaml_plan, "load", self.fake_load(self.default)):
            got = self.driver.load_approved("p", repo="r", state_dir="/elsewhere/state")
        self.assertEqual(got, "model")

    def test_S23_a_snapshot_missing_in_both_places_still_refuses(self):
        with mock.patch.object(self.driver.yaml_plan, "load", self.fake_load(Path("/nowhere"))):
            with self.assertRaises(self.driver.PlanError) as caught:
                self.driver.load_approved("p", repo="r", state_dir="/elsewhere/state")
        self.assertIn("snapshot missing", str(caught.exception))
