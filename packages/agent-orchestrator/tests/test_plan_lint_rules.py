"""The test-holding lint: which plan commands run it and which never do."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from agent_orchestrator import plan as yaml_plan
from fixtures import RunFixture, load_script, plan_data

pytestmark = pytest.mark.integration


class LintRules(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def draft(self, paths, owner=None):
        data = plan_data(("T1",))
        ticket = data["tickets"][0]
        ticket["paths"] = paths
        ticket["satisfies"] = ["S1", "S2"]
        if owner:
            ticket.pop("size")
            ticket.pop("difficulty")
            ticket["owner"] = owner
        path = self.repo / "docs" / "plans" / "rules.plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def plan_cli(self, verb, path):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    verb,
                    str(path),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                ]
            )
        return code, out.getvalue() + err.getvalue()

    def test_S1_check_and_approve_refuse_naming_ticket_scenarios_and_way_out(self):
        path = self.draft(["pyproject.toml", "src/feature.py"])
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                code, said = self.plan_cli(verb, path)
                self.assertNotEqual(code, 0, said)
                for needle in ("T1", "S1", "S2", "test file", "directory"):
                    self.assertIn(needle, said)
                self.assertNotIn("approval", yaml.safe_load(path.read_text(encoding="utf-8")))

    def test_S1_a_test_file_or_a_directory_is_enough_and_a_person_ticket_is_not_linted(self):
        for paths, owner in ((["packages/x/tests/test_a.py"], None), (["src/a/"], None)):
            with self.subTest(paths=paths):
                self.assertEqual(self.plan_cli("check", self.draft(paths, owner))[0], 0)
        self.assertEqual(self.plan_cli("check", self.draft(["docs/runbook.md"], "Felippe"))[0], 0)

    def test_S1_the_configured_patterns_decide(self):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(exist_ok=True)
        config.write_text("tests:\n  patterns: ['check_*.sh']\n", encoding="utf-8")
        self.assertNotEqual(self.plan_cli("check", self.draft(["src/test_a.py"]))[0], 0)
        self.assertEqual(self.plan_cli("check", self.draft(["ci/check_a.sh"]))[0], 0)

    def test_S3_the_driver_load_never_lints_an_approved_plan(self):
        path = self.draft(["pyproject.toml"])
        # approved before the lint existed: approval skips it here, the load must not bring it back
        with mock.patch.object(yaml_plan, "lint_problems", return_value=[]):
            self.assertEqual(self.plan_cli("approve", path)[0], 0)
        self.assertNotEqual(self.plan_cli("check", path)[0], 0)
        model = yaml_plan.load(
            path, repo=self.repo, state_dir=self.state_dir, require_approval=True
        )
        self.assertEqual(model.slug, "demo")
