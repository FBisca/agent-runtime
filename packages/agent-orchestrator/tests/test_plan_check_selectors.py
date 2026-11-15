"""A folder plan's `check` that runs pytest with `-k` over its acceptance files is linted against the tests that
selector picks out (plan-check-evidence-selectors/S1): it must name the scenario's own id, select a test of it, and
select no test that carries another scenario's id by substring. A check without `-k` and a legacy plan are not
linted."""

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

PROBE = "test_selectors_probe.py"
CHECK = 'uv run pytest pkg/acceptance/plan/%s -q -k "{}" --junitxml="$EEC_JUNIT"' % PROBE
# The tests the acceptance file holds by default: a control, two of S1's and one of S10's, in a unittest class whose
# name does not start with Test, as the repository's acceptance files are written.
TESTS = (
    "import unittest\n\n\n"
    "class ProbeTests(unittest.TestCase):\n"
    "    def test_control_the_fixture_holds(self):\n        pass\n\n"
    "    def test_S1_the_first_behaviour(self):\n        pass\n\n"
    "    def test_S1_the_second_behaviour(self):\n        pass\n\n"
    "    def test_S10_another_scenario(self):\n        pass\n"
)


def selector_plan(check):
    return {
        "schema": "plan/1",
        "slug": "selectors",
        "goal": "deliver",
        "domain": "plan",
        "archive": "discard",
        "outcome": ["S1"],
        "scenarios": [
            {
                "id": "S1",
                "when": "a",
                "then": "b",
                "evidence": {
                    "check": check,
                    "baseline": "fail",
                    "acceptance": [PROBE],
                    "package": "pkg",
                },
            }
        ],
        "tickets": [
            {
                "id": "T1",
                "delivers": "first",
                "satisfies": ["S1"],
                "paths": ["pkg/tests/test_a.py"],
                "size": "small",
                "difficulty": "simple",
            }
        ],
    }


class SelectorLint(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.folder = self.repo / "docs" / "plans" / "2026-11-15-selectors"
        (self.folder / "acceptance").mkdir(parents=True)
        (self.repo / "pkg").mkdir()
        (self.repo / ".orchestrate").mkdir(exist_ok=True)
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"plans": {"dir": "docs/plans"}})
        )
        self.plan = self.folder / "plan.yaml"
        patcher = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead-test"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # The Lead runs these commands: a worker's own session must not turn them into the worker refusal.
        resolve = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        resolve.start()
        self.addCleanup(resolve.stop)

    def write(self, selector=None, tests=TESTS, check=None):
        (self.folder / "acceptance" / PROBE).write_text(tests)
        data = selector_plan(check if check is not None else CHECK.format(selector))
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False))

    def command(self, verb, plan=None):
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

    def refused(self, *needles, verb="check", plan=None):
        code, message = self.command(verb, plan)
        self.assertEqual(code, 2, message)
        for needle in ("scenarios[0].evidence.check", "S1", "test_S1_"):
            self.assertIn(needle, message)
        for needle in needles:
            self.assertIn(needle, message)
        return message

    def accepted(self, plan=None):
        code, message = self.command("check", plan)
        self.assertEqual(code, 0, message)
        self.assertIn('"valid":true', message)

    def test_S1_a_selector_that_does_not_name_the_scenario_is_refused(self):
        # (a): no scenario id at all, and another scenario's id but not this one's
        for selector, needle in (("control", "names no scenario id"), ("S2 or control", "not S1")):
            with self.subTest(selector=selector):
                self.write(selector, tests=TESTS.replace("S10", "S2"))
                self.refused(needle)

    def test_S1_a_selector_that_selects_none_of_the_scenarios_tests_is_refused(self):
        # (b): the file holds only a control and another scenario's test, so the baseline fails vacuously
        tests = (
            TESTS.replace("test_S1_the_first", "test_S2_the_first")
            .replace("test_S1_the_second", "test_S2_the_second")
            .replace("S10", "S3")
        )
        self.write("S1 or control", tests=tests)
        self.refused("selects no test", PROBE)

    def test_S1_a_selector_that_also_selects_another_scenarios_test_is_refused(self):
        # (c): `S1` matches `test_S10_another_scenario` by substring
        self.write("S1 or control")
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                self.refused("test_S10_another_scenario", "S10", verb=verb)
                self.assertNotIn("approval", yaml.safe_load(self.plan.read_text()))

    def test_control_an_anchored_selector_keeps_the_plan_valid(self):
        self.write("test_S1_ or test_control_")
        self.accepted()

    def test_control_a_control_named_for_its_scenario_is_allowed(self):
        # A plan may write one control per scenario, `test_S2_control_…`, and `-k "S1 or control"` selecting it is not
        # the substring bleed: a control guards the fixture, it carries no scenario's proof.
        tests = (
            "import unittest\n\n\n"
            "class ProbeTests(unittest.TestCase):\n"
            "    def test_S1_the_first_behaviour(self):\n        pass\n\n"
            "    def test_S2_control_a_healthy_drive_records_no_park(self):\n        pass\n\n"
            "    def test_S4_control_a_genuinely_edited_plan_still_drains(self):\n        pass\n"
        )
        self.write("S1 or control", tests=tests)
        self.accepted()

    def test_control_a_check_without_k_is_not_linted(self):
        self.write(check='uv run pytest pkg/acceptance/plan/%s -q --junitxml="$EEC_JUNIT"' % PROBE)
        self.accepted()

    def test_control_a_legacy_plan_is_not_linted(self):
        legacy = self.repo / "docs" / "plans" / "legacy.plan.yaml"
        oracles = legacy.with_name("legacy.oracle")
        oracles.mkdir()
        (oracles / PROBE).write_text(TESTS)
        data = selector_plan(CHECK.format("S1 or control"))
        for key in ("domain", "archive"):
            data.pop(key)
        data["spec"] = "docs/specs/legacy.md"
        evidence = data["scenarios"][0]["evidence"]
        for key in ("acceptance", "package"):
            evidence.pop(key)
        evidence.update(oracle=[PROBE], place="pkg/legacy/")
        legacy.write_text(yaml.safe_dump(data, sort_keys=False))
        self.accepted(legacy)


if __name__ == "__main__":
    unittest.main()
