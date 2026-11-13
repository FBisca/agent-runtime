"""ensure_agent: every spawn records an intent first, and a restart re-attaches by the stable name."""

import json
import os
import sys
import unittest
from unittest import mock

import pytest
from fixtures import RunFixture, load_script, resume_id, spawn_id

pytestmark = pytest.mark.integration


class EnsureAgentTests(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.set_plan(("T1",))

    def crash_after_spawn(self, point="bind"):
        """Run the driver in this process and die right after the spawn: `assignment_of` is the first step after it
        (the intent is still open), `bind` the ledger bind of the task (the agent record is already written)."""
        spec = load_script()
        self.alias_state(self.plan)
        env = dict(
            os.environ,
            PATH=str(self.home) + os.pathsep + os.environ["PATH"],
            FAKE_RUNTIME_HOME=str(self.home),
            FAKE_PLAN=str(self.state_dir / "demo.json"),
            FAKE_EVENTS=str(self.run_dir_of() / "events.jsonl"),
        )
        argv = ["orchestrate", *self.driver_args()]
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(spec, point, side_effect=SystemExit("crash")),
        ):
            with self.assertRaises(SystemExit):
                spec.main()

    def test_S33_a_restart_after_a_crash_before_the_bind_re_attaches_instead_of_spawning(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S33"""
        for point in ("assignment_of", "bind"):
            with self.subTest(crash_at=point):
                self.setUp()
                self.restart_re_attaches(point)

    def restart_re_attaches(self, point):
        self.crash_after_spawn(point)
        self.assertEqual([c["args"]["name"] for c in self.calls("spawn")], ["demo-t1"])
        self.assertEqual(self.ledger()["tasks"]["t1"]["runtime_id"], None)
        self.assertEqual(
            self.ledger()["agents"]["demo-t1"]["assignment_id"] is None, point == "assignment_of"
        )
        self.states({"demo-t1": "working"})
        self.run_driver()
        self.assertEqual([c["args"]["name"] for c in self.calls("spawn")], ["demo-t1"])
        resumes = self.calls("resume")
        self.assertEqual([c["args"]["name"] for c in resumes], ["demo-t1"])
        self.assertIn("Read ", resumes[0]["args"]["task"])
        task = self.ledger()["tasks"]["t1"]
        self.assertEqual(
            (task["status"], task["runtime_id"], task["assignment_id"]),
            ("ACTIVE", "term-demo-t1", resume_id("demo-t1")),
        )
        hows = [e["how"] for e in self.run_events("agent") if e["name"] == "demo-t1"]
        self.assertEqual(hows, ["attach"] if point == "assignment_of" else ["spawn", "attach"])
        self.assertTrue(self.run_events("intent")[-1].get("recovering") or point == "bind")

    def test_S33_it_spawns_only_when_no_such_agent_exists(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S33"""
        self.crash_after_spawn("assignment_of")
        self.states({"demo-t1": "exited"})
        self.run_driver()
        self.assertEqual([c["args"]["name"] for c in self.calls("spawn")], ["demo-t1", "demo-t1"])
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.ledger()["tasks"]["t1"]["assignment_id"], spawn_id("demo-t1", 2))

    def test_S33_a_run_that_finished_binding_never_spawns_a_second_agent_with_the_name(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S33"""
        self.run_driver()
        self.reset_status("IN PROGRESS", "TO-DO")
        # exit 1: the first drive left the worker idle with no report, and the restart strands that unit
        # instead of finishing over it; the point here is that it spawns no second agent under the name
        self.run_driver(expect=1)
        self.assertEqual([c["args"]["name"] for c in self.calls("spawn")], ["demo-t1"])
        self.assertEqual(self.ledger()["agents"]["demo-t1"]["assignment_id"], spawn_id("demo-t1"))

    def test_S33_a_refused_spawn_leaves_no_open_intent(self):
        """orchestrator-engine/R5: orchestrate-lean-loop/S33"""
        self.refuse(["demo-t1"])
        self.run_driver(expect=1)
        self.assertNotIn("demo-t1", self.ledger().get("agents", {}))
        self.refuse([])
        self.run_driver()
        self.assertEqual([c["args"]["name"] for c in self.calls("spawn")], ["demo-t1", "demo-t1"])
        self.assertEqual(self.calls("resume"), [])

    def test_S33_the_fresh_worker_spawn_after_a_decision_records_an_intent_first(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S33"""
        from test_decider import DeciderFixture

        fixture = type("F", (DeciderFixture, unittest.TestCase), {"runTest": lambda self: None})()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.waits([fixture.blocked(), fixture.decided("fresh worker")])
        fixture.run_driver()
        self.assertEqual(
            [c["args"]["name"] for c in fixture.calls("spawn")], ["demo-t1", "demo-t1"]
        )
        fixture.assert_intents_precede_spawns()


class AddressRecordTests(RunFixture, unittest.TestCase):
    """The driver records an address only when the runtime returned one (agent-workbench/S8)."""

    def setUp(self):
        super().setUp()
        self.set_plan(rows=("T1",))

    def runtime_returns_no_address(self):
        real = self.runtime_process

        def answer(argv, timeout=None):
            done = real(argv, timeout)
            body = json.loads(done.stdout) if done.stdout else None
            if isinstance(body, dict) and "address" in body:
                body.pop("address")
                return type(done)(argv, done.returncode, json.dumps(body))
            return done

        self.runtime_process = answer

    def test_S8_the_address_the_runtime_returned_is_recorded_on_the_agent_and_its_task(self):
        self.run_driver()
        data = self.ledger()
        self.assertEqual(data["agents"]["demo-t1"]["address"], "term-demo-t1")
        self.assertEqual(data["tasks"]["t1"]["address"], "term-demo-t1")

    def test_S8_a_runtime_that_returned_no_address_leaves_none_not_the_assignment_or_the_name(self):
        self.runtime_returns_no_address()
        self.run_driver()
        data = self.ledger()
        self.assertIsNone(data["agents"]["demo-t1"]["address"])
        self.assertIsNone(data["tasks"]["t1"]["address"])
        task = data["tasks"]["t1"]
        self.assertEqual(
            (task["status"], task["assignment_id"]),
            ("ACTIVE", spawn_id("demo-t1")),
            "the task is still bound, under a key that is not shown as an address",
        )
        self.assertNotIn(task["runtime_id"], ("", None))


if __name__ == "__main__":
    unittest.main()
