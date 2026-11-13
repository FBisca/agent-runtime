"""The Lead's channel: requests and wakes, `await`, `decide`, the wake queue, held messages, pr-ready, plan-invalid."""

import json
import sys
import unittest
from unittest import mock

import pytest
import yaml
from agent_orchestrator import contracts
from fixtures import assignment, load_script, plan_data, report_wait, resume_id, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration

PR = "https://github.com/o/r/pull/7"
MENU = ["retry", "retry one class up", "fresh worker", "mark blocked", "skip", "answer", "amend"]


class WakeFixture(GateFixture):
    def blocked(self, kind="blocked", text="cannot reach the database", name="demo-t1", id=None):
        return report_wait(name, kind, text, id=id)

    def request(self, unit="t1", nth=1):
        name = "request.json" if nth == 1 else "request.%d.json" % nth
        return json.loads((self.run_dir / unit / name).read_text(encoding="utf-8"))

    def write_open_request(self, unit="t1", task="t1"):
        """An open request on disk, as the driver leaves it, for tests of `decide` alone."""
        path = self.run_dir / unit / "request.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                contracts.make_request(
                    unit,
                    task,
                    "blocked",
                    "cannot reach the database",
                    [str(self.run_dir / unit / "task.md")],
                )
            ),
            encoding="utf-8",
        )
        return path


