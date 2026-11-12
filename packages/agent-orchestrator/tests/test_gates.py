"""The gates after a worker reports: converge, a fresh reviewer, fix rounds, a fresh worker for a dead pane."""

import io
import itertools
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
import yaml
from fixtures import (
    RunFixture,
    assignment,
    load_script,
    report_wait,
    resume_id,
    spawn_id,
    wait_result,
)

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
        self.set_plan(("T1",))
        self.run_dir = self.state_dir / load_script().repo_key(self.repo) / "demo"
        self.worktree = self.root / "repo.worktrees" / "demo-t1"

    extra_files = None  # worktree path -> text the worker also writes
    COVERS = {"S1": ["src/a/test_a.py::test_a"]}

    def report(self, task_id, **over):
        yaml_plan = self.plan.name.endswith(".plan.yaml")
        return json.dumps(
            dict(
                {
                    # A YAML plan's ticket reports contract 3 and maps its scenarios to test nodes.
                    "contract": 3 if yaml_plan else 1,
                    **({"covers": self.COVERS} if yaml_plan else {}),
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
        for name_, text in (self.extra_files or {}).items():
            step["write"].append([str(self.worktree / name_), text])
        return step

    def use_yaml_plan(self, paths=("src/a/",), oracle=False):
        """Replace the plan with an approved one-ticket plan (and, with `oracle`, an oracle placed
        under src/a/)."""
        evidence = {"human": "confirm"}
        if oracle:
            directory = self.plan.parent / "demo.oracle"
            directory.mkdir()
            (directory / "test_oracle.py.oracle").write_text("def test_o():\n    pass\n")
            evidence = {
                "check": "true",
                "baseline": "pass",
                "oracle": ["test_oracle.py.oracle"],
                "place": "src/a/",
            }
        data = {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "deliver",
            "spec": "docs/specs/demo.md",
            "outcome": ["S1"],
            "scenarios": [dict(id="S1", when="a", then="b", evidence=evidence)],
            "tickets": [
                dict(
                    id="T1",
                    delivers="first",
                    satisfies=["S1"],
                    paths=list(paths),
                    size="small",
                    difficulty="simple",
                    max_fix_rounds=3,
                )
            ],
        }
        self.plan.unlink()
        self.plan = self.plan.with_suffix(".yaml")
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False))
        # Approving a plan with a check runs its baseline and writes a receipt under the repo key: the alias the run
        # uses must exist before that directory does.
        self.alias_state(self.plan)
        out = io.StringIO()
        with (
            mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"}),
            mock.patch.dict(
                os.environ,
                {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "test-lead"},
            ),
            redirect_stdout(out),
            redirect_stderr(out),
        ):
            code = load_script().run_cli(
                [
                    "plan",
                    "approve",
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                ]
            )
        self.assertEqual(code, 0, out.getvalue())

    def first_findings(self):
        path = self.run_dir / "t1r1" / "findings.json"
        return json.loads(path.read_text(encoding="utf-8"))

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
    def test_S5_failing_verify_resumes_the_worker_in_a_new_task_directory(self):
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
            (findings["contract"], findings["source"], findings["attempt"]), (2, "verify", 1)
        )
        self.assertEqual(
            [(i["source"], i["check"]) for i in findings["items"]],
            [("verify", "covers")],
        )
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "S1: test node src/a/test_a.py::test_a does not exist",
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
        """orchestrator-engine/R29"""
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
        """orchestrator-engine/R30"""
        self.waits([self.refused("t1"), self.refused("t1r1"), self.refused("t1r2"), self.skipped()])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2)
        request = self.request()
        self.assertEqual((request["task"], request["event"]), ("t1r2", "gate"))
        self.assertIn("complete result has failed checks: pytest", request["reason"])
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 3)

    def test_S5_a_report_the_ledger_cannot_read_asks_the_lead_and_the_driver_exits_clean(self):
        """orchestrator-engine/R29 — (a report with no `remaining`, or a check with no outcome)"""
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
        self.assertTrue(self.ticket_status("T1").startswith("COMPLETE "))

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
            [*self.repeated(self.worker_done(report=self.report("t1", contract=4))), self.skipped()]
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
            self.assertIn("Write or repair that file", call["args"]["task"])
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("2 warning(s)", self.request()["reason"])
        self.assertNotIn(
            "reviewed_commit", self.task(), "no verdict was recorded from a missing file"
        )


REVIEWER = "demo-t1-review"


