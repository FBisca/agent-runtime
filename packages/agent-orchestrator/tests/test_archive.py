"""The archive unit, the gaps reviewer, `converge --final`, and the run metrics."""

import json
import unittest

import pytest
from fixtures import load_script, report_wait, resume_id, spawn_id
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
        self.archive_tree = self.root / "repo.worktrees" / ARCHIVE

    def ticket_merged(self):
        """Waits that take T1 through review, CI and the merge."""
        return [self.worker_done(tests=True), self.reviewer_done()]

    def archive_done(self, task_id="archive", complete=True, id=None):
        """The archive worker wrote spec, renamed tests, set the plan ARCHIVED and reported (or left tests untouched)."""
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
        if complete:
            step["write"] += [
                [str(self.archive_tree / "docs/specs/demo.md"), SPEC],
                [str(self.archive_tree / "src/a/test_demo.py"), TESTS],
                [
                    str(self.archive_tree / "docs/plans/demo.plan.md"),
                    self.plan.read_text(encoding="utf-8").replace(
                        "Status: APPROVED", "Status: ARCHIVED"
                    ),
                ],
            ]
        return step

    def gaps_done(self):
        step = report_wait("demo-gaps", "complete")
        step["write"] = [[str(self.run_dir / "gaps.md"), "# Gaps\n- none\n"]]
        return step


class ArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_S15_all_tickets_complete_spawns_the_archive_worker_and_the_gaps_reviewer(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S15"""
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review", ARCHIVE, "demo-gaps"])
        archive, gaps = self.calls("spawn")[2]["args"], self.calls("spawn")[3]["args"]
        self.assertEqual(
            (archive["model"], archive["effort"], archive["cwd"]),
            ("haiku", "low", str(self.archive_tree)),
        )
        self.assertEqual((gaps["model"], gaps["kind"]), ("sonnet", "worker"))
        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "Paths:",
            "(from demo/S",
            "demo/R",
            "ARCHIVED",
            "docs/specs/demo.md",
            "pull request",
            str(self.run_dir / "archive" / "report.json"),
        ):
            self.assertIn(needle, task_md)
        gaps_md = (self.run_dir / "gaps-task.md").read_text(encoding="utf-8")
        self.assertIn(str(self.run_dir / "gaps.md"), gaps_md)
        self.assertIn(str(self.plan), gaps_md)
        task = self.task("archive")
        self.assertEqual(
            (task["packet"]["size"], task["packet"]["difficulty"]), ("small", "simple")
        )

    def test_S15_nothing_archives_while_an_agent_ticket_is_unfinished(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S15"""
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.decide_step("t1", "skip")]
        )
        self.prs(["CLOSED"])
        self.run_driver()
        self.assertNotIn(ARCHIVE, self.spawned())

    def test_S15_converge_final_passing_hands_the_archive_to_ci_and_merge(self):
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

    def test_S15_converge_final_failing_is_a_fix_round_for_the_archive_worker(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S15"""
        self.waits(
            self.ticket_merged()
            + [self.archive_done(complete=False), self.archive_done("archiver1"), self.gaps_done()]
        )
        self.run_driver()
        findings = json.loads(
            (self.run_dir / "archiver1" / "findings.json").read_text(encoding="utf-8")
        )
        self.assertEqual((findings["source"], findings["attempt"]), ("converge", 1))
        self.assertTrue(any(i["excerpt"].startswith("FAIL") for i in findings["items"]))
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], [ARCHIVE])
        self.assertEqual(self.task("archiver1")["status"], "COMPLETE")


class ArchiveMergeStateTests(ArchiveFixture, unittest.TestCase):
    def test_S12_a_conflicting_archive_pr_is_rebased_with_no_review(self):
        """orchestrator-engine/R34: orchestrator-pr-merge-state/S12"""
        dirty = {"state": "OPEN", "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}
        self.prs(
            ["MERGED", dirty, "MERGED"]
        )  # T1 merges; the archive PR conflicts once, then merges
        rebased = self.archive_done("archiver1", id=spawn_id(ARCHIVE, 2))
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done(), rebased])
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
    def test_S29_a_markdown_plan_appends_only_the_scenarios_the_spec_lacks(self):
        """A Markdown plan's archive keeps a requirement a ticket already wrote, and appends the rest."""
        from test_archive_yaml import commit_spec

        commit_spec(
            self,
            "# Demo\nPaths: src/a/\n\n- **R9** WHEN c THEN d. (from demo/S2)\n- **R4** WHEN a. (from other/S1)\n",
        )
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])
        self.run_driver()
        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        self.assertIn("keep as they are: S2 (R9).", task_md)
        listed = task_md.split("Scenarios, verbatim:", 1)[1]
        self.assertIn("**S1**", listed)
        self.assertIn("**S3**", listed)
        self.assertNotIn("**S2**", listed)


class PromotionTests(ArchiveFixture, unittest.TestCase):
    """A YAML plan archives by promotion: the driver places the oracles, the worker writes the spec."""

    def ticket_merged(self):
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "t1")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        self.prs(
            [{"state": "MERGED", "mergeCommit": {"oid": self.git(self.repo, "rev-parse", "HEAD")}}]
        )
        return super().ticket_merged()

    def archive_run(self):
        self.use_yaml_plan(oracle=True)
        self.waits(self.ticket_merged() + [self.archive_done(complete=False), self.gaps_done()])
        self.run_driver()
        return (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")

    def test_S25_the_driver_places_each_promote_oracle_before_the_archive_worker_starts(self):
        task_md = self.archive_run()
        oracle = self.plan.parent / "demo.oracle" / "test_oracle.py.oracle"
        placed = self.archive_tree / "src" / "a" / "test_oracle.py"
        # The worker's step writes nothing under src/a/, so only the driver can have put the file there.
        self.assertEqual(placed.read_bytes(), oracle.read_bytes())
        self.assertIn("Already placed", task_md)

    def test_S25_the_archive_worker_owns_the_spec_and_the_places_only(self):
        task_md = self.archive_run()
        line = next(line for line in task_md.splitlines() if line.startswith("Owns"))
        owns = [item.strip() for item in line.split(": ", 1)[1].rstrip(".").split(",")]
        self.assertEqual(owns, ["docs/specs/demo.md", "src/a/test_oracle.py"])
        for needle in ("(from demo/S", "Coverage", "renames no test", "plan.yaml"):
            self.assertIn(needle, task_md)
        self.assertNotIn("Copy byte-identical", task_md)
        self.assertNotIn("Place byte-identical", task_md)

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
