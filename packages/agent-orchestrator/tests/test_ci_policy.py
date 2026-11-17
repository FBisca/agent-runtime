"""What the driver does with a failed check: re-run it once, ask the Lead when main fails it too, else hand the
worker the last lines of each failed step; and a conflict with another open ticket waits for that ticket."""

import itertools
import json
import unittest
from types import SimpleNamespace
from unittest import mock

import pytest
from agent_orchestrator.driver.pull import Pull
from fixtures import load_script, report_wait, resume_id
from test_pr import PrFixture

pytestmark = pytest.mark.integration


class CiPolicyTests(PrFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.home / "rerun.json").write_text(json.dumps([[0, ""]]), encoding="utf-8")

    def quiet_clock(self):
        """Every reading is ten minutes later than the last, so no re-run is still inside its grace."""
        moments = itertools.count(0, 600)
        return mock.patch.object(load_script().Driver, "clock", staticmethod(lambda: next(moments)))

    def log(self, lines):
        (self.home / "run-view.json").write_text(
            json.dumps([[0, "\n".join("unit tests\tRun pytest\t%s" % ln for ln in lines), ""]]),
            encoding="utf-8",
        )

    def rereview(self):
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        return again

    @pytest.mark.spec("A failed check is re-run once")
    def test_a_failure_that_passes_on_the_one_re_run_proceeds_and_is_recorded_as_flaky(self):
        self.checks([("fail", "unit tests"), "green"])
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        with self.quiet_clock():
            self.run_driver()
        self.assertEqual(self.gh_calls("run", "rerun"), [["run", "rerun", "77", "--failed"]])
        self.assertEqual(self.run_events("round"), [])
        (flaky,) = self.run_events("flaky")
        self.assertEqual(flaky["checks"], ["unit tests"])
        self.assertEqual(self.task()["status"], "COMPLETE")

    @pytest.mark.spec("A failed check passing on main goes back to the worker")
    def test_a_failure_after_the_re_run_goes_back_with_the_last_fifty_lines_of_the_failed_step(
        self,
    ):
        self.checks([("fail", "unit tests"), ("fail", "unit tests"), "green"])
        self.log(["line %d" % n for n in range(1, 81)])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.worker_done("t1r1"),
                self.rereview(),
            ]
        )
        with self.quiet_clock():
            self.run_driver()
        self.assertEqual(len(self.gh_calls("run", "rerun")), 1, "one automatic re-run, no more")
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        (item,) = findings["items"]
        self.assertEqual((findings["source"], item["check"]), ("ci", "unit tests"))
        self.assertEqual(item["command"], "gh run view 77 --log-failed")
        self.assertIn("unit tests / Run pytest (run 77):", item["excerpt"])
        self.assertIn("line 80", item["excerpt"])
        self.assertIn("line 31", item["excerpt"])
        self.assertNotIn("line 30\n", item["excerpt"] + "\n")
        brief = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("Re-run those tests locally", brief)
        self.assertIn("environment:", brief)

    @pytest.mark.spec("A failed check that also fails on main asks the Lead once")
    def test_a_check_that_also_fails_on_main_is_one_lead_request_and_no_worker_round(self):
        (self.home / "runs-main.json").write_text(
            json.dumps([[{"databaseId": 5}]]), encoding="utf-8"
        )
        (self.home / "main-jobs.json").write_text(
            json.dumps([{"jobs": [{"name": "unit tests", "conclusion": "failure"}]}]),
            encoding="utf-8",
        )
        self.checks([("fail", "unit tests")])
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.working(), self.skipped()]
        )
        with self.quiet_clock():
            self.run_driver()
        request = self.request()
        self.assertEqual(request["event"], "ci")
        self.assertIn("main is red", request["reason"])
        self.assertEqual(self.gh_calls("run", "rerun"), [])
        self.assertEqual(self.run_events("round"), [])
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(len(self.gh_calls("run", "list")), 1, "main is asked about once per run")

    @pytest.mark.spec("A failed check is re-run once")
    def test_a_job_that_fails_twice_with_no_failed_step_asks_the_lead(self):
        self.checks([("fail", "unit tests"), ("fail", "unit tests")])
        (self.home / "run-view.json").write_text(json.dumps([[0, "", ""]]), encoding="utf-8")
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), self.working(), self.skipped()]
        )
        with self.quiet_clock():
            self.run_driver()
        self.assertIn("infrastructure", self.request()["reason"])
        self.assertEqual(self.run_events("round"), [])


class ConflictWaitTests(unittest.TestCase):
    def lane(self, files, units):
        calls = []

        def gh(argv, **_):
            calls.append(argv)
            return 0, json.dumps({"files": [{"path": p} for p in files[argv[2]]]}), ""

        driver = SimpleNamespace(
            gh=gh,
            unit_of=lambda task_id: "t1",
            units=lambda: {u: {"packet": {"task_id": u}} for u in units},
            pr_of=lambda task_id: "pr-" + task_id,
        )
        lane = Pull(driver)
        lane.gh, lane.unit_of, lane.units, lane.pr_of = (
            gh,
            driver.unit_of,
            driver.units,
            driver.pr_of,
        )
        return lane

    @pytest.mark.spec("A conflicting pull request is rebased")
    def test_a_conflict_over_files_another_open_ticket_changes_names_that_ticket(self):
        files = {"pr-t1": ["a.py", "b.py"], "pr-t2": ["b.py"], "pr-t3": ["c.py"]}
        lane = self.lane(files, ["t1", "t2", "t3"])
        self.assertEqual(lane._conflicting_ticket("t1", "pr-t1"), "t2")

    @pytest.mark.spec("A conflicting pull request is rebased")
    def test_a_conflict_nobody_else_touches_is_rebased_now(self):
        files = {"pr-t1": ["a.py"], "pr-t2": ["b.py"]}
        self.assertIsNone(self.lane(files, ["t1", "t2"])._conflicting_ticket("t1", "pr-t1"))
