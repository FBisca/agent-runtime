"""The live driver and a plan that changes under it: an edit or an unloadable file drains the run on its last approved
snapshot, an amendment is adopted on the next read, and each in-flight worker whose contract changed is resumed on its
next packet (plan-amend/S5, S12, S13, S14)."""

import json
import unittest
from unittest import mock

import pytest
import yaml
from fixtures import DRIVER, ROWS, assignment, load_script, report_wait, resume_id, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration

REASON = "widen T1 to its test file and add T5"


class DrainFixture(GateFixture):
    """A plan of T1 and T3 with both workers running, and the Lead's edits as wait-step writes."""

    rows = ("T1", "T3")

    def setUp(self):
        super().setUp()
        self.set_plan(self.rows)
        self.approved = self.plan.read_text(encoding="utf-8")
        # The Lead runs `plan amend`: a worker's own session must not turn it into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def edit(self, change):
        """The plan as the Lead's hand edit leaves it: `change(data)` applied, the stale approval block kept."""
        data = yaml.safe_load(self.approved)
        change(data)
        return yaml.safe_dump(data, sort_keys=False)

    def widen_t1_and_add_t5(self):
        def change(data):
            data["tickets"][0]["paths"] = ["src/a/", "tests/test_a.py"]
            data["tickets"].append(dict(id="T5", **ROWS["T5"]))

        return self.edit(change)

    def writes(self, text):
        return [[str(self.plan), text]]

    def amend_argv(self, reason=REASON):
        return [
            *DRIVER,
            "plan",
            "amend",
            str(self.plan),
            "--reason",
            reason,
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        ]

    def working(self, *names):
        return [assignment(name) for name in names]

    def timeout(self, *names):
        return wait_result(self.working(*names), reason="timeout")

    def kinds(self):
        return [e["kind"] for e in self.run_events()]

    def at_call(self, tool, name):
        """The run's event kinds (and drain states) when the runtime was asked `tool` for `name`, for each ask."""
        return [
            [(e["kind"], e.get("state")) for e in map(json.loads, call["events"])]
            for call in self.calls(tool)
            if call["args"]["name"] == name
        ]

    def drain_had_ended(self, seen):
        return ("drain", "ended") in seen


