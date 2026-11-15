"""Acceptance for the plan-check-evidence-selectors plan (S1): `plan check` maps a folder plan's pytest `-k` evidence
to the tests it selects in the plan's acceptance files, and refuses the two cases real planning runs shipped:

  - a plan whose checks name tests the tickets have yet to write passed with a vacuous `baseline: fail`;
  - `-k "S1 or control"` over a file that also held `test_S10_...` ran another scenario's test as S1's evidence.

It accepts the anchored selector `-k "test_S1_ or test_control_"`, leaves a check without `-k` alone, and never lints
a legacy plan.

Frozen at approval; placed at packages/agent-orchestrator/acceptance/plan/ by the runner; never edited by a ticket.
It drives `plan check` and `plan approve` in process through the package's own fixtures, over a real git repository.
"""

import io
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration

FILE = "test_widget_flow.py"
CHECK = 'uv run pytest pkg/acceptance/flows/%s -q -k "{}" --junitxml="$EEC_JUNIT"' % FILE
WRITTEN = (
    "import unittest\n\n\n"
    "class WidgetTests(unittest.TestCase):\n"
    "    def test_control_the_fixture_builds_a_widget(self):\n        pass\n\n"
    "    def test_S1_a_widget_is_listed(self):\n        pass\n\n"
    "    def test_S10_a_widget_is_archived(self):\n        pass\n"
)
# The tickets have yet to write the scenario tests: only the control exists.
UNWRITTEN = (
    "import unittest\n\n\n"
    "class WidgetTests(unittest.TestCase):\n"
    "    def test_control_the_fixture_builds_a_widget(self):\n        pass\n"
)


def plan_with(check, scenarios=("S1",)):
    return {
        "schema": "plan/1",
        "slug": "widgets",
        "goal": "list widgets",
        "domain": "flows",
        "archive": "discard",
        "outcome": list(scenarios),
        "scenarios": [
            {
                "id": sid,
                "when": "a widget exists",
                "then": "it is listed",
                "evidence": {
                    "check": check.replace("S1", sid),
                    "baseline": "fail",
                    "acceptance": [FILE],
                    "package": "pkg",
                },
            }
            for sid in scenarios
        ],
        "tickets": [
            {
                "id": "T1",
                "delivers": "widgets",
                "satisfies": list(scenarios),
                "paths": ["pkg/tests/test_widgets.py"],
                "size": "small",
                "difficulty": "simple",
            }
        ],
    }


class EvidenceSelectors(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.folder = self.repo / "docs" / "plans" / "2026-11-15-widgets"
        (self.folder / "acceptance").mkdir(parents=True)
        (self.repo / "pkg").mkdir()
        (self.repo / ".orchestrate").mkdir(exist_ok=True)
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"plans": {"dir": "docs/plans"}})
        )
        self.plan = self.folder / "plan.yaml"
        env = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead-test"}
        )
        env.start()
        self.addCleanup(env.stop)
        resolve = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        resolve.start()
        self.addCleanup(resolve.stop)

    def write(self, data, tests):
        (self.folder / "acceptance" / FILE).write_text(tests)
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False))

    def run_plan(self, verb, plan=None):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    verb,
                    str(plan or self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                ]
            )
        return code, out.getvalue() + err.getvalue()

    def assert_refused(self, *needles):
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                code, said = self.run_plan(verb)
                self.assertEqual(code, 2, said)
                for needle in needles:
                    self.assertIn(needle, said)
                self.assertNotIn("approval", yaml.safe_load(self.plan.read_text()))

    def assert_valid(self, plan=None):
        code, said = self.run_plan("check", plan)
        self.assertEqual(code, 0, said)
        self.assertIn('"valid":true', said)

    def test_S1_checks_over_tests_not_yet_written_are_refused(self):
        self.write(plan_with(CHECK.format("S1 or control"), ("S1", "S2", "S3")), UNWRITTEN)
        code, said = self.run_plan("check")
        self.assertEqual(code, 2, said)
        for sid in ("S1", "S2", "S3"):
            self.assertIn("scenarios[%d].evidence.check: %s" % (int(sid[1:]) - 1, sid), said)
            self.assertIn("test_%s_" % sid, said)
        self.assertIn("selects no test", said)
        self.assertIn(FILE, said)

    def test_S1_a_selector_that_bleeds_into_another_scenarios_test_is_refused(self):
        self.write(plan_with(CHECK.format("S1 or control")), WRITTEN)
        self.assert_refused(
            "S1", "test_S10_a_widget_is_archived", "S10", "test_S1_ or test_control_"
        )

    def test_S1_a_selector_that_names_another_scenario_is_refused(self):
        data = plan_with(CHECK.format("S1 or control"))
        data["scenarios"][0]["evidence"]["check"] = CHECK.format("S10 or control")
        self.write(data, WRITTEN)
        self.assert_refused("S1", "not S1", "test_S1_ or test_control_")

    def test_control_the_anchored_selector_keeps_the_plan_valid(self):
        self.write(plan_with(CHECK.format("test_S1_ or test_control_")), WRITTEN)
        self.assert_valid()

    def test_control_a_check_without_k_is_untouched(self):
        self.write(plan_with("uv run pytest pkg/acceptance/flows/%s -q" % FILE), UNWRITTEN)
        self.assert_valid()

    def test_control_a_legacy_plan_is_not_linted(self):
        legacy = self.repo / "docs" / "plans" / "widgets.plan.yaml"
        oracles = legacy.with_name("widgets.oracle")
        oracles.mkdir()
        (oracles / FILE).write_text(WRITTEN)
        data = plan_with(CHECK.format("S1 or control"))
        for key in ("domain", "archive"):
            data.pop(key)
        data["spec"] = "docs/specs/widgets.md"
        evidence = data["scenarios"][0]["evidence"]
        for key in ("acceptance", "package"):
            evidence.pop(key)
        evidence.update(oracle=[FILE], place="pkg/legacy/")
        legacy.write_text(yaml.safe_dump(data, sort_keys=False))
        self.assert_valid(legacy)


if __name__ == "__main__":
    unittest.main()
