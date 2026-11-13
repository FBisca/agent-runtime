"""Acceptance for the engine-gaps plan: a discard deletes a plan folder only when it sits directly under the repo
config's `plans.dir`.

Frozen at approval; placed at packages/agent-orchestrator/acceptance/driver/ by the runner; never edited by a ticket.
It drives `plan check` and `run` through the archive fixture of the folder-plan tests (fake runtime, fake gh, a real
git origin). The contract it pins (plan Decisions D2, D3):

  - `plan check` refuses a discarding folder plan whose folder's resolved parent is not the resolved `plans.dir`,
    naming the folder and `plans.dir`; a symlink into `plans.dir` does not count
  - at archive, the same rule deletes nothing and leaves the plan short of ARCHIVED, naming `plans.dir`

Each class has a control case that passes at base and after.
"""

import os
import unittest
from pathlib import Path

import pytest
import yaml
from fixtures import load_script
from test_archive_modes import FOLDER, FolderArchiveFixture, discard_plan

pytestmark = pytest.mark.integration

PARTS = ("plan.yaml", "plan.md", "acceptance/test_probe.py", "negatives/break.patch")


class GuardFixture(FolderArchiveFixture):
    def configure(self, plans_dir):
        (self.repo / ".orchestrate").mkdir(exist_ok=True)
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"plans": {"dir": str(plans_dir)}}), encoding="utf-8"
        )

    def write_folder(self, parent, name=FOLDER):
        """A discarding folder plan written under `parent`, not approved."""
        folder = parent / name
        (folder / "acceptance").mkdir(parents=True)
        (folder / "negatives").mkdir()
        (folder / "acceptance" / "test_probe.py").write_text(
            "def test_probe():\n    pass\n", encoding="utf-8"
        )
        (folder / "negatives" / "break.patch").write_text("# a patch\n", encoding="utf-8")
        (folder / "plan.md").write_text("# demo\n", encoding="utf-8")
        (folder / "plan.yaml").write_text(
            yaml.safe_dump(discard_plan(), sort_keys=False), encoding="utf-8"
        )
        return folder

    def check(self, plan):
        return self.cli("plan", "check", str(plan), "--repo", str(self.repo))

    def drive_unchecked(self, mid_run_plans_dir=None):
        """The run, its exit code left to the test: a refusal may stop it mid-run or at archive. With
        `mid_run_plans_dir`, the repo config names that plans dir from the reviewer's step on, after the run loaded."""
        steps = self.ticket_merged()
        if mid_run_plans_dir is not None:
            config = yaml.safe_dump({"plans": {"dir": str(mid_run_plans_dir)}})
            steps[-1].setdefault("write", []).append(
                [str(self.repo / ".orchestrate" / "config.yaml"), config]
            )
        self.waits(steps + [self.gaps_done()])
        self.alias_state(self.plan)
        return self.in_process(
            load_script(),
            [
                "run",
                str(self.plan),
                "--repo",
                str(self.repo),
                "--state-dir",
                str(self.state_dir),
                "--routes-file",
                str(self.route_file),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
            ],
        )

    def said(self, result):
        """Everything the run told the Lead: its output and every request it wrote."""
        texts = [result.stdout, result.stderr]
        if self.run_dir.exists():
            texts += [p.read_text(encoding="utf-8") for p in self.run_dir.rglob("request*.json")]
        return "\n".join(texts)

    def plan_state(self):
        ledger = self.state_dir / "demo.json"
        return self.ledger().get("plan", {}).get("state") if ledger.exists() else None


class PlansDirGuard(GuardFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plans = self.root / "plans"
        self.configure(self.plans)

    def test_control_a_discard_plan_directly_under_plans_dir_passes_plan_check(self):
        folder = self.write_folder(self.plans)
        result = self.check(folder / "plan.yaml")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_discard_plan_outside_plans_dir_is_refused_naming_the_folder_and_plans_dir(self):
        folder = self.write_folder(self.root / "elsewhere")
        result = self.check(folder / "plan.yaml")
        self.assertNotEqual(
            result.returncode, 0, "a discard plan outside plans.dir passed plan check"
        )
        said = result.stdout + result.stderr
        self.assertIn("plans.dir", said)
        self.assertIn(str(folder), said)
        approve = self.cli(
            "plan",
            "approve",
            str(folder / "plan.yaml"),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        )
        self.assertNotEqual(
            approve.returncode, 0, "plan approve accepted a discard plan outside plans.dir"
        )

    def test_a_discard_plan_nested_below_plans_dir_is_refused(self):
        folder = self.write_folder(self.plans / "nested")
        result = self.check(folder / "plan.yaml")
        self.assertNotEqual(
            result.returncode, 0, "a folder two levels under plans.dir passed plan check"
        )
        self.assertIn("plans.dir", result.stdout + result.stderr)

    def test_a_symlink_into_plans_dir_does_not_make_a_folder_deletable(self):
        target = self.write_folder(self.root / "elsewhere")
        self.plans.mkdir(parents=True, exist_ok=True)
        os.symlink(target, self.plans / FOLDER)
        result = self.check(self.plans / FOLDER / "plan.yaml")
        self.assertNotEqual(
            result.returncode, 0, "a symlinked folder passed as a child of plans.dir"
        )
        self.assertIn("plans.dir", result.stdout + result.stderr)


class DiscardAtArchive(GuardFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plans = self.root / "plans"
        self.configure(self.plans)
        self.folder_plan(discard_plan(), root=self.plans)

    def test_control_a_discard_directly_under_plans_dir_deletes_its_folder_and_archives(self):
        result = self.drive_unchecked()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.plan_state(), "ARCHIVED")

    def test_a_discard_whose_folder_left_plans_dir_after_approval_deletes_nothing(self):
        result = self.drive_unchecked(
            mid_run_plans_dir=Path("docs") / "plans"
        )  # plans.dir moves mid-run
        for part in PARTS:
            self.assertTrue((self.folder / part).is_file(), "the discard deleted %s" % part)
        self.assertNotEqual(self.plan_state(), "ARCHIVED")
        said = self.said(result)
        self.assertIn("plans.dir", said)
        self.assertIn(str(self.folder), said)


if __name__ == "__main__":
    unittest.main()
