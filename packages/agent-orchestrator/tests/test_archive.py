"""The archive unit, the gaps reviewer, the outcome evidence on the archive head, and the run metrics."""

import json
import unittest

import pytest
from fixtures import assignment, load_script, report_wait, resume_id, spawn_id, wait_result
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


def plan_copies(plan, tree):
    """The write steps of an archive worker that copied a YAML plan's files byte-identical: the plan, its narrative
    and its oracle directory, beside the plan in the archive checkout."""
    if not plan.name.endswith(".plan.yaml"):
        return []
    beside = tree / "docs" / "plans"
    stem = plan.name.removesuffix(".plan.yaml")
    writes = [[str(beside / plan.name), plan.read_text(encoding="utf-8")]]
    narrative = plan.with_name(stem + ".plan.md")
    if narrative.exists():
        writes.append([str(beside / narrative.name), narrative.read_text(encoding="utf-8")])
    oracle = plan.with_name(stem + ".oracle")
    if oracle.is_dir():
        for child in sorted(oracle.iterdir()):
            writes.append(
                [str(beside / oracle.name / child.name), child.read_text(encoding="utf-8")]
            )
    return writes


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

    def archive_done(self, task_id="archive", id=None):
        """The archive worker copied the plan files byte-identical and reported; the driver placed the oracles."""
        fix_round = int(task_id.split("r")[-1]) if task_id != "archive" else 0
        step = report_wait(
            ARCHIVE,
            "complete",
            id=id or (resume_id(ARCHIVE, fix_round) if fix_round else spawn_id(ARCHIVE)),
        )
        step["write"] = [
            [
                str(self.run_dir / task_id / "report.json"),
                self.report(task_id, pr="https://github.com/o/r/pull/9"),
            ]
        ]
        step["write"] += plan_copies(self.plan, self.archive_tree)
        return step

    def gaps_done(self):
        step = report_wait("demo-gaps", "complete")
        step["write"] = [[str(self.run_dir / "gaps.md"), "# Gaps\n- none\n"]]
        return step


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
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        task = self.task("archive")
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        self.assertEqual(
            set(self.checks_log()),
            {"https://github.com/o/r/pull/7", "https://github.com/o/r/pull/9"},
        )
        self.assertEqual(self.calls("resume"), [])
        self.assertNotIn("demo-archive-review", self.spawned())


