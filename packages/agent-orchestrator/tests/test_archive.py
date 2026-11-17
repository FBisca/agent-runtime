"""The archive unit the driver builds itself, and the run metrics."""

import json
import unittest

import fake_gh
import pytest
from agent_orchestrator.driver import ARCHIVE_REBUILDS
from fixtures import report_wait
from test_acceptance import SnapshotFixture
from test_gates import GateFixture

pytestmark = pytest.mark.integration

ARCHIVE = "demo-archive"
SPEC = """# Demo
Paths: src/a/

- **R1** WHEN a THEN b. (from demo/S1)
- **R2** WHEN c THEN d. (from demo/S2)
- **R3** WHEN e THEN f. (from demo/S3)
"""
TESTS = "".join('def test_%s():\n    """demo/R%d"""\n\n' % (n, i) for i, n in enumerate("abc", 1))


class ArchiveFixture(SnapshotFixture, GateFixture):
    HOLD = False  # the archive is what these tests are about

    def setUp(self):
        super().setUp()
        self.use_yaml_plan()
        self.archive_tree = self.root / "repo.worktrees" / ARCHIVE

    def ticket_merged(self):
        """Waits that take T1 through review, CI and the merge. The merge lands on origin's default branch and gh
        names it: a plan verifies there before it archives."""
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "t1")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        self.prs(
            [{"state": "MERGED", "mergeCommit": {"oid": self.git(self.repo, "rev-parse", "HEAD")}}]
        )
        return [self.worker_done(tests=True), self.reviewer_done()]


class ArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_S15_nothing_archives_while_an_agent_ticket_is_unfinished(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S15"""
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.decide_step("t1", "skip")]
        )
        self.prs(["CLOSED"])
        self.run_driver()
        self.assertNotIn(ARCHIVE, self.spawned())

    def test_S15_a_passing_archive_goes_to_ci_and_merge(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S15"""
        self.waits(self.ticket_merged())
        self.run_driver()
        task = self.task("archive")
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        self.assertEqual(
            set(self.checks_log()),
            {"https://github.com/o/r/pull/7", "https://github.com/o/r/pull/9"},
        )
        self.assertEqual(self.calls("resume"), [])
        self.assertNotIn(ARCHIVE, self.spawned())
        self.assertNotIn("demo-archive-review", self.spawned())


class ArchiveMergeStateTests(ArchiveFixture, unittest.TestCase):
    def conflict_once(self):
        """The archive's PR conflicts on the first read and answers as the fake always does after that."""
        real = self.gh_process
        first = [True]

        def conflicting(argv, timeout=None, cwd=None):
            if argv[:3] == ["pr", "view", "https://github.com/o/r/pull/9"] and first:
                first.clear()
                row = {"state": "OPEN", "isDraft": True, "mergeStateStatus": "DIRTY"}
                row.update(mergeable="CONFLICTING", mergeCommit=None)
                return fake_gh.Completed(argv, 0, json.dumps(row))
            return real(argv, timeout=timeout, cwd=cwd)

        self.gh_process = conflicting

    def test_S12_a_conflicting_archive_pr_is_rebuilt_from_the_current_base_first(self):
        """orchestrator-engine/R49: a conflict is a base that moved. The branch is built again from it, the change
        set applied there again, and nobody is asked; past the rebuilds the Lead is, as before."""
        self.conflict_once()
        self.waits(self.ticket_merged() + [{}])
        self.run_driver()
        self.assertEqual(len(self.run_events("archive-built")), 2)
        self.assertFalse((self.run_dir / "archive" / "request.json").exists())
        # The rebuild applied the change set to the base once, not a second time over its own first build.
        spec = self.git(self.origin, "show", "feat/demo-archive:docs/specs/demo.md")
        self.assertEqual(spec.count("### Requirement: Demo"), 1)
        self.assertEqual(self.task("archive")["status"], "COMPLETE")

    def test_S5_a_conflicting_archive_pr_past_the_rebuilds_asks_the_lead_and_spawns_no_worker(self):
        """orchestrator-engine/R34: the archive has no worker to rebase it."""
        real = self.gh_process

        def conflicting(argv, timeout=None, cwd=None):
            # only the archive's PR conflicts: the ticket's PR (7) answers as the fake always does
            if argv[:3] == ["pr", "view", "https://github.com/o/r/pull/9"]:
                row = {"state": "OPEN", "isDraft": True, "mergeStateStatus": "DIRTY"}
                row.update(mergeable="CONFLICTING", mergeCommit=None)
                return fake_gh.Completed(argv, 0, json.dumps(row))
            return real(argv, timeout=timeout, cwd=cwd)

        self.gh_process = conflicting
        self.waits(self.ticket_merged() + [{}, {}, {}, self.decide_step("archive", "skip")])
        self.run_driver()
        self.assertEqual(self.spawned().count(ARCHIVE), 0)
        self.assertEqual(len(self.run_events("archive-built")), 1 + ARCHIVE_REBUILDS)
        request = self.request("archive")
        self.assertEqual([o["action"] for o in request["menu"]], ["retry", "skip"])
        self.assertIn("conflicts with its base", request["reason"])
        self.assertEqual(self.task("archive")["gate"], "skipped")


class MetricsTests(ArchiveFixture, unittest.TestCase):
    def with_usage(self, step, usage):
        step["assignments"][0]["usage"] = usage
        return step

    def test_S16_a_finished_run_reports_route_rounds_escalations_and_usage(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S16"""
        worker = self.with_usage(
            self.worker_done(tests=True), {"input_tokens": 100, "output_tokens": 20}
        )
        # The reviewer reports too: a ticket total is a sum only when every assignment reported.
        reviewer = self.with_usage(self.reviewer_done(), {"input_tokens": 0, "output_tokens": 0})
        self.waits([worker, reviewer])
        result = self.run_driver()
        metrics = json.loads((self.run_dir / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["lead"]["model_turns"], 0)
        t1 = metrics["tickets"]["t1"]
        self.assertEqual(
            t1["route"],
            {"size": "small", "difficulty": "simple", "class": "small", "model": "haiku"},
        )
        self.assertEqual((t1["review_rounds"], t1["escalations"], t1["decider_calls"]), (0, 0, 0))
        self.assertEqual(t1["usage"], {"input_tokens": 100, "output_tokens": 20})
        self.assertEqual(metrics["tickets"]["archive"]["usage"], "unknown")
        self.assertIn("t1", result.stderr)
        self.assertIn("lead model turns: 0", result.stderr)

    def test_S16_a_run_with_a_request_counts_the_escalation_and_no_decider_calls(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S16"""
        blocked = report_wait("demo-t1", "blocked", "cannot reach the database")
        self.waits([blocked, self.decide_step("t1", "skip")])
        self.run_driver()
        t1 = json.loads((self.run_dir / "metrics.json").read_text(encoding="utf-8"))["tickets"][
            "t1"
        ]
        self.assertEqual((t1["decider_calls"], t1["escalations"], t1["usage"]), (0, 1, "unknown"))


class PromotionTests(ArchiveFixture, unittest.TestCase):
    """A YAML plan archives by promotion: the driver copies the plan files and writes the spec."""

    def archive_run(self):
        def build():
            self.waits(self.ticket_merged())
            self.run_driver()

        self.from_snapshot(self.snapshot_key("archive_run"), build)

    @pytest.mark.spec("Plan lifecycle")
    def test_S24_an_approved_plan_driven_to_the_end_is_archived_with_a_valid_digest(self):
        self.archive_run()
        argv = ["--repo", str(self.repo), "--state-dir", str(self.state_dir)]
        status = self.cli("status", str(self.plan), "--json", *argv)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["plan"]["state"], "ARCHIVED")
        check = self.cli("plan", "check", str(self.plan), *argv)
        self.assertEqual(check.returncode, 0, check.stderr + check.stdout)

    @pytest.mark.spec("Archive applies the change set")
    def test_S25_the_driver_copies_the_plan_file_onto_the_archive_branch(self):
        self.archive_run()
        # the worktree is gone after the merge: the pushed branch is what holds the copied plan
        copied = self.git(self.origin, "show", "feat/demo-archive:docs/plans/demo.plan.yaml")
        self.assertEqual(copied + "\n", self.plan.read_text(encoding="utf-8"))

    @pytest.mark.spec("Archive applies the change set")
    def test_S25_the_plan_is_archived_when_the_archive_pr_merges(self):
        self.archive_run()
        history = [h["state"] for h in self.ledger()["plan"]["history"]]
        self.assertEqual(history[-2:], ["ACCEPTED", "ARCHIVED"])

    @pytest.mark.spec("Plan lifecycle")
    def test_S23_a_plan_goes_approved_to_archived(self):
        self.archive_run()
        self.assertEqual(
            [h["state"] for h in self.ledger()["plan"]["history"]],
            ["APPROVED", "RUNNING", "ACCEPTED", "ARCHIVED"],
        )
        self.assertNotIn("integration", self.ledger())
