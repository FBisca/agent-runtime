"""Acceptance for a score's ladder (01-score-levels-are-bands/S1).

The runner places this file at `packages/agent-review/acceptance/screening/`. It drives the `agent-review` console
script as a subprocess over a real repository, a question bank it writes, and a canned screening provider, so the
check needs no credential and no socket; it imports nothing from the package, so it collects cleanly before the
phase exists and fails at base.

The contract it pins (docs/specs/agent-review.md R12):

  - a score answer is a position on the question's ladder, so any finite number from 0 up to one less than
    the declared levels is the provider's own answer and travels into the judgment unchanged
  - an answer below the first rung or above the last is refused naming the question and the range
  - a noul's probability is unchanged by the rule

A control passes at base and after: the same bank with a noul answered inside 0..1.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# Five levels, best first: the ladder is 0..4, so 1.22 sits between the first and second rung.
CONFIG = """\
model: jev-latest
questions:
  - id: design.one-reason
    priority: high
    type: score
    criteria:
      - the change keeps one reason to change
      - the change implies a second reason
      - the change mixes two reasons into one module
      - the change swallows unrelated concerns
      - the change has no single reason a reader could name
    ask: How severely does the change violate one reason to change
  - id: engineering.hidden-state
    priority: high
    type: noul
    ask: Does the change keep its state explicit
"""

SCORE = "design.one-reason"
NOUL = "engineering.hidden-state"


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def answer(score=None, noul=0.73):
    """The canned provider's answer: a score on the ladder and a noul's probability."""
    answers = {NOUL: {"type": "noul", "noul": noul}}
    if score is not None:
        answers[SCORE] = {"type": "score", "score": score, "confidence": 0.8}
    return {
        "model": "jev-1.14.0",
        "answers": answers,
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


class ScoreLadderFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        git(self.workspace, "init", "-b", "main")
        (self.workspace / "store.py").write_text(
            "def store(key, value):\n    return {key: value}\n"
        )
        git(self.workspace, "add", "-A")
        git(
            self.workspace,
            "-c",
            "user.email=a@b.c",
            "-c",
            "user.name=a",
            "commit",
            "-m",
            "one",
        )
        base = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "store.py").write_text(
            "def store(key, value):\n    return {key: value}  # the cache keeps one entry\n"
        )
        git(self.workspace, "add", "-A")
        git(
            self.workspace,
            "-c",
            "user.email=a@b.c",
            "-c",
            "user.name=a",
            "commit",
            "-m",
            "two",
        )
        return base, git(self.workspace, "rev-parse", "HEAD")

    def write_config(self, text=CONFIG):
        self.config.write_text(text, encoding="utf-8")
        return self.config

    def write_fixture(self, *responses):
        self.fixture.write_text(json.dumps({"responses": list(responses)}), encoding="utf-8")
        return self.fixture

    def screen(self):
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail("no `agent-review` on PATH: run the check under `uv run`")
        return subprocess.run(
            [
                binary,
                "code",
                "--base",
                self.base,
                "--head",
                self.head,
                "--output",
                str(self.output),
                "--config",
                str(self.config),
                "--screening-fixture",
                str(self.fixture),
            ],
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
                "HOME": str(Path(self.tmp.name, "home")),
            },
        )

    def report(self):
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))

    def judged(self):
        return {judgment["question"]: judgment for judgment in self.report()["judgments"]}


class ControlTests(ScoreLadderFixture):
    def test_control_a_noul_keeps_the_probability_the_provider_sent(self):
        self.write_config()
        self.write_fixture(answer(score=1.0))
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.report()["status"], "completed")
        self.assertEqual(self.judged()[NOUL]["value"], 0.73)


class ScoreBetweenRungsTests(ScoreLadderFixture):
    """S1: the ladder bounds a score; it does not snap it."""

    def test_S1_a_score_between_two_rungs_is_the_providers_own_number(self):
        self.write_config()
        self.write_fixture(answer(score=1.22))
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["status"], "completed", report.get("failure"))
        self.assertEqual(
            self.judged()[SCORE]["value"],
            1.22,
            "1.22 sits on a five-level ladder (0..4) and is the provider's own answer, not a rung",
        )

    def test_S1_the_first_rung_is_inside_the_ladder(self):
        self.write_config()
        self.write_fixture(answer(score=0))
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.judged()[SCORE]["value"], 0, "0 is the first rung, and it is inside")

    def test_S1_a_score_on_a_rung_is_still_carried_as_answered(self):
        self.write_config()
        self.write_fixture(answer(score=3.0))
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.judged()[SCORE]["value"], 3.0)

    def test_S1_a_score_above_the_last_rung_is_refused_naming_the_question(self):
        self.write_config()
        self.write_fixture(answer(score=4.6))
        result = self.screen()
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        reason = report["failure"]["reason"]
        self.assertIn(SCORE, reason)
        self.assertIn("0 to 4", reason, "a refusal names the range the ladder allows")

    def test_S1_a_score_below_the_first_rung_is_refused_naming_the_question(self):
        self.write_config()
        self.write_fixture(answer(score=-0.4))
        result = self.screen()
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        reason = report["failure"]["reason"]
        self.assertIn(SCORE, reason)
        self.assertIn("0 to 4", reason, "a refusal names the range the ladder allows")


if __name__ == "__main__":
    unittest.main()
