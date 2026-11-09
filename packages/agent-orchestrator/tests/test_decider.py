"""Applying the Lead's decisions, the request pause, person-owned tickets, and a loop that never spins on a waiting unit."""

import json
import os
import sys
import unittest
from unittest import mock

import pytest
from fixtures import (
    ROWS,
    RunFixture,
    assignment,
    load_script,
    plan_text,
    recorded_idles,
    report_wait,
    resume_id,
    spawn_id,
    wait_result,
)

pytestmark = pytest.mark.integration


class DeciderFixture(RunFixture):
    def setUp(self):
        super().setUp()
        self.plan.write_text(plan_text(rows=("T1",)), encoding="utf-8")
        self.run_dir = self.state_dir / "demo"
        self.worktree = self.root / "repo.worktrees" / "demo-t1"

    def report(self, task_id, **over):
        return json.dumps(
            dict(
                {
                    "contract": 1,
                    "task_id": task_id,
                    "status": "COMPLETE",
                    "summary": "done",
                    "acceptance_met": True,
                    "files": [],
                    "decisions": [],
                    "interfaces": [],
                    "remaining": [],
                    "checks": [],
                    "review_rounds": 0,
                    "usage": "unknown",
                    "head": "abc1234",
                    "pr": None,
                },
                **over,
            )
        )

    def failing_report(self, task_id):
        """The worker says complete with no test: converge fails on the scenario coverage."""
        fix_round = int(task_id.split("r")[1]) if "r" in task_id else 0
        step = report_wait(
            "demo-t1",
            "complete",
            id=resume_id("demo-t1", fix_round) if fix_round else spawn_id("demo-t1"),
        )
        step["write"] = [[str(self.run_dir / task_id / "report.json"), self.report(task_id)]]
        return step

    def blocked(self, kind="blocked", text="cannot reach the database", id=None):
        return report_wait("demo-t1", kind, text, id=id)

    def decided(self, action, unit="t1", reason="because", answer=None):
        """A wait step during which the Lead decides the unit's open request."""
        return self.decide_step(unit, action, reason, answer)

    def task(self, task_id="t1"):
        return self.ledger()["tasks"][task_id]

    def spawned(self):
        return [c["args"]["name"] for c in self.calls("spawn")]

    def plan_status(self, row="T1"):
        for line in self.plan.read_text(encoding="utf-8").splitlines():
            if line.startswith("| %s |" % row):
                return line.rstrip(" |").rsplit("|", 1)[1].strip()

    def attention_md(self):
        path = self.run_dir / "attention.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""


