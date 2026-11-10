"""The gates after a worker reports: converge, a fresh reviewer, fix rounds, a fresh worker for a dead pane."""

import json
import unittest

import pytest
from fixtures import RunFixture, plan_text, report_wait, resume_id, spawn_id

pytestmark = pytest.mark.integration


MAJOR = {
    "id": "M1",
    "file": "src/a/x.py",
    "line": 7,
    "problem": "drops the last row",
    "fix": "include the final row",
}
DEFERRED = {"id": "D1", "note": "rename the helper someday"}
EVIDENCE = {
    "command": "pytest -k last",
    "exit": 1,
    "excerpt": "AssertionError: 3 != 4",
    "log_path": "/run/t1/pytest.log",
}
BLOCKING = {
    "id": "B1",
    "kind": "blocking",
    "summary": "drops the last row",
    "answer_required": False,
    "file": "src/a/x.py",
    "line": 7,
    "fix": "include the final row",
    "evidence": EVIDENCE,
}
QUESTION = {
    "id": "Q1",
    "kind": "question",
    "summary": "should empty input raise?",
    "answer_required": True,
}
NIT = {"id": "N1", "kind": "nit", "summary": "rename the helper", "answer_required": False}
CONCERN = {
    "id": "K1",
    "kind": "concern",
    "summary": "the cache is never cleared",
    "answer_required": False,
}


class GateFixture(RunFixture):
    """One ticket, scripted worker and reviewer steps; shared by the gate and merge tests."""

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
                    "pr": "https://github.com/o/r/pull/7",
                },
                **over,
            )
        )

    def worker_done(self, task_id="t1", tests=False, report=None, name="demo-t1"):
        """A wait step: the worker wrote its report (and, with tests, a tagged test) and says complete."""
        fix_round = int(task_id.split("r")[1]) if "r" in task_id else 0
        step = report_wait(
            name, "complete", id=resume_id(name, fix_round) if fix_round else spawn_id(name)
        )
        step["write"] = []
        if report is not False:
            step["write"].append(
                [str(self.run_dir / task_id / "report.json"), report or self.report(task_id)]
            )
        if tests:
            step["write"].append(
                [str(self.worktree / "src" / "a" / "test_a.py"), 'def test_a():\n    """S1"""\n']
            )
        return step

    def review(self, **over):
        return json.dumps(
            dict(
                {"contract": 1, "verdict": "pass", "major": [], "deferred": [], "commit": "@HEAD"},
                **over,
            )
        )

    def review2(self, **over):
        """A contract 2 review.json: pass with no items unless told otherwise."""
        return json.dumps(
            dict(
                {
                    "contract": 2,
                    "verdict": "pass",
                    "items": [],
                    "checks_run": [],
                    "commit": "@HEAD",
                },
                **over,
            )
        )

    def reviewer_done(self, review=None, write=None):
        step = report_wait("demo-t1-review", "complete")
        step["write"] = (
            [[str(self.run_dir / "t1" / "review.json"), review or self.review()]]
            if write is None
            else write
        )
        return step

    def task(self, task_id="t1"):
        return self.ledger()["tasks"][task_id]

    def request(self, unit="t1"):
        """The unit's first request.json: what the Lead was asked."""
        return json.loads((self.run_dir / unit / "request.json").read_text(encoding="utf-8"))

    def skipped(self):
        """A wait step in which the Lead drops the unit, so a run that ends in a request can finish."""
        return self.decide_step("t1", "skip")

    def spawned(self):
        return [c["args"]["name"] for c in self.calls("spawn")]


