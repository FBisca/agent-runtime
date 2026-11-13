"""The run fixture's cost: a plan approved once per class, and a PATH that stays put between its tests."""

import io
import unittest
from unittest import mock

import pytest
from agent_orchestrator import plan as yaml_plan
from agent_orchestrator import runner
from fixtures import RunFixture

pytestmark = pytest.mark.integration


def run_class(body):
    """A throwaway TestCase class mixing in the fixture, run to its end; (the result, what each test recorded)."""
    seen = []

    class Lifecycle(RunFixture, unittest.TestCase):
        def test_first(self):
            seen.append(body(self))

        def test_second(self):
            seen.append(body(self))

        def test_third(self):
            seen.append(body(self))

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Lifecycle)
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    return result, seen


class FixtureCostTests(unittest.TestCase):
    def test_S8_a_class_approves_its_plan_once(self):
        """test-cost/S8: the plan every test starts from is approved once per class, not once per test."""
        real = yaml_plan.approve
        with mock.patch.object(yaml_plan, "approve", side_effect=real) as approve:
            result, seen = run_class(lambda case: case.plan.read_text(encoding="utf-8"))
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(len(seen), 3)
        self.assertEqual(approve.call_count, 1)
        # each test still starts from an approved plan of its own
        for text in seen:
            self.assertIn("approval:", text)

    def test_S8_each_test_of_a_class_starts_from_the_same_fresh_world(self):
        """test-cost/S8: a test that changes the repo or the fakes does not leak into the next test."""

        def dirty(case):
            leaks = [case.repo / "leak.txt", case.home / "leak.json", case.state_dir / "leak.json"]
            leaked = any(path.exists() for path in leaks)
            for path in leaks:
                path.write_text("x", encoding="utf-8")
            return leaked

        result, seen = run_class(dirty)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(seen, [False, False, False])

    def test_S8_path_stays_put_so_tool_versions_hits_its_cache(self):
        """test-cost/S8: the per-test home no longer changes PATH, so `tool_versions` is looked up once per class."""
        result, seen = run_class(lambda case: case.env()["PATH"])
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(len(set(seen)), 1, seen)

        runner.tool_versions.cache_clear()
        with mock.patch.object(runner.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="v1\n", stderr="")
            for path in seen:
                runner.tool_versions(path)
        self.assertEqual(run.call_count, len(runner.TOOLS))
