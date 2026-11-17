"""The attempt ids a Lead's answer may name."""

import json
import unittest

import pytest
from agent_orchestrator import contracts
from fixtures import load_script
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class AttemptIds(unittest.TestCase):
    """One pattern says what an attempt id of a unit looks like."""

    @pytest.mark.spec("An answer resumes the same attempt")
    def test_S13_every_round_suffix_names_an_attempt_id(self):
        """orchestrator-audit-fixes/S13"""
        foreign = load_script().foreign_attempt
        for suffix in "rdopa":
            with self.subTest(suffix=suffix):
                self.assertEqual(
                    foreign("use t1%s1" % suffix, "t1", "t1%s2" % suffix), "t1%s1" % suffix
                )
                self.assertEqual(
                    foreign("USE T1%s1" % suffix.upper(), "t1", "t1"), "T1%s1" % suffix.upper()
                )

    @pytest.mark.spec("An answer resumes the same attempt")
    def test_S13_the_expected_id_the_unit_and_other_words_are_not_foreign(self):
        """orchestrator-audit-fixes/S13"""
        foreign = load_script().foreign_attempt
        self.assertIsNone(foreign("t1a2 is the one", "t1", "t1a2"))
        self.assertIsNone(foreign("see demo-t1a1 and t1apple", "t1", "t1a2"))

    @pytest.mark.spec("An answer resumes the same attempt")
    def test_S13_the_pattern_is_the_one_the_driver_builds_ids_from(self):
        """orchestrator-audit-fixes/S13 — a letter the driver issues is a letter the
        pattern knows."""
        spec = load_script()
        pattern = spec.attempt_pattern("t1")
        for letter in spec.ATTEMPT_LETTERS:
            self.assertRegex("t1%s3" % letter, pattern)
        self.assertEqual(sorted(spec.ATTEMPT_LETTERS), sorted("rdopa"))


class AnswerRefusal(GateFixture, unittest.TestCase):
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

    @pytest.mark.spec("An answer resumes the same attempt")
    def test_S13_an_answer_naming_t1a1_for_t1a2_is_refused_as_foreign(self):
        """orchestrator-audit-fixes/S13"""
        path = self.open_request("t1a2")
        before = path.read_text(encoding="utf-8")
        result = self.decide("use t1a1")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("t1a1", result.stderr)
        self.assertIn("t1a2", result.stderr)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(path.read_text(encoding="utf-8"), before)
