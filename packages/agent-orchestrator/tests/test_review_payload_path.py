"""The review file the gate reads: the reviewer's assignment names it, and the file it names is the review."""

import unittest

import pytest
from fixtures import report_wait
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class ReviewPayloadPathAcceptance(GateFixture, unittest.TestCase):
    """The reviewer is told where to write the one review the gate reads."""

    def reviewer_wrote(self, path, text=None):
        """A wait step: the reviewer wrote its review at `path` and named that file in its report."""
        step = report_wait("demo-t1-review", "complete", text=text or "0 blocking - %s" % path)
        step["write"] = [[str(path), self.review2()]]
        return step

    def reviewer_resumes(self):
        return [c for c in self.calls("resume") if c["args"].get("name") == "demo-t1-review"]

    @pytest.mark.spec("The review file path is named")
    def test_S1_the_reviewers_assignment_names_the_review_file_it_must_write(self):
        """review-payload-path/S1"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        spawns = [c for c in self.calls("spawn") if c["args"].get("name") == "demo-t1-review"]
        self.assertEqual(len(spawns), 1, "the reviewer is spawned once")
        task = spawns[0]["args"]["task"]
        self.assertIn(str(self.run_dir / "t1" / "review.json"), task)

    def test_S2_a_review_written_under_another_name_is_not_read(self):
        elsewhere = self.run_dir / "t1-review" / "review.json"
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_wrote(elsewhere)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertNotIn("reviewed_commit", self.task())
        self.assertFalse((self.run_dir / "t1" / "review.json").exists())
        self.assertTrue(self.reviewer_resumes())
        self.assertEqual(self.request()["event"], "review")

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
