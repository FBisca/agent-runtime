"""The hand-in: a report that arrives as the text of the worker's `report` call is validated at once, refused back to
the worker that sent it with the exact errors, and recorded as an event."""

import json
import unittest

import pytest
from fixtures import report_wait, resume_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class HandInTests(GateFixture, unittest.TestCase):
    def amended(self, **over):
        return json.dumps(dict(json.loads(self.report("t1")), **over))

    def corrected(self, task_id="t1", nth=1):
        """A wait step: the warned worker hands in a report the driver accepts, under the assignment the refusal resumed."""
        return report_wait(
            "demo-t1", "complete", text=self.report(task_id), id=resume_id("demo-t1", nth)
        )

    def request_files(self):
        return sorted((self.run_dir / "t1").glob("request*.json"))

    def refusals(self):
        return self.run_events("report-refused")

    @pytest.mark.spec("A refused hand-in goes back to its sender")
    def test_a_report_with_an_unknown_field_is_refused_to_its_sender_with_the_exact_error(self):
        bad = self.amended(remaining=["the other ticket's work"])
        self.waits(
            [self.worker_done(tests=True, report=bad), self.corrected(), self.reviewer_done()]
        )
        result = self.run_driver()
        self.assertEqual(json.loads(result.stdout)["errors"], [])
        self.assertEqual(self.run_events("error"), [])
        self.assertEqual(self.request_files(), [], "a refusal never becomes a Lead request")
        (resume,) = self.calls("resume")
        self.assertEqual(resume["args"]["name"], "demo-t1")
        self.assertIn("remaining", resume["args"]["task"])
        self.assertIn("agent-runtime `report` call", resume["args"]["task"])
        (refused,) = self.refusals()
        self.assertIn("remaining", refused["reason"])
        self.assertIn("remaining", refused["payload"])
        self.assertEqual(self.task()["gate"], "done", "the corrected hand-in was gated as usual")
        self.assertEqual([e["report"]["task_id"] for e in self.run_events("report")], ["t1"])

    @pytest.mark.spec("A refused hand-in goes back to its sender")
    def test_text_that_is_not_json_and_another_tasks_report_are_refused_the_same_way(self):
        self.waits(
            [
                self.worker_done(tests=True, report="complete - see run directory"),
                report_wait(
                    "demo-t1", "complete", text=self.report("t9"), id=resume_id("demo-t1", 1)
                ),
                self.corrected(nth=2),
                self.reviewer_done(),
            ]
        )
        self.run_driver()
        reasons = [r["reason"] for r in self.refusals()]
        self.assertIn("not the report JSON", reasons[0])
        self.assertIn("t9", reasons[1])
        self.assertEqual(self.request_files(), [])
        self.assertEqual(self.task()["gate"], "done")

    @pytest.mark.spec("A refused hand-in goes back to its sender")
    def test_a_report_naming_a_commit_other_than_the_branch_head_is_refused(self):
        stale = self.amended(head="1234567")
        self.waits(
            [self.worker_done(tests=True, report=stale), self.corrected(), self.reviewer_done()]
        )
        self.run_driver()
        (refused,) = self.refusals()
        self.assertIn("1234567", refused["reason"])
        self.assertIn("HEAD", refused["reason"])
        self.assertEqual(self.request_files(), [])

    @pytest.mark.spec("A refused hand-in goes back to its sender")
    def test_the_third_refusal_asks_the_lead(self):
        bad = self.amended(contract=4)
        self.waits([*self.repeated(self.worker_done(report=bad)), self.skipped()])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2, "the sender was warned twice, no more")
        self.assertEqual(len(self.refusals()), 3)
        self.assertEqual(self.request()["event"], "report")
        self.assertIn("contract", self.request()["reason"])

    @pytest.mark.spec("A report is handed in through the runtime")
    def test_a_failed_check_and_notes_are_accepted_and_the_notes_are_recorded(self):
        report = self.amended(
            checks=[{"command": "pytest", "outcome": "failed", "reason": "flaky"}],
            notes=["the cache is never cleared"],
        )
        self.waits([self.worker_done(tests=True, report=report), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.refusals(), [])
        self.assertEqual(self.task()["gate"], "done")
        (event,) = self.run_events("report")
        self.assertEqual(event["report"]["notes"], ["the cache is never cleared"])

    @pytest.mark.spec("A report is handed in through the runtime")
    def test_a_blocked_report_goes_to_the_lead_with_its_blocker(self):
        blocked = self.amended(status="BLOCKED", blocker="needs the staging database")
        self.waits([self.worker_done(report=blocked), self.skipped()])
        self.run_driver()
        self.assertEqual(self.request()["event"], "blocked")
        self.assertIn("needs the staging database", self.request()["reason"])
        self.assertEqual(self.refusals(), [])


@pytest.mark.spec("A report is handed in through the runtime")
def test_the_plan_close_lists_the_notes_the_run_recorded(tmp_path):
    from types import SimpleNamespace

    from agent_orchestrator import events
    from agent_orchestrator.driver.archive import Archive

    log = tmp_path / "events.jsonl"
    events.append(log, "report", unit="t1", report={"notes": ["the cache is\nnever cleared"]})
    events.append(log, "report", unit="t2", report={"notes": []})
    text = Archive._run_notes(SimpleNamespace(run_dir=tmp_path))  # ty: ignore[invalid-argument-type]
    assert "- t1: the cache is never cleared" in text
    assert (
        Archive._run_notes(SimpleNamespace(run_dir=tmp_path / "none"))  # ty: ignore[invalid-argument-type]
        == ""
    )
