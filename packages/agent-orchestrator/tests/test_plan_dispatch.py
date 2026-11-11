"""A YAML plan dispatches its agent tickets with the class, effort, model and blocking order its rows name."""

import json
import unittest

import pytest
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class PlanDispatchTests(GateFixture, unittest.TestCase):
    def test_S4_names_routes_and_blocking_order_follow_the_rows_and_the_plan_stays_immutable(self):
        def capped(data):
            for ticket in data["tickets"]:
                ticket["max_fix_rounds"] = 3

        self.set_plan(("T1", "T2", "T3", "T5"), mutate=capped)
        before = self.plan.read_bytes()
        first = self.worker_done(tests=True)
        self.waits([first, self.reviewer_done()])
        self.run_driver("--cli", "codex", "--repo", str(self.repo), "--max-workers", "4")
        spawns = [c["args"] for c in self.calls("spawn")]
        # T2 is blocked by T1: it dispatches only after T1 is COMPLETE, after the other ready tickets.
        self.assertEqual(
            [s["name"] for s in spawns],
            ["demo-t1", "demo-t3", "demo-t5", "demo-t1-review", "demo-t2"],
        )
        self.assertEqual(
            {
                s["name"]: (s["cli"], s["model"], s["effort"])
                for s in spawns
                if "review" not in s["name"]
            },
            {
                "demo-t1": ("codex", "gpt-s", "low"),
                "demo-t3": ("codex", "gpt-m", "medium"),
                "demo-t5": ("codex", "gpt-m", "medium"),
                "demo-t2": ("codex", "gpt-m", "high"),
            },
        )
        self.assertEqual(self.task()["status"], "COMPLETE")
        t2 = next(c for c in self.calls("spawn") if c["args"]["name"] == "demo-t2")
        self.assertTrue(
            any(
                json.loads(e)["kind"] == "state"
                and json.loads(e).get("status", [None, None])[1] == "COMPLETE"
                for e in t2["events"]
            )
        )
        self.assertEqual(self.plan.read_bytes(), before)
