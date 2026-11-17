"""The reviewer through the runtime: its verdict, its refusals and ended turns, its place beside CI, its re-review."""

import itertools
import json
import unittest
from unittest import mock

import pytest
from fixtures import load_script, report_wait, resume_id
from test_pr import PrFixture

pytestmark = pytest.mark.integration

REVIEWER = "demo-t1-review"
FINDING = {
    "file": "src/a/x.py",
    "line": 7,
    "quote": "return rows[:-1]",
    "claim": "drops the last row for every caller",
}
RULE = "Every changed behaviour has a test that would fail without it."


def block(*findings):
    return json.dumps({"verdict": "block", "findings": list(findings)})


class ReviewerFixture(PrFixture):
    def quiet_clock(self):
        """Every reading is ten minutes later than the last."""
        moments = itertools.count(0, 600)
        return mock.patch.object(load_script().Driver, "clock", staticmethod(lambda: next(moments)))

    def reviewer_again(self, text=None, nth=1):
        """The same reviewer, resumed for the `nth` time, hands in its verdict."""
        return report_wait(
            REVIEWER, "complete", text=text or self.review(), id=resume_id(REVIEWER, nth)
        )

    def reviewer_calls(self):
        return [c["args"] for c in self.calls("resume") if c["args"]["name"] == REVIEWER]

    def requests_of(self, unit="t1"):
        return sorted((self.run_dir / unit).glob("request*.json"))


class VerdictTests(ReviewerFixture, unittest.TestCase):
    @pytest.mark.spec("A verdict is handed in through the runtime")
    def test_an_accepted_verdict_is_an_event_and_no_review_file_is_written(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        (event,) = self.run_events("review")
        self.assertEqual((event["unit"], event["verdict"], event["findings"]), ("t1", "pass", []))
        self.assertEqual(list(self.run_dir.rglob("review*.json")), [])
        self.assertEqual(self.task()["status"], "COMPLETE")

    @pytest.mark.spec("A verdict is handed in through the runtime")
    def test_a_finding_with_a_rule_and_one_without_block_alike_and_the_rule_is_recorded(self):
        other = dict(FINDING, line=9, claim="the cache is never cleared")
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block(dict(FINDING, rule=RULE), other)),
                self.worker_done("t1r1"),
                self.reviewer_again(),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.run_events("round")), 1, "one round for both findings")
        found = self.run_events("review")[0]["findings"]
        self.assertEqual([f.get("rule") for f in found], [RULE, None])
        items = json.loads((self.run_dir / "t1r1" / "findings.json").read_text())["items"]
        self.assertEqual(
            [(i["id"], i["source"], i.get("rule")) for i in items],
            [("R1", "review", RULE), ("R2", "review", None)],
        )
        brief = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("drops the last row for every caller", brief)
        self.assertIn("the cache is never cleared", brief)


class RefusalTests(ReviewerFixture, unittest.TestCase):
    @pytest.mark.spec("A refused verdict goes back to its reviewer")
    def test_a_wrong_verdict_goes_back_to_the_same_reviewer_at_once_and_asks_nobody(self):
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done("looks fine to me"),
                self.reviewer_again(json.dumps({"verdict": "block"})),
                self.reviewer_again(nth=2),
            ]
        )
        self.run_driver()
        refused = self.run_events("review-refused")
        self.assertEqual(len(refused), 2)
        self.assertIn("not the verdict JSON", refused[0]["reason"])
        self.assertEqual(refused[0]["payload"], "looks fine to me")
        self.assertIn("block needs at least one finding", refused[1]["reason"])
        told = [c["task"] for c in self.reviewer_calls()]
        self.assertTrue(all("Your verdict was refused" in text for text in told[:2]), told)
        self.assertIn("not the verdict JSON", told[0])
        self.assertEqual(self.requests_of(), [])
        self.assertEqual([e["verdict"] for e in self.run_events("review")], ["pass"])
        self.assertEqual(self.run_events("round"), [])

    @pytest.mark.spec("A refused verdict goes back to its reviewer")
    def test_prose_after_the_verdict_json_is_refused_and_the_reviewer_is_told_to_send_the_json_alone(
        self,
    ):
        verdict = json.dumps({"verdict": "pass", "findings": []})
        prose = verdict + "\n\nfiles changed: none\nchecks: none\nremaining work: none"
        self.waits([self.worker_done(tests=True), self.reviewer_done(prose), self.reviewer_again()])
        self.run_driver()
        (refused,) = self.run_events("review-refused")
        self.assertIn("Extra data", refused["reason"])
        (told,) = [c["task"] for c in self.reviewer_calls()]
        self.assertIn("verdict JSON alone", told)
        self.assertEqual([e["verdict"] for e in self.run_events("review")], ["pass"])

    @pytest.mark.spec("A refused verdict goes back to its reviewer")
    def test_the_third_refusal_asks_the_lead(self):
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done("one"),
                self.reviewer_again("two"),
                self.reviewer_again("three", nth=2),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.run_events("review-refused")), 3)
        self.assertEqual(len(self.reviewer_calls()), 2, "two refusals were put to the reviewer")
        (request,) = self.requests_of()
        self.assertEqual(json.loads(request.read_text())["event"], "review")


