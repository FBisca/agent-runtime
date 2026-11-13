"""The whole suite a repo declares as `checks.full` runs once per plan, on the integrated default-branch commit,
unless the run or the repo moves it to CI alone (check-tiers/S6, S7)."""

import json
import unittest

import pytest
from test_acceptance import STATES, AcceptanceFixture

pytestmark = pytest.mark.integration


class FullSuiteAtVerify(AcceptanceFixture, unittest.TestCase):
    def declare_full(self, run, verify=None):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        lines = ["checks:", "  full:", "    run: '%s'" % run.replace("'", "''")]
        if verify is not None:
            lines.append("    verify: %s" % ("true" if verify else "false"))
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def full_events(self):
        # Read the run's events directly: the fixture's own reader has been renamed once mid-plan.
        path = self.run_dir / "events.jsonl"
        if not path.is_file():
            return []
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return [row for row in rows if row.get("kind") == "full"]

    def deliver(self, *extra):
        self.write_plan()
        self.merged_on_main("done")
        self.waits([self.worker("done"), self.reviewer(), self.default_mutator()])
        self.run_driver("--repo", str(self.repo), *extra)

    def test_S6_a_failing_whole_suite_on_the_integrated_commit_rejects_the_plan(self):
        self.declare_full("test -f src/a/never.txt")
        self.deliver()
        self.assertEqual(self.history(), STATES + ["REJECTED"])
        (wake,) = [w for w in self.wakes() if w["kind"] == "plan-rejected"]
        self.assertIn("test -f src/a/never.txt", wake["text"])
        self.assertIn("--skip-full", wake["text"])
        log = self.run_dir / "full.log"
        self.assertTrue(log.is_file())
        self.assertIn(str(log), wake["text"])
        (event,) = self.full_events()
        self.assertEqual(event["state"], "FAIL")

    def test_S6_a_passing_whole_suite_runs_once_on_the_pinned_commit_and_the_plan_is_accepted(self):
        runs = self.root / "full-runs.txt"
        self.declare_full(
            'test "$(cat src/a/feature.txt)" = done && git rev-parse HEAD >> %s' % runs
        )
        self.deliver()
        main = self.git(self.origin, "rev-parse", "main")
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        self.assertEqual(
            runs.read_text(encoding="utf-8").split(), [main]
        )  # once, at the pinned commit
        (event,) = self.full_events()
        self.assertEqual((event["state"], event["sha"]), ("PASS", main))

    def test_S7_skip_full_leaves_the_whole_suite_to_ci(self):
        self.declare_full("test -f src/a/never.txt")
        self.deliver("--skip-full")
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        (event,) = self.full_events()
        self.assertEqual((event["state"], event["reason"]), ("skipped", "--skip-full"))

    def test_S7_a_repo_that_sets_full_verify_false_leaves_the_whole_suite_to_ci(self):
        self.declare_full("test -f src/a/never.txt", verify=False)
        self.deliver()
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        (event,) = self.full_events()
        self.assertEqual(
            (event["state"], event["reason"]), ("skipped", "checks.full.verify: false")
        )

    def test_control_a_plan_whose_evidence_holds_is_accepted_without_a_config(self):
        self.deliver()
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        self.assertEqual(self.full_events(), [])
