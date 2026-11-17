"""The reviewer's brief keeps it on targeted tests: a reviewer that reruns a whole suite serially stalls the gate."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_orchestrator import driver as app

pytestmark = pytest.mark.unit


class AttemptRecord:
    """The record of the attempt under review, as the brief reads it: its report file and the head it claims."""

    def __init__(self, task_id=None, head=None):
        self.task_id = task_id
        self.head = head

    def task(self, task_id):
        """One attempt, as the ledger holds it."""
        return {
            "packet": {"task_id": task_id},
            "report": {"task_id": self.task_id, "head": self.head} if self.head else {},
        }


class ReviewBriefTests(unittest.TestCase):
    def brief(self, delta=None, run=None, attempt=None):
        # review_text reads only these attributes; a whole Driver needs a repo and a runtime.
        stub = cast(
            Any,
            SimpleNamespace(
                run_dir=run or Path("/run"),
                plan_path=Path("/plan.yaml"),
                args=SimpleNamespace(base="main"),
                book=attempt or AttemptRecord(),
            ),
        )
        packet = {"unit": "t1", "task_id": "t1o1", "checkout": "/nonexistent"}
        return app.Driver.review_text(
            stub, packet, Path("/run/t1/review.json"), delta, [] if delta else None
        )

    @pytest.mark.spec("Review items assert violations as blocking")
    def test_S3_the_brief_asks_a_requirement_violation_to_be_blocking_with_its_metadata(self):
        text = self.brief()
        self.assertIn("requirement_violation", text)
        for field in ("source", "id", "expected", "observed"):
            self.assertIn("`%s`" % field, text)
        self.assertIn("blocking", text)
        self.assertIn("rare", text)

    def pass_steps(self, text):
        """The anchors of the numbered pass, asserted to print in the order the brief gives them."""
        anchors = [
            "Read the whole diff",
            "The ticket delivers what it says",
            "Each test proves its scenario",
            "The report matches the code",
            "The numbered requirements it touches still hold",
            "Only then write the review",
        ]
        found = []
        for number, anchor in enumerate(anchors, start=1):
            self.assertIn("\n%d. " % number + anchor, text, "step %d is missing" % number)
            found.append(text.index("\n%d. " % number + anchor))
        self.assertEqual(sorted(found), found, "the pass is printed out of order")

    @pytest.mark.spec("The reviewer brief gives one pass")
    def test_S1_the_brief_gives_a_numbered_pass_from_the_diff_to_the_verdict(self):
        text = self.brief()
        self.assertIn("## The pass, in this order", text)
        self.pass_steps(text)

    @pytest.mark.spec("The reviewer brief gives one pass")
    def test_S4_the_brief_asks_for_correctness_bugs_only(self):
        text = self.brief()
        self.assertIn("Report only what a user or caller would hit", text)
        self.assertIn("a test that would still pass without the change", text)
        self.assertIn("run no test and no command other than git reads", text)
        self.assertIn("reading it against the diff", text)
        self.assertNotIn("Run targeted tests", text)
        self.assertNotIn("background jobs", text)
        for kind in ("nit", "question", "concern"):
            self.assertNotIn(kind + "|", text)
            self.assertNotIn("`%s`" % kind, text)

    @pytest.mark.spec("The reviewer brief gives one pass")
    @pytest.mark.integration
    @pytest.mark.spec("Reviewer packet names the attempt under review")
    def test_the_brief_carries_the_report_of_the_attempt_under_review(self):
        # An earlier attempt's report is stale the moment the unit was re-dispatched; the attempt under review's own
        # hand-in is the one the pass checks, and the brief carries its content: there is no file to name.
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            text = self.brief(run=run, attempt=AttemptRecord("t1o1", "c0ffee1"))
        self.assertIn(str(run / "t1" / "task.md"), text)
        self.assertIn(str(run / "contract.md"), text)
        self.assertIn('"head": "c0ffee1"', text)
        self.assertIn('"task_id": "t1o1"', text)
        self.assertNotIn("report*.json", text)
        self.assertNotIn("report.json", text)

    @pytest.mark.spec("The reviewer brief gives one pass")
    def test_S1_the_brief_says_what_makes_a_test_prove_its_scenario(self):
        text = self.brief()
        self.assertIn("Reverting the change makes it fail", text)
        self.assertIn("would pass without the change", text)
        self.assertIn("scenario not really tested", text)

    @pytest.mark.spec("Scenario evidence")
    def test_the_brief_asks_the_reviewer_to_judge_each_red_excerpt(self):
        text = self.brief()
        self.assertIn("`red`", text)
        self.assertIn("genuine assertion failure", text)
        self.assertIn("cannot fail", text)

    @pytest.mark.spec("The review file path is named")
    def test_S4_the_brief_names_one_review_file(self):
        text = self.brief()
        self.assertIn("/run/t1/review.json", text)
        self.assertNotIn("a file written under another name", text)
        self.assertFalse(hasattr(app, "announced_path"))
        self.assertFalse(hasattr(app, "adopt_review"))

    @pytest.mark.spec("The reviewer brief gives one pass")
    def test_S6_spec_records_the_exact_report_and_no_adoption(self):
        spec = (
            Path(__file__).resolve().parents[3] / "docs/specs/orchestrator-engine.md"
        ).read_text()
        section = spec.split("### Requirement: The reviewer brief gives one pass", 1)[1].split(
            "### Requirement:", 1
        )[0]
        self.assertIn("report path the ledger recorded", section)
        self.assertNotIn("newest `report*.json`", section)

    @pytest.mark.spec("The re-review brief starts at the delta")
    def test_S2_a_re_review_reads_the_delta_first_and_keeps_the_same_pass(self):
        text = self.brief(delta={"from": "abc1234", "findings": "/run/t1/review.json"})
        self.assertIn("read the delta `abc1234..HEAD` first", text)
        self.assertIn("reuse that finding's `id`", text)
        self.assertIn("## The pass, in this order", text)
        self.pass_steps(text)