class GateTests(GateFixture, unittest.TestCase):
    def test_S5_failing_converge_resumes_the_worker_in_a_new_task_directory(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S5"""
        self.waits([self.worker_done()])
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual(len(resumes), 1)
        self.assertEqual(resumes[0]["args"]["name"], "demo-t1")
        self.assertEqual(
            resumes[0]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1r1" / "task.md"),
        )
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(
            (findings["contract"], findings["source"], findings["attempt"]), (2, "converge", 1)
        )
        self.assertEqual(
            [(i["source"], i["check"], i["excerpt"]) for i in findings["items"]],
            [("converge", "coverage", "FAIL coverage: S1")],
        )
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "FAIL coverage: S1",
            str(self.run_dir / "t1r1" / "report.json"),
            "t1r1",
            "findings.json",
        ):
            self.assertIn(needle, task_md)
        self.assertEqual(self.spawned(), ["demo-t1"])
        tasks = self.ledger()["tasks"]
        self.assertEqual(
            (tasks["t1"]["status"], tasks["t1"]["report"]["review_rounds"]), ("RELEASED", 1)
        )
        self.assertEqual(
            (tasks["t1r1"]["status"], tasks["t1r1"]["runtime_id"]), ("ACTIVE", "term-demo-t1")
        )
        self.assertEqual(tasks["t1r1"]["packet"]["unit"], "t1")
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 1)

    def ledger_unit(self, unit):
        from fixtures import load_script

        data = self.ledger()
        return load_script().unit_summary(data, unit)

    def test_S5_a_fix_round_passing_converge_goes_to_the_reviewer(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S5 — (a fix round is reviewed since orchestrate-lean-loop/S8)"""
        self.waits([self.worker_done(), self.worker_done("t1r1", tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S5_a_third_failure_goes_to_the_lead_not_a_third_fix_round(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S5"""
        self.waits(
            [self.worker_done(), self.worker_done("t1r1"), self.worker_done("t1r2"), self.skipped()]
        )
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2)
        self.assertEqual(self.request()["task"], "t1r2")
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 3)

    FAILED_CHECK = [{"command": "pytest", "outcome": "failed", "reason": "2 tests failed"}]

    def refused(self, task_id):
        """A wait step: the worker reports a unit ready with a failed check, which COMPLETE refuses."""
        return self.worker_done(task_id, report=self.report(task_id, checks=self.FAILED_CHECK))

    def test_S5_a_ready_report_that_fails_the_complete_rules_goes_back_to_its_worker(self):
        """orchestrator-engine/R2: merged-unit-completes/S5"""
        self.waits([self.refused("t1")])
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(findings["attempt"], 1)
        self.assertIn("complete result has failed checks: pytest", findings["items"][0]["excerpt"])
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("complete result has failed checks: pytest", task_md)
        self.assertFalse((self.run_dir / "t1" / "request.json").exists())
        self.assertEqual(self.spawned(), ["demo-t1"])  # no reviewer: the unit never left its gate
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 1)
        self.assertEqual(self.task("t1r1")["status"], "ACTIVE")

    def test_S5a_report_refusals_at_the_fix_round_cap_ask_the_lead(self):
        """orchestrator-engine/R2: merged-unit-completes/S5a"""
        self.waits([self.refused("t1"), self.refused("t1r1"), self.refused("t1r2"), self.skipped()])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2)
        request = self.request()
        self.assertEqual((request["task"], request["event"]), ("t1r2", "gate"))
        self.assertIn("complete result has failed checks: pytest", request["reason"])
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 3)

    def test_S5_a_report_the_ledger_cannot_read_asks_the_lead_and_the_driver_exits_clean(self):
        """orchestrator-engine/R2: merged-unit-completes/S5 — (a report with no `remaining`, or a check with no outcome)"""
        for broken in (
            {"remaining": None},
            {"checks": [{"command": "pytest"}]},
        ):
            with self.subTest(broken=broken):
                report = json.loads(self.report("t1"))
                if "remaining" in broken:
                    del report["remaining"]
                else:
                    report.update(broken)
                self.waits(
                    [
                        self.worker_done("t1", tests=True, report=json.dumps(report)),
                        self.skipped(),
                    ]
                )
                self.run_driver()
                self.assertEqual(self.request()["event"], "report")
                self.setUp()

    def test_S6_passing_converge_spawns_a_fresh_large_reviewer_with_paths_only(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6"""
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review"])
        args = self.calls("spawn")[1]["args"]
        self.assertEqual(
            (args["kind"], args["model"], args["effort"], args["cwd"]),
            ("worker", "opus", "high", str(self.worktree)),
        )
        self.assertEqual(
            args["task"], "Read %s and follow it." % (self.run_dir / "t1-review" / "task.md")
        )
        text = (self.run_dir / "t1-review" / "task.md").read_text(encoding="utf-8")
        for needle in (
            str(self.run_dir / "t1" / "task.md"),
            str(self.plan),
            "origin/main...HEAD",
            str(self.worktree),
            str(self.run_dir / "t1" / "review.json"),
            "MAJOR",
            "deferred",
        ):
            self.assertIn(needle, text)
        task = self.task()
        self.assertEqual(
            (task["status"], task["gate"], task["report"]["head"]),
            ("PARTIAL", "reviewing", "abc1234"),
        )
        self.assertEqual(self.calls("resume"), [])

    def test_S6_a_blocking_review_sends_only_its_major_items_as_a_fix_round(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6"""
        review = self.review(verdict="block", major=[MAJOR], deferred=[DEFERRED])
        self.waits([self.worker_done(tests=True), self.reviewer_done(review)])
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual((findings["source"], findings["items"]), ("review", [MAJOR]))
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("drops the last row", task_md)
        self.assertIn("src/a/x.py", task_md)
        self.assertNotIn("rename the helper", task_md)
        self.assertIn(
            "rename the helper someday", (self.run_dir / "deferred.md").read_text(encoding="utf-8")
        )
        self.assertEqual(self.task()["report"]["review_rounds"], 1)
        self.assertEqual(self.task("t1r1")["status"], "ACTIVE")

    def test_S6_a_passing_review_hands_the_ticket_to_the_ci_gate(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        task = self.task()
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        self.assertEqual(task["report_path"], str(self.run_dir / "t1" / "report.json"))
        self.assertIn("| COMPLETE ", self.plan.read_text(encoding="utf-8"))

    def test_S7_a_fix_round_for_a_dead_pane_spawns_a_fresh_worker_with_the_original_task(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S7"""
        self.states({"demo-t1": "exited"})
        self.waits([self.worker_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])
        fresh = self.calls("spawn")[1]["args"]
        for needle in (
            str(self.run_dir / "t1" / "task.md"),
            str(self.run_dir / "t1r1" / "findings.json"),
            str(self.run_dir / "t1r1" / "task.md"),
        ):
            self.assertIn(needle, fresh["task"])
        first = self.calls("spawn")[0]["args"]
        self.assertEqual(
            (fresh["cwd"], fresh["model"], fresh["effort"]),
            (first["cwd"], first["model"], first["effort"]),
        )
        self.assertEqual(self.calls("status")[0]["args"], {"name": "demo-t1"})
        tasks = self.ledger()["tasks"]
        self.assertEqual((tasks["t1"]["status"], tasks["t1r1"]["status"]), ("RELEASED", "ACTIVE"))
        self.assertEqual(tasks["t1r1"]["runtime_id"], "term-demo-t1")

    def test_S7_an_unreadable_status_counts_as_a_gone_pane(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S7"""
        self.states({"demo-t1": "error"})
        self.waits([self.worker_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])

    def test_S6a_a_missing_report_does_not_advance_the_ticket(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a

        R18: the worker that announced a report without writing it is told once, twice, and only then the Lead is
        asked. The ticket never advances on a payload that is not there."""
        self.waits(
            [
                *self.repeated(self.worker_done(report=False)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])
        warned = self.calls("resume")
        self.assertEqual(len(warned), 2, [c["args"]["task"] for c in warned])
        for call in warned:
            self.assertIn("report.json", call["args"]["task"])
        self.assertEqual(self.request()["event"], "report")
        self.assertIn("2 warning(s)", self.request()["reason"])
        self.assertNotIn("report", self.task())

    def test_S6a_an_invalid_report_does_not_advance_the_ticket(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        self.waits(
            [*self.repeated(self.worker_done(report=self.report("t1", contract=3))), self.skipped()]
        )
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.request()["event"], "report")
        self.assertIn("contract", self.request()["reason"])
        self.assertEqual(
            len(self.calls("resume")), 2, "the worker was warned twice before the Lead was asked"
        )

    def test_S6a_a_report_for_another_task_does_not_advance_the_ticket(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        self.waits([*self.repeated(self.worker_done(report=self.report("t9"))), self.skipped()])
        self.run_driver()
        self.assertEqual(self.request()["event"], "report")
        self.assertIn("t9", self.request()["reason"])
        self.assertEqual(
            len(self.calls("resume")), 2, "the worker that misnamed its report was warned twice"
        )

    def test_S6a_an_invalid_review_does_not_start_a_fix_round(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        broken = self.review(verdict="pass", major=[MAJOR])
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(broken)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(
            len(self.calls("resume")), 2, "the reviewer was warned twice before the Lead was asked"
        )
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("review.json", self.request()["reason"])

    def test_S6a_a_missing_review_does_not_advance_the_ticket(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a

        R18 moved the first answer from the Lead to the agent that sent the report: the gate waits, the reviewer is
        told its file is not on disk, and the ticket does not advance. The request comes after the warnings."""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(write=[])),
                self.skipped(),
            ]
        )
        self.run_driver()
        warned = self.calls("resume")
        self.assertEqual(len(warned), 2, [c["args"]["task"] for c in warned])
        for call in warned:
            self.assertIn("review.json", call["args"]["task"])
            self.assertIn("write it again, then report again", call["args"]["task"])
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("2 warning(s)", self.request()["reason"])
        self.assertNotIn(
            "reviewed_commit", self.task(), "no verdict was recorded from a missing file"
        )


class PayloadTests(GateFixture, unittest.TestCase):
    """R18: a report is the news, the file it names is the payload, and the driver waits for the payload."""

    def test_S2_a_payload_that_never_lands_warns_the_sender_before_the_lead(self):
        """orchestrator-engine/R18: orchestrator-payload-race/S2"""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(write=[])),
                self.skipped(),
            ]
        )
        self.run_driver()
        warned = [c["args"] for c in self.calls("resume")]
        self.assertEqual(len(warned), 2, "one warning per report, capped")
        self.assertEqual(warned[0]["name"], "demo-t1-review")
        self.assertIn("The driver reads the file, not the report", warned[0]["task"])
        self.assertEqual(self.task()["gate"], "skipped")

    def test_S3_a_sender_that_cannot_be_warned_goes_straight_to_the_lead(self):
        """orchestrator-engine/R18: orchestrator-payload-race/S3"""
        self.states({"demo-t1-review": "exited"})
        self.waits([self.worker_done(tests=True), self.reviewer_done(write=[]), self.skipped()])
        self.run_driver()
        self.assertEqual(
            self.calls("resume"), [], "a dead reviewer cannot write the file: no warning is sent"
        )
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("no agent left to write it", self.request()["reason"])


class CheckoutTimeoutTests(GateFixture, unittest.TestCase):
    def test_S3_worktree_add_gets_a_checkout_sized_timeout(self):
        """orchestrator-engine/R13: orchestrator-lead-alerts/S3"""
        from unittest import mock

        from fixtures import load_script

        driver = load_script()
        with mock.patch.object(driver, "run", wraps=driver.run) as spy:
            self.waits([self.worker_done()])
            self.run_driver()
        adds = [c for c in spy.call_args_list if c.args[0][3:5] == ["worktree", "add"]]
        self.assertTrue(adds, "no worktree was added")
        for call in adds:
            self.assertGreaterEqual(call.kwargs.get("timeout", 15), 300)


if __name__ == "__main__":
    unittest.main()
