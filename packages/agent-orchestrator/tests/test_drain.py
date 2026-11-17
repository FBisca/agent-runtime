"""The live driver and a plan that changes under it: an edit or an unloadable file drains the run on its last approved
snapshot, a re-approval (`plan approve --reason` under the live driver) is adopted on the next read, and each in-flight
worker whose contract changed is resumed on its next packet."""

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
        # The Lead re-approves: a worker's own session must not turn it into the worker refusal.
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

    def reapprove_argv(self, reason=REASON):
        return [
            *DRIVER,
            "plan",
            "approve",
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
    @pytest.mark.spec("Drain on an unapproved edit")
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
        self.assertIn("plan approve --base", wake)
        self.assertIn("--reason", wake)
        self.assertEqual(self.run_events("crash"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t3"])

    @pytest.mark.spec("Drain on an unapproved edit")
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
        self.assertIn("--reason", wake)

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Drain on an unapproved edit")
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

    @pytest.mark.spec("Re-approval under a live driver")
    def test_a_reapproval_ends_the_drain_and_the_driver_gates_against_the_new_contract(self):
        """T1 now satisfies S2 too: the report that covers only S1 fails the gate on the new contract."""
        self.set_plan(("T1",))
        self.approved = self.plan.read_text(encoding="utf-8")

        def satisfy_s2(data):
            # S2 joins the plan for T1 to satisfy, so a test node must prove it
            data["tickets"][0].update(satisfies=["S1", "S2"])
            data["requirements"]["added"][0]["scenarios"].append(
                {"id": "S2", "when": "c", "then": "d"}
            )

        first = self.edit(satisfy_s2)
        resumed = report_wait(
            "demo-t1", "complete", text=self.report("t1"), id=resume_id("demo-t1")
        )
        resumed["write"] = self.worker_done(tests=True)["write"]
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(first)),
                wait_result(self.working("demo-t1"), run=[self.reapprove_argv()]),
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


class ReapproveTests(DrainFixture, unittest.TestCase):
    """The Lead edits the plan under a live drive (a running ticket's paths and a new ticket) and re-approves it."""

    def drive_through_a_reapproval(self, text=None):
        self.digest = yaml.safe_load(self.approved)["approval"]["digest"]
        both = ("demo-t1", "demo-t3")
        self.waits(
            [
                wait_result(
                    self.working(*both), write=self.writes(text or self.widen_t1_and_add_t5())
                ),
                self.timeout(*both),
                wait_result(self.working(*both), run=[self.reapprove_argv()]),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
                wait_result(self.working(*both, "demo-t5"), reason="timeout"),
            ]
        )
        return self.run_driver()

    @pytest.mark.spec("Re-approval under a live driver")
    def test_one_drive_drains_on_the_edit_then_adopts_the_reapproval(self):
        self.drive_through_a_reapproval()
        self.assertIn("drain", self.kinds())
        (amended,) = self.run_events("amended")
        approval = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]
        self.assertEqual(
            (amended["prev_digest"], amended["digest"]), (self.digest, approval["digest"])
        )
        self.assertEqual(amended["reason"], REASON)
        self.assertEqual(approval["reason"], REASON)
        (wake,) = self.wake_lines("drain")
        self.assertIn("plan approve --base", wake)
        self.assertIn("--reason", wake)

    @pytest.mark.spec("Re-approval under a live driver")
    def test_the_new_ticket_is_dispatched_only_after_the_reapproval_is_adopted(self):
        self.drive_through_a_reapproval()
        self.assertIn("demo-t5", self.spawned())
        events = self.run_events()
        drained = next(i for i, e in enumerate(events) if e["kind"] == "drain")
        adopted = next(i for i, e in enumerate(events) if e["kind"] == "amended")
        t5 = next(
            i for i, e in enumerate(events) if e["kind"] == "intent" and e.get("name") == "demo-t5"
        )
        self.assertLess(drained, adopted)
        self.assertLess(adopted, t5)

    @pytest.mark.spec("A re-approval reaches running workers")
    @pytest.mark.spec("Re-approval under a live driver")
    def test_the_changed_worker_is_resumed_on_its_new_packet_and_the_unchanged_one_is_not(self):
        self.drive_through_a_reapproval()
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

    @pytest.mark.spec("Re-approval under a live driver")
    def test_control_an_unedited_plan_dispatches_its_tickets_without_a_drain(self):
        self.waits([self.timeout("demo-t1", "demo-t3")])
        self.run_driver()
        self.assertEqual(sorted(self.spawned()), ["demo-t1", "demo-t3"])
        self.assertEqual(self.run_events("drain"), [])
        self.assertEqual(self.run_events("amended"), [])

    @pytest.mark.spec("A re-approval reaches running workers")
    @pytest.mark.spec("Re-approval under a live driver")
    def test_a_changed_satisfies_or_delivers_resumes_the_worker_on_the_next_packet(self):
        def satisfies(data):
            data["tickets"][0]["satisfies"] = ["S1", "S2"]
            data["requirements"]["added"][0]["scenarios"].append(
                {"id": "S2", "when": "c", "then": "d"}
            )

        def delivers(data):
            data["tickets"][0]["delivers"] = "first, and then some"

        for name, change, needle in (
            ("satisfies", satisfies, "S2"),
            ("delivers", delivers, "first, and then some"),
        ):
            with self.subTest(changed=name):
                self.setUp()
                self.drive_through_a_reapproval(self.edit(change))
                packet = (self.run_dir / "t1" / "task.v2.md").read_text(encoding="utf-8")
                self.assertIn(needle, packet)
                resumes = [r for r in self.calls("resume") if r["args"]["name"] == "demo-t1"]
                self.assertEqual(len(resumes), 1)
                self.assertIn("task.v2.md", resumes[0]["args"]["task"])
                self.assertEqual([r for r in self.calls("resume") if "t3" in r["args"]["name"]], [])

    @pytest.mark.spec("A re-approval reaches running workers")
    @pytest.mark.spec("Re-approval under a live driver")
    def test_a_second_reapproval_writes_the_packet_after_the_last(self):
        everyone = ("demo-t1", "demo-t3", "demo-t5")
        wider = yaml.safe_load(self.widen_t1_and_add_t5())
        wider["tickets"][0]["paths"] = ["src/a/", "tests/test_a.py", "tests/test_b.py"]
        self.waits(
            [
                wait_result(
                    self.working("demo-t1", "demo-t3"),
                    write=self.writes(self.widen_t1_and_add_t5()),
                ),
                wait_result(self.working("demo-t1", "demo-t3"), run=[self.reapprove_argv()]),
                wait_result(
                    self.working(*everyone),
                    write=self.writes(self.reapproved(wider)),
                ),
                wait_result(self.working(*everyone), run=[self.reapprove_argv("one more path")]),
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
        """The re-approved plan's file with `data`'s tickets and its new approval block kept: the Lead's second hand
        edit."""
        current = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        current["tickets"] = data["tickets"]
        return yaml.safe_dump(current, sort_keys=False)


class RecoveredSpan(DrainFixture, unittest.TestCase):
    """A plan that lost its `approval` span but is the plan the drive holds: the drive recovers the approval it holds,
    from its own block or from the ledger, and keeps running instead of draining (parked-drive-release/S4)."""

    rows = ("T1",)

    def without_approval(self):
        """The plan as a hand edit that dropped the `approval` span leaves it: the contract untouched."""
        data = yaml.safe_load(self.approved)
        data.pop("approval", None)
        return yaml.safe_dump(data, sort_keys=False)

    @pytest.mark.spec("A drive parks on a plan it cannot use")
    def test_S4_a_plan_that_lost_its_approval_span_is_recovered_and_the_drive_keeps_running(self):
        approval = yaml.safe_load(self.approved)["approval"]
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(self.without_approval())),
                self.timeout("demo-t1"),
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.timeout("demo-t1"),
            ]
        )
        self.run_driver("--max-workers", "1")
        self.assertEqual(
            self.run_events("drain"),
            [],
            "the drive drained on a plan that lost only its approval span",
        )
        self.assertEqual(self.wake_lines("drain"), [])
        (recovered,) = self.run_events("approval-recovered")
        self.assertEqual(
            (recovered["base"], recovered["digest"]), (approval["base"], approval["digest"])
        )
        self.assertEqual(
            self.task("t1")["status"],
            "COMPLETE",
            "the plan the drive read was not the approved one",
        )
        self.assertEqual(
            self.ledger()["approval"],
            {"base": approval["base"], "digest": approval["digest"]},
        )

    @pytest.mark.spec("A drive parks on a plan it cannot use")
    def test_S4_a_restarted_drive_recovers_the_approval_its_ledger_holds(self):
        approval = yaml.safe_load(self.approved)["approval"]
        # the drive held the plan, so its ledger keeps the approval the file is about to lose
        self.waits([self.worker_done(tests=True), self.reviewer_done(), self.timeout("demo-t1")])
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.task("t1")["status"], "COMPLETE")
        self.assertEqual(
            self.ledger()["approval"],
            {"base": approval["base"], "digest": approval["digest"]},
            "the ledger does not hold the approval the drive held",
        )
        self.plan.write_text(self.without_approval(), encoding="utf-8")
        # a new drive reads the same plan: the ledger restores the approval and the run finishes, it does not refuse
        self.waits([wait_result()])
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.run_events("drain"), [])
        self.assertEqual(self.run_events("crash"), [])
        self.assertEqual(
            [e["kind"] for e in self.run_events("finish")],
            ["finish", "finish"],
            "the run that started on the span-less plan never finished",
        )
        self.assertTrue(
            self.run_events("approval-recovered"),
            "the restarted drive never said it restored the span",
        )


