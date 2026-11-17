"""The reviewer's brief keeps it on targeted tests: a reviewer that reruns a whole suite serially stalls the gate."""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_orchestrator import driver as app

pytestmark = pytest.mark.unit


class AttemptRecord:
    """The record of the attempt under review, as the brief reads it: its report file and the head it claims."""

    def __init__(self, report=None, head=None):
        self.report = Path(report) if report else None
        self.head = head

    def task(self, task_id):
        """One attempt, as the ledger holds it."""
        return {
            "packet": {"task_id": task_id},
            "report_path": str(self.report) if self.report else None,
            "report": {"head": self.head} if self.head else {},
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

    def test_the_brief_asks_for_targeted_tests_and_points_at_the_suites_already_run(self):
        text = self.brief()
        self.assertIn("Run targeted tests only", text)
        self.assertIn("CI already ran the suites", text)

    def test_S3_the_brief_asks_a_requirement_violation_to_be_blocking_with_its_metadata(self):
        """orchestrator-engine/R136"""
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
            "Test cost",
            "The report matches the code",
            "The numbered requirements it touches still hold",
            "The code follows the repository's rules",
            "Only then judge severity",
        ]
        found = []
        for number, anchor in enumerate(anchors, start=1):
            self.assertIn("\n%d. " % number + anchor, text, "step %d is missing" % number)
            found.append(text.index("\n%d. " % number + anchor))
        self.assertEqual(sorted(found), found, "the pass is printed out of order")

    def test_S1_the_brief_gives_a_numbered_pass_from_the_diff_to_the_verdict(self):
        """orchestrator-engine/R145"""
        text = self.brief()
        self.assertIn("## The pass, in this order", text)
        self.pass_steps(text)

    def test_S4_the_brief_measures_a_tests_cost_and_blocks_a_material_slowdown(self):
        """orchestrator-engine/R145: a test that slows every later run is a MAJOR defect."""
        text = self.brief()
        self.assertIn("test_cost", text)
        self.assertIn("time the diff's test files yourself", text)
        self.assertNotIn("--durations", text)
        self.assertIn("over 2 s", text)
        self.assertIn("materially slows the suite", text)

    @pytest.mark.integration
    def test_S1_the_brief_names_the_report_whose_claims_the_pass_checks(self):
        """orchestrator-engine/R145"""
        # The unit's directory holds the first attempt's report, stale the moment the unit was
        # re-dispatched; the attempt under review wrote its own, and that is the one the pass checks.
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "t1").mkdir()
            (run / "t1" / "report.json").write_text("{}", encoding="utf-8")
            report = run / "t1o1" / "report.json"
            report.parent.mkdir()
            report.write_text("{}", encoding="utf-8")
            written = datetime(2026, 11, 16, 10, 0, 0, tzinfo=timezone.utc).timestamp()
            os.utime(report, (written, written))
            (run / "receipts").mkdir()
            (run / "receipts" / "check.json").write_text("{}")
            text = self.brief(run=run, attempt=AttemptRecord(report, "c0ffee1"))
        # The task file, the run's contract and its evidence, by path.
        self.assertIn(str(run / "t1" / "task.md"), text)
        self.assertIn(str(run / "contract.md"), text)
        self.assertIn("1 files in " + str(run / "receipts"), text)
        # The attempt under review's own report, when it was written, and the head it claims.
        self.assertIn(str(report), text)
        self.assertIn("2026-11-16T10:00:00", text)
        self.assertIn("c0ffee1", text)
        # Never the glob a stale report in the unit's directory would answer with, and never a file read
        # under another name as a fallback for the review the driver asked for.
        self.assertNotIn("report*.json", text)
        self.assertNotIn(str(run / "t1" / "report.json"), text)
        self.assertNotIn("written under another name", text)

    def test_S1_the_brief_says_what_makes_a_test_prove_its_scenario(self):
        """orchestrator-engine/R145"""
        text = self.brief()
        self.assertIn("Reverting the change makes it fail", text)
        self.assertIn("would pass without the change", text)
        self.assertIn("scenario not really tested", text)

    def test_S4_the_brief_names_one_review_file(self):
        text = self.brief()
        self.assertIn("/run/t1/review.json", text)
        self.assertNotIn("a file written under another name", text)
        self.assertFalse(hasattr(app, "announced_path"))
        self.assertFalse(hasattr(app, "adopt_review"))

    def test_S6_spec_records_the_exact_report_and_no_adoption(self):
        spec = (
            Path(__file__).resolve().parents[3] / "docs/specs/orchestrator-engine.md"
        ).read_text()
        section = spec.split("## Requirements: reviewer-brief", 1)[1].split(
            "## Requirements: lead-decision-gates", 1
        )[0]
        self.assertIn("report path the ledger recorded", section)
        self.assertNotIn("newest `report*.json`", section)
        payload = spec.split("## Requirements: review-payload-path", 1)[1].split(
            "## Requirements:", 1
        )[0]
        self.assertNotIn("**R155**", payload)
        self.assertNotIn("| R155 |", payload)

    def test_S2_a_re_review_reads_the_delta_first_and_keeps_the_same_pass(self):
        """orchestrator-engine/R146"""
        text = self.brief(delta={"from": "abc1234", "findings": "/run/t1/review.json"})
        self.assertIn("read the delta `abc1234..HEAD` first", text)
        self.assertIn("reuse that finding's `id`", text)
        self.assertIn("## The pass, in this order", text)
        self.pass_steps(text)
