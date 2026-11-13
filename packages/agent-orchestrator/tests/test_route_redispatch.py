"""A new attempt of a unit that already had one keeps the unit's last route: its class is the higher of the last
attempt's class and the one the routes compute now, never lower."""

import json
import unittest

import pytest
from test_decider import DeciderFixture

pytestmark = pytest.mark.integration


class RedispatchRouteTests(DeciderFixture, unittest.TestCase):
    def set_last_class(self, task_id, klass):
        """An earlier attempt that ran on `klass`, as a raised retry leaves it in the ledger."""
        path = self.state_dir / "demo.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["tasks"][task_id]["packet"]["class"] = klass
        path.write_text(json.dumps(data), encoding="utf-8")

    def spawn_args(self, name):
        return [c["args"] for c in self.calls("spawn") if c["args"]["name"] == name][-1]

    def pause(self):
        self.waits([self.blocked(), self.decided("mark blocked", reason="which way?")])
        self.run_driver()
        self.assertEqual(self.task()["gate"], "blocked")

    def test_S19_an_orphan_redispatch_keeps_the_higher_last_class(self):
        """orchestrator-engine/R2: plan-amend/S19 — T1 is small, its last attempt ran large: the new attempt is large."""
        self.run_driver()
        self.set_last_class("t1", "large")
        self.gone("demo-t1", "exited")
        self.waits([])
        self.run_driver()
        redone = self.ledger()["tasks"]["t1o1"]["packet"]
        self.assertEqual((redone["class"], redone["model"]), ("large", "opus"))
        self.assertEqual(self.spawn_args("demo-t1")["model"], "opus")

    def test_S19_a_reopened_parked_unit_keeps_the_higher_last_class(self):
        """orchestrator-engine/R2: plan-amend/S19 — a parked unit reopened to TO-DO keeps its last attempt's larger class."""
        self.pause()
        self.set_last_class("t1", "large")
        self.set_ticket_status("T1", "TO-DO")
        self.waits([])
        self.run_driver()
        fresh = self.ledger()["tasks"]["t1p1"]["packet"]
        self.assertEqual((fresh["class"], fresh["model"]), ("large", "opus"))
        self.assertEqual(self.spawn_args("demo-t1")["model"], "opus")

    def test_S19_a_reserved_attempt_keeps_the_higher_last_class(self):
        """orchestrator-engine/R2: plan-amend/S19 — a reservation made for a reopened unit carries the higher class."""
        self.pause()
        self.set_last_class("t1", "large")
        self.set_ticket_status("T1", "TO-DO")
        self.refuse(["demo-t1"])
        self.waits([])
        self.run_driver(expect=1)
        reserved = self.ledger()["tasks"]["t1p1"]
        self.assertEqual(reserved["status"], "RESERVED")
        self.assertEqual(
            (reserved["packet"]["class"], reserved["packet"]["model"]), ("large", "opus")
        )