class ParkedDrive(DrainFixture, unittest.TestCase):
    """A drive that cannot use its plan: it records the park, shows it, wakes the Lead with a way out that works, and
    asks again while nothing is in flight without giving up the plan (parked-drive-release/S2, S3)."""

    rows = ("T1", "T2")  # T2 waits for T1's merge, so a park that starts no worker is visible

    def setUp(self):
        super().setUp()
        (self.root / "clock").write_text("0", encoding="utf-8")

    def clock(self):
        return int((self.root / "clock").read_text(encoding="utf-8") or 0)

    def tick(self, seconds, *agents):
        """A wait step that moves the drive's clock, listing the agents the runtime still holds open: the park's next
        ask falls due, and what the runtime lists is what the drive takes for in-flight work."""
        return wait_result(list(agents), write=[[str(self.root / "clock"), str(seconds)]])

    def widened_t1(self):
        """An edit that leaves every ticket and path alone but changes the plan's bytes."""

        def change(data):
            data["tickets"][0]["delivers"] = "first, and wider"

        return self.edit(change)

    def snapshot(self):
        """A wait step that copies the ledger and attention.md aside: the state of the drive while it is parked."""
        out = self.root / "parked"
        return wait_result(
            self.working("demo-t1"),
            run=[
                [
                    "sh",
                    "-c",
                    "mkdir -p '%s' && cp '%s' '%s/ledger.json'"
                    % (out, self.state_dir / "demo.json", out),
                ],
                ["sh", "-c", "cp '%s' '%s/attention.md'" % (self.run_dir / "attention.md", out)],
            ],
        )

    def parked(self):
        return json.loads((self.root / "parked" / "ledger.json").read_text(encoding="utf-8"))[
            "park"
        ]

    def parked_attention(self):
        return (self.root / "parked" / "attention.md").read_text(encoding="utf-8")

    def approval(self, text=None):
        return yaml.safe_load(text or self.approved)["approval"]

    def approve_argv(self, base):
        return [
            "plan",
            "approve",
            str(self.plan),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
            "--base",
            base,
        ]

    @pytest.mark.spec("A drive parks on a plan it cannot use")
    def test_S2_a_parked_drive_records_the_park_and_names_a_way_out_that_works(self):
        edit = self.widened_t1()
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(edit)),
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.snapshot(),
                # the way out it names works: the approved bytes come back and the park ends
                wait_result(self.working("demo-t1"), write=self.writes(self.approved)),
                self.timeout("demo-t2"),
            ]
        )
        self.run_driver("--max-workers", "2")
        park = self.parked()
        self.assertEqual(park["cause"], "edited")
        self.assertIn(self.plan.name, park["error"])
        self.assertEqual(park["digest"], self.approval()["digest"])
        self.assertEqual(
            park["base"], self.approval()["base"], "the park does not name its own base"
        )
        self.assertTrue(park["since"], "the park does not say since when")
        attention = self.parked_attention()
        self.assertIn("Parked", attention)
        self.assertIn("plan approve --base", attention)
        self.assertIn(park["base"], attention)
        (wake,) = self.wake_lines("drain")
        self.assertIn("plan approve --base", wake)
        self.assertIn(park["base"], wake)
        self.assertIn("--reason", wake)
        # it started no new work while it was parked: T2 waited for the park to end, not for a slot
        (t2,) = self.at_call("spawn", "demo-t2")
        self.assertTrue(self.drain_had_ended(t2), "demo-t2 was started while the drive was parked")
        # the ledger holds the approval the drive gates on, and stops naming a park that has ended
        self.assertEqual(
            self.ledger()["approval"],
            {"base": self.approval()["base"], "digest": self.approval()["digest"]},
        )
        self.assertIsNone(self.ledger().get("park"), "the ledger still names a park that ended")
        # the way out works on the edit the drive could not use, at the approval's own base
        self.plan.write_text(edit, encoding="utf-8")
        approved = self.cli(*self.approve_argv(park["base"]))
        self.assertEqual(approved.returncode, 0, approved.stderr)
        reapproved = self.approval(self.plan.read_text(encoding="utf-8"))
        self.assertEqual(reapproved["base"], park["base"], "the way out left the approval's base")

    @pytest.mark.spec("A drive parks on a plan it cannot use")
    def test_S3_a_parked_drive_asks_again_after_fifteen_minutes_and_then_every_thirty(self):
        # a person's ticket: no worker is in flight, so only the park is left
        self.set_plan(("T4",))
        self.approved = self.plan.read_text(encoding="utf-8")
        self.waits(
            [
                wait_result(write=self.writes("tickets: [unclosed\n")),
                wait_result(),  # a quiet tick: nothing is due yet
                self.tick(16 * 60),  # sixteen minutes in: the first re-ask falls due
                wait_result(),
                self.tick(16 * 60 + 31 * 60),  # thirty-one since the last ask: the second falls due
                wait_result(),
                self.tick(16 * 60 + 32 * 60),  # a minute later: nothing is due
                wait_result(),
                # the plan comes back: the way out works and the park ends
                wait_result(write=self.writes(self.approved)),
                wait_result(),
            ]
        )
        spec = load_script()
        with mock.patch.object(spec.Driver, "clock", staticmethod(self.clock)):
            self.run_driver()
        wakes = self.wake_lines("drain")
        self.assertEqual(
            len(wakes), 3, "the park did not ask again after 15 and 30 minutes: %s" % wakes
        )
        for wake in wakes[1:]:
            self.assertIn("still parked", wake)
            self.assertIn("plan approve --base", wake)
        self.assertEqual(self.spawned(), [], "a worker started while the drive was parked")
        self.assertTrue(self.plan.exists(), "the drive gave up the plan")
        self.assertEqual([e["kind"] for e in self.run_events("finish", "crash")], ["finish"])

    @pytest.mark.spec("A drive parks on a plan it cannot use")
    def test_S3_a_park_asks_again_while_its_own_parked_unit_lists_open(self):
        """A parked unit's assignment stays open in the runtime but is not work: it must not silence the park."""
        self.set_plan(("T1",))
        self.approved = self.plan.read_text(encoding="utf-8")
        self.waits(
            [
                report_wait("demo-t1", "blocked", "cannot reach the database"),
                self.decide_step("t1", "mark blocked", "T1 needs its test file"),
                # the Lead's edit drains the drive while the parked unit's assignment stays open
                wait_result(self.working("demo-t1"), write=self.writes(self.widened_t1())),
                self.tick(16 * 60, *self.working("demo-t1")),
                wait_result(self.working("demo-t1"), write=self.writes(self.approved)),
                wait_result(),
            ]
        )
        spec = load_script()
        with mock.patch.object(spec.Driver, "clock", staticmethod(self.clock)):
            self.run_driver()
        self.assertEqual(self.task("t1")["gate"], "blocked")
        wakes = self.wake_lines("drain")
        self.assertEqual(
            len(wakes),
            2,
            "the park did not ask again while its own parked unit listed open: %s" % wakes,
        )
        self.assertIn("still parked", wakes[1])
        self.assertIn("plan approve --base", wakes[1])
        self.assertEqual(self.spawned(), ["demo-t1"], "a worker started while the drive was parked")
        self.assertEqual(
            [e.get("state") for e in self.run_events("drain")],
            ["draining", "ended"],
            "the park ended without the plan ever being given up",
        )
