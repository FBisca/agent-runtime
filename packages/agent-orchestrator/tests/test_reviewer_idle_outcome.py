"""Outcome oracle for the orchestrator-silent-idle plan (issue #94).

A live reviewer whose turn ends while a background job runs is not finished: the driver waits for its `complete`
report under the assignment it was spawned with, and only the timed idle watchdog asks the Lead. The `control`
tests hold today and must keep holding: a prompt reviewer completes the unit, and a dead reviewer with no review
still goes straight to the Lead.
"""

import unittest

import pytest
from fixtures import assignment, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration

REVIEWER = "demo-t1-review"


def silent(runtime_state="idle"):
    """A wait step: the reviewer's turn ended (idle or done) under its original assignment; no report, no file."""
    return wait_result([assignment(REVIEWER, "working", runtime_state)])


class SilentReviewerTests(GateFixture, unittest.TestCase):
    def recovery_resumes(self):
        return [c for c in self.calls("resume") if c["args"].get("name") == REVIEWER]

    def payload_trouble(self):
        return [e for e in self.events("payload-warning", "contract-error") if e.get("where") == "review"]

    def test_a_reviewer_idle_on_a_background_job_completes_when_it_reports(self):
        """S1: idle and done turns are not a finished review; the later complete under the original id is."""
        self.waits(
            [
                self.worker_done(tests=True),
                silent("idle"),
                silent("done"),
                silent("idle"),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.recovery_resumes(), [], "a silent reviewer was resumed with a warning")
        self.assertEqual(self.payload_trouble(), [], "a silent reviewer was treated as a missing payload")
        self.assertFalse((self.run_dir / "t1" / "request.json").exists(), "the Lead was asked")
        task = self.task()
        self.assertEqual(task["review"]["assignment_id"], "a-" + REVIEWER, "the reviewer's assignment was replaced")
        self.assertIn("reviewed_commit", task, "the reviewer's verdict was not recorded")
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))

    def test_a_reviewer_that_never_reports_is_asked_about_by_the_idle_watchdog(self):
        """S4: no warning resumes and no missing-file error; one idle request naming the reviewer and the way out."""
        self.waits(
            [
                self.worker_done(tests=True),
                silent("idle"),
                silent("idle"),
                silent("idle"),
                silent("idle"),
                self.skipped(),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.recovery_resumes(), [], "a silent reviewer was resumed with a warning")
        self.assertEqual(self.payload_trouble(), [], "a silent reviewer was treated as a missing payload")
        request = self.request()
        self.assertEqual(request["event"], "idle")
        self.assertIn(REVIEWER, request["reason"])
        self.assertIn("review.json", request["reason"])
        self.assertIn("decide", request["reason"])
        self.assertEqual(len(self.wake_lines("decision")), 1)

    def test_control_a_reviewer_that_reports_at_once_completes_the_unit(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        task = self.task()
        self.assertIn("reviewed_commit", task)
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))

    def test_control_a_dead_reviewer_without_a_review_goes_straight_to_the_lead(self):
        self.states({REVIEWER: "exited"})
        self.waits([self.worker_done(tests=True), self.reviewer_done(write=[]), self.skipped()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [], "a dead reviewer cannot write the file")
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("no agent left to write it", self.request()["reason"])


if __name__ == "__main__":
    unittest.main()
