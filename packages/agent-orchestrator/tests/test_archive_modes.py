"""The archive modes of a plan in its own folder: promote moves the acceptance files into their domain and deletes the
plan folder, discard deletes the folder and never spawns a worker."""

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import load_script
from test_gates import GateFixture

pytestmark = pytest.mark.integration

FOLDER = "2026-11-13-demo"
ACCEPTANCE = "def test_probe():\n    pass\n"
TICKET = dict(
    id="T1",
    delivers="first",
    satisfies=["S1"],
    paths=["src/a/"],
    size="small",
    difficulty="simple",
    max_fix_rounds=3,
)


def promote_plan():
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "domain": "billing",
        "archive": "promote",
        "outcome": ["S1"],
        "scenarios": [
            dict(
                id="S1",
                when="a",
                then="b",
                evidence={
                    "check": "test -f acceptance/billing/test_probe.py",
                    "baseline": "pass",
                    "acceptance": ["test_probe.py"],
                    "package": ".",
                },
            )
        ],
        "tickets": [dict(TICKET)],
    }


def discard_plan():
    data = promote_plan()
    for key in ("spec", "domain"):
        del data[key]
    data["archive"] = "discard"
    data["scenarios"][0]["evidence"]["check"] = "test -f acceptance/test_probe.py"
    return data


class FolderArchiveFixture(GateFixture):
    """One ticket of a plan in its own folder, driven through the archive with the fakes of the gate tests."""

    def alias_state(self, plan):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        alias = self.state_dir / load_script().repo_key(self.repo)
        if not alias.is_symlink():
            alias.symlink_to(".")

    def report(self, task_id, **over):
        # GateFixture tells a YAML plan by its file name, and a folder plan is named plan.yaml
        data = json.loads(super().report(task_id))
        data.update(contract=3, covers=self.COVERS)
        data.update(over)
        return json.dumps(data)

    def folder_plan(self, data, root=None, tracked=False):
        """Write and approve the plan folder under `root` (the repository's docs/plans by default); `tracked`
        commits it to origin's default branch first, as a plan folder kept in the repository is."""
        self.plan.unlink()
        self.plans = root or self.repo / "docs" / "plans"
        self.folder = self.plans / FOLDER
        (self.folder / "acceptance").mkdir(parents=True)
        (self.folder / "negatives").mkdir()
        (self.folder / "acceptance" / "test_probe.py").write_text(ACCEPTANCE, encoding="utf-8")
        (self.folder / "negatives" / "break.patch").write_text("# a patch\n", encoding="utf-8")
        (self.folder / "plan.md").write_text("# demo\n", encoding="utf-8")
        self.plan = self.folder / "plan.yaml"
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        if tracked:
            self.git(self.repo, "add", "docs/plans")
            self.git(self.repo, "commit", "-q", "-m", "plan")
            self.git(self.repo, "push", "-q", "origin", "HEAD:main")
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

    def configure(self, plans_dir):
        """The repo config names `plans_dir` as plans.dir."""
        (self.repo / ".orchestrate").mkdir(exist_ok=True)
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"plans": {"dir": str(plans_dir)}}), encoding="utf-8"
        )

    def ticket_merged(self):
        self.git(self.repo, "commit", "-q", "--allow-empty", "-m", "t1")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        self.prs(
            [{"state": "MERGED", "mergeCommit": {"oid": self.git(self.repo, "rev-parse", "HEAD")}}]
        )
        return [self.worker_done(tests=True), self.reviewer_done()]

    def gaps_done(self):
        from fixtures import report_wait

        step = report_wait("demo-gaps", "complete")
        step["write"] = [[str(self.run_dir / "gaps.md"), "# Gaps\n- none\n"]]
        return step

    def drive(self):
        self.waits(self.ticket_merged() + [self.gaps_done()])
        self.run_driver("--repo", str(self.repo))

    def origin_show(self, ref, path):
        result = subprocess.run(
            ["git", "-C", str(self.origin), "show", "%s:%s" % (ref, path)], capture_output=True
        )
        return result.stdout if result.returncode == 0 else None

    def origin_files(self, ref, under):
        return self.git(self.origin, "ls-tree", "-r", "--name-only", ref, "--", under).split()

    def history(self):
        return [h["state"] for h in self.ledger()["plan"]["history"]]


