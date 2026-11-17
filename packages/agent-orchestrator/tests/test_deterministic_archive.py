"""The deterministic-archive plan's outcome: the driver archives a plan itself, with no agent.

It drives the `run` CLI through the suite's existing archive fixture (fake runtime, fake gh, a real git origin).
The contract it pins (plan Decisions D1-D6):

  - no `demo-archive` agent is spawned; the driver commits on `feat/demo-archive`, pushes it to origin and runs
    `gh pr create`, whose stdout is the PR url; the archive unit then merges and the plan is ARCHIVED
  - the spec on that branch is the base spec, unchanged, followed by the plan's added requirement block

Each class has a `control` test that holds whatever the change: it proves the fixture is real.
"""

import json
import unittest

import pytest
import yaml
from fixtures import scenarios_of
from test_archive import ARCHIVE, ArchiveFixture
from test_archive_yaml import commit_spec

pytestmark = [pytest.mark.integration, pytest.mark.spec("Archive applies the change set")]

BLOCK = "### Requirement: Demo\nThe demo SHALL deliver.\n\n#### Scenario: a\n- WHEN a\n- THEN b\n"


class DriverArchiveFixture(ArchiveFixture):
    def run_to_archive(self):
        """T1 merges; nothing else is scripted: no archive worker report."""
        self.waits(self.ticket_merged())
        self.run_driver()

    def on_branch(self, path):
        """A file's text on origin's `feat/demo-archive`, or None when the branch or file is absent."""
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(self.origin), "show", "feat/demo-archive:" + path],
            capture_output=True,
            text=True,
        )
        return result.stdout if result.returncode == 0 else None


class NoAgentArchive(DriverArchiveFixture, unittest.TestCase):
    def test_control_the_plan_has_one_scenario(self):
        data = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        self.assertEqual([s["id"] for s in scenarios_of(data)], ["S1"])

    def test_the_driver_opens_the_archive_pr_itself_and_the_plan_is_archived(self):
        self.run_to_archive()
        self.assertNotIn(ARCHIVE, self.spawned())
        self.assertEqual(len(self.gh_calls("pr", "create")), 1)
        task = self.task("archive")
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        history = [h["state"] for h in self.ledger()["plan"]["history"]]
        self.assertEqual(history[-2:], ["ACCEPTED", "ARCHIVED"])
        # The url gh printed is the unit's PR: CI is polled on it.
        pr = "https://github.com/o/r/pull/9"
        report = json.loads((self.run_dir / "archive" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["pr"], pr)
        self.assertIn(pr, self.checks_log())

    def test_the_archive_branch_carries_the_plan_file_byte_identical(self):
        self.run_to_archive()
        self.assertEqual(self.on_branch("docs/plans/" + self.plan.name), self.plan.read_text())


class SpecSection(DriverArchiveFixture, unittest.TestCase):
    def test_control_a_committed_spec_is_on_origin_main(self):
        commit_spec(self, "# Demo\n")
        self.assertEqual(self.git(self.origin, "show", "main:docs/specs/demo.md"), "# Demo")

    @pytest.mark.spec("Archive applies the change set")
    def test_the_added_block_is_appended_after_the_spec_unchanged(self):
        base = "# Demo\n\n## Requirements\n\n### Requirement: Other\nIt SHALL hold.\n"
        commit_spec(self, base)
        self.run_to_archive()
        self.assertEqual(self.on_branch("docs/specs/demo.md"), base + "\n" + BLOCK)

    @pytest.mark.spec("Archive applies the change set")
    def test_a_missing_spec_is_created_holding_the_block_alone(self):
        self.run_to_archive()
        self.assertEqual(self.on_branch("docs/specs/demo.md"), BLOCK)

    @pytest.mark.spec("Archive applies the change set")
    def test_the_archive_commit_carries_the_plan_goal(self):
        self.run_to_archive()
        message = self.git(self.origin, "log", "-1", "--format=%B", "feat/demo-archive")
        self.assertIn("deliver", message)
