"""One idle watchdog over every agent kind: a reviewer, a mutation reviewer and a gaps reviewer whose turn ended."""

import json
import unittest
from pathlib import Path

import pytest
from fixtures import assignment, envelope, resume_id, spawn_id, wait_result
from test_acceptance import AcceptanceFixture
from test_gates import REVIEWER, GateFixture

pytestmark = pytest.mark.integration

MUTATOR = "demo-mut-s1-1"
GAPS = "demo-gaps"
PATCH = "--- a/src/a/feature.txt\n+++ b/src/a/feature.txt\n@@ -1 +1 @@\n-done\n+nope\n"


class SilentSupportReviewers(AcceptanceFixture, unittest.TestCase):
    """The mutation and gaps reviewers are no ticket's agent; their idle turn is still the Lead's business."""

    def silent_mutator(self, write=True):
        """A wait step: the mutation reviewer's turn ended (idle) with no `complete`; with `write`, its patch is on disk."""
        step = wait_result([assignment(MUTATOR, "working", "idle")])
        if write:
            step["write"] = [[str(self.run_dir / "mutation" / "S1.1.patch"), PATCH]]
        return step

    def silent_gaps(self, write=True):
        """A wait step: the gaps reviewer's turn ended with no `complete`; with `write`, gaps.md is on disk."""
        step = wait_result([assignment(GAPS, "working", "idle")])
        if write:
            step["write"] = [[str(self.run_dir / "gaps.md"), "# Gaps\n- none\n"]]
        return step

    def idle_wakes(self, name):
        return [w for w in self.wakes() if name in w.get("text", "") and "idle" in w["text"]]

    def landed(self):
        return self.run_events("output-landed")

    def test_S8_a_mutation_reviewer_silent_after_writing_its_patch_is_accepted(self):
        """orchestrator-engine/R82: orchestrator-audit-fixes/S8"""
        self.write_plan()
        self.merged_on_main("done")
        self.waits([self.worker("done"), self.reviewer(), self.silent_mutator()])
        self.run_driver("--repo", str(self.repo))
        self.assertEqual([e["agent"] for e in self.landed()], [MUTATOR])
        (receipt,) = self.receipts("mutation")
        self.assertTrue(receipt["mutation"]["killed"], "the patch is validated as for a report")
        self.assertIn("ACCEPTED", self.history())
        self.assertNotIn("demo-mut-s1-2", self.spawned())

    def test_S8_a_mutation_reviewer_silent_with_a_bad_patch_is_judged_as_a_reported_one(self):
        """orchestrator-engine/R82: orchestrator-audit-fixes/S8 — output that fails validation is an ineffective
        attempt, exactly as when the reviewer reports it."""
        self.write_plan()
        self.merged_on_main("done")
        step = self.silent_mutator(write=False)
        step["write"] = [[str(self.run_dir / "mutation" / "S1.1.patch"), "not a patch\n"]]
        self.waits([self.worker("done"), self.reviewer(), step, self.default_mutator(attempt=2)])
        self.run_driver("--repo", str(self.repo))
        self.assertIn("demo-mut-s1-2", self.spawned())
        self.assertEqual(self.ledger()["mutation"]["S1"]["history"][0]["outcome"], "invalid")

    def test_S9_a_mutation_reviewer_silent_with_no_patch_gets_an_idle_request(self):
        """orchestrator-engine/R82: orchestrator-audit-fixes/S9"""
        self.write_plan()
        self.merged_on_main("done")
        steps = [self.worker("done"), self.reviewer()] + [self.silent_mutator(write=False)] * 6
        self.waits(steps + [self.default_mutator()])
        self.run_driver("--repo", str(self.repo), "--idle-minutes", "0")
        (wake,) = self.idle_wakes(MUTATOR)
        self.assertIn(str(self.run_dir / "mutation" / "S1.1.patch"), wake["text"])
        self.assertNotIn("demo-mut-s1-2", self.spawned(), "the silent reviewer was ended at once")
        (receipt,) = self.receipts("mutation")
        self.assertTrue(receipt["mutation"]["killed"], "its late complete is still accepted")
        self.assertIn("ACCEPTED", self.history())

    def test_S9_a_silent_reviewer_that_writes_nothing_is_asked_about_once(self):
        """orchestrator-engine/R82: orchestrator-audit-fixes/S9 — one wake per attempt, however long it stays quiet."""
        self.write_plan()
        self.merged_on_main("done")
        steps = [self.worker("done"), self.reviewer()] + [self.silent_mutator(write=False)] * 12
        self.waits(steps + [self.default_mutator()])
        self.run_driver("--repo", str(self.repo), "--idle-minutes", "0")
        self.assertEqual(len(self.idle_wakes(MUTATOR)), 1)


class SilentWorker(GateFixture, unittest.TestCase):
    def silent_worker(self, write=()):
        return wait_result([assignment("demo-t1", "working", "idle")], write=write)

    def test_S11_the_idle_request_names_the_next_write_once_report_not_the_newest(self):
        """orchestrator-engine/R83: orchestrator-audit-fixes/S11"""
        unfinished = self.report("t1", acceptance_met=False)
        self.waits(
            [self.silent_worker(write=[[str(self.run_dir / "t1" / "report.json"), unfinished]])]
            + [self.silent_worker()] * 4
            + [self.skipped()]
        )
        self.run_driver("--idle-minutes", "0")
        reason = self.request()["reason"]
        self.assertEqual(self.request()["event"], "idle")
        self.assertIn(str(self.run_dir / "t1" / "report.2.json"), reason)
        self.assertNotIn(str(self.run_dir / "t1" / "report.json"), reason)

    def test_S11_a_first_try_is_asked_for_report_json(self):
        """orchestrator-engine/R83: orchestrator-audit-fixes/S11"""
        self.waits([self.silent_worker()] * 5 + [self.skipped()])
        self.run_driver("--idle-minutes", "0")
        self.assertIn(str(self.run_dir / "t1" / "report.json"), self.request()["reason"])


class LateReviewerComplete(GateFixture, unittest.TestCase):
    """An escalated reviewer's `complete` while its idle request is open (supersedes orchestrator-silent-idle/S13)."""

    def silent(self):
        return wait_result([assignment(REVIEWER, "working", "idle")])

    def gates(self):
        return [e["gate"][1] for e in self.run_events("state") if e["task"] == "t1"]

    def test_S10_a_complete_before_the_lead_decides_finishes_the_review_and_closes_the_request(
        self,
    ):
        """orchestrator-engine/R84: orchestrator-audit-fixes/S10"""
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

    def test_S10_a_complete_after_the_lead_decided_is_ignored(self):
        """orchestrator-engine/R84: orchestrator-audit-fixes/S10 — the decision and the complete reach the driver in
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
        self.assertIn(REVIEWER, second["reason"])
        self.assertNotIn("demo-t1 ", second["reason"])
        self.assertEqual(self.resumed("demo-t1"), [], "the implementer was never answered")
        self.assertEqual(len(self.resumed(REVIEWER)), 2, "the refused resume and the retry")
        self.assertEqual((self.task()["status"], self.task()["gate"]), ("COMPLETE", "done"))

    def request_file(self, nth):
        path = self.run_dir / "t1" / ("request.json" if nth == 1 else "request.%d.json" % nth)
        return json.loads(path.read_text(encoding="utf-8"))
