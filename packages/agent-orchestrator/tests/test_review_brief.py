"""The reviewer's brief: one pass, the verdict it hands in, and what a re-review is told."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_orchestrator import driver as app
from agent_orchestrator.driver.review import rereview_text

pytestmark = pytest.mark.unit


class AttemptRecord:
    """The record of the attempt under review, as the brief reads it: the report it handed in."""

    def __init__(self, task_id=None, head=None):
        self.task_id = task_id
        self.head = head

    def task(self, task_id):
        """One attempt, as the ledger holds it."""
        return {
            "packet": {"task_id": task_id},
            "report": {"task_id": self.task_id, "head": self.head or "abc1234"},
        }


class ReviewBriefTests(unittest.TestCase):
    def stub(self, run=None, attempt=None):
        # the brief reads only these attributes; a whole Driver needs a repo and a runtime
        return cast(
            Any,
            SimpleNamespace(
                run_dir=run or Path("/run"),
                repo=Path("/nonexistent-repo"),
                plan_path=Path("/plan.yaml"),
                args=SimpleNamespace(base="main"),
                book=attempt or AttemptRecord(),
            ),
        )

    def brief(self, run=None, attempt=None):
        packet = {"unit": "t1", "task_id": "t1o1", "checkout": "/nonexistent"}
        return app.Driver.review_text(self.stub(run, attempt), packet)

    def pass_steps(self, text):
        """The anchors of the numbered pass, asserted to print in the order the brief gives them."""
        anchors = [
            "Read the whole diff first",
            "Name what a user or caller would hit",
            "When a claim depends on how a library or tool behaves",
            "Each finding is one specific, checkable claim",
        ]
        found = []
        for number, anchor in enumerate(anchors, start=1):
            self.assertIn("\n%d. " % number + anchor, text, "step %d is missing" % number)
            found.append(text.index("\n%d. " % number + anchor))
        self.assertEqual(sorted(found), found, "the pass is printed out of order")

    @pytest.mark.spec("The reviewer brief gives one pass")
    def test_the_brief_gives_a_numbered_pass_from_the_diff_to_the_findings(self):
        text = self.brief()
        self.assertIn("## The pass", text)
        self.pass_steps(text)

    @pytest.mark.spec("The reviewer brief gives one pass")
    def test_the_brief_asks_for_what_a_caller_would_hit_and_leaves_the_rest_to_ci(self):
        text = self.brief()
        self.assertIn("a scenario the ticket satisfies that no test proves", text)
        self.assertIn("a test that still passes with the change reverted", text)
        self.assertIn("your commands are git reads", text)
        self.assertIn("CI runs the tests, the format and the lint", text)
        self.assertIn("report` call", text)
        for kind in ("nit", "question", "concern", "evidence"):
            self.assertNotIn("`%s`" % kind, text)
        self.assertNotIn("review.json", text)

    @pytest.mark.spec("The reviewer brief gives one pass")
    @pytest.mark.spec("Reviewer packet names the attempt under review")
    def test_the_brief_names_its_paths_and_carries_the_report_of_the_attempt_under_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            text = self.brief(run=run, attempt=AttemptRecord("t1o1", "c0ffee1"))
        self.assertIn(str(run / "t1" / "task.md"), text)
        self.assertIn("main...c0ffee1", text)
        self.assertIn('"head": "c0ffee1"', text)
        self.assertIn('"task_id": "t1o1"', text)
        self.assertNotIn("report*.json", text)

    @pytest.mark.spec("Scenario evidence")
    def test_the_brief_points_the_reviewer_at_each_red_excerpt(self):
        self.assertIn("`red`", self.brief())

    @pytest.mark.spec("A re-review resumes the reviewer on the fix")
    def test_a_re_review_message_holds_the_fix_diff_and_never_the_whole_change(self):
        packet = {"unit": "t1", "task_id": "t1o1", "checkout": "/work"}
        delta = {"from": "abc1234", "findings": "/run/t1r1/findings.json", "blocked": True}
        text = rereview_text(self.stub(), packet, delta, "/run/t1-review/task.md")
        self.assertIn("git -C /work diff abc1234..abc1234", text)
        self.assertIn("/run/t1r1/findings.json", text)
        self.assertIn("check each of your earlier findings", text)
        self.assertNotIn("Read the whole diff", text)
        passed = rereview_text(
            self.stub(), packet, dict(delta, blocked=False), "/run/t1-review/task.md"
        )
        self.assertNotIn("earlier findings", passed)
