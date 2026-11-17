"""The unit's panes stop when it merges or pauses."""

import unittest

import pytest
from fixtures import report_wait, resume_id
from test_gates import BLOCKING, GateFixture

pytestmark = pytest.mark.integration


class ReuseFixture(GateFixture):
    def git_commit(self, message="fix"):
        wt = str(self.worktree)
        git = ["git", "-C", wt, "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        return [git + ["add", "."], git + ["commit", "-q", "-m", message]]

    def fix_report(self, task_id="t1r1", nth=1, commit=True):
        """The worker's fix-round report; the fix is a committed change to src/a/x.py."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", nth))
        step["write"] = [
            [str(self.run_dir / task_id / "report.json"), self.report(task_id)],
            [str(self.worktree / "src" / "a" / "x.py"), "fixed = True\n"],
        ]
        if commit:
            step["run"] = self.git_commit()
        return step

    def re_review(self, review=None, nth=1):
        """The unit's reviewer, resumed for the `nth` time, hands in its verdict."""
        return report_wait(
            "demo-t1-review",
            "complete",
            text=review or self.review(),
            id=resume_id("demo-t1-review", nth),
        )

    def blocking(self):
        return BLOCKING


class PaneStopTests(ReuseFixture, unittest.TestCase):
    def stopped(self):
        return [c["args"]["name"] for c in self.calls("stop")]

    def test_S11_a_merged_unit_stops_its_implementer_and_reviewer(self):
        """orchestrate-lean-loop/S11"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task()["status"], "COMPLETE")

    def test_S11_a_dirty_checkout_still_stops_both_panes(self):
        """orchestrate-lean-loop/S11"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertTrue(self.worktree.exists())
        self.assertIn("uncommitted", self.task()["cleanup"])
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])

    def test_S11_a_unit_the_lead_blocks_stops_its_implementer_and_reviewer(self):
        """orchestrate-lean-loop/S11"""
        stuck = report_wait("demo-t1-review", "blocked", "I cannot read the diff")
        self.waits(
            [self.worker_done(tests=True), stuck, self.decide_step("t1", "mark blocked")]
        )  # the reviewer is stuck: the unit pauses for the Lead
        self.run_driver()
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task()["gate"], "blocked")

    def test_S11_a_unit_waiting_for_the_lead_keeps_its_panes_for_the_decision(self):
        """orchestrate-lean-loop/S11"""
        stuck = report_wait("demo-t1-review", "blocked", "I cannot read the diff")
        self.waits([self.worker_done(tests=True), stuck, self.skipped()])
        self.run_driver()
        self.assertEqual(self.request()["event"], "reviewer")
        kinds = [e["kind"] for e in self.run_events("decision", "stop")]
        self.assertEqual(
            kinds[0], "decision"
        )  # nothing is stopped while the request is open; the skip that parks the unit does
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])

    def test_S11_a_pane_that_is_already_gone_is_not_stopped_again(self):
        """orchestrate-lean-loop/S11"""
        self.states({"demo-t1-review": "exited"})
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertNotIn("demo-t1-review", self.stopped())


if __name__ == "__main__":
    unittest.main()
