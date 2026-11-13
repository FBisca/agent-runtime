"""Every spawn and resume is booked as an intent keyed by the runtime's operation id before it is sent; a restart looks
the key up and never re-sends what it cannot tell was delivered; a failed gate is booked once, before any git or gh."""

import json
import unittest
import uuid

import pytest
from fake_runtime import Completed
from fixtures import report_wait, resume_id, spawn_id, wait_result
from test_envelope import RESPONSE, EnvelopeFixture
from test_gates import GateFixture
from test_pr import PR, PrFixture

pytestmark = pytest.mark.integration

ADOPTED = ("bound", "working", "blocked", "decision", "complete", "failed")
DIRTY = {"state": "OPEN", "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}


class Crash(BaseException):
    """The driver dies here: nothing after this line of the driver runs, as for a SIGKILL."""


class OperationRuntime(GateFixture):
    """The fake runtime with operation keys: the answer `call operation` gives is the test's to script, and a send
    can be made to reach the runtime and kill the driver before the answer comes back (`die`)."""

    def setUp(self):
        super().setUp()
        self.die = None  # (tool, name, delivered): the driver dies at that send
        self.sent = {}  # operation id -> the assignment the runtime gave it
        self.lookups = []
        self.operation_state = (
            "absent"  # what `call operation` answers for a key the runtime did see
        )
        self.lookup_fails = False
        self.refused = ()  # tools for which the runtime does not take an operation id
        self.refusals = []  # the keyed sends it turned away

    def runtime_process(self, argv, timeout=None):
        tool = argv[2]
        args = json.loads(argv[argv.index("--json") + 1]) if "--json" in argv else {}
        if tool == "operation":
            key = args["operation_id"]
            self.lookups.append(key)
            if self.lookup_fails:
                return Completed(argv, 1, "", "agent-runtime: no such verb: operation")
            seen = key in self.sent
            answer = {
                "operation_id": key,
                "state": self.operation_state if seen else "absent",
                "assignment_id": self.sent.get(key),
            }
            return Completed(argv, 0, json.dumps(answer), "")
        if tool in self.refused and "operation_id" in args:
            self.refusals.append((tool, args["name"]))
            return Completed(argv, 1, "", "operation_id requires AGENT_RUNTIME_DISPATCH=1")
        dying = self.die and self.die[:2] == (tool, args.get("name"))
        done = super().runtime_process(argv, timeout)
        if dying:
            delivered = self.die[2]
            self.die = None
            if delivered:
                self.sent[args["operation_id"]] = json.loads(done.stdout)["assignment_id"]
            else:  # the call never reached the runtime: take it out of the fake's record
                self.forget_last_call()
            raise Crash("the driver died during the %s of %s" % (tool, args["name"]))
        return done

    def forget_last_call(self):
        rows = (self.home / "calls.jsonl").read_text(encoding="utf-8").splitlines()
        (self.home / "calls.jsonl").write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")

    def keyed(self, *tools):
        """Every spawn and resume the runtime saw (of the tools named, all by default): (call, operation id)."""
        return [
            (c, c["args"].get("operation_id"))
            for c in self.calls()
            if c["tool"] in (tools or ("spawn", "resume"))
        ]

    def assert_keyed(self, calls):
        """Each call carries its own fresh UUID, and the driver had booked an intent for it before the call."""
        keys = [key for _, key in calls]
        self.assertTrue(calls, "the flow made no spawn or resume")
        for call, key in calls:
            self.assertIsNotNone(
                key, "%s of %s went out unkeyed" % (call["tool"], call["args"]["name"])
            )
            self.assertEqual(str(uuid.UUID(key, version=4)), key)
            booked = [
                json.loads(line).get("operation_id")
                for line in call["events"]
                if json.loads(line)["kind"] == "intent-booked"
            ]
            self.assertIn(key, booked, "no intent event for %s before its send" % key)
        self.assertEqual(len(set(keys)), len(keys), "an operation id was used twice")

    def restart(self):
        """The driver starts again over the same ledger, without asserting how it ends."""
        from fixtures import load_script

        argv = [
            "run",
            str(self.plan),
            "--state-dir",
            str(self.state_dir),
            "--routes-file",
            str(self.route_file),
            "--wait-seconds",
            "1",
            "--idle-interval",
            "0",
        ]
        return self.in_process(load_script(), argv)

    def crash_the_first_resume(self, delivered=True):
        """The driver dies sending the fix round's resume to demo-t1; the ledger is what it left."""
        self.die = ("resume", "demo-t1", delivered)
        self.waits([self.worker_done()])
        with self.assertRaises(Crash):
            self.run_driver()
        reached = [c for c in self.calls("resume") if c["args"]["name"] == "demo-t1"]
        self.assertEqual(
            len(reached), 1 if delivered else 0, "the fixture did not reach the resume"
        )

    def open_intents(self):
        return {
            key: row
            for key, row in (self.ledger().get("intents") or {}).items()
            if row["state"] in ("open", "held")
        }

    def requests_of(self, unit="t1"):
        return [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((self.run_dir / unit).glob("request*.json"))
        ]


class IntentBooking(OperationRuntime, unittest.TestCase):
    def test_S14_every_spawn_and_resume_is_booked_with_a_fresh_uuid_before_the_call(self):
        """orchestrator-engine/R103: orchestrator-audit-fixes/S14 — spawn, attach and fix-round resume."""
        self.waits([self.worker_done(), self.worker_done("t1r1", tests=True), self.reviewer_done()])
        self.run_driver()
        calls = self.keyed()
        self.assertEqual(
            [(c["tool"], c["args"]["name"]) for c, _ in calls],
            [("spawn", "demo-t1"), ("resume", "demo-t1"), ("spawn", "demo-t1-review")],
        )
        self.assert_keyed(calls)
        intents = self.ledger()["intents"]
        by_path = {row["path"]: row for row in intents.values()}
        for path in ("t1/t1/spawn", "t1/t1r1/resume", "t1/t1/spawn"):
            self.assertIn(path, by_path, "the readable unit/attempt/operation path is metadata")
        for key, row in intents.items():
            self.assertEqual(row["state"], "bound")
            self.assertTrue(row["assignment_id"])
            self.assertIn(row["name"], ("demo-t1", "demo-t1-review"))

    def test_S14_a_runtime_that_does_not_take_operation_ids_gets_no_unkeyed_resume_and_the_lead_is_asked(
        self,
    ):
        """orchestrator-engine/R103: orchestrator-audit-fixes/S14"""
        self.refused = ("resume",)
        self.waits([self.worker_done(), self.skipped()])
        self.run_driver()
        self.assertTrue(self.refusals, "the driver never sent the keyed resume")
        self.assertEqual(self.keyed("resume"), [], "a resume went out without its operation id")
        self.assertEqual(self.request()["event"], "intent")
        self.assertIn("operation", self.request()["reason"])
        self.assertEqual(self.open_intents(), {})

    def test_S14_a_runtime_that_does_not_take_operation_ids_gets_no_unkeyed_spawn_and_the_lead_is_woken(
        self,
    ):
        """orchestrator-engine/R103: orchestrator-audit-fixes/S14"""
        self.refused = ("spawn",)
        self.waits([])
        self.run_driver(expect=1)
        self.assertTrue(self.refusals, "the driver never sent the keyed spawn")
        self.assertEqual(self.keyed("spawn"), [], "a spawn went out without its operation id")
        self.assertTrue(self.wake_lines("not-dispatched"), "the Lead was not told")


class ChaseAndAnswersKeys(OperationRuntime, EnvelopeFixture, unittest.TestCase):
    def test_S14_a_chase_for_missing_responses_resumes_under_its_own_key(self):
        """orchestrator-engine/R103: orchestrator-audit-fixes/S14"""
        self.waits(
            self.to_fix_round()
            + [
                self.fix_round_report(),
                self.fix_round_report(name="report.2.json", nth=2, responses=[RESPONSE]),
                self.re_review(),
            ]
        )
        self.run_driver()
        calls = self.keyed()
        self.assert_keyed(calls)
        paths = {row["path"] for row in self.ledger()["intents"].values()}
        self.assertIn("t1/t1r1/resume", paths)
        self.assertEqual(len([c for c, _ in calls if c["args"]["name"] == "demo-t1"]), 3)

    def test_S14_the_answers_resume_carries_a_key_too(self):
        """orchestrator-engine/R103: orchestrator-audit-fixes/S14"""
        from test_gates import NIT, QUESTION

        answers = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 1))
        answers["write"] = [
            [str(self.run_dir / "t1a1" / "report.json"), self.report("t1a1", responses=[RESPONSE])]
        ]
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(self.review2(items=[QUESTION, NIT])),
                answers,
            ]
        )
        self.run_driver("--idle-minutes", "0")
        calls = self.keyed()
        self.assert_keyed(calls)
        self.assertIn("t1/t1a1/resume", {row["path"] for row in self.ledger()["intents"].values()})


