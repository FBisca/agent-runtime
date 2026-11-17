"""One idle watchdog over every agent kind: a worker and a reviewer whose turn ended."""

import json
import unittest
from pathlib import Path

import pytest
from fixtures import assignment, envelope, resume_id, spawn_id, wait_result
from test_gates import REVIEWER, GateFixture

pytestmark = pytest.mark.integration


class SilentWorker(GateFixture, unittest.TestCase):
    def silent_worker(self, write=()):
        return wait_result([assignment("demo-t1", "working", "idle")], write=write)

    @pytest.mark.spec("One idle watchdog")
    def test_the_idle_request_names_the_worker(self):
        """orchestrator-audit-fixes/S11"""
        self.waits([self.silent_worker()] * 5 + [self.skipped()])
        self.run_driver("--idle-minutes", "0")
        self.assertIn("demo-t1", self.request()["reason"])


class LateReviewerComplete(GateFixture, unittest.TestCase):
    """An escalated reviewer's `complete` while its idle request is open (supersedes orchestrator-silent-idle/S13)."""

    def silent(self):
        return wait_result([assignment(REVIEWER, "working", "idle")])

    def gates(self):
        return [e["gate"][1] for e in self.run_events("state") if e["task"] == "t1"]

    @pytest.mark.spec("One idle watchdog")
    def test_S10_a_complete_before_the_lead_decides_finishes_the_review_and_closes_the_request(
        self,
    ):
        """orchestrator-audit-fixes/S10"""
        self.waits([self.worker_done(tests=True)] + [self.silent()] * 4 + [self.reviewer_done()])
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.run_events("escalation")[0]["cause"], "idle")
        self.assertIn("reviewed", self.gates())
        task = self.task()
        self.assertIn("reviewed_commit", task)
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        request = self.request()
        self.assertEqual(request["status"], "resolved")
        decision = json.loads((self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8"))
        self.assertIn("worker", decision["reason"])
        self.assertEqual(len(self.wake_lines("decision")), 1)

    @pytest.mark.spec("One idle watchdog")
    def test_S10_a_complete_after_the_lead_decided_is_ignored(self):
        """orchestrator-audit-fixes/S10 — the decision and the complete reach the driver in
        one wait, the decision first: the request is no longer undecided."""
        entry = assignment(REVIEWER, "complete")
        late = self.decide_step("t1", "mark blocked", messages=[envelope(entry, "complete")])
        late["write"] = [[str(self.run_dir / "t1" / "review.json"), self.review()]]
        self.waits([self.worker_done(tests=True)] + [self.silent()] * 4 + [late])
        self.run_driver("--idle-minutes", "0")
        task = self.task()
        self.assertEqual(task["gate"], "blocked")
        self.assertNotIn("reviewed_commit", task)
        self.assertNotIn("reviewed", self.gates())


class ReviewerAnswer(GateFixture, unittest.TestCase):
    """A request raised while the reviewer owed its review is answered to the reviewer, not the implementer."""

    ANSWER = "the cache is cleared on logout, so K1 is not a defect"

    def silent(self):
        return wait_result([assignment(REVIEWER, "working", "idle")])

    def answered(self, then, lost=False):
        """Waits up to the Lead's `answer` on the reviewer's idle request, then `then`; with `lost`, the reviewer's pane
        is gone by the time the answer is applied."""
        decision = self.decide_step("t1", "answer", "carry on", answer=self.ANSWER)
        if lost:
            decision["write"] = self.goes(REVIEWER)
        return [self.worker_done(tests=True)] + [self.silent()] * 4 + [decision] + then

    def resumed(self, name):
        return [c for c in self.calls("resume") if c["args"].get("name") == name]

    @pytest.mark.spec("A decision reaches its worker")
    def test_S9_an_answer_to_an_idle_reviewer_resumes_the_reviewer_not_the_implementer(self):
        """engine-gaps/S9"""
        finished = self.reviewer_done()
        finished["assignments"] = [assignment(REVIEWER, "complete", id=resume_id(REVIEWER))]
        finished["messages"] = [envelope(finished["assignments"][0], "complete")]
        self.waits(self.answered([finished]))
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.request()["event"], "idle")
        self.assertEqual(self.resumed("demo-t1"), [], "the implementer was not the one answered")
        (call,) = self.resumed(REVIEWER)
        self.assertIn(self.ANSWER, call["args"]["task"] + self.brief_text())
        task = self.task()
        self.assertEqual(task["review"]["assignment_id"], resume_id(REVIEWER))
        self.assertIn("reviewed_commit", task)
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))

    def brief_text(self):
        """What the answered reviewer can read: the files the resume names, under the review's directory."""
        folder = Path(self.task()["review"]["dir"])
        return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md")))

    @pytest.mark.spec("A decision reaches its worker")
    def test_S9_a_reviewer_whose_pane_is_gone_is_replaced_by_one_whose_brief_carries_the_answer(
        self,
    ):
        """engine-gaps/S9"""
        late = self.reviewer_done()
        late["assignments"] = [assignment(REVIEWER, "complete", id=spawn_id(REVIEWER, 2))]
        late["messages"] = [envelope(late["assignments"][0], "complete")]
        self.waits(self.answered([late], lost=True))
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.resumed("demo-t1"), [])
        self.assertEqual(self.spawned().count(REVIEWER), 2, "a fresh reviewer replaced it")
        self.assertIn(self.ANSWER, self.brief_text())
        self.assertEqual(self.task()["review"]["assignment_id"], spawn_id(REVIEWER, 2))

    @pytest.mark.spec("A decision reaches its worker")
    def test_S9_a_refused_reviewer_resume_asks_again_and_the_next_answer_still_reaches_the_reviewer(
        self,
    ):
        """engine-gaps/S9"""
        (self.home / "refuse_resume.json").write_text(
            json.dumps({REVIEWER: "the pane is blocked on a login"}), encoding="utf-8"
        )
        again = self.decide_step("t1", "answer", "the pane is back", answer=self.ANSWER)
        again["write"] = [[str(self.home / "refuse_resume.json"), "{}"]]
        finished = self.reviewer_done()
        finished["assignments"] = [assignment(REVIEWER, "complete", id=resume_id(REVIEWER, 2))]
        finished["messages"] = [envelope(finished["assignments"][0], "complete")]
        self.waits(self.answered([again, finished]))
        self.run_driver("--idle-minutes", "0")
        second = self.request_file(2)
        self.assertEqual((second["event"], second["status"]), ("handover", "resolved"))
        self.assertIn("reach demo-t1-review", second["reason"])
        self.assertIn("answer again once the pane works, or mark blocked or skip", second["reason"])
        self.assertEqual([o["action"] for o in second["menu"]], ["answer", "mark blocked", "skip"])
        self.assertEqual(self.resumed("demo-t1"), [], "the implementer was never answered")
        self.assertEqual(len(self.resumed(REVIEWER)), 2, "the refused resume and the retry")
        self.assertEqual((self.task()["status"], self.task()["gate"]), ("COMPLETE", "done"))

    def request_file(self, nth):
        path = self.run_dir / "t1" / ("request.json" if nth == 1 else "request.%d.json" % nth)
        return json.loads(path.read_text(encoding="utf-8"))
