"""The reviewer's brief keeps it on targeted tests: a reviewer that reruns a whole suite serially stalls the gate."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agent_orchestrator import driver as app

pytestmark = pytest.mark.unit


class ReviewBriefTests(unittest.TestCase):
    def brief(self):
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
        return app.Driver.review_text(stub, packet, Path("/run/t1/review.json"))

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