class IntentRestart(OperationRuntime, unittest.TestCase):
    def test_S15_an_intent_the_runtime_never_saw_is_acted_on_again(self):
        """orchestrator-engine/R104: orchestrator-audit-fixes/S15 — absent."""
        self.crash_the_first_resume(delivered=False)
        (key,) = self.open_intents()
        self.waits([self.worker_done("t1r1", tests=True), self.reviewer_done()])
        self.restart()
        self.assertEqual(self.lookups, [key])
        resumes = self.keyed("resume")
        self.assertEqual(len(resumes), 1, "the restart did not send the resume the crash lost")
        self.assertNotEqual(
            resumes[0][1], key, "an intent that never reached the runtime is booked anew"
        )
        self.assertEqual(self.requests_of(), [])
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S15_an_intent_the_runtime_holds_as_working_is_adopted_and_not_sent_again(self):
        """orchestrator-engine/R104: orchestrator-audit-fixes/S15 — bound, working, blocked, decision, complete, failed."""
        self.crash_the_first_resume()
        (key,) = self.open_intents()
        self.operation_state = "working"
        self.waits([self.worker_done("t1r1", tests=True), self.reviewer_done()])
        self.restart()
        self.assertEqual(self.lookups, [key])
        self.assertEqual(
            [c["args"]["name"] for c, _ in self.keyed("resume")],
            ["demo-t1"],
            "the restart sent the resume again",
        )
        self.assertEqual(self.ledger()["intents"][key]["state"], "adopted")
        self.assertEqual(self.ledger()["tasks"]["t1r1"]["assignment_id"], resume_id("demo-t1"))
        self.assertEqual(self.requests_of(), [])
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S15_every_state_the_runtime_has_bound_is_adopted(self):
        """orchestrator-engine/R104: orchestrator-audit-fixes/S15"""
        for state in ADOPTED:
            with self.subTest(state=state):
                self.setUp()
                self.crash_the_first_resume()
                (key,) = self.open_intents()
                self.operation_state = state
                self.waits([])
                self.restart()
                self.assertEqual(len(self.keyed("resume")), 1, "%s: sent again" % state)
                self.assertEqual(self.ledger()["intents"][key]["state"], "adopted")
                self.assertEqual(self.requests_of(), [], "%s: a request was raised" % state)

    def test_S15_a_pending_or_unknown_intent_asks_the_lead_and_never_resends(self):
        """orchestrator-engine/R104: orchestrator-audit-fixes/S15 — the Lead retries, and only then does a resume go out."""
        for state, lookup_fails in (("pending", False), ("unknown", False), ("pending", True)):
            with self.subTest(state=state, lookup_fails=lookup_fails):
                self.setUp()
                self.crash_the_first_resume()
                (key,) = self.open_intents()
                self.operation_state, self.lookup_fails = state, lookup_fails
                self.waits([self.skipped()])
                self.restart()
                self.assertEqual(len(self.keyed("resume")), 1, "the restart sent demo-t1 again")
                (request,) = self.requests_of()
                self.assertEqual(request["event"], "intent")
                self.assertIn("may or may not have reached demo-t1", request["reason"])
                self.assertIn("retry or skip", request["reason"])
                self.assertEqual(request["menu"], ["retry", "skip"])
                self.assertTrue(self.wake_lines("decision"), "the Lead was not woken")
                self.assertEqual(self.ledger()["intents"][key]["state"], "released")

    def test_S15_the_lead_deciding_retry_sends_once_under_a_new_key(self):
        """orchestrator-engine/R104: orchestrator-audit-fixes/S15"""
        self.crash_the_first_resume()
        (first,) = self.open_intents()
        self.operation_state = "pending"
        again = self.worker_done("t1r1", tests=True)  # the retry is the pane's second resume
        again["assignments"][0]["assignment_id"] = resume_id("demo-t1", 2)
        again["messages"][0]["assignment_id"] = resume_id("demo-t1", 2)
        self.waits([self.decide_step("t1", "retry"), again, self.reviewer_done()])
        self.restart()
        resumes = self.keyed("resume")
        self.assertEqual(len(resumes), 2, "the retry sent nothing, or sent more than once")
        self.assertNotEqual(resumes[1][1], first)
        self.assertEqual(self.ledger()["intents"][first]["state"], "released")
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S15_a_held_intent_is_not_looked_up_again_each_round(self):
        """orchestrator-engine/R104: orchestrator-audit-fixes/S15"""
        self.crash_the_first_resume()
        self.operation_state = "pending"
        self.waits([wait_result()] * 4 + [self.skipped()])
        self.restart()
        self.assertEqual(len(self.lookups), 1)