class DrainTests(DrainFixture, unittest.TestCase):
    def test_S12_an_edited_plan_drains_instead_of_crashing(self):
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=self.writes(self.widen_t1_and_add_t5())),
                self.timeout(*both),
                self.timeout(*both),
                wait_result(self.working(*both), write=self.writes(self.approved)),
                self.timeout(*both),
            ]
        )
        self.run_driver()
        (drained,) = [e for e in self.run_events("drain") if e.get("state") != "ended"]
        self.assertEqual(drained["cause"], "edited")
        self.assertIn("demo.plan.yaml", drained["error"])
        (wake,) = self.wake_lines("drain")
        self.assertIn("plan amend", wake)
        self.assertEqual(self.run_events("crash"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t3"])

    def test_S12_an_unloadable_plan_drains_and_names_the_load_error(self):
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=self.writes("tickets: [unclosed\n")),
                self.timeout(*both),
                wait_result(self.working(*both), write=self.writes(self.approved)),
                self.timeout(*both),
            ]
        )
        self.run_driver()
        (drained,) = [e for e in self.run_events("drain") if e.get("state") != "ended"]
        self.assertEqual(drained["cause"], "unloadable")
        self.assertIn("invalid YAML", drained["error"])
        (wake,) = self.wake_lines("drain")
        self.assertIn("invalid YAML", wake)
        self.assertIn("plan amend", wake)

    def test_S12_a_plan_that_is_not_a_mapping_drains_too(self):
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=self.writes("- not\n- a plan\n")),
                wait_result(self.working(*both), write=self.writes(self.approved)),
                self.timeout(*both),
            ]
        )
        self.run_driver()
        self.assertEqual(len([e for e in self.run_events("drain") if e.get("state") != "ended"]), 1)
        self.assertEqual(self.run_events("crash"), [])

    def test_S12_one_drain_and_one_wake_however_long_the_edit_stays(self):
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=self.writes(self.widen_t1_and_add_t5())),
                *[self.timeout(*both) for _ in range(5)],
                wait_result(self.working(*both), write=self.writes(self.approved)),
                self.timeout(*both),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.run_events("drain")), 2, "the drain, and its end")
        self.assertEqual(len(self.wake_lines("drain")), 1)

    def test_S12_an_edit_to_the_brief_alone_does_not_drain(self):
        brief = self.plan.with_name("demo.brief.md")
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=[[str(brief), "# a brief\n"]]),
                wait_result(
                    self.working(*both),
                    write=self.writes("# a comment\n" + self.approved),
                ),
                self.timeout(*both),
            ]
        )
        self.run_driver()
        self.assertEqual(self.run_events("drain"), [])
        self.assertEqual(self.wake_lines("drain"), [])

    def test_S12_a_drain_starts_no_first_dispatch_while_reports_and_merges_go_on(self):
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(self.widen_t1_and_add_t5())),
                self.worker_done(tests=True),
                self.reviewer_done(),
                wait_result(write=self.writes(self.approved)),
                self.timeout("demo-t3"),
            ]
        )
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.task("t1")["status"], "COMPLETE", "the merge bookkeeping went on")
        (t3,) = self.at_call("spawn", "demo-t3")
        self.assertTrue(self.drain_had_ended(t3), "demo-t3 was started only once the drain ended")

    def test_S12_a_drain_starts_no_fix_round_until_it_ends(self):
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(self.widen_t1_and_add_t5())),
                self.worker_done(),
                wait_result(write=self.writes(self.approved)),
            ]
        )
        self.run_driver("--max-workers", "1")
        resumes = self.at_call("resume", "demo-t1")
        self.assertTrue(resumes, "the fix round was handed over once the drain ended")
        self.assertTrue(all(self.drain_had_ended(seen) for seen in resumes))

    def test_S12_a_drain_starts_no_redispatch_of_a_lost_worker(self):
        self.run_driver("--max-workers", "1")
        self.gone("demo-t1", "exited")
        self.waits(
            [
                wait_result(write=self.writes(self.widen_t1_and_add_t5())),
                wait_result(write=self.writes(self.approved)),
            ]
        )
        self.run_driver("--max-workers", "1")
        spawns = self.at_call("spawn", "demo-t1")
        self.assertEqual(len(spawns), 2)
        self.assertTrue(self.drain_had_ended(spawns[1]))

    def test_S12_a_drain_does_not_finish_the_run(self):
        self.waits(
            [
                wait_result(write=self.writes("tickets: [unclosed\n")),
                *[wait_result() for _ in range(3)],
                wait_result(write=self.writes(self.approved)),
            ]
        )
        self.run_driver()
        kinds = self.kinds()
        self.assertLess(
            [(e["kind"], e.get("state")) for e in self.run_events()].index(("drain", "ended")),
            kinds.index("finish"),
        )

    def test_S13_the_drain_ends_when_the_file_is_back_to_the_approved_bytes(self):
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(self.working(*both), write=self.writes(self.widen_t1_and_add_t5())),
                wait_result(self.working(*both), write=self.writes(self.approved)),
                self.timeout(*both),
            ]
        )
        self.run_driver()
        states = [e.get("state") for e in self.run_events("drain")]
        self.assertEqual(states[-1], "ended")
        self.assertEqual(self.run_events("amended"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t3"])

    def test_S13_an_amendment_ends_the_drain_and_the_driver_gates_against_the_new_contract(self):
        """T1 now satisfies S2 too: the report that covers only S1 fails the gate on the new contract."""
        self.set_plan(("T1",))
        self.approved = self.plan.read_text(encoding="utf-8")

        def satisfy_s2(data):
            # S2 drops the fixture's `human` evidence, so a test node must prove it
            data["tickets"][0].update(satisfies=["S1", "S2"])
            data["scenarios"][1].pop("evidence")

        first = self.edit(satisfy_s2)
        resumed = report_wait("demo-t1", "complete", id=resume_id("demo-t1"))
        resumed["write"] = self.worker_done(tests=True)["write"]
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(first)),
                wait_result(self.working("demo-t1"), run=[self.amend_argv()]),
                resumed,
            ]
        )
        self.run_driver("--max-workers", "1")
        (amended,) = self.run_events("amended")
        approval = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]
        self.assertEqual(amended["digest"], approval["digest"])
        self.assertTrue(
            [e for e in self.run_events("drain") if e.get("state") != "ended"],
            "the edit drained the run first",
        )
        self.assertEqual(
            [e for e in self.run_events("drain") if e.get("state") == "ended"], [], "no revert"
        )
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertIn("S2", json.dumps(findings["items"]))


