"""The report timing: a worker whose turn ends without a report is a live worker: only its complete or the idle
watchdog moves it (One idle watchdog)."""

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
        del report  # a worker hands its report in with its complete: a turn that ended carries none
        return wait_result([assignment("demo-t1", "working", runtime_state="idle")])

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

    @pytest.mark.spec("One idle watchdog")
    def test_S5_a_worker_whose_turn_ended_without_a_report_stays_pending_until_its_complete(self):
        """orchestrator-silent-idle/S5 — idle and done are a live worker, not an exit."""
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
        self.assertEqual(self.run_events("escalation"), [])
        self.assertEqual(self.task()["gate"], "done")

    @pytest.mark.spec("One idle watchdog")
    def test_S5_a_worker_that_never_reports_is_asked_about_by_the_idle_watchdog(self):
        """orchestrator-silent-idle/S5"""
        self.waits([self.turn_ended(report=False)] * 4 + [self.skipped()])
        self.run_driver("--idle-minutes", "0")
        request = self.request()
        self.assertEqual(request["event"], "idle")
        self.assertIn("demo-t1", request["reason"])
        self.assertIn("decide", request["reason"])
        self.assertEqual(len(self.wake_lines("decision")), 1)
        self.assertEqual(self.task().get("gate"), "skipped", self.task())


if __name__ == "__main__":
    unittest.main()
