"""The driver counts a unit's failed gates itself; the cap is --max-fix-rounds, or a plan line per ticket."""

import unittest

import pytest
from test_gates import GateFixture
from test_step_routes import RoutedRunFixture

pytestmark = pytest.mark.integration


class RoundCapTests(GateFixture, unittest.TestCase):
    def failing_reports(self, count):
        """The first report and `count - 1` fix-round reports, none with tests, so converge fails each one."""
        return [self.worker_done()] + [self.worker_done("t1r%d" % n) for n in range(1, count)]

    def test_S18_the_third_failure_raises_a_request_by_default(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        self.waits(self.failing_reports(3) + [self.skipped()])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2)
        self.assertEqual((self.request()["task"], self.request()["event"]), ("t1r2", "gate"))
        self.assertEqual(self.ledger()["rounds"], {"t1": 3})

    def test_S18_a_report_claiming_review_rounds_changes_neither_count_nor_cap(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        lying = [
            self.worker_done(report=self.report("t1", review_rounds=9)),
            self.worker_done("t1r1", report=self.report("t1r1", review_rounds=0)),
            self.worker_done("t1r2", report=self.report("t1r2", review_rounds=-4)),
            self.skipped(),
        ]
        self.waits(lying)
        self.run_driver()
        self.assertEqual(self.ledger()["rounds"], {"t1": 3})
        self.assertEqual(self.request()["task"], "t1r2")
        self.assertEqual(len(self.calls("resume")), 2)

    def test_S18_a_report_claiming_rounds_does_not_use_up_the_cap(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        self.waits(
            [self.worker_done(report=self.report("t1", review_rounds=9)), self.worker_done("t1r1")]
        )
        self.run_driver()
        self.assertEqual(self.run_events("escalation"), [])
        self.assertEqual(len(self.calls("resume")), 2)
        self.assertEqual(self.ledger()["rounds"], {"t1": 2})


class CapPrecedenceTests(RoutedRunFixture, unittest.TestCase):
    """The fix-round cap a unit gets, read where the driver settles its run; the default-cap test above shows a cap stopping the rounds."""

    def with_cap(self, caps, rows=("T1",)):
        """The plan with `max_fix_rounds` on the tickets `caps` names: {"T1": 3}."""

        def cap(data):
            for ticket in data["tickets"]:
                if ticket["id"] in caps:
                    ticket["max_fix_rounds"] = caps[ticket["id"]]

        self.set_plan(rows, mutate=cap)

    def cap_of(self, unit="t1"):
        return self.settled.fix_cap(unit)

    def test_S18_max_fix_rounds_sets_the_cap(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        self.settle("--max-fix-rounds", "1")
        self.assertEqual(self.cap_of(), 1)

    def test_S18_a_larger_cap_allows_more_fix_rounds(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        self.settle("--max-fix-rounds", "3")
        self.assertEqual(self.cap_of(), 3)

    def test_S32_a_ticket_field_sets_the_cap_of_one_ticket(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        self.with_cap({"T1": 3})
        self.settle()
        self.assertEqual(self.cap_of(), 3)

    def test_S32_other_tickets_keep_the_default(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        self.with_cap({"T2": 3}, rows=("T1", "T2"))
        self.settle()
        self.assertEqual((self.cap_of("t1"), self.cap_of("t2")), (2, 3))

    def test_S32_several_tickets_may_carry_a_cap_and_beats_the_flag_for_its_own(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        self.with_cap({"T1": 1, "T2": 5}, rows=("T1", "T2"))
        self.settle("--max-fix-rounds", "4")
        self.assertEqual((self.cap_of("t1"), self.cap_of("t2"), self.cap_of("t3")), (1, 5, 4))


if __name__ == "__main__":
    unittest.main()