class SilentReviewerTests(GateFixture, unittest.TestCase):
    """R62: a reviewer whose turn ended is still a live reviewer. The driver waits for its complete under the
    assignment it was given, and only the timed idle watchdog asks the Lead."""

    def silent(self, runtime_state="idle", write=()):
        """A wait step: the reviewer's turn ended under its original assignment; no report."""
        return wait_result([assignment(REVIEWER, "working", runtime_state)], write=write)

    def review_file(self):
        return [[str(self.run_dir / "t1" / "review.json"), self.review()]]

    def reviewer_resumes(self):
        return [c for c in self.calls("resume") if c["args"].get("name") == REVIEWER]

    def payload_trouble(self):
        return [
            e
            for e in self.events("payload-warning", "contract-error")
            if e.get("where") == "review"
        ]

    def gates(self):
        return [e["gate"][1] for e in self.events("state") if e["task"] == "t1"]

    def test_S1_a_reviewer_that_ends_its_turn_idle_then_reports_complete_is_accepted(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S1"""
        self.waits(
            [
                self.worker_done(tests=True),
                self.silent("idle"),
                self.silent("done"),
                self.silent("idle"),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.reviewer_resumes(), [], "a silent reviewer was resumed")
        self.assertEqual(self.payload_trouble(), [])
        self.assertFalse((self.run_dir / "t1" / "request.json").exists(), "the Lead was asked")
        task = self.task()
        self.assertEqual(task["review"]["assignment_id"], "a-" + REVIEWER)
        self.assertIn("reviewed_commit", task)
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))

    def test_S2_a_review_file_on_disk_without_a_complete_report_is_not_a_verdict(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S2"""
        self.waits(
            [
                self.worker_done(tests=True),
                self.silent("idle", write=self.review_file()),
                self.silent("done"),
                self.silent("idle"),
                self.silent("idle"),
                self.silent("idle"),
                self.skipped(),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertNotIn("reviewed", self.gates(), "the gate left reviewing without a report")
        self.assertNotIn("reviewed_commit", self.task())
        self.assertEqual(self.request()["event"], "idle")

    def test_S3_idle_observations_between_ticks_consume_no_ticks_and_raise_no_request(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S3"""
        spec = load_script()
        self.waits(
            [self.worker_done(tests=True)] + [self.silent("idle")] * 8 + [self.reviewer_done()]
        )
        with mock.patch.object(spec.Driver, "clock", staticmethod(lambda: 0)):
            self.run_driver("--idle-minutes", "10")
        self.assertEqual([e for e in self.events("idle") if e.get("unit")], [])
        self.assertFalse((self.run_dir / "t1" / "request.json").exists())
        self.assertEqual(self.reviewer_resumes(), [])
        self.assertEqual(self.task()["gate"], "done")

    def test_S4_a_reviewer_that_never_reports_is_asked_about_once_by_the_idle_watchdog(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S4"""
        spec = load_script()
        moments = itertools.count(0, 600)  # the init reading, then ten minutes per tick
        self.waits([self.worker_done(tests=True)] + [self.silent("idle")] * 6 + [self.skipped()])
        with mock.patch.object(spec.Driver, "clock", staticmethod(lambda: next(moments))):
            self.run_driver("--idle-minutes", "10")
        self.assertEqual(self.reviewer_resumes(), [], "a warning preceded the request")
        self.assertEqual(self.payload_trouble(), [], "a payload error preceded the request")
        request = self.request()
        self.assertEqual(request["event"], "idle")
        reason = request["reason"]
        self.assertIn(REVIEWER, reason)
        self.assertIn(str(self.run_dir / "t1" / "review.json"), reason)
        self.assertIn("20 minutes", reason)
        self.assertIn("decide %s t1 retry | answer | skip" % self.plan, reason)
        self.assertEqual(len(self.wake_lines("decision")), 1)

    def test_S9_a_missing_review_is_recovered_with_text_that_does_not_claim_a_read_report(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S9"""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(write=[])),
                self.skipped(),
            ]
        )
        self.run_driver()
        warned = self.reviewer_resumes()
        self.assertEqual(len(warned), 2, "the R18 cap still applies")
        path = str(self.run_dir / "t1" / "review.json")
        for call in warned:
            text = call["args"]["task"]
            self.assertIn(path, text)
            self.assertIn("Write or repair that file", text)
            self.assertIn("report complete under this assignment", text)
            self.assertNotIn("reached the driver", text)
        self.assertEqual(self.request()["event"], "review")

    def test_S10_a_reviewer_the_runtime_reports_failed_goes_straight_to_the_lead(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S10"""
        self.states({REVIEWER: "exited"})
        self.waits(
            [
                self.worker_done(tests=True),
                wait_result([assignment(REVIEWER, "failed")]),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.reviewer_resumes(), [], "a failed reviewer cannot write the file")
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("no agent left to write it", self.request()["reason"])

    def test_S10_a_restart_orphaned_reviewer_with_a_valid_review_is_still_accepted(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S10"""
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(self.task()["gate"], "reviewing")
        self.gone(REVIEWER)
        head = self.git(self.worktree, "rev-parse", "HEAD")
        (self.run_dir / "t1" / "review.json").write_text(
            self.review().replace("@HEAD", head), encoding="utf-8"
        )
        self.waits([wait_result()])
        self.run_driver()
        self.assertEqual(self.reviewer_resumes(), [])
        self.assertIn("reviewed_commit", self.task())

    def test_S10_a_restart_orphaned_reviewer_without_a_review_goes_straight_to_the_lead(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S10"""
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.gone(REVIEWER)
        self.waits([wait_result(), self.skipped()])
        self.run_driver()
        self.assertEqual(self.reviewer_resumes(), [])
        self.assertEqual(self.request()["event"], "review")

    def test_S11_a_reviewer_the_runtime_reports_unreported_or_error_is_handled_at_once(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S11 — as before, not deferred to the idle watchdog."""
        for runtime_state in ("unreported", "error"):
            with self.subTest(runtime_state=runtime_state):
                self.setUp()
                recovered = report_wait(REVIEWER, "complete", id=resume_id(REVIEWER, 1))
                recovered["write"] = self.review_file()
                self.waits([self.worker_done(tests=True), self.silent(runtime_state), recovered])
                self.run_driver()  # the default idle watchdog could not have fired
                self.assertEqual(len(self.reviewer_resumes()), 1, "no recovery at once")
                self.assertEqual(len(self.events("payload-warning")), 1)
                self.assertEqual(self.task()["gate"], "done")

    def test_S12_the_reviewer_brief_allows_background_jobs_and_names_the_report_order(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S12"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        brief = (self.run_dir / "t1-review" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "background jobs",
            "Ending a turn does not finish the review",
            "after the jobs it needs finish",
            "report complete with the assignment id",
        ):
            self.assertIn(needle, brief)


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
        self.assertIn("Write or repair that file", warned[0]["task"])
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


class VerifyGateTests(GateFixture, unittest.TestCase):
    """A YAML plan's ticket is gated by verify's invariants, not by converge."""

    def setUp(self):
        super().setUp()
        self.use_yaml_plan()

    def found(self):
        findings = self.first_findings()
        self.assertEqual((findings["contract"], findings["source"]), (2, "verify"))
        return [(i["check"], i["excerpt"]) for i in findings["items"]]

    def test_S9_a_report_that_maps_every_satisfied_scenario_to_changed_nodes_goes_to_review(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.calls("resume"), [])

    def test_S9_a_node_that_does_not_exist_is_a_fix_round_naming_scenario_node_and_reason(self):
        covers = {"S1": ["src/a/test_a.py::test_nope"]}
        report = self.report("t1", covers=covers)
        self.waits([self.worker_done(tests=True, report=report)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [
                (
                    "covers",
                    "S1: test node src/a/test_a.py::test_nope does not exist"
                    " (no test_nope in src/a/test_a.py)",
                )
            ],
        )
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        self.assertEqual(self.spawned(), ["demo-t1"])
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("test_nope", task_md)

    def test_S9_a_satisfied_scenario_left_unmapped_is_a_fix_round(self):
        report = self.report("t1", covers={})
        self.waits([self.worker_done(tests=True, report=report)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [("covers", "S1: no test node in covers; map the scenario to the tests that prove it")],
        )

    def test_S9_a_report_that_is_not_contract_3_is_refused_by_name(self):
        report = json.loads(self.report("t1", contract=2))
        del report["covers"]
        self.waits([self.worker_done(tests=True, report=json.dumps(report))])
        self.run_driver()
        found = self.found()
        self.assertEqual([c for c, _ in found], ["covers"])
        self.assertIn("report is contract 2, not 3", found[0][1])

    def test_S10_a_file_outside_the_ticket_paths_is_a_fix_round_naming_file_and_scope(self):
        self.extra_files = {"src/z/extra.py": "x = 1\n"}
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [("scope", "src/z/extra.py is outside the ticket paths (src/a/)")],
        )
        self.assertEqual(self.spawned(), ["demo-t1"])

    def test_S10_touching_the_plan_is_an_integrity_failure_not_a_scope_one(self):
        self.extra_files = {"docs/plans/demo.plan.yaml": "schema: plan/1\n"}
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [
                (
                    "integrity",
                    "docs/plans/demo.plan.yaml is the plan; a ticket never edits its contract",
                )
            ],
        )

    def test_S10_an_oracle_place_is_integrity(self):
        self.use_yaml_plan(oracle=True)
        self.extra_files = {"src/a/test_oracle.py": "def test_o(): pass\n"}
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(
            [c for c, _ in self.found()],
            ["integrity"],
        )
        self.assertIn("src/a/test_oracle.py is an oracle place", self.found()[0][1])


class IsolationTests(GateFixture, unittest.TestCase):
    def test_D27_a_cli_approve_without_a_state_dir_writes_under_the_test_home(self):
        """yaml-eec/D27"""
        plan = self.approved_yaml_plan()
        real = Path.home() / ".local" / "state" / "orchestrate" / "approvals"
        before = sorted(real.iterdir()) if real.is_dir() else []
        result = self.cli("plan", "approve", str(plan), "--repo", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        isolated = self.root / "home" / ".local" / "state" / "orchestrate" / "approvals"
        self.assertTrue(isolated.is_dir() and any(isolated.iterdir()))
        self.assertEqual(sorted(real.iterdir()) if real.is_dir() else [], before)
