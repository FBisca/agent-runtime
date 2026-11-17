"""The pull request lane has one home: `driver/pull.py`, and a `Pull` the Driver holds.

Polling a unit's pull request, reading its checks, putting it back to draft, bringing it up to date,
taking a conflict to a fix round, reading the failing checks as evidence, and settling a merge — the
report it completes with, the status it writes, the findings it defers, the worktree it keeps — is one
method of the collaborator, so the lane reads in one place instead of seven thousand lines apart. The
names the package, the gate steps and the Lead's commands ask for still resolve on the Driver, and
`agent_orchestrator.driver` is still a package inside the member's source directory.
"""

import inspect
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.unit

# <repo>/packages/agent-orchestrator/tests/test_driver/<this file>
REPO = Path(__file__).resolve().parents[4]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
PULL = MEMBER / "driver" / "pull.py"

# What the lane asks GitHub, and what it writes down once GitHub answers: the pull request reads, the
# draft and update round, the conflict, the checks, the merge and what a merge settles.
MOVED = (
    "gh",
    "closed_prs",
    "pr_url",
    "pr_of",
    "pr_view",
    "pr_checks",
    "units",
    "unit_flag",
    "poll",
    "ensure_draft",
    "undraft",
    "unready",
    "poll_checks",
    "merge_moved",
    "update_branch",
    "conflict",
    "ci_items",
    "put_status",
    "ticket_name",
    "mark_merged",
    "complete_merged",
    "merged_early",
    "merged_report",
    "keep",
)

# The log helpers the lane reads a failing job with: they move with the only caller that needs them.
HELPERS = ("CI_EXCERPT_CHARS", "job_log", "log_tail")


class PullHomeTests(unittest.TestCase):
    """S10: the pull request lane lives in `driver/pull.py`, and the Driver holds it as one collaborator."""

    def test_S10_the_pull_members_live_in_one_module(self):
        from agent_orchestrator import driver
        from agent_orchestrator.driver import pull

        self.assertEqual(Path(pull.__file__).resolve(), PULL.resolve())
        self.assertEqual(
            Path(driver.__file__).resolve().parent.parent,
            MEMBER.resolve(),
            "the engine still has one home: the member's source directory",
        )
        for name in MOVED:
            with self.subTest(name=name):
                self.assertIn(name, vars(pull.Pull), "%s is not the collaborator's own" % name)
                self.assertTrue(
                    callable(getattr(driver.Driver, name)),
                    "the package, the gate steps and the suite ask `Driver.%s`" % name,
                )
        # A helper the lane reads a failing job with lives beside the lane, and the package publishes
        # the one copy rather than a second rule.
        for name in HELPERS:
            with self.subTest(name=name):
                self.assertIn(name, vars(pull), "%s did not move with the lane" % name)
                self.assertIs(getattr(driver, name), getattr(pull, name))

    def test_S10_the_driver_reaches_every_moved_member_through_the_collaborator(self):
        from agent_orchestrator import driver as engine

        driver = engine.Driver.__new__(engine.Driver)
        lane = driver.pull
        self.assertIs(lane.driver, driver, "a Driver a test stubs without `__init__` still answers")
        self.assertIs(driver.pull, lane, "the lane is built once, not per ask")
        for name in MOVED:
            with self.subTest(name=name):
                body = [
                    line.strip()
                    for line in inspect.getsource(getattr(engine.Driver, name)).splitlines()
                    if line.strip()
                ]
                self.assertTrue(
                    body[-1].startswith("return self.pull.%s(" % name),
                    "%s answers for itself instead of the collaborator" % name,
                )
                self.assertLessEqual(len(body), 5, "%s carries more than the delegation" % name)
        # The delegation is what runs: one merge-state read, asked once, with the arguments it was given.
        task_id, url, view = "t1r1", "https://example.test/o/r/pull/1", {"mergeable": "MERGEABLE"}
        with mock.patch.object(engine.Pull, "merge_moved", return_value=True) as merge_moved:
            self.assertTrue(driver.merge_moved(task_id, url, view))
        merge_moved.assert_called_once_with(task_id, url, view)


if __name__ == "__main__":
    unittest.main()
