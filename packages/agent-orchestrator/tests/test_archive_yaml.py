"""The archive of a YAML plan: where its plan files and oracles land, and the spec the tests commit."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fixtures import load_script

pytestmark = pytest.mark.integration


def commit_spec(case, text):
    """Land `docs/specs/demo.md` on origin/main, as a merged ticket that wrote requirements would have."""
    spec = case.repo / "docs" / "specs" / "demo.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(text, encoding="utf-8")
    case.git(case.repo, "add", "docs/specs/demo.md")
    case.git(case.repo, "commit", "-q", "-m", "spec")
    case.git(case.repo, "push", "-q", "origin", "main")


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
        def load(*_args, state_dir, **_kwargs):
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
