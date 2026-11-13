"""Contracts v2 in the driver: strict commit check, review envelope, evidence findings, responses, answers."""

import json
import unittest

import pytest
from fixtures import assignment, report_wait, resume_id, wait_result
from test_gates import BLOCKING, CONCERN, NIT, QUESTION, GateFixture

pytestmark = pytest.mark.integration

RUN = {"command": "pytest", "exit": 0, "at_commit": "abc1234"}
RESPONSE = {"id": "Q1", "status": "fixed", "commit": "abc1234", "note": "it raises ValueError"}


class EnvelopeFixture(GateFixture):
    def ledger_failed(self):
        from fixtures import load_script

        return load_script().unit_summary(self.ledger(), "t1")["failed_reviews"]

    def requests(self):
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((self.run_dir / "t1").glob("request*.json"))
        ]

    def read(self, *parts):
        return json.loads(self.run_dir.joinpath(*parts).read_text(encoding="utf-8"))

    def fix_round_report(self, task_id="t1r1", name="report.json", nth=1, **over):
        """The worker's report for a fix round, written under `name`, as the resume number `nth` of its pane."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", nth))
        step["write"] = [[str(self.run_dir / task_id / name), self.report(task_id, **over)]]
        return step

    def re_review(self, index=2, nth=1):
        """The unit's reviewer, resumed, passes the fix round."""
        step = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review", nth))
        step["write"] = [[str(self.run_dir / "t1" / ("review.%d.json" % index)), self.review2()]]
        return step

    def worker_resumes(self):
        return [r["args"]["name"] for r in self.calls("resume") if r["args"]["name"] == "demo-t1"]

    def blocked_with_question(self, **over):
        return self.review2(
            verdict="block", items=[BLOCKING, QUESTION, NIT, CONCERN], checks_run=[RUN], **over
        )

    def to_fix_round(self):
        """Steps that end with the worker resumed for fix round t1r1 (findings: B1 and Q1)."""
        return [self.worker_done(tests=True), self.reviewer_done(self.blocked_with_question())]


class CommitTests(EnvelopeFixture, unittest.TestCase):
    def rejected(self, review):
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(review)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")
        self.assertEqual(
            len(self.calls("resume")), 2, "the reviewer was warned twice before the Lead"
        )
        self.assertTrue(self.run_events("contract-error"))
        return self.request()["reason"]

    def test_S10_a_commit_shorter_than_seven_hex_characters_is_not_accepted(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S10"""
        self.assertIn("commit", self.rejected(self.review2(commit="abc123")))

    def test_S10_a_commit_that_is_not_the_checkouts_head_is_not_accepted(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S10"""
        self.assertIn("HEAD", self.rejected(self.review2(commit="deadbee")))
        self.assertIn("HEAD", self.rejected(self.review2(commit="d" * 40)))

    def test_S10_a_commit_that_is_not_hex_is_not_accepted(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S10"""
        self.rejected(self.review2(commit="not-a-sha"))

    def test_S10_an_abbreviation_of_head_of_at_least_seven_characters_is_accepted(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S10"""
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(self.review2(commit=head[:7]))]
        )
        self.run_driver()
        self.assertEqual(self.task()["gate"], "done")

    def test_S10_a_version_1_review_is_held_to_the_same_check(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S10"""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review(commit="abc1234"))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")


class ItemTests(EnvelopeFixture, unittest.TestCase):
    def test_S12_an_item_without_a_core_field_is_a_request_that_names_the_item_and_field(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S12"""
        item = {k: v for k, v in QUESTION.items() if k != "summary"}
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review2(items=[item]))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("item Q1", self.request()["reason"])
        self.assertIn("summary", self.request()["reason"])
        self.assertEqual(
            len(self.calls("resume")), 2, "the reviewer was warned twice before the Lead"
        )

    def test_S12_a_blocking_item_without_evidence_is_a_request_that_names_the_item_and_field(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S12"""
        item = {k: v for k, v in BLOCKING.items() if k != "evidence"}
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review2(verdict="block", items=[item]))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertIn("item B1", self.request()["reason"])
        self.assertIn("evidence", self.request()["reason"])

    def test_S12_fields_outside_the_core_appear_verbatim_in_findings_json(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S12"""
        item = dict(
            BLOCKING,
            severity="high",
            scenario="S4",
            why="the total is wrong",
            team={"owner": ["a"]},
        )
        question = dict(QUESTION, hint="look at the parser")
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(self.review2(verdict="block", items=[item, question])),
            ]
        )
        self.run_driver()
        self.assertEqual(self.read("t1r1", "findings.json")["items"], [item, question])

    def test_S13_a_block_without_a_blocking_item_is_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S13"""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review2(verdict="block", items=[QUESTION]))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("block", self.request()["reason"])
        self.assertEqual(
            len(self.calls("resume")), 2, "the reviewer was warned twice before the Lead"
        )

    def test_S13_a_pass_with_a_blocking_item_is_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S13"""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review2(verdict="pass", items=[BLOCKING]))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")
        self.assertIn("pass", self.request()["reason"])


