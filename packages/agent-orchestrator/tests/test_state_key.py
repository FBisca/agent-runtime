"""A YAML plan's ledger is keyed by repository and slug: two repositories may run the same slug."""

import json
import shutil
import unittest

import pytest
from fixtures import RunFixture, assignment, load_script, template_repo, wait_result

pytestmark = pytest.mark.integration


class StateKeyFixture(RunFixture):
    alias_repo_key = False  # these tests read the real layout

    def setUp(self):
        super().setUp()
        self.repo2 = self.root / "repo2"
        template = template_repo()
        shutil.copytree(template / "origin.git", self.root / "origin2.git")
        shutil.copytree(template / "repo", self.repo2)
        self.git(self.repo2, "remote", "set-url", "origin", str(self.root / "origin2.git"))
        self.plan = self.approved_yaml_plan(("T1",))
        self.plan2 = self.approved_yaml_plan(("T1",), repo=self.repo2)
        spec = load_script()
        self.key, self.key2 = spec.repo_key(self.repo), spec.repo_key(self.repo2)

    def drive(self, repo, plan, expect=0):
        """One drive of the plan: its worker is spawned, the run ends before it reports. A drive that finds an
        imported ledger strands that ledger's worker, which this run's runtime never assigned: it exits 1."""
        self.waits([wait_result([assignment("demo-t1", "working")], reason="timeout")])
        self.run_driver("--repo", str(repo), plan=plan, expect=expect)

    def ledger_of(self, key):
        return json.loads((self.state_dir / key / "demo.json").read_text(encoding="utf-8"))

    def directories(self):
        return sorted(p.name for p in self.state_dir.iterdir() if p.is_dir())


class StateKeyTests(StateKeyFixture, unittest.TestCase):
    @pytest.mark.spec("Ledger and status")
    def test_S8_two_repositories_with_one_slug_each_get_their_own_ledger(self):
        """plan-contract/S8: <state>/<repo-key>/<slug>, nothing under the old key"""
        self.assertNotEqual(self.key, self.key2)
        self.drive(self.repo, self.plan)
        self.drive(self.repo2, self.plan2)
        first, second = self.ledger_of(self.key), self.ledger_of(self.key2)
        self.assertTrue(first["tasks"]["t1"]["packet"]["checkout"].startswith(str(self.repo)))
        self.assertTrue(second["tasks"]["t1"]["packet"]["checkout"].startswith(str(self.repo2)))
        self.assertTrue((self.state_dir / self.key / "demo" / "events.jsonl").is_file())
        self.assertTrue((self.state_dir / self.key2 / "demo" / "events.jsonl").is_file())
        self.assertFalse((self.state_dir / "demo.json").exists())
        self.assertEqual(self.directories(), sorted(["approvals", self.key, self.key2]))

    def test_S8_the_key_is_the_repository_not_the_checkout_or_the_state_dir(self):
        """plan-contract/S8: a worktree of a repository has its repository's key"""
        spec = load_script()
        worktree = self.root / "wt"
        self.git(self.repo, "worktree", "add", "-q", "--detach", str(worktree))
        self.assertEqual(spec.repo_key(worktree), self.key)
        self.assertTrue(self.key.startswith("repo-"), self.key)
