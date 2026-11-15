"""The review file the gate reads: the reviewer's assignment names it, and the file it names is the review."""

import unittest

import pytest
from fixtures import report_wait
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class ReviewPayloadPathAcceptance(GateFixture, unittest.TestCase):
    """R147, R148: the reviewer is told where to write the review, and a review it wrote under another name
    inside the run directory is the review the gate reads."""

    def reviewer_wrote(self, path, text=None):
        """A wait step: the reviewer wrote its review at `path` and named that file in its report."""
        step = report_wait("demo-t1-review", "complete", text=text or "0 blocking - %s" % path)
        step["write"] = [[str(path), self.review2()]]
        return step

    def reviewer_resumes(self):
        return [c for c in self.calls("resume") if c["args"].get("name") == "demo-t1-review"]

    def test_S1_the_reviewers_assignment_names_the_review_file_it_must_write(self):
        """orchestrator-engine/R154: review-payload-path/S1"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        spawns = [c for c in self.calls("spawn") if c["args"].get("name") == "demo-t1-review"]
        self.assertEqual(len(spawns), 1, "the reviewer is spawned once")
        task = spawns[0]["args"]["task"]
        self.assertIn(str(self.run_dir / "t1" / "review.json"), task)

    def test_S2_a_review_written_under_another_name_is_the_review_the_gate_reads(self):
        """orchestrator-engine/R155: review-payload-path/S2"""
        elsewhere = self.run_dir / "t1-review" / "review.json"
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_wrote(elsewhere),
            ]
        )
        self.run_driver()
        self.assertEqual(
            self.reviewer_resumes(), [], "the reviewer is not asked for a file it already wrote"
        )
        self.assertIn("reviewed_commit", self.task(), "the gate read the review the reviewer named")
        self.assertTrue(
            (self.run_dir / "t1" / "review.json").is_file(),
            "the review the gate read is the one the driver's own path holds",
        )

    def test_control_a_review_named_outside_the_run_directory_is_not_read(self):
        """The driver reads its own run directory: a file elsewhere is nobody's."""
        elsewhere = self.root / "outside" / "review.json"
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_wrote(elsewhere)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertNotIn(
            "reviewed_commit", self.task(), "a file outside the run directory is not a review"
        )
        self.assertEqual(self.request()["event"], "review")
