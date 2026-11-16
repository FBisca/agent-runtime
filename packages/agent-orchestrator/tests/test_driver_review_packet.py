"""The review packet follows the report recorded for the active attempt."""

import unittest
from datetime import datetime, timezone

import pytest
from fixtures import report_wait, resume_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class ReviewPacketTests(GateFixture, unittest.TestCase):
    def test_S1_redispatch_brief_names_the_ledger_report_and_its_metadata(self):
        """orchestrator-engine/R145: locator-invariants/S1"""
        report = self.run_dir / "t1d1" / "report.json"
        completed = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 1))
        completed["write"] = [
            [str(report), self.report("t1d1", head="def5678")],
            [str(self.worktree / "src/a/test_a.py"), 'def test_a():\n    """S1"""\n'],
        ]
        self.waits(
            [
                report_wait("demo-t1", "blocked", "cannot continue"),
                self.decide_step("t1", "retry"),
                completed,
            ]
        )
        self.run_driver()

        self.assertEqual(self.task("t1d1")["report_path"], str(report))
        brief = (self.run_dir / "t1-review/task.md").read_text(encoding="utf-8")
        timestamp = datetime.fromtimestamp(report.stat().st_mtime, timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        reported = next(
            line for line in brief.splitlines() if line.startswith("- What it reported:")
        )
        self.assertIn(str(report), reported)
        self.assertIn(timestamp, reported)
        self.assertIn("def5678", reported)
        for search in ("newest", "report*", "*.json"):
            self.assertNotIn(search, brief)
