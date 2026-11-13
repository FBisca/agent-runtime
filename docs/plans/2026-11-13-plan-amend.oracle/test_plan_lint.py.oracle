"""A ticket whose Paths can hold no test is refused before it is built: the covers gate would strand its worker
(plan-amend/S1)."""

import unittest
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script, plan_data

pytestmark = pytest.mark.integration

CONFIG_ONLY = [".orchestrate/config.yaml", "pyproject.toml", "uv.lock"]


class PlanLint(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # The Lead runs these commands: a worker's own session must not turn them into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_plan(self, paths, owner=None):
        data = plan_data(("T1",))
        ticket = data["tickets"][0]
        ticket["paths"] = paths
        ticket["satisfies"] = ["S1", "S2"]
        if owner:
            ticket.pop("size")
            ticket.pop("difficulty")
            ticket["owner"] = owner
        path = self.repo / "docs" / "plans" / "lint.plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def plan_cli(self, verb, path):
        return self.cli(
            "plan", verb, str(path), "--repo", str(self.repo), "--state-dir", str(self.state_dir)
        )

    def assert_refused(self, result, path):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        said = result.stdout + result.stderr
        for needle in ("T1", "S1", "S2"):
            self.assertIn(needle, said)
        self.assertIn("paths", said.lower())
        self.assertIn("test", said.lower())
        self.assertNotIn("approval", yaml.safe_load(path.read_text(encoding="utf-8")))

    def test_S1_plan_check_refuses_an_agent_ticket_whose_paths_hold_no_test(self):
        path = self.write_plan(CONFIG_ONLY)
        self.assert_refused(self.plan_cli("check", path), path)

    def test_S1_plan_approve_refuses_it_and_writes_no_approval(self):
        path = self.write_plan(CONFIG_ONLY)
        self.assert_refused(self.plan_cli("approve", path), path)

    def test_S1_a_lone_source_file_cannot_hold_a_test_either(self):
        path = self.write_plan(["src/feature.py"])
        self.assert_refused(self.plan_cli("check", path), path)

    def test_S1_a_test_file_among_the_paths_is_enough(self):
        path = self.write_plan(CONFIG_ONLY + ["packages/x/tests/test_tiers.py"])
        result = self.plan_cli("check", path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_S1_a_person_ticket_is_not_linted(self):
        path = self.write_plan(["docs/runbook.md"], owner="Felippe")
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                result = self.plan_cli(verb, path)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_control_a_directory_path_can_hold_a_test(self):
        path = self.write_plan(["src/a/"])
        result = self.plan_cli("check", path)
        self.assertEqual(result.returncode, 0, result.stderr)
