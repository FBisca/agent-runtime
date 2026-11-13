"""The implementer's answers and the attempt ids a Lead's answer may name."""

import json
import unittest

import pytest
from agent_orchestrator import contracts
from fixtures import assignment, load_script, report_wait, resume_id, wait_result
from test_envelope import RESPONSE, EnvelopeFixture

pytestmark = pytest.mark.integration


class OpenAnswers(EnvelopeFixture, unittest.TestCase):
    """The answers are working when a request that does not name them is decided."""

    def answerer(self):
        """A wait step: the implementer's turn on the answers ended."""
        return wait_result([assignment("demo-t1", "working", "idle", id=resume_id("demo-t1", 1))])

    def answers_complete(self):
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 1))
        step["write"] = [
            [str(self.run_dir / "t1a1" / "report.json"), self.report("t1a1", responses=[RESPONSE])]
        ]
        return step

    def passing_with_question(self):
        from test_gates import NIT, QUESTION

        return [
            self.worker_done(tests=True),
            self.reviewer_done(self.review2(items=[QUESTION, NIT])),
        ]

    def broken_checks(self, then_green=False):
        """`gh pr checks` fails once with a code it does not use for a state: the unit asks the Lead about CI."""
        rows = [[2, "", "gh is down"]] + ([[0, json.dumps(self.GREEN), ""]] if then_green else [])
        (self.home / "checks.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_S12_a_fresh_worker_on_a_ci_request_does_not_fail_the_answerer_record(self):
        """orchestrator-engine/R85: orchestrator-audit-fixes/S12"""
        self.broken_checks()
        self.waits(self.passing_with_question() + [self.decide_step("t1", "fresh worker")])
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["cause"] for e in self.run_events("escalation")], ["ci"])
        self.assertEqual(self.task()["answers"]["state"], "working", "the record was failed")
        self.assertEqual(self.run_events("answers-dropped"), [])

    def test_S12_an_answerer_complete_after_a_decision_that_did_not_name_it_is_received(self):
        """orchestrator-engine/R85: orchestrator-audit-fixes/S12"""
        self.broken_checks(then_green=True)
        worker = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 2))
        worker["write"] = [[str(self.run_dir / "t1" / "report.2.json"), self.report("t1")]]
        self.waits(
            self.passing_with_question()
            + [
                self.decide_step("t1", "answer", answer="carry on"),
                self.answers_complete(),
                worker,
                self.re_review(),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["cause"] for e in self.run_events("escalation")], ["ci"])
        self.assertEqual(self.run_events("answers-dropped"), [])
        self.assertEqual(
            self.task()["answers"]["state"], "done", "the answerer's complete was not received"
        )

    def test_S12_a_decision_on_a_request_that_named_the_answerer_drops_it_and_says_why(self):
        """orchestrator-engine/R85: orchestrator-audit-fixes/S12 — the event is why pr-ready stopped waiting."""
        self.waits(
            self.passing_with_question() + [self.answerer()] * 5 + [self.decide_step("t1", "retry")]
        )
        self.run_driver("--idle-minutes", "0")
        self.assertEqual([e["event"] for e in self.requests()][:1], ["idle"])
        self.assertEqual(self.task()["answers"]["state"], "failed")
        (dropped,) = self.run_events("answers-dropped")
        self.assertEqual(dropped["task"], "t1")
        self.assertIn("pr-ready", dropped["why"])
        self.assertIn("retry", dropped["why"])


class AttemptIds(unittest.TestCase):
    """One pattern says what an attempt id of a unit looks like."""

    def test_S13_every_round_suffix_names_an_attempt_id(self):
        """orchestrator-engine/R86: orchestrator-audit-fixes/S13"""
        foreign = load_script().foreign_attempt
        for suffix in "rdopa":
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    foreign("use t1%s1" % suffix, "t1", "t1%s2" % suffix), "t1%s1" % suffix
                )
                self.assertEqual(
                    foreign("USE T1%s1" % suffix.upper(), "t1", "t1"), "T1%s1" % suffix.upper()
                )

    def test_S13_the_expected_id_the_unit_and_other_words_are_not_foreign(self):
        """orchestrator-engine/R86: orchestrator-audit-fixes/S13"""
        foreign = load_script().foreign_attempt
        self.assertIsNone(foreign("t1a2 is the one", "t1", "t1a2"))
        self.assertIsNone(foreign("see demo-t1a1 and t1apple", "t1", "t1a2"))

    def test_S13_the_pattern_is_the_one_the_driver_builds_ids_from(self):
        """orchestrator-engine/R86: orchestrator-audit-fixes/S13 — a letter the driver issues is a letter the
        pattern knows."""
        spec = load_script()
        pattern = spec.attempt_pattern("t1")
        for letter in spec.ATTEMPT_LETTERS:
            self.assertRegex("t1%s3" % letter, pattern)
        self.assertEqual(sorted(spec.ATTEMPT_LETTERS), sorted("rdopa"))


class AnswerRefusal(EnvelopeFixture, unittest.TestCase):
    def open_request(self, task):
        path = self.run_dir / "t1" / "request.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(contracts.make_request("t1", task, "blocked", "cannot go on", [str(path)])),
            encoding="utf-8",
        )
        return path

    def decide(self, answer):
        return self.cli(
            "decide",
            str(self.plan),
            "t1",
            "answer",
            "--reason",
            "x",
            "--answer",
            answer,
            "--state-dir",
            str(self.state_dir),
        )

    def test_S13_an_answer_naming_t1a1_for_t1a2_is_refused_as_foreign(self):
        """orchestrator-engine/R86: orchestrator-audit-fixes/S13"""
        path = self.open_request("t1a2")
        before = path.read_text(encoding="utf-8")
        result = self.decide("use t1a1")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("t1a1", result.stderr)
        self.assertIn("t1a2", result.stderr)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(path.read_text(encoding="utf-8"), before)
