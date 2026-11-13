"""Acceptance for the engine-gaps plan: a decision the driver cannot apply comes back to the Lead as a new request, a
worker's complete that lands meanwhile is acted on, and an `answer` to a request about an idle reviewer reaches the
reviewer.

Frozen at approval; placed at packages/agent-orchestrator/acceptance/driver/ by the runner; never edited by a ticket.
It drives `run` and `decide` through the gate fixture (fake runtime, fake gh). The contract it pins (plan Decisions
D6-D8):

  - the fake runtime refuses a `resume` of each name in `<home>/refuse_resume.json` ({name: error text}) with that
    text, as hc refuses a pane blocked on a login
  - a decision whose hand-over fails writes the unit's next request (`request.2.json`) naming the failure, and the
    unit is not left silent
  - a `complete` from the unit's worker while that request is open is acted on by the next decision, with no new
    hand-over
  - `answer` to a request raised while the reviewer owed its review resumes the reviewer with the answer, not the
    implementer

Each class has a control case that passes at base and after.
"""

import json
import unittest

import pytest
from fixtures import assignment, report_wait, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration

WORKER = "demo-t1"
REVIEWER = "demo-t1-review"
LOGIN = "t29b is blocked (auth)"
ANSWER = "write review.json from the local suite now; the CI wait is the driver's"


class DecisionFixture(GateFixture):
    def nth_request(self, nth):
        path = self.run_dir / "t1" / ("request.json" if nth == 1 else "request.%d.json" % nth)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def run_unchecked(self, *extra):
        """The run with its exit code left to the test: what follows the decision is not the scenario's."""
        from fixtures import load_script

        self.alias_state(self.plan)
        argv = [
            "run",
            str(self.plan),
            "--state-dir",
            str(self.state_dir),
            "--routes-file",
            str(self.route_file),
        ]
        argv += ["--wait-seconds", "1", "--idle-interval", "0", *extra]
        return self.in_process(load_script(), argv)

    def resumes(self, name):
        return [c for c in self.calls("resume") if c["args"].get("name") == name]

    def carries(self, call, text):
        """Whether a runtime call hands `text` over: in its arguments, or in a file its arguments name."""
        blob = json.dumps(call["args"])
        if text in blob:
            return True
        for path in self.run_dir.rglob("*"):
            if path.is_file() and str(path) in blob and text in path.read_text(encoding="utf-8"):
                return True
        return False


class FailedDecision(DecisionFixture, unittest.TestCase):
    def test_control_a_blocked_worker_raises_a_request_naming_its_blocker(self):
        self.waits([report_wait(WORKER, "blocked", "cannot reach the database"), self.skipped()])
        self.run_driver()
        self.assertIn("cannot reach the database", self.nth_request(1)["reason"])

    def test_a_decision_that_cannot_be_handed_over_comes_back_and_a_late_complete_is_acted_on(self):
        refused = [[str(self.home / "refuse_resume.json"), json.dumps({WORKER: LOGIN})]]
        self.waits(
            [
                report_wait(WORKER, "blocked", "the session needs a login"),
                self.decide_step("t1", "answer", "carry on", answer="carry on", write=refused),
                wait_result(),
                self.worker_done(tests=True),
                self.decide_step("t1", "retry", "the worker finished"),
                self.reviewer_done(),
            ]
        )
        self.run_unchecked()
        again = self.nth_request(2)
        self.assertIsNotNone(again, "the failed hand-over raised no new request")
        self.assertIn("blocked (auth)", again["reason"])
        self.assertEqual(
            len(self.resumes(WORKER)), 1, "the finished worker was handed the unit again"
        )
        self.assertEqual(
            self.spawned().count(WORKER), 1, "a fresh worker was spawned for a finished unit"
        )
        held = [e.get("held") for e in self.run_events("held")]
        self.assertIn("complete", held, "the late complete was not held")
        self.assertIn(REVIEWER, self.spawned(), "the late complete was not acted on")


class ReviewerAnswer(DecisionFixture, unittest.TestCase):
    def silent(self, runtime_state="idle"):
        return wait_result([assignment(REVIEWER, "working", runtime_state)])

    def test_control_an_idle_reviewer_raises_an_idle_request(self):
        self.waits(
            [
                self.worker_done(tests=True),
                self.silent("idle"),
                self.silent("done"),
                self.silent("idle"),
                self.silent("idle"),
                self.silent("idle"),
                self.skipped(),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.nth_request(1)["event"], "idle")

    def test_an_answer_about_an_idle_reviewer_resumes_the_reviewer_with_the_answer(self):
        self.waits(
            [
                self.worker_done(tests=True),
                self.silent("idle"),
                self.silent("done"),
                self.silent("idle"),
                self.silent("idle"),
                self.silent("idle"),
                self.decide_step("t1", "answer", "the reviewer waits on CI", answer=ANSWER),
            ]
        )
        self.run_unchecked("--idle-minutes", "0")
        self.assertEqual(self.nth_request(1)["event"], "idle")
        reviewer = self.resumes(REVIEWER)
        self.assertTrue(reviewer, "the answer did not resume the reviewer")
        self.assertTrue(
            any(self.carries(c, ANSWER) for c in reviewer), "the reviewer was not given the answer"
        )
        self.assertEqual(self.resumes(WORKER), [], "the answer went to the implementer")
        self.assertEqual(self.spawned().count(WORKER), 1, "the answer started a fresh implementer")
        events = self.run_events("decision", "state")
        after = events[[e["kind"] for e in events].index("decision") + 1 :]
        gates = [e["gate"][1] for e in after if e["kind"] == "state" and e.get("task") == "t1"]
        self.assertNotIn(None, gates, "the answer took the unit off its review: %s" % gates)


if __name__ == "__main__":
    unittest.main()
