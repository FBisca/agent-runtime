"""Acceptance for the locator-invariants plan: a pointer names the thing, never a rule for finding it.

The review packet is the case that cost a gate round. A unit re-dispatched to a second attempt (`t1d1`)
writes its report in `t1d1/`, and the reviewer's brief named `t1`'s directory and a `report*.json` glob
instead, so the reviewer could only ever read the first attempt's file. The three contract points
pinned here (plan Decisions D1-D3):

  - the brief names the exact report path the ledger recorded for the attempt under review, with that
    file's mtime in UTC seconds and the head its report claims, so a reader can see which attempt it
    is and how old it is without searching anything;
  - no pointer in the brief is a rule for finding a file: `newest`, `report*.json` and a bare
    directory to search are gone from it;
  - a review written under another name inside the run directory is not the review: the gate asks for
    the file the driver named, because with that name a second file is a second answer, not a fallback.

Frozen at approval; placed at packages/agent-orchestrator/acceptance/driver/ by the runner; never
edited by a ticket. It drives `run` through the package's own gate fixture (fake runtime, fake gh).
"""

import json
import unittest
from datetime import datetime, timezone

import pytest
from fixtures import report_wait, resume_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration

SECOND = "def5678"
TEST_FILE = "src/a/test_a.py"
# The id the driver gives the unit's first re-dispatch: the unit, the letter that says why the attempt
# exists (`d` for a retry) and its number.
SECOND_ATTEMPT = "t1d1"


def written_at(path):
    """The mtime the brief must print: UTC, whole seconds, as `YYYY-MM-DDTHH:MM:SSZ`."""
    moment = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class PacketFixture(GateFixture):
    """A unit that reached its second attempt and then reported on it."""

    def blocked(self, text="cannot reach the database"):
        """A wait step: the unit's worker cannot go on, so the Lead is asked."""
        return report_wait("demo-t1", "blocked", text)

    def decided(self, action):
        """A wait step during which the Lead decides the unit's open request."""
        return self.decide_step("t1", action)

    def reported(self, task_id, name="demo-t1", resumed=1, head=SECOND):
        """A wait step: the worker on its `resumed`th resume wrote `report.json` for `task_id` and says complete."""
        step = report_wait(name, "complete", id=resume_id(name, resumed))
        step["write"] = [
            [str(self.run_dir / task_id / "report.json"), self.report(task_id, head=head)],
            [str(self.worktree / TEST_FILE), 'def test_a():\n    """S1"""\n'],
        ]
        return step

    def brief(self):
        return (self.run_dir / "t1-review" / "task.md").read_text(encoding="utf-8")

    def re_dispatched(self):
        """Run the driver until the reviewer of the unit's second attempt has its brief written."""
        self.waits(
            [
                self.blocked(),
                self.decided("retry"),
                self.reported(SECOND_ATTEMPT),
            ]
        )
        self.run_driver()

    def reviewer_wrote(self, path, text=None):
        """A wait step: the reviewer wrote its review at `path` and named that file in its report."""
        step = report_wait("demo-t1-review", "complete", text=text or "0 blocking - %s" % path)
        step["write"] = [[str(path), self.review2()]]
        return step

    def reviewer_resumes(self):
        return [c for c in self.calls("resume") if c["args"].get("name") == "demo-t1-review"]


class ReviewPacketPointsAtTheAttempt(PacketFixture, unittest.TestCase):
    def test_S1_the_brief_names_the_second_attempts_report_its_mtime_and_its_claimed_head(self):
        """orchestrator-engine/R145, R209: locator-invariants/S1"""
        self.re_dispatched()
        report = self.run_dir / SECOND_ATTEMPT / "report.json"
        self.assertTrue(report.is_file(), "the second attempt reported")
        self.assertEqual(self.task(SECOND_ATTEMPT)["report_path"], str(report))
        brief = self.brief()
        self.assertIn(str(report), brief, "the brief names the report of the attempt under review")
        self.assertIn(written_at(report), brief, "the brief says how old that report is")
        self.assertIn(SECOND, brief, "the brief names the head the report claims")

    def test_S1_no_pointer_in_the_brief_is_a_rule_for_finding_a_file(self):
        """orchestrator-engine/R145: locator-invariants/S1"""
        self.re_dispatched()
        brief = self.brief()
        for rule in ("newest", "report*", "*.json"):
            self.assertNotIn(rule, brief, "the brief searches for a file instead of naming it")

    def test_control_the_first_attempts_brief_still_names_the_task_and_the_plan(self):
        """The pass still prints every path it works from: this holds at base and after."""
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        brief = self.brief()
        for needle in (
            str(self.run_dir / "t1" / "task.md"),
            str(self.plan),
            str(self.run_dir / "contract.md"),
            str(self.run_dir / "receipts"),
        ):
            self.assertIn(needle, brief)


class ReviewWrittenUnderAnotherName(PacketFixture, unittest.TestCase):
    def test_S2_a_review_under_another_name_is_not_the_review_the_gate_reads(self):
        """orchestrator-engine/R155 (repealed): locator-invariants/S2"""
        elsewhere = self.run_dir / "t1-review" / "review.json"
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_wrote(elsewhere)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertNotIn(
            "reviewed_commit", self.task(), "a file under another name is not the review"
        )
        self.assertEqual(self.request()["event"], "review")
        self.assertTrue(self.reviewer_resumes(), "the reviewer is asked for the file it was named")
        self.assertFalse(
            (self.run_dir / "t1" / "review.json").is_file(),
            "the driver wrote nothing into the path it named on the reviewer's behalf",
        )

    def test_control_a_review_at_the_named_path_is_the_review(self):
        """The path the assignment names is read: this holds at base and after."""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertIn("reviewed_commit", self.task())
        self.assertEqual(
            json.loads((self.run_dir / "t1" / "review.json").read_text(encoding="utf-8"))[
                "verdict"
            ],
            "pass",
        )