class RequestTests(WakeFixture, unittest.TestCase):
    def assert_request(self, event, needle, spawned=("demo-t1",), whole=True):
        """`whole`: the wake carries the reason as written; a long reason is cut in the wake, and the file has it all."""
        request = self.request()
        wake = "decision t1: %s -> %s" % (request["reason"], self.run_dir / "t1" / "request.json")
        if whole:
            self.assertEqual(self.wake_lines("decision"), [wake])
        else:
            (line,) = self.wake_lines("decision")
            self.assertTrue(line.startswith("decision t1: " + request["reason"][:100]), line)
            self.assertTrue(line.endswith(" -> %s" % (self.run_dir / "t1" / "request.json")), line)
        self.assertEqual(
            (request["event"], request["unit"], [o["action"] for o in request["menu"]]),
            (event, "t1", MENU),
        )
        self.assertIn(needle, request["reason"])
        self.assertIn(str(self.run_dir / "t1" / "task.md"), request["evidence"])
        self.assertEqual(self.spawned(), list(spawned))  # no decider is spawned

    def test_S1_a_blocked_worker_writes_a_request_and_one_wake_without_a_decider(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1"""
        self.waits([self.blocked(), self.decide_step("t1", "skip")])
        self.run_driver()
        self.assert_request("blocked", "cannot reach the database")

    def test_S1_a_worker_asking_for_a_decision_writes_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1"""
        self.waits([self.blocked("decision", "two ways to do it"), self.decide_step("t1", "skip")])
        self.run_driver()
        self.assert_request("decision", "two ways to do it")

    def test_S1_a_unit_that_fails_more_gates_than_the_cap_allows_writes_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1"""
        self.waits(
            [
                self.worker_done(),
                self.worker_done("t1r1"),
                self.worker_done("t1r2"),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        self.assert_request("gate", "failed its gates 3 times")
        self.assertEqual(len(self.calls("resume")), 2)  # two fix rounds, no third
        self.assertIn(str(self.run_dir / "t1r2" / "report.json"), self.request()["evidence"])

    def test_S1_a_worker_that_ends_without_a_valid_report_writes_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1

        R18: the worker that announced a report it never wrote is warned twice before the Lead is asked."""
        self.waits(
            [
                *self.repeated(report_wait("demo-t1", "complete")),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        self.assertEqual(
            len(self.calls("resume")), 2, "two warnings to the worker that announced the file"
        )
        self.assert_request("report", "report.json", whole=False)

    def test_S1_a_worker_whose_turn_ended_without_a_report_writes_an_idle_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1

        R62: an ended turn is not an exit; the idle watchdog asks, naming the worker and the way out."""
        self.waits(
            [wait_result([assignment("demo-t1", "working", "idle")])] * 4
            + [self.decide_step("t1", "skip")]
        )
        self.run_driver("--idle-minutes", "0")
        self.assert_request("idle", "demo-t1", whole=False)
        self.assertIn("decide", self.request()["reason"])

    def test_S1_a_reviewer_that_ends_without_a_review_writes_an_idle_request_with_no_warnings(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1

        R62: an idle reviewer is a live reviewer: no warning resume replaces its assignment, and the watchdog asks
        the Lead once, naming the reviewer and the review file."""
        self.waits(
            [
                self.worker_done(tests=True),
                *[wait_result([assignment("demo-t1-review", "working", "idle")])] * 4,
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.calls("resume"), [], "no warning resume")
        self.assert_request(
            "idle", "review.json", spawned=("demo-t1", "demo-t1-review"), whole=False
        )
        self.assertIn("demo-t1-review", self.request()["reason"])

    def test_S1_the_unit_waits_for_the_lead_and_the_wake_is_written_once(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1"""
        probe = wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    "import sys,shutil;shutil.copy(sys.argv[1], sys.argv[2])",
                    str(self.state_dir / "demo.json"),
                    str(self.root / "plan-while-open.json"),
                ]
            ]
        )
        self.waits([self.blocked(), probe, wait_result(), self.decide_step("t1", "skip")])
        self.run_driver()
        self.assertEqual(len(self.wake_lines("decision")), 1)  # polled again, still one wake
        self.assertIn(
            "decision pending", (self.root / "plan-while-open.json").read_text(encoding="utf-8")
        )


class AwaitTests(WakeFixture, unittest.TestCase):
    def finished_run(self):
        self.waits([self.blocked(), self.decide_step("t1", "skip")])
        self.run_driver()

    def test_S2_await_prints_the_oldest_unprinted_wake_and_exits_0(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S2"""
        self.finished_run()
        first = self.cli("await", str(self.plan), "--state-dir", str(self.state_dir))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, self.wake_lines()[0] + "\n")
        self.assertTrue(first.stdout.startswith("decision t1: "))

    def test_S2_a_run_that_ended_with_nothing_left_prints_finished_and_exits_0(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S2"""
        self.finished_run()
        self.cli("await", str(self.plan), "--state-dir", str(self.state_dir))
        for _ in range(2):  # finished is not consumed
            again = self.cli("await", str(self.plan), "--state-dir", str(self.state_dir))
            self.assertEqual(
                (again.returncode, again.stdout),
                (0, "finished -> %s\n" % (self.run_dir / "retro.md")),
            )

    def test_S2_await_waits_for_a_wake_that_arrives_later(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S2"""
        (self.run_dir).mkdir(parents=True)
        spec = load_script()
        polls = []

        def poll(seconds):
            """The wait itself: the wake lands during the first one, so `await` has polled and found nothing."""
            polls.append(seconds)
            (self.run_dir / "wakes.jsonl").write_text(
                json.dumps({"kind": "decision", "unit": "t1", "text": "decision t1: x -> /p"})
                + "\n"
            )

        clock = mock.Mock(wraps=spec.time)
        clock.sleep.side_effect = poll
        with mock.patch.object(spec, "time", clock):
            result = self.cli(
                "await", str(self.plan), "--state-dir", str(self.state_dir), "--poll-seconds", "0.1"
            )
        self.assertEqual(polls, [0.1])
        self.assertEqual(
            (result.returncode, result.stdout), (0, "decision t1: x -> /p\n"), result.stderr
        )

    def test_S2_an_unfinished_run_with_nothing_to_print_does_not_say_finished(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S2"""
        self.run_dir.mkdir(parents=True)
        (self.run_dir / "events.jsonl").write_text(json.dumps({"kind": "start"}) + "\n")
        spec = load_script()

        class PollObserved(Exception):
            pass

        clock = mock.Mock(wraps=spec.time)
        clock.sleep.side_effect = PollObserved
        with mock.patch.object(spec, "time", clock), self.assertRaises(PollObserved):
            self.cli(
                "await", str(self.plan), "--state-dir", str(self.state_dir), "--poll-seconds", "0.1"
            )


class DecideTests(WakeFixture, unittest.TestCase):
    def decide(self, *args):
        return self.cli("decide", str(self.plan), *args, "--state-dir", str(self.state_dir))

    def test_S3_a_menu_action_writes_a_valid_decision_and_marks_the_request_resolved(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.write_open_request()
        result = self.decide("t1", "retry", "--reason", "the database is back")
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = contracts.load(
            self.run_dir / "t1" / "decision.json", contracts.validate_decision
        )
        self.assertEqual(
            (decision["action"], decision["reason"]), ("retry", "the database is back")
        )
        request = self.request()
        self.assertEqual(
            (request["status"], request["decision"]),
            ("resolved", str(self.run_dir / "t1" / "decision.json")),
        )
        contracts.validate_request(request)

    def test_S3_the_unit_name_is_case_insensitive(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.write_open_request()
        self.assertEqual(self.decide("T1", "skip", "--reason", "x").returncode, 0)

    def test_S3_an_action_off_the_menu_exits_2_and_writes_nothing(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        path = self.write_open_request()
        before = path.read_text(encoding="utf-8")
        for action in ("ask user", "delete everything"):
            result = self.decide("t1", action, "--reason", "x")
            self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_S3_a_unit_without_an_open_request_exits_2_and_writes_nothing(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        result = self.decide("t1", "retry", "--reason", "x")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())

    def test_S3_a_second_decision_for_one_request_exits_2(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.write_open_request()
        self.assertEqual(self.decide("t1", "retry", "--reason", "x").returncode, 0)
        before = (self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8")
        self.assertEqual(self.decide("t1", "skip", "--reason", "y").returncode, 2)
        self.assertEqual(
            (self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8"), before
        )

    def test_S3_answer_needs_the_answer_text_and_the_other_actions_ignore_it(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.write_open_request()
        self.assertEqual(self.decide("t1", "answer", "--reason", "x").returncode, 2)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(
            self.decide("t1", "answer", "--reason", "x", "--answer", "use port 5432").returncode, 0
        )
        self.assertEqual(
            json.loads((self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8"))[
                "answer"
            ],
            "use port 5432",
        )

    def test_S7_an_answer_naming_another_attempt_exits_2_writes_nothing_and_names_the_expected_id(
        self,
    ):
        """orchestrator-engine/R59, orchestrator-engine/R63"""
        for wrong in ("t1", "t1d1", "t1r1", "t1o3", "t1p1", "T1", "T1R1"):
            with self.subTest(wrong=wrong):
                path = self.write_open_request(task="t1r2")
                before = path.read_text(encoding="utf-8")
                result = self.decide(
                    "t1", "answer", "--reason", "x", "--answer", "report under task_id `%s`" % wrong
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(wrong, result.stderr)
                self.assertIn("t1r2", result.stderr)
                self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
                self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_S7_an_answer_naming_the_requests_attempt_or_no_attempt_is_written(self):
        """orchestrator-engine/R59"""
        for text in (
            "report under task_id t1r2",
            "report under task_id T1R2",
            "see %s/t1r2/report.json" % self.run_dir,
            "ask demo-t1 and use port 5432; t2 is not yours",
        ):
            with self.subTest(text=text):
                self.write_open_request(task="t1r2")
                result = self.decide("t1", "answer", "--reason", "x", "--answer", text)
                self.assertEqual(result.returncode, 0, result.stderr)
                (self.run_dir / "t1" / "decision.json").unlink()

    def test_S3_a_reason_is_required(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.write_open_request()
        self.assertNotEqual(self.decide("t1", "retry").returncode, 0)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())

    def test_S3_the_driver_applies_retry_on_its_next_round(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.waits([self.blocked(), self.decide_step("t1", "retry", "the database is back")])
        self.run_driver(expect=0)
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        self.assertEqual(
            resumes[0]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1d1" / "task.md"),
        )
        self.assertIn(
            "the database is back", (self.run_dir / "t1d1" / "task.md").read_text(encoding="utf-8")
        )
        self.assertEqual(self.spawned(), ["demo-t1"])

    def test_S3_answer_resumes_the_worker_with_the_answer_text(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.waits(
            [
                self.blocked(),
                self.decide_step("t1", "answer", "the port is known", answer="use port 5432"),
            ]
        )
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        self.assertIn("use port 5432", resumes[0]["args"]["task"])
        self.assertIn(
            "use port 5432", (self.run_dir / "t1" / "answer.md").read_text(encoding="utf-8")
        )

    def answered_report(self, task_id="t1", nth=1):
        """The resumed worker finishes under the task id it was resumed on, and writes a test the gate can see."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", nth))
        step["write"] = [
            [str(self.run_dir / task_id / "report.json"), self.report(task_id)],
            [str(self.worktree / "src" / "a" / "test_a.py"), 'def test_a():\n    """S1"""\n'],
        ]
        return step

    def test_S6_an_answer_resumes_the_worker_on_the_same_task_id_and_its_report_passes_the_gate(
        self,
    ):
        """orchestrator-engine/R30"""
        self.waits(
            [
                self.blocked(),
                self.decide_step("t1", "answer", "the port is known", answer="use port 5432"),
                self.answered_report(),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        self.assertIn("use port 5432", resumes[0]["args"]["task"])
        brief = (self.run_dir / "t1" / "answer.md").read_text(encoding="utf-8")
        for needle in ("use port 5432", "`t1`", str(self.run_dir / "t1" / "report.json")):
            self.assertIn(needle, brief)
        self.assertEqual(
            [t for t in self.ledger()["tasks"] if t != "archive"], ["t1"]
        )  # no new attempt
        self.assertFalse((self.run_dir / "t1d1").exists())
        self.assertEqual(self.task()["assignment_id"], resume_id("demo-t1", 1))
        self.assertEqual(self.task()["gate"], "done")

    def test_S6_an_answer_to_a_gate_failure_resumes_the_same_attempt_with_the_findings(self):
        """orchestrator-engine/R30"""
        self.waits(
            [
                self.worker_done(),
                self.worker_done("t1r1"),
                self.worker_done("t1r2"),
                self.decide_step("t1", "answer", "cover S1", answer="name the test S1"),
            ]
        )
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual(len(resumes), 3)  # two fix rounds, then the answer
        self.assertIn("name the test S1", resumes[2]["args"]["task"])
        self.assertIn("S1: test node", (self.run_dir / "t1r2" / "answer.md").read_text())
        self.assertNotIn("t1r3", self.ledger()["tasks"])
        self.assertEqual(self.task("t1r2")["status"], "ACTIVE")

    def test_S6_a_report_written_before_the_answer_is_not_the_answered_rounds_news(self):
        """orchestrator-engine/R30"""
        blocked = self.blocked()
        blocked["write"] = [[str(self.run_dir / "t1" / "report.json"), self.report("t1")]]
        ended = wait_result([assignment("demo-t1", "working", "idle", id=resume_id("demo-t1", 1))])
        self.waits(
            [
                blocked,
                self.decide_step("t1", "answer", "the port is known", answer="use port 5432"),
                *[ended] * 4,
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.request(nth=2)["event"], "idle")

    def test_S3_skip_and_mark_blocked_close_the_unit(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        for action, status in (("mark blocked", "BLOCKED"), ("skip", "SKIPPED")):
            with self.subTest(action=action):
                self.setUp()
                self.waits([self.blocked(), self.decide_step("t1", action, "not needed")])
                self.run_driver()
                self.assertEqual(self.task()["gate"], status.lower())
                self.assertEqual(self.calls("resume"), [])

    def test_S3_a_second_request_for_a_unit_gets_its_own_file(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S3"""
        self.waits(
            [
                self.blocked(),
                self.decide_step("t1", "retry"),
                self.blocked(text="still stuck", id=resume_id("demo-t1", 1)),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request(nth=2)["reason"].count("still stuck"), 1)
        self.assertEqual(len(self.wake_lines("decision")), 2)
        self.assertIn("request.2.json", self.wake_lines("decision")[1])


class QueueTests(WakeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.set_plan(("T1", "T3"))

    def test_S4_other_units_keep_dispatching_and_gating_while_one_request_is_open(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S4"""
        third = self.worker_done("t3", name="demo-t3")
        # t3 maps its scenario to a node that does not exist, so its gate fails
        gone = self.report("t3", covers={"S3": ["src/c/test_gone.py::test_x"]})
        third["write"] = [[str(self.run_dir / "t3" / "report.json"), gone]]
        self.waits([self.blocked(), third, self.decide_step("t1", "skip")])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t3"])
        # t3 failed converge while t1's request was open, and got its fix round
        self.assertEqual([c["args"]["name"] for c in self.calls("resume")], ["demo-t3"])
        self.assertEqual(len(self.wake_lines("decision")), 1)

    def test_S4_later_wakes_come_out_of_await_one_at_a_time_in_the_order_written(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S4"""
        self.waits(
            [
                self.blocked(),
                self.blocked(name="demo-t3", text="no disk space"),
                self.decide_step("t1", "skip"),
                self.decide_step("t3", "skip"),
            ]
        )
        self.run_driver()
        lines = self.wake_lines("decision")
        self.assertEqual([line.split(":")[0] for line in lines], ["decision t1", "decision t3"])
        printed = [
            self.cli("await", str(self.plan), "--state-dir", str(self.state_dir)).stdout
            for _ in range(3)
        ]
        self.assertEqual(
            printed,
            [lines[0] + "\n", lines[1] + "\n", "finished -> %s\n" % (self.run_dir / "retro.md")],
        )


class HeldMessageTests(WakeFixture, unittest.TestCase):
    def test_S7_a_complete_that_arrives_during_a_request_is_acted_on_after_the_decision(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S7"""
        self.waits(
            [
                self.blocked(),
                self.worker_done(tests=True),
                self.decide_step("t1", "retry", "carry on"),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        # the held report went through converge and review instead of being dropped
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task()["status"], "COMPLETE")
        self.assertEqual([e["held"] for e in self.run_events("held")], ["complete"])
        self.assertEqual(
            self.calls("resume"), []
        )  # the worker had already finished: nothing to hand over again

    def test_S7_a_blocked_during_a_request_raises_a_new_request_after(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S7"""
        self.waits(
            [
                self.blocked(),
                self.blocked("decision", "also need the schema"),
                self.decide_step("t1", "retry", "carry on"),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        lines = self.wake_lines("decision")
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("also need the schema", lines[1])
        self.assertEqual(self.request(nth=2)["event"], "decision")
        self.assertEqual([e["held"] for e in self.run_events("held")], ["decision"])

    def test_S7_the_event_that_caused_the_request_is_not_held_again(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S7"""
        self.waits(
            [
                self.blocked(),
                wait_result([assignment("demo-t1", "blocked")]),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        self.assertEqual(self.run_events("held"), [])
        self.assertEqual(len(self.wake_lines("decision")), 1)


class FailedHandOverTests(WakeFixture, unittest.TestCase):
    def refuse_resumes(self, name="demo-t1", error="the pane is blocked on a login"):
        """The runtime refuses every resume of `name` with `error`, as it does a pane blocked on a login."""
        (self.home / "refuse_resume.json").write_text(json.dumps({name: error}), encoding="utf-8")

    def test_S8_a_decision_that_cannot_be_handed_over_raises_a_new_request_naming_why(self):
        """engine-gaps/S8"""
        self.refuse_resumes()
        self.waits(
            [
                self.blocked(),
                self.decide_step("t1", "retry", "carry on"),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        again = self.request(nth=2)
        self.assertEqual((again["event"], again["status"]), ("handover", "resolved"))
        for needle in ("retry", "login", "demo-t1", "fresh worker", "skip"):
            self.assertIn(needle, again["reason"])
        self.assertEqual(len(self.wake_lines("decision")), 2)
        self.assertEqual(
            self.task()["status"], "RELEASED"
        )  # the second decision, `skip`, ended the unit
        self.assertEqual(len(self.calls("resume")), 1, "one resume, refused; nothing sent again")

    def test_S8_a_complete_that_lands_while_that_request_is_open_is_acted_on_without_a_hand_over(
        self,
    ):
        """engine-gaps/S8"""
        self.refuse_resumes()
        self.waits(
            [
                self.blocked(),
                self.decide_step("t1", "retry", "carry on"),
                self.worker_done(tests=True),
                self.decide_step("t1", "retry", "the pane is back"),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        self.assertEqual([e["held"] for e in self.run_events("held")], ["complete"])
        self.assertEqual(self.task()["status"], "COMPLETE")
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(len(self.calls("resume")), 1, "the refused resume is the only hand-over")


class PrReadyTests(WakeFixture, unittest.TestCase):
    def test_S5_a_reviewed_green_unit_writes_exactly_one_pr_ready_wake(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S5"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.wake_lines("pr-ready"), ["pr-ready t1: " + PR])
        self.assertEqual(self.wakes()[0]["key"], "t1:abc1234")

    def test_S5_a_failing_check_then_a_green_one_still_writes_one_wake(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S5"""
        self.checks([("fail", "unit tests"), "green"])
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.worker_done("t1r1", tests=True),
                again,
            ]
        )
        self.run_driver()
        self.assertEqual(self.wake_lines("pr-ready"), ["pr-ready t1: " + PR])

    def test_S5_the_same_unit_and_head_is_never_woken_twice(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S5"""
        spec = load_script()
        self.run_dir.mkdir(parents=True)
        driver = spec.Driver.__new__(spec.Driver)
        driver.run_dir = self.run_dir
        self.assertTrue(driver.wake("pr-ready", "t1", "pr-ready t1: " + PR, key="t1:abc1234"))
        self.assertFalse(driver.wake("pr-ready", "t1", "pr-ready t1: " + PR, key="t1:abc1234"))
        self.assertTrue(driver.wake("pr-ready", "t1", "pr-ready t1: " + PR, key="t1:def5678"))
        self.assertEqual(len(self.wake_lines("pr-ready")), 2)

    def test_S5_a_unit_that_fails_review_gets_no_pr_ready_wake(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S5"""
        review = self.review(
            verdict="block",
            major=[{"id": "M1", "file": "a", "line": 1, "problem": "p", "fix": "f"}],
        )
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(review),
                self.blocked(id=resume_id("demo-t1", 1)),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver()
        self.assertEqual(self.wake_lines("pr-ready"), [])
        self.assertEqual(len(self.wake_lines("decision")), 1)


class PlanInvalidTests(WakeFixture, unittest.TestCase):
    def test_S31_a_plan_that_is_unapproved_before_the_run_starts_still_exits_2(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S31"""
        self.plan.write_text(yaml.safe_dump(plan_data(), sort_keys=False), encoding="utf-8")
        self.assertEqual(self.run_driver(expect=2).returncode, 2)
        self.assertEqual(self.wakes(), [])


FAKE_HC = """#!/usr/bin/env python3
import json, os, sys
home = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(home, "hc.log"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "send" and os.path.exists(os.path.join(home, "hc.fail")):
    print("no such terminal", file=sys.stderr)
    sys.exit(1)
print("{}")
"""


class AlertTests(WakeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        hc = self.home / "hc"
        hc.write_text(FAKE_HC, encoding="utf-8")
        hc.chmod(0o755)

    def sends(self):
        path = self.home / "hc.log"
        rows = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        return [argv for argv in map(json.loads, rows) if argv[0] == "send"]

    def test_S4_S5_a_new_wake_is_sent_once_to_the_launching_terminal(self):
        """orchestrator-engine/R14: orchestrator-lead-alerts/S4, orchestrator-lead-alerts/S5"""
        self.waits([self.blocked(), self.decide_step("t1", "skip")])
        self.run_driver("--notify-terminal", "t175")
        sends = self.sends()
        self.assertEqual(len(sends), 1, sends)
        self.assertEqual(sends[0][:2], ["send", "t175"])
        self.assertIn("orchestrate-demo needs you", sends[0][2])
        self.assertIn(self.wake_lines("decision")[0], sends[0][2])
        self.assertNotIn("\n", sends[0][2])

    def test_S4_a_run_without_a_notify_terminal_sends_nothing(self):
        """orchestrator-engine/R14: orchestrator-lead-alerts/S4"""
        self.waits([self.blocked(), self.decide_step("t1", "skip")])
        self.run_driver()
        self.assertEqual(self.sends(), [])

    def test_S6_a_failed_send_is_logged_and_the_run_goes_on(self):
        """orchestrator-engine/R14: orchestrator-lead-alerts/S6"""
        (self.home / "hc.fail").write_text("", encoding="utf-8")
        self.waits([self.blocked(), self.decide_step("t1", "skip")])
        self.run_driver("--notify-terminal", "t175")
        self.assertEqual(len(self.sends()), 1)
        failed = self.run_events("notify-failed")
        self.assertEqual(len(failed), 1)
        self.assertIn("no such terminal", failed[0]["error"])
        self.assert_request_skipped()

    def assert_request_skipped(self):
        self.assertEqual(self.request()["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
