"""ensure_agent: every spawn records an intent first, and a restart re-attaches by the stable name."""

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
        argv = [
            "orchestrate",
            "run",
            str(self.plan),
            "--state-dir",
            str(self.state_dir),
            "--models-file",
            str(self.models),
            "--wait-seconds",
            "1",
            "--idle-interval",
            "0",
        ]
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
        hows = [e["how"] for e in self.events("agent") if e["name"] == "demo-t1"]
        self.assertEqual(hows, ["attach"] if point == "assignment_of" else ["spawn", "attach"])
        self.assertTrue(self.events("intent")[-1].get("recovering") or point == "bind")

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


if __name__ == "__main__":
    unittest.main()
