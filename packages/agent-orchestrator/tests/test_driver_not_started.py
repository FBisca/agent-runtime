"""A resume the runtime saw produce nothing: the drive reads the runtime's verdict, not its own screen reading.

`wait` answers a resumed attempt that produced no progress, no report and no turn advance with
`attention: "not_started"` and the way out in `next`, once its 60-second grace is past. The idle tick asks the
same question from screen freshness and answers it two ticks (20 minutes) later, so a worker whose pty write was
accepted and whose turn never moved was logged `waiting: agent` and then never escalated. The drive acts on the
verdict `wait` delivers, on the tick it arrives, and never logs such a worker as waiting on nothing.
"""

import itertools
import unittest
from unittest import mock

import pytest
from fixtures import assignment, load_script, resume_id, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration

NOT_STARTED_NEXT = (
    "resume has not started: collect the worker's screen or stop it if no longer needed; "
    "the runtime never resends the task"
)


class NotStartedTests(GateFixture, unittest.TestCase):
    def resumed(self, verdict=True):
        """A wait step for the fix round's worker: it was resumed, its terminal is still live, its turn never
        advanced and it wrote nothing. With `verdict`, the runtime says what it decided about that."""
        entry = assignment("demo-t1", "working", "idle", id=resume_id("demo-t1", 1))
        if verdict:
            entry = dict(entry, attention="not_started", next=NOT_STARTED_NEXT)
        return wait_result([entry], reason="attention")

    def unit_ticks(self):
        return [e for e in self.run_events("idle") if e.get("unit") == "t1"]

    @pytest.mark.spec("Not-started attempts reach the Lead")
    def test_a_resumed_worker_that_never_started_is_named_on_the_tick_its_verdict_arrives(self):
        spec = load_script()
        moments = itertools.count(0, 600)  # the init reading, then ten minutes per tick
        self.waits([self.worker_done(), *(self.resumed() for _ in range(3)), self.skipped()])
        with mock.patch.object(spec.Driver, "clock", staticmethod(lambda: next(moments))):
            self.run_driver("--idle-minutes", "10")
        self.assertEqual(
            self.unit_ticks(),
            [],
            "the drive logged a worker the runtime had already decided about as waiting: %s"
            % (self.unit_ticks(),),
        )
        request = self.request("t1")
        self.assertEqual(request["event"], "not_started", request["reason"])
        self.assertIn("demo-t1", request["reason"])
        self.assertIn("not started", request["reason"])
        # the runtime's own sentence reaches the Lead, and so does the way out the driver owns
        self.assertIn("the runtime never resends the task", request["reason"])
        self.assertIn("look at its terminal", request["reason"])
        self.assertIn("fresh worker", request["reason"])
        self.assertEqual(len(self.wake_lines("decision")), 1, self.wake_lines())

    @pytest.mark.spec("Not-started attempts reach the Lead")
    def test_a_resumed_worker_that_reports_is_not_woken(self):
        spec = load_script()
        moments = itertools.count(0, 600)
        self.waits([self.worker_done(), self.worker_done("t1r1", tests=True)])
        with mock.patch.object(spec.Driver, "clock", staticmethod(lambda: next(moments))):
            self.run_driver("--idle-minutes", "10")
        self.assertEqual(self.unit_ticks(), [])
        self.assertFalse(
            (self.run_dir / "t1" / "request.json").exists(),
            "a resumed worker that advanced its turn and reported was asked about",
        )
        self.assertEqual(self.wake_lines("decision"), [])
        self.assertEqual(self.task("t1r1")["gate"], "reviewing")
