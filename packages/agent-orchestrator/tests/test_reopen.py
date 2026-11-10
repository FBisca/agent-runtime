"""A unit the Lead parked (mark blocked) is answered through the plan: TO-DO retries it, COMPLETE hands it back done,
anything else holds."""

import unittest

import pytest
from fixtures import plan_text
from test_decider import DeciderFixture

pytestmark = pytest.mark.integration


class ReopenTests(DeciderFixture, unittest.TestCase):
    def pause(self, rows=("T1",)):
        self.plan.write_text(plan_text(rows=rows), encoding="utf-8")
        self.waits([self.blocked(), self.decided("mark blocked", reason="which way?")])
        self.run_driver()
        self.assertEqual(self.task()["gate"], "blocked")

    def set_status(self, new):
        text = self.plan.read_text(encoding="utf-8").replace("BLOCKED · which way?", new)
        self.assertIn(new, text)
        self.plan.write_text(text, encoding="utf-8")

    def stops(self):
        return [c["args"]["name"] for c in self.calls("stop")]

    def test_S4_a_paused_ticket_set_to_todo_gets_a_fresh_attempt_of_the_same_unit(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S4"""
        self.pause()
        self.set_status("TO-DO")
        self.waits([])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])
        self.assertEqual(self.stops(), ["demo-t1"])
        tasks = self.ledger()["tasks"]
        self.assertEqual(tasks["t1"]["status"], "RELEASED")
        fresh = tasks["t1p1"]
        self.assertEqual((fresh["packet"]["unit"], fresh["status"]), ("t1", "ACTIVE"))
        self.assertEqual(self.plan_status(), "IN PROGRESS")

    def test_S5_a_refused_reopen_is_asked_about_and_dispatched_again_after_the_lead_s_todo(self):
        """orchestrator-engine/R5: drive-restart-recovery/S5"""
        self.pause()
        self.set_status("TO-DO")
        self.refuse(["demo-t1"])
        self.waits([])
        self.run_driver(expect=1)
        (wake,) = [w for w in self.wakes() if w["kind"] == "not-dispatched"]
        self.assertEqual(wake["unit"], "t1")
        self.assertEqual(self.ledger()["tasks"]["t1p1"]["status"], "RESERVED")
        self.refuse([])
        self.plan.write_text(plan_text(rows=("T1",)), encoding="utf-8")
        self.waits([])
        self.run_driver()
        tasks = self.ledger()["tasks"]
        self.assertEqual(tasks["t1p1"]["status"], "ACTIVE")
        self.assertNotIn("t1p2", tasks)

    def test_S4_a_paused_ticket_set_to_complete_unblocks_its_dependent(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S4"""
        self.pause(rows=("T1", "T2"))
        self.set_status("COMPLETE 2026-11-07")
        self.waits([])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t2"])
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "RELEASED")
        self.assertEqual(self.plan_status("T1"), "COMPLETE 2026-11-07")

    def test_S4_any_other_edit_leaves_the_ticket_paused(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S4"""
        self.pause()
        self.set_status("IN PROGRESS · thinking")
        self.waits([])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(
            self.stops(), ["demo-t1"]
        )  # stopped when the unit paused, not again by the edit
        self.assertEqual(self.task()["gate"], "blocked")


if __name__ == "__main__":
    unittest.main()