class ArchiveMergeStateTests(ArchiveFixture, unittest.TestCase):
    def test_S12_a_conflicting_archive_pr_is_rebased_with_no_review(self):
        """orchestrator-engine/R34: orchestrator-pr-merge-state/S12"""
        dirty = {"state": "OPEN", "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}
        merged = self.ticket_merged()
        merged_t1 = {
            "state": "MERGED",
            "mergeCommit": {"oid": self.git(self.repo, "rev-parse", "HEAD")},
        }
        self.prs(
            [merged_t1, dirty, "MERGED"]
        )  # T1 merges; the archive PR conflicts once, then merges
        rebased = self.archive_done("archiver1", id=spawn_id(ARCHIVE, 2))
        self.waits(merged + [self.archive_done(), self.gaps_done(), rebased])
        self.run_driver()
        findings = json.loads(
            (self.run_dir / "archiver1" / "findings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(findings["source"], "conflict")
        archive_pr = "https://github.com/o/r/pull/9"
        self.assertIn(["pr", "ready", archive_pr, "--undo"], self.gh_calls("pr", "ready"))
        self.assertEqual(self.spawned().count(ARCHIVE), 2)  # the rebase worker is fresh
        self.assertNotIn("demo-archive-review", self.spawned())
        self.assertEqual(self.events("error"), [])
        task = self.task("archiver1")
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))


class MetricsTests(ArchiveFixture, unittest.TestCase):
    def with_usage(self, step, usage):
        step["assignments"][0]["usage"] = usage
        return step

    def test_S16_a_finished_run_reports_route_rounds_escalations_and_usage(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S16"""
        worker = self.with_usage(
            self.worker_done(tests=True), {"input_tokens": 100, "output_tokens": 20}
        )
        self.waits([worker, self.reviewer_done(), self.archive_done(), self.gaps_done()])
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


class HeldRequirementTests(ArchiveFixture, unittest.TestCase):
    def test_S29_the_archive_appends_only_the_scenarios_the_spec_lacks(self):
        """The archive keeps a requirement a ticket already wrote, and appends the rest."""
        from test_archive_yaml import commit_spec

        commit_spec(
            self,
            "# Demo\nPaths: src/a/\n\n- **R9** WHEN a THEN b. (from demo/S1)\n- **R4** WHEN a. (from other/S1)\n",
        )
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        self.assertIn("keep as they are: S1 (R9).", task_md)
        self.assertIn("Every scenario is already in the spec: append none.", task_md)
        self.assertNotIn("Scenarios, verbatim:", task_md)


class PromotionTests(ArchiveFixture, unittest.TestCase):
    """A YAML plan archives by promotion: the driver places the oracles, the worker writes the spec."""

    def archive_run(self):
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        return (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")

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

    def test_S25_the_driver_places_each_promote_oracle_before_the_archive_worker_starts(self):
        task_md = self.archive_run()
        oracle = self.plan.parent / "demo.oracle" / "test_oracle.py.oracle"
        placed = self.archive_tree / "src" / "a" / "test_oracle.py"
        # The worker's step writes nothing under src/a/, so only the driver can have put the file there.
        self.assertEqual(placed.read_bytes(), oracle.read_bytes())
        self.assertIn("Already placed", task_md)

    def test_S25_the_archive_worker_owns_the_spec_the_copied_plan_files_and_the_places(self):
        task_md = self.archive_run()
        line = next(line for line in task_md.splitlines() if line.startswith("Owns"))
        owns = [item.strip() for item in line.split(": ", 1)[1].rstrip(".").split(",")]
        self.assertEqual(owns[0], "docs/specs/demo.md")
        self.assertEqual(owns[-1], "src/a/test_oracle.py")
        self.assertIn("docs/plans/demo.plan.yaml", owns)
        for needle in ("(from demo/S", "Coverage", "renames no test", "plan.yaml"):
            self.assertIn(needle, task_md)
        self.assertNotIn("Place byte-identical", task_md)  # the driver places; the worker does not

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

    def edited(self):
        step = self.archive_done()
        step["write"].append(
            [str(self.archive_tree / "src/a/test_oracle.py"), "def test_o():\n    assert 1\n"]
        )
        return step

    def test_S25_an_edited_placed_oracle_is_a_fix_round_not_a_masked_rerun(self):
        restored = self.archive_done("archiver1", id=resume_id(ARCHIVE, 1))
        restored["write"].append(
            [
                str(self.archive_tree / "src/a/test_oracle.py"),
                (self.plan.parent / "demo.oracle" / "test_oracle.py.oracle").read_text(
                    encoding="utf-8"
                ),
            ]
        )
        self.waits(self.ticket_merged() + [self.edited(), restored, self.gaps_done()])
        self.run_driver()
        findings = (self.run_dir / "archiver1" / "findings.json").read_text(encoding="utf-8")
        self.assertIn("src/a/test_oracle.py", findings)
        self.assertIn(
            "S1.archive.1", self.ledger()["receipts"]
        )  # evidence ran only after the restore

    def test_S25_an_archive_pr_merged_during_a_fix_round_still_archives_the_plan(self):
        merged = wait_result([assignment(ARCHIVE, "working", None, id=resume_id(ARCHIVE, 1))])
        merged.setdefault("write", []).append([str(self.home / "view-early.json"), '["MERGED"]'])
        self.waits(self.ticket_merged() + [self.edited(), merged, self.gaps_done()])
        self.run_driver()
        history = [h["state"] for h in self.ledger()["plan"]["history"]]
        self.assertEqual(history[-2:], ["ACCEPTED", "ARCHIVED"])
        self.assertEqual(self.events("merged-early")[-1]["unit"], "archive")
