"""The report timing: a worker that writes its report and whose turn ends before the runtime says so.

S0: WHEN a known orchestration failure is reproduced on an isolated fixture, THEN the test fails before repair and
passes afterwards under the same fixed acceptance criteria. The failure is the report timing one: the report is on
disk, the runtime says the turn ended (`runtime state idle`), and the driver parks the ticket as `ended without a
report` and asks a human about a report it already has. After the repair the driver reads the report and moves on;
a turn that ended with no report at all is a live worker: only its complete or the idle watchdog moves it (R62).
"""

import shlex
import unittest
from unittest import mock

import pytest
from fixtures import assignment, load_script, wait_result
from test_gates import GateFixture

# Every test here runs the driver: these are integration tests.
pytestmark = pytest.mark.integration


class ReportTimingTests(GateFixture, unittest.TestCase):
    """One ticket, a worker whose turn ends; the report is on disk or it is not."""

    def turn_ended(self, report=True):
        """A wait step: the worker's turn ended without a message; with `report` its report is on disk."""
        write = [[str(self.run_dir / "t1" / "report.json"), self.report("t1")]] if report else []
        return wait_result([assignment("demo-t1", "working", runtime_state="idle")], write=write)

    def worker_done(self, task_id="t1", tests=False, report=None, name="demo-t1"):
        """A wait step: the runtime delivers the worker's completion message."""
        del task_id, tests, report  # this step carries no report and no test file
        return wait_result([assignment(name, "complete")])

    def answer(self):
        """A wait step: the Lead answers the open request. There is none when the driver did not ask, so the
        answer is allowed to fail: the run must end either way."""
        argv = self.decide_argv("t1", "skip", reason="because")
        return wait_result(
            run=[["sh", "-c", " ".join(shlex.quote(part) for part in argv) + " || true"]]
        )

    def asked(self):
        """The unit's request, or None: the driver asked nobody anything."""
        return (self.run_dir / "t1" / "request.json").exists()

    def test_S6_a_report_on_disk_when_the_turn_ends_advances_the_worker_as_today(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/R6 — the report_landed shortcut is kept."""
        self.waits([self.turn_ended(), self.answer(), self.worker_done()])
        self.run_driver()
        self.assertFalse(self.asked())
        self.assertEqual(len(self.events("report-landed")), 1)
        self.assertEqual((self.task().get("report") or {}).get("task_id"), "t1")

    def test_S0_a_report_on_disk_is_not_a_reason_to_ask_a_human(self):
        """orchestrator-engine/R2: P3.T2/S0 — the report landed and the turn ended; nobody is asked about it."""
        self.waits([self.turn_ended(), self.answer(), self.worker_done()])
        self.run_driver()
        self.assertFalse(self.asked(), "the driver asked a human about a report it already had")
        task = self.task()
        self.assertNotEqual(task.get("gate"), "requested", task)
        self.assertEqual(
            (task.get("report") or {}).get("task_id"), "t1", "the driver did not read the report"
        )

    def test_S5_a_worker_whose_turn_ended_without_a_report_stays_pending_until_its_complete(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/R5 — idle and done are a live worker, not an exit."""
        spec = load_script()
        done = GateFixture.worker_done(self, tests=True)
        self.waits(
            [
                self.turn_ended(report=False),
                wait_result([assignment("demo-t1", "working", runtime_state="done")]),
                self.turn_ended(report=False),
                done,
                self.reviewer_done(),
            ]
        )
        with mock.patch.object(spec.Driver, "clock", staticmethod(lambda: 0)):
            self.run_driver("--idle-minutes", "10")
        self.assertFalse(self.asked(), "an idle worker was asked about at once")
        self.assertEqual(self.events("escalation"), [])
        self.assertEqual(self.task()["gate"], "done")

    def test_S5_a_worker_that_never_reports_is_asked_about_by_the_idle_watchdog(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/R5"""
        self.waits([self.turn_ended(report=False)] * 4 + [self.skipped()])
        self.run_driver("--idle-minutes", "0")
        request = self.request()
        self.assertEqual(request["event"], "idle")
        self.assertIn("demo-t1", request["reason"])
        self.assertIn(str(self.run_dir / "t1" / "report.json"), request["reason"])
        self.assertIn("decide", request["reason"])
        self.assertEqual(len(self.wake_lines("decision")), 1)
        self.assertEqual(self.task().get("gate"), "skipped", self.task())


if __name__ == "__main__":
    unittest.main()