class AmendTests(DrainFixture, unittest.TestCase):
    def drive_through_an_amendment(self, text=None):
        self.digest = yaml.safe_load(self.approved)["approval"]["digest"]
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(
                    self.working(*both), write=self.writes(text or self.widen_t1_and_add_t5())
                ),
                self.timeout(*both),
                wait_result(self.working(*both), run=[self.amend_argv()]),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
            ]
        )
        return self.run_driver()

    def test_S5_one_drive_drains_on_the_edit_then_adopts_the_amendment(self):
        self.drive_through_an_amendment()
        self.assertIn("drain", self.kinds())
        (amended,) = self.run_events("amended")
        approval = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]
        self.assertEqual(
            (amended["prev_digest"], amended["digest"]), (self.digest, approval["digest"])
        )
        (wake,) = self.wake_lines("drain")
        self.assertIn("plan amend", wake)

    def test_S5_the_new_ticket_is_dispatched_only_after_the_amendment_is_adopted(self):
        self.drive_through_an_amendment()
        self.assertIn("demo-t5", self.spawned())
        events = self.run_events()
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

    def test_S5_control_an_unedited_plan_dispatches_its_tickets_without_a_drain(self):
        self.waits([self.timeout("demo-t1", "demo-t3")])
        self.run_driver()
        self.assertEqual(sorted(self.spawned()), ["demo-t1", "demo-t3"])
        self.assertEqual(self.run_events("drain"), [])
        self.assertEqual(self.run_events("amended"), [])

    def test_S14_a_changed_satisfies_or_delivers_resumes_the_worker_on_the_next_packet(self):
        def satisfies(data):
            data["tickets"][0]["satisfies"] = ["S1", "S2"]

        def delivers(data):
            data["tickets"][0]["delivers"] = "first, and then some"

        for name, change, needle in (
            ("satisfies", satisfies, "S2"),
            ("delivers", delivers, "first, and then some"),
        ):
            with self.subTest(changed=name):
                self.setUp()
                self.drive_through_an_amendment(self.edit(change))
                packet = (self.run_dir / "t1" / "task.v2.md").read_text(encoding="utf-8")
                self.assertIn(needle, packet)
                resumes = [r for r in self.calls("resume") if r["args"]["name"] == "demo-t1"]
                self.assertEqual(len(resumes), 1)
                self.assertIn("task.v2.md", resumes[0]["args"]["task"])
                self.assertEqual([r for r in self.calls("resume") if "t3" in r["args"]["name"]], [])

    def test_S14_a_second_amendment_writes_the_packet_after_the_last(self):
        everyone = ("demo-t1", "demo-t3", "demo-t5")
        wider = yaml.safe_load(self.widen_t1_and_add_t5())
        wider["tickets"][0]["paths"] = ["src/a/", "tests/test_a.py", "tests/test_b.py"]
        self.waits(
            [
                wait_result(
                    self.working("demo-t1", "demo-t3"),
                    write=self.writes(self.widen_t1_and_add_t5()),
                ),
                wait_result(self.working("demo-t1", "demo-t3"), run=[self.amend_argv()]),
                wait_result(
                    self.working(*everyone),
                    write=self.writes(self.reapproved(wider)),
                ),
                wait_result(self.working(*everyone), run=[self.amend_argv("one more path")]),
                wait_result(self.working(*everyone), reason="timeout"),
            ]
        )
        self.run_driver()
        for version in ("task.v2.md", "task.v3.md"):
            self.assertTrue((self.run_dir / "t1" / version).is_file(), version)
        self.assertIn(
            "tests/test_b.py", (self.run_dir / "t1" / "task.v3.md").read_text(encoding="utf-8")
        )

    def reapproved(self, data):
        """The amended plan's file with `data`'s tickets, its new approval block and history kept: the Lead's second
        hand edit."""
        current = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        current["tickets"] = data["tickets"]
        return yaml.safe_dump(current, sort_keys=False)
