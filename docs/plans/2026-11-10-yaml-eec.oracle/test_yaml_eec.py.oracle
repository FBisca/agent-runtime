"""Outcome oracle for the yaml-eec plan: a YAML plan is approved, run, verified on the integrated tree and archived,
and verification refuses what is not evidence.

Frozen at approval; placed into packages/agent-orchestrator/tests/ by the runner; never edited by a ticket.
It drives the real driver through the suite's existing seam (GateFixture: a temp repo with an origin, the fake
runtime and the fake gh answering in process) and talks to the engine only through its CLI contract:

  plan approve <plan> --repo <repo>      -> exit 0, writes `approval: {by, at, base, digest}`; non-zero refuses
  status <plan> --state-dir <d> --json   -> {"run_dir", "plan": {"slug", "state", "history"}, "tickets": [...]}
  verify <plan> --state-dir <d>          -> exit 0 iff the plan's acceptance holds on its receipts
  run <plan> ...                         -> the existing driver loop
  receipts                               -> <run_dir>/receipts/*.json with scenario, phase, digest, tree, result

The control cases feed known-bad observations to the assertion helpers and pass on any revision: they prove the
assertions bite before any implementation exists.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import pytest
import yaml
from fixtures import load_script, report_wait, spawn_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration

LIFECYCLE = ["APPROVED", "RUNNING", "DELIVERED", "VERIFYING", "ACCEPTED", "ARCHIVED"]
PROBE = "probe\n"


def plan_yaml(check):
    return yaml.safe_dump(
        {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "The feature file says done.",
            "spec": "docs/specs/demo.md",
            "outcome": ["S1"],
            "scenarios": [
                {
                    "id": "S1",
                    "when": "the feature is delivered",
                    "then": "src/a/feature.txt reads done",
                    "evidence": {
                        "check": check,
                        "oracle": ["probe.txt.oracle"],
                        "place": "checks/",
                        "baseline": "fail",
                    },
                }
            ],
            "tickets": [
                {
                    "id": "T1",
                    "delivers": "the feature file",
                    "satisfies": ["S1"],
                    "paths": ["src/a/"],
                    "size": "small",
                    "difficulty": "simple",
                }
            ],
        },
        sort_keys=False,
    )


GOOD_CHECK = "grep -qx done src/a/feature.txt && test -f checks/probe.txt"


# The assertion helpers. Every check the outcome cases make goes through one of these.


def assert_receipt(case, receipt, tree, digest, result="PASS"):
    case.assertEqual(receipt["result"], result, receipt)
    case.assertEqual(receipt["tree"], tree, "the receipt is for another tree")
    case.assertEqual(receipt["digest"], digest, "the receipt is for another approval")


def assert_history(case, history):
    case.assertEqual(history, LIFECYCLE, "the plan skipped or reordered a state")


def assert_same_bytes(case, before, after):
    case.assertEqual(before, after, "plan.yaml changed after approval")


class ControlCases(unittest.TestCase):
    """Known-bad observations: each helper must raise. These pass before and after the implementation."""

    def test_control_receipt_for_another_tree_or_digest_is_refused(self):
        good = {"result": "PASS", "tree": "t1", "digest": "d1"}
        assert_receipt(self, good, "t1", "d1")
        for bad, tree, digest in (
            (good, "t2", "d1"),
            (good, "t1", "d2"),
            (dict(good, result="UNKNOWN"), "t1", "d1"),
        ):
            with self.assertRaises(AssertionError):
                assert_receipt(self, bad, tree, digest)

    def test_control_skipped_or_reordered_lifecycle_is_refused(self):
        assert_history(self, list(LIFECYCLE))
        for bad in (
            [s for s in LIFECYCLE if s != "VERIFYING"],
            LIFECYCLE[:4] + ["ARCHIVED"],
            ["APPROVED", "RUNNING", "DELIVERED", "ARCHIVED"],
        ):
            with self.assertRaises(AssertionError):
                assert_history(self, bad)

    def test_control_one_changed_byte_is_refused(self):
        assert_same_bytes(self, b"a: 1\n", b"a: 1\n")
        with self.assertRaises(AssertionError):
            assert_same_bytes(self, b"a: 1\n", b"a: 2\n")


class YamlPlanFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.plan.unlink()
        self.plan = self.repo / "docs" / "plans" / "2026-11-10-demo.plan.yaml"
        self.oracle_dir = self.repo / "docs" / "plans" / "2026-11-10-demo.oracle"
        self.oracle_dir.mkdir()
        (self.oracle_dir / "probe.txt.oracle").write_text(PROBE, encoding="utf-8")
        self.archive_tree = self.root / "repo.worktrees" / "demo-archive"

    def write_plan(self, check=GOOD_CHECK):
        self.plan.write_text(plan_yaml(check), encoding="utf-8")

    def approve(self, expect_ok=True):
        result = self.cli("plan", "approve", str(self.plan), "--repo", str(self.repo))
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def status(self):
        result = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def verify(self):
        return self.cli("verify", str(self.plan), "--state-dir", str(self.state_dir))

    def approval(self):
        return yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]

    def receipts(self, run_dir, phase=None):
        found = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((run_dir / "receipts").glob("*.json"))
        ]
        return [r for r in found if phase in (None, r.get("phase"))]

    def origin_tree(self):
        return subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", "main^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def steps(self, run_dir, content="done", archive=True):
        """The worker delivers src/a/feature.txt with a covering test; the reviewer passes; the work reaches main."""
        worktree = self.root / "repo.worktrees" / "demo-t1"
        report = json.loads(self.report("t1"))
        report.update(contract=3, covers={"S1": ["src/a/test_feature.py::test_feature"]})
        worker = report_wait("demo-t1", "complete", id=spawn_id("demo-t1"))
        worker["write"] = [
            [str(run_dir / "t1" / "report.json"), json.dumps(report)],
            [str(worktree / "src" / "a" / "feature.txt"), content + "\n"],
            [
                str(worktree / "src" / "a" / "test_feature.py"),
                "def test_feature():\n    assert open('src/a/feature.txt').read().strip() == 'done'\n",
            ],
        ]
        reviewer = report_wait("demo-t1-review", "complete")
        reviewer["write"] = [[str(run_dir / "t1" / "review.json"), self.review2()]]
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        reviewer["run"] = [
            ["git", "-C", str(worktree), "add", "-A"],
            ["git", "-C", str(worktree), *identity, "commit", "-qm", "feature"],
            ["git", "-C", str(worktree), "push", "-q", "origin", "HEAD:main"],
        ]
        steps = [worker, reviewer]
        if archive:
            archiver = report_wait("demo-archive", "complete", id=spawn_id("demo-archive"))
            archiver["write"] = [
                [
                    str(run_dir / "archive" / "report.json"),
                    self.report("archive", pr="https://github.com/o/r/pull/9"),
                ],
                [
                    str(self.archive_tree / "docs" / "specs" / "demo.md"),
                    "# Demo\nPaths: src/a/\n\n- **R1** WHEN the feature is delivered, THEN src/a/feature.txt reads"
                    " done. (from demo/S1)\n\n## Coverage\n\n| Requirement | Test |\n|---|---|\n"
                    "| R1 | `src/a/test_feature.py::test_feature` |\n",
                ],
            ]
            gaps = report_wait("demo-gaps", "complete")
            gaps["write"] = [[str(run_dir / "gaps.md"), "# Gaps\n- none\n"]]
            steps += [archiver, gaps]
        return steps

    def run_yaml(self, steps):
        self.waits(steps)
        return self.in_process(
            load_script(),
            [
                "run",
                str(self.plan),
                "--state-dir",
                str(self.state_dir),
                "--models-file",
                str(self.models),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
            ],
        )

    def delivered(self):
        """Approve, run to the end, and return (approved bytes, status, run_dir)."""
        self.write_plan()
        self.approve()
        approved = self.plan.read_bytes()
        run_dir = Path(self.status()["run_dir"])
        result = self.run_yaml(self.steps(run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        return approved, self.status(), run_dir


class Lifecycle(YamlPlanFixture, unittest.TestCase):
    def test_lifecycle_yaml_plan_is_verified_on_the_integrated_tree_then_archived(self):
        approved, status, run_dir = self.delivered()
        assert_history(self, status["plan"]["history"])
        self.assertEqual(status["plan"]["state"], "ARCHIVED")
        assert_same_bytes(self, approved, self.plan.read_bytes())
        digest = self.approval()["digest"]
        integration = [r for r in self.receipts(run_dir, "integration") if r["scenario"] == "S1"]
        self.assertTrue(integration, "no integration receipt for the outcome scenario")
        assert_receipt(self, integration[-1], self.origin_tree(), digest)
        self.assertTrue(
            [
                r
                for r in self.receipts(run_dir, "archive")
                if r["scenario"] == "S1" and r["result"] == "PASS"
            ],
            "the outcome was not re-verified on the archive head",
        )
        promoted = self.archive_tree / "checks" / "probe.txt"
        self.assertEqual(
            promoted.read_text(encoding="utf-8"),
            PROBE,
            "the oracle was not promoted byte-identical",
        )
        self.assertEqual(self.verify().returncode, 0)


class Rejections(YamlPlanFixture, unittest.TestCase):
    def test_rejects_a_check_that_passes_at_base_as_vacuous(self):
        self.write_plan(check="true")
        result = self.approve(expect_ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("S1", result.stdout + result.stderr)
        self.assertIn("baseline", (result.stdout + result.stderr).lower())
        self.assertNotIn("approval", yaml.safe_load(self.plan.read_text(encoding="utf-8")))

    def test_rejects_a_plan_or_oracle_edited_after_approval(self):
        self.write_plan()
        self.approve()
        for edit in (
            lambda: self.plan.write_text(
                self.plan.read_text(encoding="utf-8").replace("reads done", "reads finished"),
                encoding="utf-8",
            ),
            lambda: (self.oracle_dir / "probe.txt.oracle").write_text(
                "tampered\n", encoding="utf-8"
            ),
        ):
            backup = {p: p.read_bytes() for p in (self.plan, self.oracle_dir / "probe.txt.oracle")}
            edit()
            result = self.run_yaml([])
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("plan approve", result.stdout + result.stderr)
            self.assertEqual(self.spawned(), [], "a worker was spawned on an unapproved contract")
            for path, data in backup.items():
                path.write_bytes(data)

    def test_rejects_missing_stale_and_other_tree_receipts(self):
        _, _, run_dir = self.delivered()
        self.assertEqual(self.verify().returncode, 0)
        path = next(
            p
            for p in sorted((run_dir / "receipts").glob("*.json"))
            if json.loads(p.read_text(encoding="utf-8")).get("phase") == "integration"
        )
        original = path.read_text(encoding="utf-8")
        path.write_text(json.dumps(dict(json.loads(original), tree="0" * 40)), encoding="utf-8")
        stale = self.verify()
        self.assertNotEqual(stale.returncode, 0, "a receipt for another tree still counted")
        path.write_text(original, encoding="utf-8")
        self.assertEqual(self.verify().returncode, 0)
        shutil.move(str(path), str(path) + ".gone")
        missing = self.verify()
        self.assertNotEqual(missing.returncode, 0, "acceptance held with its receipt missing")
        self.assertIn("S1", missing.stdout + missing.stderr)

    def test_rejects_a_plan_whose_tickets_completed_but_whose_outcome_fails(self):
        """The ticket gate holds a failing outcome (D29), so the ticket completes only as a person's merge by hand past
        that gate; the outcome then fails at integration and the plan is rejected."""
        self.write_plan()
        self.approve()
        run_dir = Path(self.status()["run_dir"])
        worker, reviewer = self.steps(run_dir, content="almost", archive=False)
        worker["run"] = reviewer["run"]  # the work reaches main with the merge by hand
        self.early([{"state": "MERGED"}])
        self.run_yaml([worker])
        status = self.status()
        self.assertEqual([t["state"] for t in status["tickets"]], ["COMPLETE"])
        self.assertEqual(status["plan"]["state"], "REJECTED")
        self.assertNotIn("demo-archive", self.spawned(), "a plan whose outcome failed was archived")
        integration = self.receipts(run_dir, "integration")
        self.assertEqual([r["result"] for r in integration if r["scenario"] == "S1"], ["FAIL"])
        self.assertNotEqual(self.verify().returncode, 0)