class PromoteArchiveTests(FolderArchiveFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.folder_plan(promote_plan(), tracked=True)

    def test_S8_the_driver_moves_the_acceptance_file_and_deletes_the_plan_folder_in_the_archive_tree(
        self,
    ):
        self.drive()
        self.assertEqual(
            self.origin_show("feat/demo-archive", "acceptance/billing/test_probe.py"),
            ACCEPTANCE.encode(),
        )
        self.assertEqual(self.origin_files("feat/demo-archive", "docs/plans/" + FOLDER), [])
        self.assertEqual(self.spawned().count("demo-archive"), 0)
        spec = self.origin_show("feat/demo-archive", "docs/specs/demo.md").decode()
        self.assertIn("(from demo/S1)", spec)

    def test_S8_outcome_evidence_reruns_on_the_archive_head_from_the_promoted_file(self):
        self.drive()
        ledger = self.ledger()
        stems = [s for s in ledger["receipts"] if s.startswith("S1.archive.")]
        self.assertEqual(stems, ["S1.archive.1"])
        receipt = json.loads(
            (self.run_dir / "receipts" / "S1.archive.1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["result"], "PASS")
        self.assertEqual(list(receipt["placed"]), ["acceptance/billing/test_probe.py"])
        self.assertEqual(
            ledger["archive"]["sha"], self.git(self.origin, "rev-parse", "feat/demo-archive")
        )

    def test_S10_a_promoted_folder_plan_runs_to_archived_and_verify_exits_0(self):
        self.drive()
        self.assertEqual(
            self.history(),
            ["APPROVED", "RUNNING", "DELIVERED", "VERIFYING", "ACCEPTED", "ARCHIVED"],
        )
        self.assertEqual(
            self.origin_show("feat/demo-archive", "acceptance/billing/test_probe.py"),
            (self.folder / "acceptance" / "test_probe.py").read_bytes(),
        )
        self.assertEqual(self.origin_files("feat/demo-archive", "docs/plans"), [])
        result = self.cli(
            "verify", str(self.plan), "--repo", str(self.repo), "--state-dir", str(self.state_dir)
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


class DiscardArchiveTests(FolderArchiveFixture, unittest.TestCase):
    def test_S9_a_discard_plan_outside_the_repository_is_deleted_and_archived_with_no_pr(self):
        self.configure(self.root / "plans")
        self.folder_plan(discard_plan(), root=self.root / "plans")
        self.drive()
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.history()[-2:], ["ACCEPTED", "ARCHIVED"])
        self.assertNotIn("demo-archive", self.spawned())
        self.assertEqual(self.gh_calls("pr", "create"), [])
        self.assertNotIn("archive", self.ledger()["tasks"])

    def test_S9_a_discard_plan_tracked_in_the_repository_is_deleted_by_a_delete_only_pr(self):
        self.folder_plan(discard_plan(), tracked=True)
        self.drive()
        self.assertNotIn("demo-archive", self.spawned())
        changes = self.git(self.origin, "diff", "--name-status", "main", "feat/demo-archive")
        rows = [line.split("\t") for line in changes.splitlines()]
        self.assertEqual(
            sorted(rows),
            sorted(
                [
                    ["D", "docs/plans/%s/%s" % (FOLDER, name)]
                    for name in (
                        "acceptance/test_probe.py",
                        "negatives/break.patch",
                        "plan.md",
                        "plan.yaml",
                    )
                ]
            ),
        )
        self.assertEqual(
            self.git(self.origin, "rev-list", "--count", "main..feat/demo-archive"), "1"
        )
        self.assertEqual(self.history()[-2:], ["ACCEPTED", "ARCHIVED"])

    def test_S5_an_archived_discard_plan_still_starts_the_gaps_reviewer_and_gets_its_gaps_md(self):
        self.configure(self.root / "plans")
        self.folder_plan(discard_plan(), root=self.root / "plans")
        self.drive()
        self.assertEqual(self.ledger()["plan"]["state"], "ARCHIVED")
        self.assertIn("demo-gaps", self.spawned())
        self.assertEqual((self.run_dir / "gaps.md").read_text(encoding="utf-8"), "# Gaps\n- none\n")

    def test_S11_a_discard_plan_outside_the_repository_ends_archived_leaving_origin_untouched(self):
        self.configure(self.root / "plans")
        self.folder_plan(discard_plan(), root=self.root / "plans")
        self.drive()
        self.assertEqual(self.ledger()["plan"]["state"], "ARCHIVED")
        self.assertNotIn("demo-archive", self.spawned())
        self.assertFalse(self.folder.exists())
        self.assertEqual(
            self.git(self.origin, "diff", "--name-only", self.base, "main"),
            "",
        )
        self.assertEqual(self.git(self.origin, "branch", "--list", "feat/demo-archive"), "")


class PlansDirDeletionTests(FolderArchiveFixture, unittest.TestCase):
    """A plan folder is deleted only when its resolved parent is the resolved plans.dir, asked again at the deletion
    itself: the plan's load refuses first, so these call the deletion directly, then drive a config that moves."""

    def driver(self):
        import argparse

        return load_script().Driver(
            argparse.Namespace(
                plan=str(self.plan),
                repo=str(self.repo),
                state_dir=str(self.state_dir),
                base="origin/main",
            )
        )

    def start(self, data, plans, tracked=False):
        """The plan approved under `plans`, which is the repo's plans.dir while it is approved."""
        self.configure(plans)
        self.folder_plan(data, root=plans, tracked=tracked)

    def drive_while_plans_dir_moves(self, moved, then=()):
        """The run, the repo config naming `moved` as plans.dir from the reviewer's step on, after the plan loaded."""
        steps = self.ticket_merged()
        config = yaml.safe_dump({"plans": {"dir": str(moved)}})
        steps[-1].setdefault("write", []).append(
            [str(self.repo / ".orchestrate" / "config.yaml"), config]
        )
        self.waits(steps + [self.gaps_done()] + list(then))
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
                "--models-file",
                str(self.models),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
            ],
        )

    def said(self, result):
        texts = [result.stdout, result.stderr]
        if self.run_dir.exists():
            texts += [p.read_text(encoding="utf-8") for p in self.run_dir.rglob("request*.json")]
        return "\n".join(texts)

    def test_S4_discarding_a_folder_outside_plans_dir_deletes_nothing_and_names_both(self):
        self.start(discard_plan(), self.root / "plans")
        self.configure(self.repo / "docs" / "plans")  # the config moved after the plan loaded
        with self.assertRaises(ValueError) as caught:
            self.driver().discard_plan({})
        self.assertTrue((self.folder / "plan.yaml").is_file())
        self.assertIn("plans.dir", str(caught.exception))
        self.assertIn(str(self.folder), str(caught.exception))
        self.assertIn(str(self.repo / "docs" / "plans"), str(caught.exception))

    def test_S4_the_plan_folder_of_a_promote_archive_tree_is_deleted_only_directly_under_plans_dir(
        self,
    ):
        self.start(promote_plan(), self.repo / "docs" / "plans", tracked=True)
        checkout = self.root / "archive-tree"
        kept = checkout / "docs" / "plans" / FOLDER / "plan.yaml"
        kept.parent.mkdir(parents=True)
        kept.write_text("x", encoding="utf-8")
        self.configure(self.repo / "docs" / "elsewhere")
        with self.assertRaises(ValueError) as caught:
            self.driver().remove_plan_folder(checkout)
        self.assertTrue(kept.is_file())
        self.assertIn("plans.dir", str(caught.exception))
        self.configure(self.repo / "docs" / "plans")  # control: the folder is directly under it
        self.driver().remove_plan_folder(checkout)
        self.assertFalse(kept.parent.exists())

    def test_S4_a_symlink_into_plans_dir_is_not_deleted(self):
        self.start(discard_plan(), self.root / "elsewhere")
        plans = self.root / "plans"
        plans.mkdir()
        (plans / FOLDER).symlink_to(self.folder)
        self.configure(plans)
        self.plan = plans / FOLDER / "plan.yaml"
        with self.assertRaises(ValueError):
            self.driver().discard_plan({})
        self.assertTrue((self.folder / "plan.yaml").is_file())

    def test_S4_a_discard_run_whose_plans_dir_moved_mid_run_is_not_archived_and_keeps_its_folder(
        self,
    ):
        self.start(discard_plan(), self.root / "plans")
        result = self.drive_while_plans_dir_moves(self.repo / "docs" / "plans")
        self.assertTrue((self.folder / "plan.yaml").is_file())
        self.assertNotEqual(self.ledger()["plan"]["state"], "ARCHIVED")
        said = self.said(result)
        self.assertIn("plans.dir", said)
        self.assertIn(str(self.folder), said)

    def test_S4_a_promote_archive_whose_plans_dir_moved_mid_run_asks_the_lead_and_deletes_nothing(
        self,
    ):
        self.start(promote_plan(), self.repo / "docs" / "plans", tracked=True)
        result = self.drive_while_plans_dir_moves(
            self.repo / "docs" / "elsewhere", then=[{}, self.decide_step("archive", "skip")]
        )
        self.assertNotEqual(self.ledger()["plan"]["state"], "ARCHIVED")
        self.assertEqual(self.gh_calls("pr", "create"), [])
        requests = sorted(self.run_dir.glob("archive/request*.json"))
        self.assertEqual(len(requests), 1)  # the archive asked the Lead, who then skipped it
        said = self.said(result)
        self.assertIn("plans.dir", said)
        self.assertIn(str(self.folder), said)
        self.assertTrue((self.folder / "plan.yaml").is_file())
