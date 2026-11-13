"""One reviewer per unit: a fix round of any source is re-reviewed as a delta by the same reviewer, a blocking item
outside the delta is deferred, and the unit's panes stop when it merges or pauses."""

import json
import unittest

import pytest
from fixtures import report_wait, resume_id
from test_gates import BLOCKING, GateFixture

pytestmark = pytest.mark.integration

OFF_SCOPE = dict(BLOCKING, id="B2", file="src/zzz/other.py", summary="unrelated code is wrong")


class ReuseFixture(GateFixture):
    def git_commit(self, message="fix"):
        wt = str(self.worktree)
        git = ["git", "-C", wt, "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        return [git + ["add", "."], git + ["commit", "-q", "-m", message]]

    def first_block(self, *items):
        """Steps: the worker reports, the first review blocks with `items`."""
        return [
            self.worker_done(tests=True),
            self.reviewer_done(self.review2(verdict="block", items=list(items) or [BLOCKING])),
        ]

    def fix_report(self, task_id="t1r1", nth=1, commit=True):
        """The worker's fix-round report; the fix is a committed change to src/a/x.py."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", nth))
        step["write"] = [
            [str(self.run_dir / task_id / "report.json"), self.report(task_id)],
            [str(self.worktree / "src" / "a" / "x.py"), "fixed = True\n"],
        ]
        if commit:
            step["run"] = self.git_commit()
        return step

    def re_review(self, review=None, index=2, nth=1):
        """The unit's reviewer, resumed for the `nth` time, writes review.<index>.json."""
        step = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review", nth))
        name = "review.json" if index == 1 else "review.%d.json" % index
        step["write"] = [[str(self.run_dir / "t1" / name), review or self.review2()]]
        return step

    def blocking(self):
        return BLOCKING

    def states_of(self, task_id):
        gates = [e["gate"] for e in self.run_events("state") if e["task"] == task_id]
        return [new for old, new in gates if new and new != old]


class ReviewerReuseTests(ReuseFixture, unittest.TestCase):
    def test_S8_a_fix_round_resumes_the_same_reviewer_with_the_delta(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S8"""
        self.waits(self.first_block() + [self.fix_report(), self.re_review()])
        self.run_driver()
        base = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        spawns = {c["args"]["name"]: c["args"] for c in self.calls("spawn")}
        self.assertEqual(spawns["demo-t1"]["agent"], "implementer")
        self.assertNotIn("agent", spawns["demo-t1-review"])
        self.assertEqual(self.spawned().count("demo-t1-review"), 1)
        resumes = [(c["args"]["name"], c["args"]["task"]) for c in self.calls("resume")]
        self.assertEqual([n for n, _ in resumes], ["demo-t1", "demo-t1-review"])
        task_md = self.run_dir / "t1-review2" / "task.md"
        self.assertEqual(resumes[1][1], "Read %s and follow it." % task_md)
        text = task_md.read_text(encoding="utf-8")
        self.assertIn("%s..HEAD" % base, text)  # the commit the first review covered, up to HEAD
        self.assertIn(str(self.run_dir / "t1r1" / "findings.json"), text)
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S8_a_converge_fix_round_is_reviewed_by_a_reviewer_spawned_once(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S8"""
        self.waits(
            [
                self.worker_done(),
                self.worker_done("t1r1", tests=True),
                self.reviewer_done(self.review2()),
            ]
        )
        self.run_driver()
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.spawned().count("demo-t1-review"), 1)
        self.assertEqual(self.states_of("t1r1")[:3], ["reported", "converged", "reviewing"])
        self.assertEqual(set(self.checks_log()), {"https://github.com/o/r/pull/7"})

    def test_S8_a_ci_fix_round_is_re_reviewed_before_ci_runs_again(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S8"""
        self.checks([("fail", "unit tests"), "green"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.fix_report(),
                self.re_review(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.spawned().count("demo-t1-review"), 1)
        self.assertEqual(
            [c["args"]["name"] for c in self.calls("resume")], ["demo-t1", "demo-t1-review"]
        )
        self.assertEqual(
            self.states_of("t1r1")[:4], ["reported", "converged", "reviewing", "reviewed"]
        )
        self.assertGreaterEqual(len(self.checks_log()), 2)
        self.assertIn(
            str(self.run_dir / "t1r1" / "findings.json"),
            (self.run_dir / "t1-review2" / "task.md").read_text(encoding="utf-8"),
        )

    def test_S8_a_blocking_re_review_keeps_the_unit_out_of_ci(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S8"""
        blocked = self.review2(verdict="block", items=[dict(BLOCKING, id="B3", file="src/a/x.py")])
        self.waits(self.first_block() + [self.fix_report(), self.re_review(blocked)])
        self.run_driver()
        self.assertNotIn("ready-for-ci", [e["gate"][1] for e in self.run_events("state")])
        self.assertEqual(self.task("t1r1")["gate"], "superseded")
        self.assertTrue((self.run_dir / "t1r2" / "findings.json").exists())


class ScopeFilterTests(ReuseFixture, unittest.TestCase):
    def blocked_again(self, *items):
        return self.review2(verdict="block", items=list(items))

    def test_S9_a_blocking_item_outside_the_delta_with_no_earlier_finding_is_deferred(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S9"""
        self.waits(
            self.first_block() + [self.fix_report(), self.re_review(self.blocked_again(OFF_SCOPE))]
        )
        self.run_driver()
        scopes = self.run_events("scope")
        self.assertEqual(
            [(e["unit"], e["id"], e["file"]) for e in scopes], [("t1", "B2", "src/zzz/other.py")]
        )
        self.assertIn("B2", (self.run_dir / "deferred.md").read_text(encoding="utf-8"))
        self.assertIn(
            "unrelated code is wrong", (self.run_dir / "deferred.md").read_text(encoding="utf-8")
        )
        self.assertFalse((self.run_dir / "t1r2").exists())  # no further fix round
        self.assertIn(
            "ready-for-ci", [e["gate"][1] for e in self.run_events("state")]
        )  # the gate did not hold
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S9_a_blocking_item_in_a_changed_file_still_gates(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S9"""
        inside = dict(OFF_SCOPE, id="B4", file="src/a/x.py")
        self.waits(
            self.first_block() + [self.fix_report(), self.re_review(self.blocked_again(inside))]
        )
        self.run_driver()
        self.assertEqual(self.run_events("scope"), [])
        self.assertTrue((self.run_dir / "t1r2" / "findings.json").exists())
        self.assertNotIn("ready-for-ci", [e["gate"][1] for e in self.run_events("state")])

    def test_S9_a_blocking_item_that_references_an_earlier_finding_still_gates(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S9"""
        again = dict(OFF_SCOPE, id="B5", finding="B1")
        self.waits(
            self.first_block() + [self.fix_report(), self.re_review(self.blocked_again(again))]
        )
        self.run_driver()
        self.assertEqual(self.run_events("scope"), [])
        self.assertEqual(
            [
                i["id"]
                for i in json.loads((self.run_dir / "t1r2" / "findings.json").read_text())["items"]
            ],
            ["B5"],
        )

    def test_S9_only_the_out_of_scope_item_is_dropped_from_a_mixed_re_review(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S9"""
        inside = dict(OFF_SCOPE, id="B4", file="src/a/x.py")
        self.waits(
            self.first_block()
            + [self.fix_report(), self.re_review(self.blocked_again(OFF_SCOPE, inside))]
        )
        self.run_driver()
        items = json.loads((self.run_dir / "t1r2" / "findings.json").read_text())["items"]
        self.assertEqual([i["id"] for i in items], ["B4"])
        self.assertEqual([e["id"] for e in self.run_events("scope")], ["B2"])

    def test_S9_a_first_review_is_not_filtered(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S9"""
        self.waits(self.first_block(OFF_SCOPE))
        self.run_driver()
        self.assertEqual(self.run_events("scope"), [])
        self.assertTrue((self.run_dir / "t1r1" / "findings.json").exists())


class PaneStopTests(ReuseFixture, unittest.TestCase):
    def stopped(self):
        return [c["args"]["name"] for c in self.calls("stop")]

    def test_S11_a_merged_unit_stops_its_implementer_and_reviewer(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S11"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task()["status"], "COMPLETE")

    def test_S11_a_dirty_checkout_still_stops_both_panes(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S11"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertTrue(self.worktree.exists())
        self.assertIn("uncommitted", self.task()["cleanup"])
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])

    def test_S11_a_unit_the_lead_blocks_stops_its_implementer_and_reviewer(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S11"""
        broken = self.review2(
            verdict="pass", items=[BLOCKING]
        )  # breaks its contract: the unit pauses for the Lead
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(broken)),
                self.decide_step("t1", "mark blocked"),
            ]
        )
        self.run_driver()
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task()["gate"], "blocked")

    def test_S11_a_unit_waiting_for_the_lead_keeps_its_panes_for_the_decision(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S11"""
        broken = self.review2(verdict="pass", items=[BLOCKING])
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(broken)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")
        kinds = [e["kind"] for e in self.run_events("decision", "stop")]
        self.assertEqual(
            kinds[0], "decision"
        )  # nothing is stopped while the request is open; the skip that parks the unit does
        self.assertEqual(sorted(self.stopped()), ["demo-t1", "demo-t1-review"])

    def test_S11_a_pane_that_is_already_gone_is_not_stopped_again(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S11"""
        self.states({"demo-t1-review": "exited"})
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertNotIn("demo-t1-review", self.stopped())


if __name__ == "__main__":
    unittest.main()
