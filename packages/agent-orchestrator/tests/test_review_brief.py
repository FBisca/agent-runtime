"""The reviewer's brief keeps it on targeted tests: a reviewer that reruns a whole suite serially stalls the gate."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_orchestrator import driver as app

pytestmark = pytest.mark.unit


class ReviewBriefTests(unittest.TestCase):
    def brief(self, delta=None):
        # review_text reads only these three attributes; a whole Driver needs a repo and a runtime.
        stub = cast(
            Any,
            SimpleNamespace(
                run_dir=Path("/run"),
                plan_path=Path("/plan.yaml"),
                args=SimpleNamespace(base="main"),
            ),
        )
        packet = {"unit": "t1", "checkout": "/nonexistent"}
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

    def test_S1_the_brief_names_the_report_whose_claims_the_pass_checks(self):
        """orchestrator-engine/R145"""
        text = self.brief()
        self.assertIn("newest report", text)
        self.assertIn(str(Path("/run/t1")), text)
        self.assertIn(str(Path("/run/receipts")), text)
        self.assertIn(str(Path("/run/contract.md")), text)

    def test_S1_the_brief_says_what_makes_a_test_prove_its_scenario(self):
        """orchestrator-engine/R145"""
        text = self.brief()
        self.assertIn("Reverting the change makes it fail", text)
        self.assertIn("would pass without the change", text)
        self.assertIn("scenario not really tested", text)

    def test_S2_a_re_review_reads_the_delta_first_and_keeps_the_same_pass(self):
        """orchestrator-engine/R146"""
        text = self.brief(delta={"from": "abc1234", "findings": "/run/t1/review.json"})
        self.assertIn("read the delta `abc1234..HEAD` first", text)
        self.assertIn("reuse that finding's `id`", text)
        self.assertIn("## The pass, in this order", text)
        self.pass_steps(text)
