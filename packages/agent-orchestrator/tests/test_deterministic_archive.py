"""Outcome oracle for the deterministic-archive plan: the driver archives a plan itself, with no agent, and starts
the gaps reviewer only once the archive has merged.

Frozen at approval; placed into packages/agent-orchestrator/tests/ by the runner; never edited by a ticket.
It drives the `run` CLI through the suite's existing archive fixture (fake runtime, fake gh, a real git origin).
The contract it pins (plan Decisions D1-D6):

  - no `demo-archive` agent is spawned; the driver commits on `feat/demo-archive`, pushes it to origin and runs
    `gh pr create`, whose stdout is the PR url; the archive unit then merges and the plan is ARCHIVED
  - the spec on that branch is the base spec, unchanged, followed by one appended section:

        ## Requirements: <slug>                (omitted when every scenario is already in the spec)

        - **R<n>** WHEN <when>, THEN <then> (from <slug>/<id>)

        ## Coverage: <slug>

        | Requirement | Scenario | Test nodes |
        |---|---|---|
        | R<n> | <id> | `<covers node>`<br>`<placed oracle path>` |

  - `gaps` is spawned only after the archive unit is COMPLETE, and the run does not wait for its report

Each class has a control case that passes at base and after, so a class selection always has a passing case.
"""

import json
import unittest

import pytest
from test_archive import ARCHIVE, ArchiveFixture
from test_archive_yaml import commit_spec

pytestmark = pytest.mark.integration

SECTION = (
    "## Requirements: demo\n"
    "\n"
    "- **R{n}** WHEN a, THEN b (from demo/S1)\n"
    "\n"
    "## Coverage: demo\n"
    "\n"
    "| Requirement | Scenario | Test nodes |\n"
    "|---|---|---|\n"
    "| R{n} | S1 | `src/a/test_a.py::test_a`<br>`src/a/test_oracle.py` |\n"
)


class DriverArchiveFixture(ArchiveFixture):
    def run_to_archive(self):
        """T1 merges; nothing else is scripted: no archive worker report and no gaps report."""
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
    def test_control_the_plan_has_one_outcome_scenario_with_an_oracle(self):
        self.assertTrue((self.plan.parent / "demo.oracle" / "test_oracle.py.oracle").is_file())

    def test_the_driver_opens_the_archive_pr_itself_and_the_plan_is_archived(self):
        self.run_to_archive()
        self.assertNotIn(ARCHIVE, self.spawned())
        self.assertEqual(len(self.gh_calls("pr", "create")), 1)
        task = self.task("archive")
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        history = [h["state"] for h in self.ledger()["plan"]["history"]]
        self.assertEqual(history[-2:], ["ACCEPTED", "ARCHIVED"])
        # The url gh printed is the unit's PR: CI is polled on it, and the outcome evidence re-ran on its head.
        pr = "https://github.com/o/r/pull/9"
        report = json.loads((self.run_dir / "archive" / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["pr"], pr)
        self.assertIn(pr, self.checks_log())
        self.assertIn("S1.archive.1", self.ledger()["receipts"])

    def test_the_archive_branch_carries_the_plan_files_and_the_placed_oracle_byte_identical(self):
        self.run_to_archive()
        oracle = (self.plan.parent / "demo.oracle" / "test_oracle.py.oracle").read_text()
        self.assertEqual(self.on_branch("src/a/test_oracle.py"), oracle)
        self.assertEqual(self.on_branch("docs/plans/demo.oracle/test_oracle.py.oracle"), oracle)
        self.assertEqual(self.on_branch("docs/plans/" + self.plan.name), self.plan.read_text())


class SpecSection(DriverArchiveFixture, unittest.TestCase):
    def test_control_a_committed_spec_is_on_origin_main(self):
        commit_spec(self, "# Demo\n")
        self.assertEqual(self.git(self.origin, "show", "main:docs/specs/demo.md"), "# Demo")

    def test_the_section_is_appended_after_the_spec_unchanged_numbered_from_its_highest_requirement(
        self,
    ):
        base = "# Demo\n\n- **R7** WHEN x, THEN y (from other/S1)\n- **R3** WHEN z, THEN w (from other/S2)\n"
        commit_spec(self, base)
        self.run_to_archive()
        self.assertEqual(self.on_branch("docs/specs/demo.md"), base + "\n" + SECTION.format(n=8))

    def test_a_missing_spec_is_created_holding_the_section_alone(self):
        self.run_to_archive()
        self.assertEqual(self.on_branch("docs/specs/demo.md"), SECTION.format(n=1))

    def test_a_scenario_already_in_the_spec_is_not_appended_and_keeps_its_number_in_coverage(self):
        base = "# Demo\n\n- **R9** WHEN a THEN b. (from demo/S1)\n"
        commit_spec(self, base)
        self.run_to_archive()
        coverage = SECTION.format(n=9).split("## Coverage: demo")[1]
        self.assertEqual(
            self.on_branch("docs/specs/demo.md"), base + "\n## Coverage: demo" + coverage
        )


class GapsAfterArchive(DriverArchiveFixture, unittest.TestCase):
    def test_control_a_run_with_no_finished_ticket_spawns_no_gaps_reviewer(self):
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.decide_step("t1", "skip")]
        )
        self.prs(["CLOSED"])
        self.run_driver()
        self.assertNotIn("demo-gaps", self.spawned())

    def test_gaps_starts_after_the_archive_merges_and_the_run_does_not_wait_for_it(self):
        # No gaps report is scripted. The fakes end a run either way, so not waiting is pinned by T2's own test.
        self.run_to_archive()
        self.assertIn("demo-gaps", self.spawned())
        events = self.run_events()
        done = next(
            i
            for i, e in enumerate(events)
            if e["kind"] == "state"
            and e.get("unit") == "archive"
            and (e.get("status") or [0, 0])[1] == "COMPLETE"
        )
        gaps = next(
            i for i, e in enumerate(events) if e["kind"] == "agent" and e.get("name") == "demo-gaps"
        )
        self.assertGreater(gaps, done)
