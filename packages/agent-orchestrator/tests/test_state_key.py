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

    def test_S8_the_old_ledger_is_imported_once_into_the_repository_that_owns_it(self):
        """plan-contract/S8: <state>/<slug>.json goes to the repository whose tickets it holds, one time"""
        self.drive(self.repo2, self.plan2)
        # What an older driver left: the same files, one level up, under the repository's slug alone.
        keyed = self.state_dir / self.key2
        (keyed / "demo.json").rename(self.state_dir / "demo.json")
        (keyed / "demo").rename(self.state_dir / "demo")
        shutil.rmtree(keyed)
        legacy = json.loads((self.state_dir / "demo.json").read_text(encoding="utf-8"))
        legacy["marker"] = "old"
        (self.state_dir / "demo.json").write_text(json.dumps(legacy), encoding="utf-8")
        (self.state_dir / "demo" / "old.txt").write_text("from the old run", encoding="utf-8")

        self.drive(self.repo, self.plan)  # another repository's run does not take it
        self.assertTrue((self.state_dir / "demo.json").is_file())
        self.assertNotIn("marker", self.ledger_of(self.key))

        self.drive(self.repo2, self.plan2, expect=1)  # its owner does
        self.assertFalse((self.state_dir / "demo.json").exists())
        self.assertFalse((self.state_dir / "demo").exists())
        self.assertEqual(self.ledger_of(self.key2)["marker"], "old")
        self.assertEqual(
            (self.state_dir / self.key2 / "demo" / "old.txt").read_text(encoding="utf-8"),
            "from the old run",
        )

        # once: a ledger written under the old key later is not imported over the new one
        (self.state_dir / "demo.json").write_text(
            json.dumps(dict(legacy, marker="again")), encoding="utf-8"
        )
        self.drive(self.repo2, self.plan2, expect=1)
        self.assertEqual(self.ledger_of(self.key2)["marker"], "old")
        self.assertTrue((self.state_dir / "demo.json").is_file())
