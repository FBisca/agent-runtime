"""The pull request and CI loop: a draft PR polled during review, `gh pr ready` after the last pass, checks and merge
polled without blocking, slots freed while a PR waits for merge, branch updates and conflicts, early merges, idle ticks."""

import itertools
import json
import unittest
from unittest import mock

import fake_gh
import pytest
from fixtures import (
    assignment,
    load_script,
    plan_text,
    report_wait,
    resume_id,
    spawn_id,
    wait_result,
)
from test_gates import MAJOR, GateFixture

pytestmark = pytest.mark.integration

PR = "https://github.com/o/r/pull/7"
PR9 = "https://github.com/o/r/pull/9"


class PrFixture(GateFixture):
    def flip(self, step, name, content):
        """The Lead's world changes during this wait step: a fake gh file is rewritten."""
        step.setdefault("write", []).append([str(self.home / name), json.dumps(content)])
        return step

    def working(self, name="demo-t1", runtime_state=None, id=None):
        return wait_result([assignment(name, "working", runtime_state, id=id)])

    def rounds_of(self, source):
        return [e for e in self.events("round") if e.get("source") == source]

    def unit_spawned(self):
        """The agents of this unit: the archive and the gaps review are other units' business."""
        return [name for name in self.spawned() if name.startswith("demo-t1")]


