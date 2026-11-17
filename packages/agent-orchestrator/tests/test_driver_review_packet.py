"""The review packet carries the report recorded for the active attempt."""

import unittest

import pytest
from fixtures import report_wait, resume_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class ReviewPacketTests(GateFixture, unittest.TestCase):
    @pytest.mark.spec("Reviewer packet names the attempt under review")
    def test_redispatch_brief_carries_the_report_the_active_attempt_handed_in(self):
        completed = report_wait(
            "demo-t1", "complete", text=self.report("t1d1"), id=resume_id("demo-t1", 1)
        )
        completed["write"] = [[str(self.worktree / "src/a/test_a.py"), "def test_a():\n    pass\n"]]
        self.waits(
            [
                report_wait("demo-t1", "blocked", "cannot continue"),
                self.decide_step("t1", "retry"),
                completed,
            ]
        )
        self.run_driver()

        head = self.task("t1d1")["report"]["head"]
        brief = (self.run_dir / "t1-review/task.md").read_text(encoding="utf-8")
        self.assertIn('"task_id": "t1d1"', brief)
        self.assertIn(head, brief)
        for search in ("newest", "report*", "*.json"):
            self.assertNotIn(search, brief)
