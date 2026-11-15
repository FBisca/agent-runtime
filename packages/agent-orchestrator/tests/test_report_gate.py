"""The report gate against the ledger: a report the ledger cannot read goes back to the worker that wrote it, and
the drive books no error for it."""

import json
import unittest

import pytest
from fixtures import report_wait, resume_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class ReportGateTests(GateFixture, unittest.TestCase):
    """A worker's payload that `checkpoint` refuses is the sender's to fix, never the drive's to crash on."""

    def unreadable(self, task_id="t1"):
        """A report that fails a COMPLETE rule and carries a check outcome the ledger cannot read: the payload the
        00c run sent, which `checkpoint` refused from inside `fail_gate` and so ended the drive."""
        report = json.loads(self.report(task_id))
        report["checks"] = [{"command": "uv run pytest", "outcome": "green, 394 passed"}]
        report["remaining"] = ["the other ticket's work"]
        return json.dumps(report)

    def corrected(self, task_id="t1"):
        """A wait step: the warned worker writes the next report — one the driver accepts — and says complete."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 1))
        step["write"] = [[str(self.run_dir / task_id / "report.2.json"), self.report(task_id)]]
        return step

    def test_S1_a_report_the_ledger_cannot_read_is_refused_to_its_sender(self):
        """The unreadable field is the sender's to fix: the drive books no error, reaches finish, and the report the
        resumed worker writes is gated as usual."""
        self.waits(
            [
                self.worker_done(tests=True, report=self.unreadable()),
                self.corrected(),
                self.reviewer_done(),
            ]
        )
        result = self.run_driver()
        self.assertEqual(
            json.loads(result.stdout)["errors"], [], "a worker payload ended the drive"
        )
        self.assertEqual(self.run_events("error"), [])
        self.assertTrue(self.run_events("finish"), "the drive did not reach finish")
        resumed = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumed], ["demo-t1"])
        self.assertIn(str(self.run_dir / "t1" / "report.json"), resumed[0]["args"]["task"])
        self.assertIn("invalid check outcome", resumed[0]["args"]["task"])
        self.assertIn("checks[].outcome", resumed[0]["args"]["task"], "the field is not named")
        self.assertEqual(self.task()["gate"], "done", "the corrected report was not gated as usual")

    def test_S1_a_refusal_the_sender_repeats_asks_the_lead(self):
        """The resume is bounded: a worker that sends the same unreadable report twice is asked about, not looped."""
        self.waits(
            [
                *self.repeated(self.worker_done(tests=True, report=self.unreadable())),
                self.skipped(),
            ]
        )
        result = self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2, "the sender was warned more than twice")
        self.assertEqual(self.request()["event"], "report")
        self.assertIn("invalid check outcome", self.request()["reason"])
        self.assertIn("checks[].outcome", self.request()["reason"], "the field is not named")
        self.assertEqual(json.loads(result.stdout)["errors"], [])
