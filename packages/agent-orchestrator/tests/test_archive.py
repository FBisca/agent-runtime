"""The archive unit the driver builds itself, the gaps reviewer, the outcome evidence on the archive head, and the run metrics."""

import json
import unittest

import fake_gh
import pytest
from fixtures import load_script, report_wait
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


class ArchiveFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.use_yaml_plan(oracle=True)
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
    def test_S5_a_conflicting_archive_pr_asks_the_lead_and_spawns_no_worker(self):
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
        self.waits(self.ticket_merged() + [{}, self.decide_step("archive", "skip")])
        self.run_driver()
        self.assertEqual(self.spawned().count(ARCHIVE), 0)
        request = self.request("archive")
        self.assertEqual(request["menu"], ["retry", "skip"])
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
    """A YAML plan archives by promotion: the driver places the oracles and writes the spec."""

    def archive_run(self):
        self.waits(self.ticket_merged())
        self.run_driver()

    def test_S24_an_approved_plan_driven_to_the_end_is_archived_with_receipts_and_a_valid_digest(
        self,
    ):
        self.archive_run()
        argv = ["--repo", str(self.repo), "--state-dir", str(self.state_dir)]
        status = self.cli("status", str(self.plan), "--json", *argv)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["plan"]["state"], "ARCHIVED")
        stems = list(self.ledger()["receipts"])
        self.assertTrue(stems)
        for stem in stems:
            self.assertTrue((self.run_dir / "receipts" / (stem + ".json")).is_file(), stem)
        check = self.cli("plan", "check", str(self.plan), *argv)
        self.assertEqual(check.returncode, 0, check.stderr + check.stdout)

    def test_S25_the_driver_places_each_promote_oracle_in_the_archive_worktree(self):
        self.archive_run()
        oracle = (self.plan.parent / "demo.oracle" / "test_oracle.py.oracle").read_text()
        # the worktree is gone after the merge: the pushed branch is what holds the placed file
        self.assertEqual(
            self.git(self.origin, "show", "feat/demo-archive:src/a/test_oracle.py") + "\n", oracle
        )

    def test_S25_the_driver_reruns_outcome_evidence_on_the_archive_head(self):
        self.archive_run()
        ledger = self.ledger()
        stems = [s for s in ledger["receipts"] if s.startswith("S1.archive.")]
        self.assertEqual(stems, ["S1.archive.1"])
        receipt = json.loads(
            (self.run_dir / "receipts" / "S1.archive.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["result"], "PASS")
        self.assertEqual(receipt["digest"], ledger["integration"]["digest"])

    def test_S25_the_plan_is_archived_when_the_archive_pr_merges(self):
        self.archive_run()
        history = [h["state"] for h in self.ledger()["plan"]["history"]]
        self.assertEqual(history[-2:], ["ACCEPTED", "ARCHIVED"])

    def test_S23_a_plan_goes_approved_to_archived_and_verify_holds_on_the_archive_head(self):
        self.archive_run()
        ledger = self.ledger()
        self.assertEqual(
            [h["state"] for h in ledger["plan"]["history"]],
            ["APPROVED", "RUNNING", "DELIVERED", "VERIFYING", "ACCEPTED", "ARCHIVED"],
        )
        verify = load_script().verify
        model = load_script().yaml_plan.load(
            self.plan, repo=self.repo, state_dir=self.state_dir, verify=False
        )
        result = verify.acceptance(
            model,
            ledger,
            self.run_dir / "receipts",
            digest=ledger["integration"]["digest"],
        )
        self.assertEqual(result["state"], "ACCEPTED")
        # The archive's outcome receipt is part of the verdict: take it away and the outcome is unknown.
        stripped = {
            **ledger,
            "receipts": {k: v for k, v in ledger["receipts"].items() if ".archive." not in k},
        }
        again = verify.acceptance(
            model, stripped, self.run_dir / "receipts", digest=ledger["integration"]["digest"]
        )
        self.assertEqual(again["state"], "REJECTED")


GAPS = "demo-gaps"


class GapsAfterArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_S6_before_the_archive_merges_no_gaps_reviewer_exists(self):
        """orchestrator-engine/R2: deterministic-archive/S6 — the archive PR is closed unmerged, so nothing gaps."""
        real = self.gh_process

        def closed_archive(argv, timeout=None, cwd=None):
            if argv[:3] == ["pr", "view", "https://github.com/o/r/pull/9"]:
                row = {"state": "CLOSED", "isDraft": False, "mergeStateStatus": "UNKNOWN"}
                row.update(mergeable="UNKNOWN", mergeCommit=None)
                return fake_gh.Completed(argv, 0, json.dumps(row))
            return real(argv, timeout=timeout, cwd=cwd)

        self.gh_process = closed_archive
        self.waits(self.ticket_merged() + [{}, {}, self.decide_step("archive", "skip")])
        self.run_driver()
        self.assertNotIn(GAPS, self.spawned())
        self.assertFalse(self.task("archive").get("merge_commit"))

    def test_S6_once_the_archive_merges_the_gaps_reviewer_starts_and_is_the_only_one(self):
        """orchestrator-engine/R2: deterministic-archive/S6 — the merged archive spawns the reviewer exactly once."""
        self.waits(self.ticket_merged())
        self.run_driver()
        self.assertEqual(self.task("archive")["gate"], "done")
        self.assertEqual(self.spawned().count(GAPS), 1)

    def test_S6_the_run_ends_while_the_gaps_reviewer_is_still_open(self):
        """orchestrator-engine/R2: deterministic-archive/S6 — its assignment stays open in the runtime; the run ends."""
        held = {"agents": [GAPS]}  # the reviewer holds its assignment open and never reports
        self.waits(self.ticket_merged() + [held] * 10)  # more waits than the run needs
        self.run_driver()
        self.assertEqual(self.spawned().count(GAPS), 1)
        self.assertEqual(self.task("archive")["gate"], "done")
        self.assertFalse(self.ledger()["gaps"]["ended"])
        left = json.loads((self.home / "waits.json").read_text(encoding="utf-8"))
        self.assertTrue(left, "the run drained every held wait: it waited on gaps")