class EndedTests(ReviewerFixture, unittest.TestCase):
    def ended(self, nth=None):
        return report_wait(
            REVIEWER,
            "failed",
            "reviewer terminal exited",
            id=resume_id(REVIEWER, nth) if nth else None,
        )

    @pytest.mark.spec("A reviewer that ends without a verdict is resumed once")
    def test_a_reviewer_that_ends_is_resumed_once_and_the_second_end_asks_the_lead(self):
        self.waits([self.worker_done(tests=True), self.ended(), self.ended(1), self.skipped()])
        self.run_driver()
        (resumed,) = self.reviewer_calls()
        self.assertIn("You ended without a verdict; hand it in", resumed["task"])
        self.assertEqual(len(self.run_events("review-ended")), 2)
        self.assertEqual(len(self.requests_of()), 1, "only the second end asks the Lead")

    @pytest.mark.spec("A reviewer that ends without a verdict is resumed once")
    def test_a_reviewer_whose_turn_ended_idle_is_answered_by_the_driver_too(self):
        from fixtures import assignment, wait_result

        self.waits(
            [
                self.worker_done(tests=True),
                wait_result([assignment(REVIEWER, "working", "idle")]),
                self.reviewer_again(),
            ]
        )
        self.run_driver()
        (resumed,) = self.reviewer_calls()
        self.assertIn("You ended without a verdict", resumed["task"])
        self.assertEqual(self.requests_of(), [])
        self.assertEqual(self.task()["status"], "COMPLETE")


class TogetherTests(ReviewerFixture, unittest.TestCase):
    @pytest.mark.spec("Review and CI run together")
    def test_the_review_findings_and_the_ci_failures_reach_the_worker_in_one_round(self):
        self.checks([("fail", "unit tests"), "green"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block(FINDING)),
                self.worker_done("t1r1"),
                self.reviewer_again(),
            ]
        )
        with self.quiet_clock():
            self.run_driver()
        self.assertEqual(len(self.run_events("round")), 1)
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text())
        self.assertEqual([i["source"] for i in findings["items"]], ["review", "ci"])
        worker = [c for c in self.calls("resume") if c["args"]["name"] == "demo-t1"]
        self.assertEqual(len(worker), 1, "the worker hears once")

    @pytest.mark.spec("Review and CI run together")
    def test_a_verdict_waits_for_ci_and_no_round_is_raised_before_it_is_in(self):
        self.checks(["pending", "pending", "green"])
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block(FINDING)),
                self.working(),
                self.working(),
                self.worker_done("t1r1"),
                self.reviewer_again(),
            ]
        )
        self.run_driver()
        order = [(e["kind"], e.get("state")) for e in self.run_events("ci", "round")]
        first_round = next(i for i, (kind, _) in enumerate(order) if kind == "round")
        self.assertIn(("ci", "pending"), order[:first_round])
        self.assertIn(("ci", "green"), order[:first_round])
        self.assertTrue(any(e["gate"] == "reviewed" for e in self.run_events("ci")))

    @pytest.mark.spec("Review and CI run together")
    def test_every_ticket_is_reviewed(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertIn(REVIEWER, self.spawned())


class ReReviewTests(ReviewerFixture, unittest.TestCase):
    def first_round(self, *extra):
        return [
            self.worker_done(tests=True),
            self.reviewer_done(block(FINDING)),
            self.worker_done("t1r1"),
            *extra,
        ]

    @pytest.mark.spec("A re-review resumes the reviewer on the fix")
    def test_the_same_reviewer_gets_the_fix_diff_and_what_it_answered_and_no_whole_change(self):
        self.waits(self.first_round(self.reviewer_again()))
        self.run_driver()
        self.assertEqual(self.spawned().count(REVIEWER), 1, "no second reviewer")
        (text,) = [c["task"] for c in self.reviewer_calls()]
        reviewed = self.run_events("review")[0]["commit"]
        self.assertIn("diff %s..%s" % (reviewed, self.task("t1r1")["report"]["head"]), text)
        self.assertIn(str(self.run_dir / "t1r1" / "findings.json"), text)
        self.assertIn("check each of your earlier findings", text)
        self.assertNotIn("Read the whole diff", text)

    @pytest.mark.spec("A re-review resumes the reviewer on the fix")
    def test_a_reviewer_whose_pane_is_gone_gets_the_whole_brief_again(self):
        gone = self.flip(self.worker_done("t1r1"), "states.json", {REVIEWER: "exited"})
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(block(FINDING)),
                gone,
                report_wait(REVIEWER, "complete", text=self.review(), id="a-%s-2" % REVIEWER),
            ]
        )
        self.run_driver()
        self.assertEqual(self.spawned().count(REVIEWER), 2)
        again = [c for c in self.calls("spawn") if c["args"]["name"] == REVIEWER][1]["args"]["task"]
        self.assertIn("task.md and follow it", again)

    @pytest.mark.spec("A re-review resumes the reviewer on the fix")
    def test_a_re_review_that_blocks_again_goes_to_the_lead_not_to_another_round(self):
        self.waits(self.first_round(self.reviewer_again(block(FINDING)), self.skipped()))
        self.run_driver()
        self.assertEqual(len(self.run_events("round")), 1)
        (request,) = self.requests_of()
        document = json.loads(request.read_text())
        self.assertEqual(document["event"], "review")
        self.assertIn("drops the last row", document["reason"])
