"""The driver counts a unit's failed gates itself; the cap is --max-fix-rounds, or a plan line per ticket."""

import unittest

import pytest
from fixtures import plan_text
from test_gates import GateFixture

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

    def test_S18_max_fix_rounds_sets_the_cap(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        self.waits(self.failing_reports(2) + [self.skipped()])
        self.run_driver("--max-fix-rounds", "1")
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual((self.request()["task"], self.request()["event"]), ("t1r1", "gate"))

    def test_S18_a_larger_cap_allows_more_fix_rounds(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S18"""
        self.waits(self.failing_reports(4) + [self.skipped()])
        self.run_driver("--max-fix-rounds", "3")
        self.assertEqual(len(self.calls("resume")), 3)
        self.assertEqual(self.request()["task"], "t1r3")

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
        self.assertEqual(self.events("escalation"), [])
        self.assertEqual(len(self.calls("resume")), 2)
        self.assertEqual(self.ledger()["rounds"], {"t1": 2})


class PlanCapTests(GateFixture, unittest.TestCase):
    def with_cap(self, line, rows=("T1",)):
        self.plan.write_text(plan_text(rows=rows) + "\n" + line + "\n", encoding="utf-8")

    def test_S32_a_plan_line_sets_the_cap_of_one_ticket(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        self.with_cap("Max fix rounds: T1=3")
        self.waits(
            [self.worker_done()]
            + [self.worker_done("t1r%d" % n) for n in (1, 2, 3)]
            + [self.skipped()]
        )
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 3)
        self.assertEqual(self.request()["task"], "t1r3")

    def test_S32_other_tickets_keep_the_default(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        self.with_cap("Max fix rounds: T2=3", rows=("T1", "T2"))
        self.waits(
            [self.worker_done(), self.worker_done("t1r1"), self.worker_done("t1r2"), self.skipped()]
        )
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2)
        self.assertEqual(self.request()["task"], "t1r2")

    def test_S32_the_line_may_list_several_tickets_and_beats_the_flag_for_its_own(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        self.with_cap("Max fix rounds: T1=1, T2=5", rows=("T1", "T2"))
        self.waits([self.worker_done(), self.worker_done("t1r1"), self.skipped()])
        self.run_driver("--max-fix-rounds", "4")
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual(self.request()["task"], "t1r1")

    def test_S32_a_line_naming_an_unknown_ticket_or_a_bad_number_is_a_plan_error(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S32"""
        for line in ("Max fix rounds: T9=2", "Max fix rounds: T1=two", "Max fix rounds: T1"):
            self.with_cap(line)
            result = self.run_driver(expect=2)
            self.assertIn("Max fix rounds", result.stderr)


if __name__ == "__main__":
    unittest.main()
