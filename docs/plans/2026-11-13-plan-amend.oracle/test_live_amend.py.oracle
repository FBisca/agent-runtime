"""A plan edited while its driver runs drains instead of crashing, and `plan amend` lands the edit in the same drive:
the new ticket is dispatched, and the in-flight worker whose Paths changed is resumed on its new packet while the
unchanged one is left alone (plan-amend/S5)."""

import json
import unittest
from unittest import mock

import pytest
import yaml
from fixtures import DRIVER, ROWS, assignment, load_script, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration

REASON = "widen T1 to its test file and add T5"


class LiveAmend(GateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.set_plan(("T1", "T3"))
        # The Lead runs `plan amend`: a worker's own session must not turn it into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def edited(self):
        """The Lead's edit: T1 gains a path and T5 joins; T3 is untouched. The stale approval block stays, as a hand
        edit leaves it."""
        data = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        data["tickets"][0]["paths"] = ["src/a/", "tests/test_a.py"]
        data["tickets"].append(dict(id="T5", **ROWS["T5"]))
        return yaml.safe_dump(data, sort_keys=False)

    def amend_argv(self):
        return [
            *DRIVER,
            "plan",
            "amend",
            str(self.plan),
            "--reason",
            REASON,
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        ]

    def working(self, *names):
        return [assignment(name) for name in names]

    def logged(self, *kinds):
        """The run's events.jsonl, optionally only the given kinds. Read here, not through the fixture: its reader
        was renamed (#235), and the oracle must collect on both sides of that rename."""
        path = self.run_dir_of() / "events.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return [r for r in rows if not kinds or r["kind"] in kinds]

    def drive_through_an_amendment(self):
        self.digest = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]["digest"]
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=[[str(self.plan), self.edited()]]),
                wait_result(self.working(*both), reason="timeout"),
                wait_result(self.working(*both), run=[self.amend_argv()]),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
            ]
        )
        return self.run_driver()

    def test_S5_one_drive_drains_on_the_edit_then_adopts_the_amendment(self):
        self.drive_through_an_amendment()
        kinds = [e["kind"] for e in self.logged()]
        self.assertIn("drain", kinds)
        (amended,) = self.logged("amended")
        approval = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]
        self.assertEqual(
            (amended["prev_digest"], amended["digest"]), (self.digest, approval["digest"])
        )
        wakes = self.wake_lines("drain")
        self.assertTrue(wakes)
        self.assertIn("plan amend", wakes[0])

    def test_S5_the_new_ticket_is_dispatched_only_after_the_amendment_is_adopted(self):
        self.drive_through_an_amendment()
        self.assertIn("demo-t5", self.spawned())
        events = self.logged()
        drained = next(i for i, e in enumerate(events) if e["kind"] == "drain")
        adopted = next(i for i, e in enumerate(events) if e["kind"] == "amended")
        t5 = next(
            i for i, e in enumerate(events) if e["kind"] == "intent" and e.get("name") == "demo-t5"
        )
        self.assertLess(drained, adopted)
        self.assertLess(adopted, t5)

    def test_S5_the_changed_worker_is_resumed_on_its_new_packet_and_the_unchanged_one_is_not(self):
        self.drive_through_an_amendment()
        packet = self.run_dir / "t1" / "task.v2.md"
        self.assertIn("tests/test_a.py", packet.read_text(encoding="utf-8"))
        resumes = [r for r in self.calls("resume") if r["args"]["name"] == "demo-t1"]
        self.assertTrue(resumes)
        self.assertIn("task.v2.md", resumes[-1]["args"]["task"])
        self.assertIn("contract changed", resumes[-1]["args"]["task"].lower())
        self.assertIn("tests/test_a.py", self.task("t1")["packet"]["owns"])
        self.assertEqual(self.spawned().count("demo-t1"), 1)
        self.assertEqual([r for r in self.calls("resume") if r["args"]["name"] == "demo-t3"], [])
        self.assertFalse((self.run_dir / "t3" / "task.v2.md").exists())

    def test_S5_the_plan_records_the_amendment(self):
        self.drive_through_an_amendment()
        data = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        (entry,) = data["amendments"]
        self.assertEqual(entry["prev_digest"], self.digest)
        self.assertEqual(entry["digest"], data["approval"]["digest"])
        self.assertEqual(entry["reason"], REASON)
        self.assertEqual(sorted(entry["tickets"]), ["T1", "T5"])
        self.assertTrue(entry["at"] and entry["by"])

    def test_control_an_unedited_plan_dispatches_its_tickets_without_a_drain(self):
        self.waits([wait_result(self.working("demo-t1", "demo-t3"), reason="timeout")])
        self.run_driver()
        self.assertEqual(sorted(self.spawned()), ["demo-t1", "demo-t3"])
        self.assertEqual(self.logged("drain"), [])