class EvidenceTests(EnvelopeFixture, unittest.TestCase):
    def assert_item(self, item, source, check):
        for key in ("kind", "source", "check", "command", "exit", "excerpt", "log_path"):
            self.assertIn(key, item)
        self.assertEqual((item["kind"], item["source"], item["check"]), ("blocking", source, check))
        self.assertTrue(item["command"])
        self.assertIsInstance(item["exit"], int)

    def test_S14_a_failing_verify_fills_each_item_with_its_evidence(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S14"""
        self.waits([self.worker_done()])
        self.run_driver()
        findings = self.read("t1r1", "findings.json")
        self.assertEqual((findings["contract"], findings["source"]), (2, "verify"))
        item = findings["items"][0]
        self.assert_item(item, "verify", "covers")
        excerpt = "S1: test node src/a/test_a.py::test_a does not exist (no file src/a/test_a.py)"
        self.assertEqual((item["exit"], item["excerpt"]), (1, excerpt))
        self.assertIn("verify T1", item["command"])
        with open(item["log_path"], encoding="utf-8") as log:
            self.assertIn("FAIL covers: " + excerpt, log.read())
        self.assertIn(excerpt, (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8"))
        self.assertIn(
            item["log_path"], (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        )

    def test_S14_a_failing_ci_check_takes_its_excerpt_from_the_failing_log(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S14"""
        self.checks([("fail", "unit tests", "lint"), "green"])
        self.waits([self.worker_done(tests=True), self.reviewer_done(), self.worker_done("t1r1")])
        self.run_driver()
        findings = self.read("t1r1", "findings.json")
        self.assertEqual((findings["contract"], findings["source"]), (2, "ci"))
        checks = [i["check"] for i in findings["items"]]
        self.assertEqual(checks, ["unit tests", "lint"])
        for item in findings["items"]:
            self.assert_item(item, "ci", item["check"])
            self.assertIn("FAILED STEP LOG TAIL for run 77", item["excerpt"])
            with open(item["log_path"], encoding="utf-8") as log:
                self.assertIn("FAILED STEP LOG TAIL for run 77", log.read())
        self.assertEqual(
            findings["items"][0]["command"], "gh pr checks https://github.com/o/r/pull/7"
        )


class FixRoundTests(EnvelopeFixture, unittest.TestCase):
    def test_S15_a_fix_round_holds_blocking_and_answer_required_items(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S15"""
        self.waits(self.to_fix_round())
        self.run_driver()
        findings = self.read("t1r1", "findings.json")
        self.assertEqual(findings["items"], [BLOCKING, QUESTION])
        self.assertEqual(findings["checks_run"], [RUN])
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("drops the last row", task_md)
        self.assertIn("should empty input raise?", task_md)
        self.assertIn("pytest", task_md)
        self.assertNotIn("rename the helper", task_md)
        self.assertNotIn("never cleared", task_md)

    def test_S15_every_other_item_is_appended_to_deferred_md(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S15"""
        self.waits(self.to_fix_round())
        self.run_driver()
        deferred = (self.run_dir / "deferred.md").read_text(encoding="utf-8")
        self.assertIn("N1", deferred)
        self.assertIn("rename the helper", deferred)
        self.assertIn("K1", deferred)
        self.assertIn("the cache is never cleared", deferred)
        self.assertNotIn("B1", deferred)
        self.assertNotIn("Q1", deferred)
        kinds = sorted((e["finding_kind"], e["id"]) for e in self.run_events("finding"))
        self.assertEqual(
            kinds, [("blocking", "B1"), ("concern", "K1"), ("nit", "N1"), ("question", "Q1")]
        )

    def test_S16_a_missing_response_resumes_the_worker_once_at_no_round_cost(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S16"""
        self.waits(
            self.to_fix_round()
            + [
                self.fix_round_report(),
                self.fix_round_report(name="report.2.json", nth=2, responses=[RESPONSE]),
                self.re_review(),
            ]
        )
        self.run_driver()
        errors = self.run_events("contract-error")
        self.assertEqual(len(errors), 1)
        self.assertIn("Q1", errors[0]["error"])
        resumes = self.calls("resume")
        self.assertEqual(
            [r["args"]["name"] for r in resumes], ["demo-t1", "demo-t1", "demo-t1-review"]
        )
        self.assertEqual(
            resumes[1]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1r1" / "missing.md"),
        )
        brief = (self.run_dir / "t1r1" / "missing.md").read_text(encoding="utf-8")
        self.assertIn("Q1", brief)
        self.assertIn("report.2.json", brief)
        self.assertEqual(self.ledger_failed(), 1)
        self.assertEqual(len(self.run_events("round")), 1)
        self.assertEqual(self.task("t1r1")["gate"], "done")
        self.assertEqual(self.run_events("escalation"), [])

    def test_S16_a_second_report_still_missing_the_response_is_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S16"""
        self.waits(
            self.to_fix_round()
            + [
                self.fix_round_report(),
                self.fix_round_report(name="report.2.json", nth=2),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "responses")
        self.assertIn("Q1", self.request()["reason"])
        self.assertEqual(len(self.calls("resume")), 2)

    def test_S16_a_complete_set_of_responses_passes_the_first_time(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S16"""
        self.waits(
            self.to_fix_round() + [self.fix_round_report(responses=[RESPONSE]), self.re_review()]
        )
        self.run_driver()
        self.assertEqual(self.worker_resumes(), ["demo-t1"])
        self.assertEqual(self.run_events("contract-error"), [])
        self.assertEqual(self.task("t1r1")["gate"], "done")


class AnswerTests(EnvelopeFixture, unittest.TestCase):
    def answers_report(self, responses, nth=1, name="report.json"):
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", nth))
        step["write"] = [
            [str(self.run_dir / "t1a1" / name), self.report("t1a1", responses=responses)]
        ]
        return step

    def passing_with_question(self):
        return [
            self.worker_done(tests=True),
            self.reviewer_done(self.review2(items=[QUESTION, NIT])),
        ]

    def silent_answerer(self, runtime_state="idle"):
        """A wait step: the implementer's turn on the answers ended, under the assignment the answers resume gave."""
        return wait_result(
            [assignment("demo-t1", "working", runtime_state, id=resume_id("demo-t1", 1))]
        )

    def test_S7_an_answerer_whose_turn_ended_stays_working_and_its_complete_is_accepted(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S7"""
        self.waits(
            self.passing_with_question()
            + [
                self.silent_answerer("idle"),
                self.silent_answerer("done"),
                self.answers_report([RESPONSE]),
            ]
        )
        self.run_driver("--idle-minutes", "10")
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        self.assertEqual(self.run_events("escalation"), [])
        self.assertEqual(self.task()["gate"], "done")
        self.assertEqual(self.task()["answers"]["state"], "done")

    def test_S8_an_answerer_that_never_reports_is_one_idle_request_and_the_pr_stays_blocked(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S8"""
        self.waits(
            self.passing_with_question() + [self.silent_answerer("idle")] * 5 + [self.skipped()]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual(self.request()["event"], "idle")
        self.assertEqual(len(self.wake_lines("decision")), 1)
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        ready = [c for c in self.gh_calls("pr", "ready") if "--undo" not in c]
        self.assertEqual(ready, [], "the PR was marked ready with the answers outstanding")

    def test_S8_after_the_lead_answers_an_idle_answerer_the_unit_reaches_pr_ready_and_done(self):
        """orchestrator-engine/R62: orchestrator-silent-idle/S8 — the Lead's decision replaces the silent answers
        assignment, so the answers no longer hold pr-ready."""
        worker = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 2))
        worker["write"] = [[str(self.run_dir / "t1" / "report.2.json"), self.report("t1")]]
        self.waits(
            self.passing_with_question()
            + [self.silent_answerer("idle")] * 5
            + [self.decide_step("t1", "answer", answer="use the file"), worker, self.re_review()]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["event"] for e in self.requests()], ["idle"])
        self.assertEqual(self.task()["answers"]["state"], "failed")
        ready = [c for c in self.gh_calls("pr", "ready") if "--undo" not in c]
        self.assertEqual(len(ready), 1, "the PR never reached ready")
        self.assertEqual(self.task()["gate"], "done")

    def test_S17_a_pass_with_answer_required_items_goes_to_ci(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S17"""
        self.waits(self.passing_with_question() + [self.answers_report([RESPONSE])])
        self.run_driver()
        self.assertIn("ready-for-ci", [e["gate"][1] for e in self.run_events("state")])
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        self.assertEqual(
            resumes[0]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1a1" / "task.md"),
        )
        brief = (self.run_dir / "t1a1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("should empty input raise?", brief)
        self.assertIn("Answers only", brief)
        self.assertNotIn("rename the helper", brief)
        self.assertEqual(self.read("t1a1", "findings.json")["items"], [QUESTION])
        self.assertEqual(self.task()["gate"], "done")
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.ledger_failed(), 0)
        self.assertIn(
            "rename the helper", (self.run_dir / "deferred.md").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [(e["finding"], e["status"]) for e in self.run_events("response")], [("Q1", "fixed")]
        )

    def test_S17_the_pr_is_not_ready_until_the_answers_are_in(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S17"""
        self.waits(self.passing_with_question() + [self.answers_report([RESPONSE])])
        self.run_driver()
        order = [e["kind"] for e in self.run_events("wake", "response")]
        self.assertEqual(order[:2], ["response", "wake"])

    def test_S17_a_disputed_response_that_changes_code_goes_to_a_delta_re_review(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S17"""
        disputed = dict(RESPONSE, status="disputed", changes_code=True, note="the contract says no")
        second = self.reviewer_done(self.review2())
        second["write"][0][0] = str(self.run_dir / "t1" / "review.2.json")
        second["assignments"][0]["assignment_id"] = "r1-demo-t1-review"
        second["messages"][0]["assignment_id"] = "r1-demo-t1-review"
        self.waits(self.passing_with_question() + [self.answers_report([disputed]), second])
        self.run_driver()
        self.assertEqual(
            self.spawned().count("demo-t1-review"), 1
        )  # the same reviewer is resumed, not spawned again
        self.assertEqual(
            [c["args"]["name"] for c in self.calls("resume")], ["demo-t1", "demo-t1-review"]
        )
        text = (self.run_dir / "t1-review2" / "task.md").read_text(encoding="utf-8")
        head = self.git(self.worktree, "rev-parse", "HEAD")
        self.assertIn("%s..HEAD" % head, text)
        self.assertIn(str(self.run_dir / "t1a1" / "findings.json"), text)
        self.assertEqual(self.task()["gate"], "done")
        self.assertGreaterEqual(len(self.checks_log()), 2)

    def test_S8_a_disputed_change_drafts_the_pr_again_and_readies_it_after_the_re_review(self):
        """orchestrator-engine/R30: orchestrator-pr-merge-state/S8"""
        self.born_ready()  # the review's draft undo marks the unit; the dispute must not leave that mark behind
        disputed = dict(RESPONSE, status="disputed", changes_code=True, note="the contract says no")
        second = self.reviewer_done(self.review2())
        second["write"][0][0] = str(self.run_dir / "t1" / "review.2.json")
        second["assignments"][0]["assignment_id"] = "r1-demo-t1-review"
        second["messages"][0]["assignment_id"] = "r1-demo-t1-review"
        self.prs(["OPEN"])  # open while it is ready; merged once the re-review passed
        second["write"].append([str(self.home / "prs.json"), json.dumps(["MERGED"])])
        self.waits(self.passing_with_question() + [self.answers_report([disputed]), second])
        self.run_driver()
        pr = "https://github.com/o/r/pull/7"
        self.assertEqual(
            self.gh_calls("pr", "ready"), [["pr", "ready", pr, "--undo"], ["pr", "ready", pr]]
        )
        readied = [c for c in self.gh_log() if c["argv"] == ["pr", "ready", pr]][0]
        self.assertTrue(any('"response"' in line for line in readied["events"]))
        self.assertEqual(self.task()["gate"], "done")

    def test_S16_missing_answers_resume_the_worker_once_then_become_a_request(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S16"""
        self.waits(
            self.passing_with_question()
            + [
                self.answers_report([]),
                self.answers_report([], nth=2, name="report.2.json"),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "responses")
        self.assertEqual(
            [e["where"] for e in self.run_events("contract-error")], ["responses", "responses"]
        )
        self.assertEqual(len(self.calls("resume")), 2)


class ClarificationTests(EnvelopeFixture, unittest.TestCase):
    ASKED = {"finding": "Q1", "asked_by": "implementer", "note": "does empty input raise?"}

    def test_S26_each_clarification_in_a_report_is_logged_with_its_finding_id(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S26"""
        second = dict(self.ASKED, finding="B1", note="which row is last?")
        self.waits(
            [
                self.worker_done(
                    report=self.report("t1", clarifications=[self.ASKED, second]), tests=True
                )
            ]
        )
        self.run_driver()
        rows = self.run_events("clarification")
        self.assertEqual(
            [(r["finding"], r["asked_by"], r["note"], r["source"]) for r in rows],
            [
                ("Q1", "implementer", "does empty input raise?", "report"),
                ("B1", "implementer", "which row is last?", "report"),
            ],
        )
        self.assertEqual({r["unit"] for r in rows}, {"t1"})

    def test_S26_each_clarification_in_a_review_is_logged_with_its_finding_id(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S26"""
        asked = dict(self.ASKED, asked_by="reviewer")
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(self.review2(clarifications=[asked]))]
        )
        self.run_driver()
        rows = self.run_events("clarification")
        self.assertEqual(
            [(r["finding"], r["asked_by"], r["source"]) for r in rows],
            [("Q1", "reviewer", "review")],
        )


VIOLATION = {
    "source": "docs/spec.md",
    "id": "R2",
    "expected": "exit 2 means no command ran",
    "observed": "exit 2 after execution",
}


class RequirementViolationTests(EnvelopeFixture, unittest.TestCase):
    """A review that asserts a requirement violation blocks with its metadata, or is refused before CI."""

    def refused(self, item, verdict="pass"):
        """The reviewer is warned twice, then the Lead is asked; the unit never reaches CI."""
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review2(verdict=verdict, items=[item]))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.request()["event"], "review")
        self.assertEqual(
            len(self.calls("resume")), 2, "the reviewer was warned twice before the Lead"
        )
        self.assertTrue(self.run_events("contract-error"))
        gates = [e["gate"] for e in self.run_events("state") if e["task"] == "t1"]
        self.assertNotIn("ready-for-ci", gates, "a refused review advanced the unit to CI")
        return self.request()["reason"]

    def test_S3_a_concern_asserting_a_requirement_violation_is_refused_before_ci(self):
        reason = self.refused(dict(CONCERN, requirement_violation=VIOLATION))
        self.assertIn(CONCERN["id"], reason)
        self.assertIn("requirement_violation", reason)
        self.assertIn("blocking", reason)

    def test_S3_a_requirement_violation_missing_its_observed_behavior_is_refused(self):
        violation = {k: v for k, v in VIOLATION.items() if k != "observed"}
        reason = self.refused(dict(BLOCKING, requirement_violation=violation), "block")
        self.assertIn(BLOCKING["id"], reason)
        self.assertIn("observed", reason)

    def test_S4_a_concern_without_a_requirement_violation_still_reaches_ci(self):
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(self.review2(items=[CONCERN]))]
        )
        self.run_driver()
        self.assertEqual(self.task()["gate"], "done")
        self.assertTrue(self.checks_log())
        self.assertFalse((self.run_dir / "t1r1" / "findings.json").exists())


if __name__ == "__main__":
    unittest.main()
