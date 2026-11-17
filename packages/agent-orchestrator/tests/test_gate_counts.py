"""The Lead is asked after one gate fails three times, and a fix round never reuses a task id the ledger holds."""

import copy
import unittest
from unittest import mock

import pytest
from fixtures import load_script, report_wait, spawn_id
from test_review_verdict import FINDING, ReviewerFixture, block

pytestmark = pytest.mark.integration

DIRTY = {"state": "OPEN", "mergeStateStatus": "DIRTY", "mergeable": "CONFLICTING"}


class GateCountTests(ReviewerFixture, unittest.TestCase):
    @pytest.mark.spec("Failures are counted per gate")
    def test_a_review_block_then_a_conflict_each_get_a_fix_round_under_a_cap_of_one(self):
        rebased = report_wait("demo-t1", "complete", id=spawn_id("demo-t1", 2))
        rebased["write"] = [[str(self.run_dir / "t1r2" / "report.json"), self.report("t1r2")]]
        self.prs([DIRTY, "MERGED"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block(FINDING)),
                self.worker_done("t1r1"),
                self.reviewer_again(),
                rebased,
                self.reviewer_again(nth=2),
            ]
        )
        self.run_driver("--max-fix-rounds", "1")
        self.assertEqual([e["source"] for e in self.run_events("round")], ["review", "conflict"])
        self.assertEqual([e["attempt"] for e in self.run_events("round")], [1, 2])
        self.assertEqual(sorted((self.run_dir / "t1").glob("request*.json")), [])
        self.assertEqual(self.task("t1r2")["status"], "COMPLETE")


class FixRoundIdTests(ReviewerFixture, unittest.TestCase):
    @pytest.mark.spec("A fix round never reuses a task id")
    def test_a_fix_round_skips_a_task_id_the_ledger_already_holds(self):
        original = load_script().Gates.step_fix
        planted = []

        def plant(driver, task_id):
            # A record a driver restart left behind: same id, another assignment, released.
            if not planted:
                with driver.book() as data:
                    stale = copy.deepcopy(data["tasks"]["t1"])
                    stale["packet"].update(task_id="t1r1", objective="an earlier round")
                    stale.update(
                        status="RELEASED", release_reason="restart", gate="superseded", fix=None
                    )
                    data["tasks"]["t1r1"] = stale
                planted.append(True)
            return original(driver, task_id)

        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block(FINDING)),
                self.worker_done("t1r2"),
                self.reviewer_again(),
            ]
        )
        with mock.patch.object(load_script().Gates, "step_fix", plant):
            self.run_driver()
        self.assertEqual(self.run_events("request"), [])
        self.assertEqual(self.task("t1")["fix_task"], "t1r2")
        self.assertEqual(self.task("t1r2")["packet"]["task_id"], "t1r2")
        self.assertEqual(self.task("t1r1")["packet"]["objective"], "an earlier round")