class FailGateBooking(OperationRuntime, PrFixture, unittest.TestCase):
    def gh_process(self, argv, timeout=None, cwd=None):
        if argv[:2] == ["pr", "ready"] and "--undo" in argv and self.at_undo:
            self.at_undo(self.ledger())
        return super().gh_process(argv, timeout, cwd)

    def setUp(self):
        super().setUp()
        self.at_undo = None

    def rebased(self):
        rebased = report_wait("demo-t1", "complete", id=spawn_id("demo-t1", 2))
        rebased["write"] = [[str(self.run_dir / "t1r1" / "report.json"), self.report("t1r1")]]
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        return [rebased, again]

    def test_S16_the_round_the_next_phase_and_the_draft_are_booked_before_any_gh_call(self):
        """orchestrator-engine/R105: orchestrator-audit-fixes/S16"""
        seen = []
        self.at_undo = seen.append
        self.prs([DIRTY, "MERGED"])
        self.waits([self.worker_done(tests=True), self.reviewer_done(), *self.rebased()])
        self.run_driver()
        self.assertTrue(seen, "the pull request was never put back to draft")
        ledger = seen[0]
        self.assertEqual(ledger["rounds"], {"t1": 1})
        task = ledger["tasks"]["t1"]
        self.assertEqual(task["gate"], "fix-pending")
        self.assertEqual(task["fix"]["draft"], PR)
        self.assertEqual(task["fix"]["source"], "conflict")
        self.assertEqual(task["report"]["review_rounds"], 1)

    def test_S16_a_crash_between_the_booking_and_the_draft_counts_the_round_once(self):
        """orchestrator-engine/R105: orchestrator-audit-fixes/S16"""

        def die(_ledger):
            raise Crash("the driver died at the first gh call after the gate failed")

        self.at_undo = die
        self.prs([DIRTY, "MERGED"])
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        with self.assertRaises(Crash):
            self.run_driver()
        self.assertEqual(self.ledger()["rounds"], {"t1": 1})
        self.assertEqual(self.task()["gate"], "fix-pending")
        self.at_undo = None
        self.waits([])
        self.restart()
        self.assertEqual(self.ledger()["rounds"], {"t1": 1}, "the restart counted the round twice")
        self.assertEqual(len(self.run_events("round")), 1)
        self.assertEqual(
            self.task()["status"], "COMPLETE"
        )  # merged by hand meanwhile: the fake's next PR state

    def test_S16_a_gate_past_the_cap_is_booked_with_its_request_before_any_gh_call(self):
        """orchestrator-engine/R105: orchestrator-audit-fixes/S16"""
        seen = []
        self.at_undo = seen.append
        self.prs([DIRTY])
        self.waits([self.worker_done(tests=True), self.reviewer_done(), self.skipped()])
        self.run_driver("--max-fix-rounds", "0")
        ledger = seen[0]
        self.assertEqual(ledger["rounds"], {"t1": 1})
        task = ledger["tasks"]["t1"]
        self.assertEqual(task["gate"], "requested")
        self.assertEqual(task["fix"]["draft"], PR)


if __name__ == "__main__":
    unittest.main()