class DraftTests(PrFixture, unittest.TestCase):
    def test_S20_the_first_report_leaves_a_draft_pr_and_its_checks_are_polled_during_review(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S20"""
        self.born_ready()  # the worker opened it ready for review: the driver turns it back into a draft
        self.prs(["OPEN", "MERGED"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.working("demo-t1-review", id=spawn_id("demo-t1-review")),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.gh_calls("pr", "ready")[0], ["pr", "ready", PR, "--undo"])
        self.assertIn("draft", (self.run_dir / "t1" / "task.md").read_text(encoding="utf-8"))
        during = [e for e in self.events("ci") if e["gate"] == "reviewing"]
        self.assertTrue(during, "no checks were polled while the reviewer worked")
        self.assertEqual(self.task()["status"], "COMPLETE")

    def test_S20_a_pr_that_is_already_a_draft_is_left_alone(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S20"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertNotIn(["pr", "ready", PR, "--undo"], self.gh_calls("pr", "ready"))

    def test_S20_gh_pr_ready_runs_after_the_last_review_passes_with_green_checks(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S20"""
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(self.review(verdict="block", major=[MAJOR])),
                self.worker_done("t1r1"),
                self.reviewer_done_again(),
            ]
        )
        self.run_driver()
        ready = [c for c in self.gh_log() if c["argv"][:2] == ["pr", "ready"]]
        self.assertEqual([c["argv"] for c in ready], [["pr", "ready", PR]])
        states = [
            json.loads(line) for line in ready[0]["events"] if json.loads(line)["kind"] == "state"
        ]
        self.assertEqual(
            states[-1]["gate"][1], "ready-for-ci"
        )  # the review passed and the checks were green
        self.assertEqual([e["source"] for e in self.events("round")], ["review"])
        self.assertEqual(self.task("t1r1")["status"], "COMPLETE")

    def test_S20_a_unit_that_never_passes_review_is_never_marked_ready(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S20"""
        block = self.review(verdict="block", major=[MAJOR])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block),
                self.worker_done("t1r1"),
                self.reviewer_done_again(block),
                self.worker_done("t1r2"),
                self.reviewer_done_again(block, 3),
                self.skipped(),
            ]
        )
        self.run_driver("--max-fix-rounds", "2")
        self.assertEqual(self.gh_calls("pr", "ready"), [])

    def reviewer_done_again(self, review=None, nth=2):
        """The same reviewer, resumed for a delta re-review; its file is the next review.json."""
        name = "demo-t1-review"
        step = report_wait(name, "complete", id=resume_id(name, nth - 1))
        path = "review.json" if nth == 1 else "review.%d.json" % nth
        step["write"] = [[str(self.run_dir / "t1" / path), review or self.review()]]
        return step


class ChecksTests(PrFixture, unittest.TestCase):
    def test_S21_no_checks_yet_and_running_checks_are_polled_again_without_a_fix_round(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S21"""
        self.checks(["none", "pending", "none", "pending", "green"])
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.working(), self.working()]
        )
        self.run_driver()
        polls = self.gh_calls("pr", "checks")
        self.assertGreaterEqual(len(polls), 5)
        self.assertTrue(all(p[3:] == ["--json", "name,bucket,link,description"] for p in polls))
        self.assertEqual(self.events("round"), [])
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual([e["state"] for e in self.events("ci")][:3], ["none", "pending", "none"])
        self.assertEqual(self.task()["status"], "COMPLETE")

    def test_S21_a_failed_check_after_the_review_is_a_ci_fix_round_with_the_failed_log(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S21"""
        self.checks([("fail", "unit tests"), "green"])
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.worker_done("t1r1"), again]
        )
        self.run_driver()
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual((findings["source"], findings["attempt"]), ("ci", 1))
        item = findings["items"][0]
        self.assertEqual((item["check"], item["kind"], item["exit"]), ("unit tests", "blocking", 1))
        self.assertIn("FAILED STEP LOG TAIL for run 77", item["excerpt"])
        self.assertEqual(self.gh_calls("run", "view")[0], ["run", "view", "77", "--log-failed"])

    def test_S10_several_failing_checks_with_long_logs_carry_only_their_own_capped_excerpt(self):
        """orchestrator-engine/R2: merged-unit-completes/S10"""

        def job(name, marker):
            return "\n".join(
                "%s\tRun step\t2026-11-10T10:00:00Z %s line %d" % (name, marker, number)
                for number in range(4000)
            )

        run_log = job("unit tests", "UNIT") + "\n" + job("lint", "LINT")
        real = fake_gh.process

        def answer(argv, timeout=None):
            if argv[:2] == ["run", "view"]:
                return fake_gh.Completed(argv, 0, run_log)
            return real(argv, timeout=timeout)

        self.checks([("fail", "unit tests", "lint"), "green"])
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.worker_done("t1r1"), again]
        )
        with mock.patch.object(fake_gh, "process", side_effect=answer):
            self.run_driver()
        findings_path = self.run_dir / "t1r1" / "findings.json"
        self.assertLess(findings_path.stat().st_size, 64 * 1024)
        items = {
            item["check"]: item
            for item in json.loads(findings_path.read_text(encoding="utf-8"))["items"]
        }
        self.assertEqual(set(items), {"unit tests", "lint"})
        self.assertNotEqual(items["unit tests"]["log_path"], items["lint"]["log_path"])
        for check, marker, other in (("unit tests", "UNIT", "LINT"), ("lint", "LINT", "UNIT")):
            item = items[check]
            self.assertIn(marker + " line 3999", item["excerpt"])
            self.assertNotIn(other, item["excerpt"])
            self.assertLess(len(item["excerpt"]), 4096)
            with open(item["log_path"], encoding="utf-8") as log:
                full = log.read()
            self.assertIn(marker + " line 0\n", full)
            self.assertIn(marker + " line 3999", full)
            self.assertNotIn(other, full)

    def test_S21_a_second_unit_goes_on_while_the_first_waits_for_its_checks(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S21"""
        self.plan.write_text(plan_text(rows=("T1", "T3")), encoding="utf-8")
        self.checks(["pending"], pr=7)
        self.checks(["green"], pr=9)
        first = self.worker_done(tests=True)
        third = self.worker_done("t3", name="demo-t3", report=self.report("t3", pr=PR9))
        third["write"].append(
            [
                str(self.root / "repo.worktrees" / "demo-t3" / "src" / "c" / "test_c.py"),
                'def test_c():\n    """S3"""\n',
            ]
        )
        review3 = report_wait("demo-t3-review", "complete")
        review3["write"] = [[str(self.run_dir / "t3" / "review.json"), self.review()]]
        release = self.flip(self.working(), "checks-7.json", [[0, json.dumps(self.GREEN), ""]])
        self.waits([first, third, self.reviewer_done(), review3, release])
        self.run_driver()
        ready = self.wake_lines("pr-ready")
        self.assertEqual([line.split(":")[0] for line in ready], ["pr-ready t3", "pr-ready t1"])
        self.assertEqual(self.events("round"), [])


class SlotTests(PrFixture, unittest.TestCase):
    def test_S22_a_unit_waiting_for_merge_does_not_count_toward_max_workers(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S22"""
        self.plan.write_text(plan_text(rows=("T1", "T3")), encoding="utf-8")
        self.prs(["OPEN"])
        blocked = report_wait("demo-t3", "blocked", text="stop here")
        self.flip(blocked, "prs.json", ["MERGED"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(),
                blocked,
                self.decide_step("t3", "skip"),
            ]
        )
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review", "demo-t3"])
        third = next(c for c in self.calls("spawn") if c["args"]["name"] == "demo-t3")
        trail = [json.loads(line) for line in third["events"]]
        awaiting = [
            e
            for e in trail
            if e["kind"] == "state" and e["task"] == "t1" and e["gate"][1] == "awaiting-merge"
        ]
        done = [
            e
            for e in trail
            if e["kind"] == "state" and e["task"] == "t1" and e["gate"][1] in ("cleanup", "done")
        ]
        self.assertTrue(
            awaiting and not done,
            "t3 was dispatched before t1 waited for its merge, or after it merged",
        )

    def test_S22_a_unit_still_in_review_holds_its_slot(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S22"""
        self.plan.write_text(plan_text(rows=("T1", "T3")), encoding="utf-8")
        self.waits(
            [
                self.worker_done(tests=True),
                self.working("demo-t1-review", id=spawn_id("demo-t1-review")),
            ]
        )
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review"])


class BranchTests(PrFixture, unittest.TestCase):
    BEHIND = {"state": "OPEN", "mergeStateStatus": "BEHIND", "mergeable": "MERGEABLE"}

    def test_S23_a_pr_behind_base_is_updated_and_needs_no_review(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S23"""
        self.prs([self.BEHIND, "OPEN", "MERGED"])
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.gh_calls("pr", "update-branch"), [["pr", "update-branch", PR]])
        self.assertEqual(self.unit_spawned(), ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.events("round"), [])
        self.assertEqual([e["outcome"] for e in self.events("update-branch")], ["updated"])
        self.assertEqual(self.task()["status"], "COMPLETE")

    def test_S23_a_conflict_gives_a_fresh_worker_a_rebase_task_then_a_delta_re_review(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S23"""
        self.updates(
            [[1, "GraphQL: Cannot update PR branch due to conflicts (updatePullRequestBranch)"]]
        )
        self.prs([self.BEHIND, "OPEN", "OPEN", "MERGED"])
        rebased = report_wait("demo-t1", "complete", id=spawn_id("demo-t1", 2))
        rebased["write"] = [[str(self.run_dir / "t1r1" / "report.json"), self.report("t1r1")]]
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        self.waits([self.worker_done(tests=True), self.reviewer_done(), rebased, again])
        self.run_driver()
        self.assertEqual(
            self.unit_spawned(), ["demo-t1", "demo-t1-review", "demo-t1"]
        )  # the rebase worker is fresh
        self.assertEqual(
            [r["args"]["name"] for r in self.calls("resume")], ["demo-t1-review"]
        )  # the reviewer is the same one
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(findings["source"], "conflict")
        self.assertIn("conflicts", findings["items"][0]["excerpt"])
        brief = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("rebase", brief)
        self.assertEqual(
            [e["source"] for e in self.events("round")], ["conflict"]
        )  # the conflict counts as a round
        self.assertEqual(self.ledger()["rounds"]["t1"], 1)
        re_review = (self.run_dir / "t1-review2" / "task.md").read_text(encoding="utf-8")
        self.assertIn("This is a re-review", re_review)
        self.assertEqual(self.task("t1r1")["status"], "COMPLETE")

    def test_S23_a_conflict_past_the_fix_round_cap_goes_to_the_lead(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S23"""
        self.updates([[1, "Cannot update PR branch due to conflicts"]])
        self.prs([self.BEHIND])
        self.waits([self.worker_done(tests=True), self.reviewer_done(), self.skipped()])
        self.run_driver("--max-fix-rounds", "0")
        self.assertEqual(self.request()["event"], "gate")
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review"])


class EarlyMergeTests(PrFixture, unittest.TestCase):
    def test_S24_a_merge_during_a_fix_round_completes_the_unit_and_defers_its_open_findings(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S24"""
        merged = self.flip(self.working(id=resume_id("demo-t1")), "view-early.json", ["MERGED"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(self.review(verdict="block", major=[MAJOR])),
                merged,
            ]
        )
        self.run_driver()
        self.assertEqual(self.task("t1r1")["status"], "COMPLETE")
        self.assertIn("COMPLETE", self.plan.read_text(encoding="utf-8"))
        deferred = (self.run_dir / "deferred.md").read_text(encoding="utf-8")
        self.assertIn("merged-early", deferred)
        self.assertIn("drops the last row", deferred)
        self.assertEqual([e["unit"] for e in self.events("merged-early")], ["t1"])
        self.assertEqual(
            sorted(c["args"]["name"] for c in self.calls("stop")), ["demo-t1", "demo-t1-review"]
        )
        self.assertEqual(self.gh_calls("pr", "ready"), [])

    def test_S24_a_merge_while_the_reviewer_works_completes_the_unit_and_stops_both_panes(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S24"""
        merged = self.flip(
            self.working("demo-t1-review", id=spawn_id("demo-t1-review")),
            "view-early.json",
            ["MERGED"],
        )
        self.waits([self.worker_done(tests=True), merged])
        self.run_driver()
        self.assertEqual(self.task()["status"], "COMPLETE")
        self.assertEqual(
            sorted(c["args"]["name"] for c in self.calls("stop")), ["demo-t1", "demo-t1-review"]
        )
        self.assertFalse((self.run_dir / "deferred.md").exists())  # nothing was open
        self.assertEqual(self.gh_calls("pr", "update-branch"), [])

    def test_S7_a_merge_on_a_drive_with_a_named_gate_completes_the_unit_as_a_merged_unverified_one(
        self,
    ):
        """orchestrator-engine/R2: merged-unit-completes/S7"""
        self.plan.write_text(plan_text(rows=("T1", "T2")), encoding="utf-8")
        merged = self.flip(
            self.working("demo-t1-review", id=spawn_id("demo-t1-review")),
            "view-early.json",
            ["MERGED"],
        )
        self.waits([self.worker_done(tests=True), merged])
        self.run_driver("--gate", "scripts/checks.sh")
        self.assertEqual(self.task()["status"], "COMPLETE")
        self.assertIn("| COMPLETE 2026-", self.plan.read_text(encoding="utf-8"))
        deferred = (self.run_dir / "deferred.md").read_text(encoding="utf-8")
        line = [x for x in deferred.splitlines() if "[merged-unverified]" in x]
        self.assertEqual(len(line), 1, deferred)
        self.assertTrue(line[0].startswith("- [merged-unverified] t1: "), line[0])
        self.assertIn("scripts/checks.sh", line[0])
        self.assertFalse((self.run_dir / "t1" / "request.json").exists())
        self.assertIn("demo-t2", self.spawned())

    def test_S24_a_merge_while_a_request_is_open_resolves_it_and_completes_the_unit(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S24"""
        merged = self.flip(self.working(), "view-early.json", ["MERGED"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(self.review(verdict="block", major=[MAJOR])),
                self.worker_done("t1r1"),
                merged,
            ]
        )
        self.run_driver("--max-fix-rounds", "0")
        self.assertEqual(self.task("t1")["status"], "COMPLETE")
        self.assertEqual(
            json.loads((self.run_dir / "t1" / "request.json").read_text(encoding="utf-8"))[
                "status"
            ],
            "resolved",
        )
        self.assertIn("merged-early", (self.run_dir / "deferred.md").read_text(encoding="utf-8"))


class IdleTests(PrFixture, unittest.TestCase):
    def idle_events(self):
        return [e for e in self.events("idle") if e.get("unit")]

    def test_S6_a_unit_whose_agent_shows_nothing_new_since_the_last_tick_is_logged_idle(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S6"""
        self.waits([self.working(), self.working()])
        self.run_driver("--idle-minutes", "0")
        rows = self.idle_events()
        self.assertEqual([(e["unit"], e["waiting"]) for e in rows], [("t1", "agent")])
        self.assertEqual(self.wake_lines("decision"), [])

    def test_S6_the_same_unit_idle_at_two_consecutive_ticks_writes_a_decision_wake(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S6"""
        self.waits([self.working(), self.working(), self.working(), self.decide_step("t1", "skip")])
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["ticks"] for e in self.idle_events()], [1, 2])
        lines = self.wake_lines("decision")
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("decision t1: "), lines[0])
        self.assertIn("idle", lines[0])
        self.assertEqual(self.request()["event"], "idle")

    def test_S8_a_unit_whose_agent_the_runtime_reports_working_is_left_alone(self):
        """orchestrator-engine/R2: drive-restart-recovery/S8"""
        self.waits([self.working(runtime_state="working")] * 5)
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.idle_events(), [])
        self.assertEqual(self.wake_lines("decision"), [])

    def test_S8_a_unit_working_past_working_minutes_is_asked_with_how_long(self):
        """orchestrator-engine/R2: drive-restart-recovery/S8"""
        self.waits([self.working(runtime_state="working")] * 4 + [self.decide_step("t1", "skip")])
        self.run_driver("--idle-minutes", "0", "--working-minutes", "0")
        self.assertEqual(self.request()["event"], "idle")
        self.assertIn("working for", self.wake_lines("decision")[0])

    def test_S8_a_working_agent_is_left_alone_until_working_minutes_have_passed(self):
        """orchestrator-engine/R2: drive-restart-recovery/S8"""
        spec = load_script()
        moments = itertools.count(0, 600)  # the init reading, then ten minutes per tick
        self.waits([self.working(runtime_state="working")] * 6 + [self.decide_step("t1", "skip")])
        with mock.patch.object(spec.Driver, "clock", staticmethod(lambda: next(moments))):
            self.run_driver("--idle-minutes", "10", "--working-minutes", "30")
        self.assertTrue(all(e["ticks"] >= 1 for e in self.idle_events()))
        text = self.wake_lines("decision")[0]
        self.assertIn("working for 50 minutes", text)
        self.assertEqual([e["ticks"] for e in self.idle_events()], [1, 2])

    def test_S8a_an_agent_not_reported_working_is_asked_at_two_ticks_as_today(self):
        """orchestrator-engine/R2: drive-restart-recovery/S8a"""
        self.waits([self.working()] * 3 + [self.decide_step("t1", "skip")])
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["ticks"] for e in self.idle_events()], [1, 2])
        self.assertEqual(self.request()["event"], "idle")

    def test_S6_a_new_state_between_ticks_resets_the_count(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S6"""
        self.waits(
            [
                self.working(),
                self.working(),
                self.working(runtime_state="busy"),
                self.working(runtime_state="busy"),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["ticks"] for e in self.idle_events()], [1, 1])
        self.assertEqual(self.wake_lines("decision"), [])

    def test_S6_ci_that_shows_no_progress_is_idle_and_wakes_the_lead_at_two_ticks(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S6"""
        self.checks(["pending"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.working(),
                self.working(),
                self.working(),
                self.decide_step("t1", "skip"),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        rows = self.idle_events()
        self.assertEqual([(e["waiting"], e["ticks"]) for e in rows], [("ci", 1), ("ci", 2)])
        self.assertEqual(len(self.wake_lines("decision")), 1)

    def test_S8_a_unit_waiting_for_its_merge_is_never_idle(self):
        """orchestrator-engine/R15: orchestrator-lead-alerts/S8"""
        self.prs(["OPEN"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.working(),
                self.working(),
                self.working(),
                self.flip(self.working(), "prs.json", ["MERGED"]),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        gates = [e["gate"][1] for e in self.events("state") if e["task"] == "t1"]
        self.assertIn("awaiting-merge", gates)
        self.assertEqual(self.idle_events(), [])
        self.assertEqual(self.wake_lines("decision"), [])
        self.assertEqual(self.task("t1")["status"], "COMPLETE")

    def test_S6_the_default_tick_is_ten_minutes(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S6"""
        self.waits([self.working(), self.working(), self.working()])
        self.run_driver()
        self.assertEqual(self.idle_events(), [])


if __name__ == "__main__":
    unittest.main()