class DeciderTests(DeciderFixture, unittest.TestCase):
    def test_S11_a_working_assignment_without_an_ended_turn_is_not_an_event(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits([wait_result([assignment("demo-t1", "working")], reason="timeout")])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task().get("gate"), None)
        self.assertEqual(self.wakes(), [])

    def test_S11_a_superseded_assignment_reported_failed_is_not_an_event(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits([self.failing_report("t1"), wait_result([assignment("demo-t1", "failed")])])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task("t1r1")["assignment_id"], resume_id("demo-t1", 1))
        self.assertEqual(self.task("t1r1").get("gate"), None)

    def test_S11_a_worker_whose_turn_ended_without_a_report_is_one_request(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        for runtime_state in ("idle", "done", "unreported", "error"):
            with self.subTest(runtime_state=runtime_state):
                self.setUp()
                self.waits(
                    [
                        wait_result([assignment("demo-t1", "working", runtime_state)]),
                        self.decided("skip"),
                    ]
                )
                self.run_driver()
                self.assertEqual(self.spawned(), ["demo-t1"])
                self.assertIn("runtime state " + runtime_state, self.wake_lines()[0])
                self.assertEqual(len(self.wakes()), 1)

    def test_S11_a_failed_worker_is_one_request_by_state_and_message(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits(
            [
                report_wait("demo-t1", "failed", "worker terminal exited before completion"),
                self.decided("skip"),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.wakes()), 1)
        self.assertIn("terminal exited", self.wake_lines()[0])

    def test_S11_retry_after_a_failed_gate_runs_one_more_fix_round(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits(
            [
                self.failing_report("t1"),
                self.failing_report("t1r1"),
                self.failing_report("t1r2"),
                self.decided("retry", unit="t1"),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 3)
        self.assertEqual(
            self.calls("resume")[2]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1r3" / "task.md"),
        )
        findings = json.loads((self.run_dir / "t1r3" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual((findings["source"], findings["attempt"]), ("converge", 3))
        self.assertEqual(
            (self.task("t1r3")["status"], self.task("t1r2")["status"]), ("ACTIVE", "RELEASED")
        )

    def test_S11_retry_resumes_a_live_worker_in_a_new_task_directory(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits([self.blocked(), self.decided("retry", reason="the database is back")])
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        self.assertEqual(
            resumes[0]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1d1" / "task.md"),
        )
        text = (self.run_dir / "t1d1" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "cannot reach the database",
            "the database is back",
            str(self.run_dir / "t1d1" / "report.json"),
            str(self.run_dir / "t1" / "task.md"),
            "t1d1",
        ):
            self.assertIn(needle, text)
        self.assertEqual(
            (self.task("t1d1")["status"], self.task("t1")["status"]), ("ACTIVE", "RELEASED")
        )
        self.assertEqual(self.spawned(), ["demo-t1"])

    def test_S11_retry_for_an_exited_worker_spawns_a_fresh_one(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.states({"demo-t1": "exited"})
        self.waits([self.blocked(), self.decided("retry")])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])

    def test_S11_fresh_worker_replaces_a_live_pane_with_the_same_class(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits([self.blocked(), self.decided("fresh worker")])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual([c["args"]["name"] for c in self.calls("stop")], ["demo-t1"])
        first, fresh = self.calls("spawn")[0]["args"], self.calls("spawn")[1]["args"]
        self.assertEqual(
            (fresh["name"], fresh["model"], fresh["effort"]),
            ("demo-t1", first["model"], first["effort"]),
        )
        self.assertIn(str(self.run_dir / "t1d1" / "task.md"), fresh["task"])
        self.assertEqual(self.task("t1d1")["status"], "ACTIVE")

    def test_S11_retry_one_class_up_spawns_a_fresh_worker_on_the_next_class(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits([self.blocked(), self.decided("retry one class up")])
        self.run_driver()
        first, fresh = self.calls("spawn")[0]["args"], self.calls("spawn")[1]["args"]
        self.assertEqual((first["model"], fresh["model"]), ("haiku", "sonnet"))
        self.assertEqual(self.task("t1d1")["packet"]["class"], "medium")
        self.assertEqual(self.task("t1d1")["packet"]["model"], "sonnet")

    def test_S11_mark_blocked_and_skip_close_the_unit_in_ledger_and_plan(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        for action, status in (("mark blocked", "BLOCKED"), ("skip", "SKIPPED")):
            with self.subTest(action=action):
                self.setUp()
                self.waits([self.blocked(), self.decided(action, reason="not needed")])
                self.run_driver()
                self.assertTrue(self.plan_status().startswith(status + " · "), self.plan_status())
                self.assertIn("not needed", self.plan_status())
                self.assertEqual(self.calls("resume"), [])
                self.assertEqual(self.spawned(), ["demo-t1"])
                self.assertEqual(self.task()["gate"], status.lower())

    def test_S11_the_lead_may_decide_one_unit_as_often_as_it_needs(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        self.waits(
            [
                self.blocked(),
                self.decided("retry"),
                self.blocked(text="still stuck", id=resume_id("demo-t1", 1)),
                self.decided("retry"),
                self.blocked(text="again", id=resume_id("demo-t1", 2)),
                self.decided("skip"),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.wake_lines("decision")), 3)
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task("t1d2")["gate"], "skipped")


class RequestPauseTests(DeciderFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plan.write_text(plan_text(rows=("T1", "T3")), encoding="utf-8")

    def assert_requested(self, needle):
        self.assertEqual(len(self.wake_lines("decision")), 1)
        self.assertIn(needle, self.wake_lines("decision")[0])

    def test_S12_a_waiting_unit_reads_decision_pending_while_the_others_keep_running(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S12"""
        probe = wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    "import sys,shutil;shutil.copy(sys.argv[1], sys.argv[2])",
                    str(self.plan),
                    str(self.root / "open.md"),
                ]
            ]
        )
        self.waits([self.blocked(), probe, self.decided("skip")])
        self.run_driver()
        rows = {
            line.split("|")[1].strip(): line.rstrip(" |").rsplit("|", 1)[1].strip()
            for line in (self.root / "open.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("| T")
        }
        self.assertTrue(rows["T1"].startswith("IN PROGRESS · decision pending: "), rows["T1"])
        self.assertEqual(rows["T3"], "IN PROGRESS")
        self.assertEqual(self.task("t3")["status"], "ACTIVE")

    def test_S12_a_decision_that_breaks_its_contract_raises_a_new_request(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S12"""
        bad = self.run_dir / "t1" / "decision.json"
        resolve = wait_result(
            write=[
                [
                    str(bad),
                    json.dumps({"contract": 1, "action": "delete everything", "reason": "x"}),
                ]
            ]
        )
        resolve["run"] = [
            [
                sys.executable,
                "-c",
                "import json,sys;p=sys.argv[1];r=json.load(open(p));r.update(status='resolved',decision=sys.argv[2]);"
                "json.dump(r,open(p,'w'))",
                str(self.run_dir / "t1" / "request.json"),
                str(bad),
            ]
        ]
        self.waits([self.blocked(), resolve, self.decided("skip")])
        self.run_driver()
        self.assertEqual(len(self.wake_lines("decision")), 2)
        self.assertIn("decision.json", self.wake_lines("decision")[1])

    def test_S12_a_contract_error_raises_a_request_too(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S12

        R18: a report that names a file nobody wrote is warned about twice before the Lead is asked."""
        self.waits(
            [
                *self.repeated(report_wait("demo-t1", "complete")),
                self.decided("skip"),
            ]
        )
        self.run_driver()
        self.assert_requested("report.json")

    def test_S12_a_request_status_stays_one_table_cell(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S12"""
        self.waits([self.blocked(text="a | b\nc " + "x" * 400), self.decided("skip")])
        self.run_driver()
        self.assertEqual(len(self.wake_lines("decision")), 1)
        self.assertNotIn("\n", self.wake_lines("decision")[0])
        self.assertEqual(
            len(self.plan.read_text(encoding="utf-8").splitlines()),
            len(plan_text(rows=("T1", "T3")).splitlines()),
        )

    def test_S12_a_waiting_unit_neither_spins_the_loop_nor_gets_a_second_request(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S12"""
        parked = wait_result([assignment("demo-t1", "blocked")])
        again = report_wait("demo-t1", "blocked", "still blocked")
        self.plan.write_text(plan_text(rows=("T1",)), encoding="utf-8")
        self.waits([self.blocked(), parked, again, parked, parked, self.decided("skip")])
        self.run_driver()
        self.assertEqual(len(self.wake_lines("decision")), 1)
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task()["gate"], "skipped")

    def test_S12_a_waiting_unit_counts_as_no_progress_so_the_loop_backs_off(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S12"""
        spec = load_script()
        busy = wait_result(
            [assignment("demo-t1", "blocked"), assignment("demo-t3")], reason="timeout"
        )
        self.waits(
            [self.blocked()]
            + [busy] * 4
            + [self.decided("skip"), wait_result([assignment("demo-t3", "complete")])]
        )
        env = self.env()
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
            "30",
        ]
        with (
            mock.patch.dict(os.environ, env),
            mock.patch.object(sys, "argv", argv),
            recorded_idles(spec) as idles,
        ):
            spec.main()
        self.assertGreaterEqual(len(idles), 4)
        self.assertEqual(set(idles), {30})
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t3"])


class PersonOwnedTests(DeciderFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plan.write_text(plan_text(rows=("T1", "T4")), encoding="utf-8")

    def test_S13_a_ready_person_owned_ticket_reads_needs_you_and_is_logged(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S13"""
        self.run_driver()
        self.assertEqual(self.plan_status("T4"), "needs you: sign-off")
        self.assertIn("T4", self.attention_md())
        self.assertIn("sign-off", self.attention_md())
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.plan_status("T1"), "IN PROGRESS")

    def test_S13_it_waits_until_the_user_sets_it_complete(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S13"""
        self.run_driver()
        self.run_driver()
        self.assertEqual(self.plan_status("T4"), "needs you: sign-off")
        self.assertEqual(self.attention_md().count("sign-off"), 1)
        text = self.plan.read_text(encoding="utf-8").replace(
            "needs you: sign-off", "COMPLETE 2026-11-07"
        )
        self.plan.write_text(text, encoding="utf-8")
        self.run_driver()
        self.assertEqual(self.plan_status("T4"), "COMPLETE 2026-11-07")
        self.assertEqual(self.attention_md().count("sign-off"), 1)

    def test_S13_a_person_ticket_with_open_blockers_stays_to_do(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S13"""
        row = ROWS["T4"].replace("| none | none |", "| none | T1 |")
        self.plan.write_text(plan_text(rows=("T1",)) + row + "\n", encoding="utf-8")
        self.run_driver()
        self.assertEqual(self.plan_status("T4"), "TO-DO")
        self.assertEqual(self.attention_md(), "")


if __name__ == "__main__":
    unittest.main()
