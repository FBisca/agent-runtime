"""Acceptance for the risk-adaptive deep review (02-agent-review-deep-review/S1..S6).

The runner places this file at `packages/agent-review/acceptance/deep-review/`. It drives the `agent-review` console
script as a subprocess over a real repository, a configuration and a canned screening provider, with the deep
review's spawn replaced by a canned reviewer; it imports nothing from the package, so it collects cleanly before the
phase exists and fails at base.

The contract it pins (brief.md, P2-D1..P2-D7):

  - `--deep-review <harness>` makes an escalated review spawn one read-only reviewer, and `--deep-review-fixture
    <dir>` replaces that spawn with canned answers (`answers.json` in, `spawns.json` out), so no check needs a model
  - a harness whose family cannot enforce read/search-only is refused before anything runs: exit 2, no report, no
    spawn; the harness families that can enforce it are named beside the way out
  - the review's scope is focused when every reason that fired names a judged question, and full when one is about
    the change itself (a sensitive path, a safeguard, a coverage gap)
  - specialized instruction sets are composed from configuration: one that declares no `applies` joins every review,
    one that matches a fired rule or a changed path joins it too, and the report names what was composed in order
  - every critical/high signal gets its own disposition carrying the signal's rule, question, value, confidence and
    the declaration that fired; a missing disposition stays unresolved and an incomplete review is never retried
  - a confirmed mandatory-contract violation is blocking whichever the reviewer answered, and the verdict tells
    `screened` (nothing escalated) from `passed` (a clean deep review) and `blocked` (a confirmed blocking finding)

Each class carries a control that passes at base and after.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

QUESTION = "repo.migration-safety"
MIGRATION = "db/migrations/002_index.sql"

BASE_FILES = {
    "store.py": "def load(path):\n    return open(path).read()\n",
    "docs/guide.md": "guide\n",
}

HEAD_EDIT = """\
def load(path):
    with open(path) as stream:
        return stream.read()
"""

CONFIG = """\
model: jev-latest
sensitive_paths: {sensitive_paths}
chunk:
  max_diff_tokens: 200000
cross_file:
  max_files: 40
thresholds:
  repo.migration-safety:
    value: 1.5
    confidence: 0.6
{deep_review}questions:
  - id: {question}
    priority: critical
    type: score
    criteria: [none, minor, moderate, major, severe]
    ask: How severely does the change violate the migration contract
"""

# The deep-review stage's own configuration: what it may spend, and the instruction sets it composes from.
DEEP_REVIEW = """\
deep_review:
  timeout_seconds: 600
  instructions:
    - id: evidence-first
      text: Cite the changed line for every finding you report.
    - id: contract-signal
      text: Read the area of a question whose calibrated number fired, whole.
      applies:
        rules: [value-above-threshold, confidence-below-band]
    - id: payments
      text: Read the payment service with the change in hand.
      applies:
        paths: ["services/payments/**"]
"""

ANSWERS = "answers.json"
SPAWNS = "spawns.json"


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def score(value, confidence=0.9):
    return {"type": "score", "score": value, "confidence": confidence}


class DeepReviewFixture(unittest.TestCase):
    """A repository and a configuration whose critical question is calibrated; the head edit is small by default."""

    # Whether the head edit also adds a migration under the configuration's sensitive path.
    migration = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.screening = Path(self.tmp.name, "screening.json")
        # The canned reviewer: `answers.json` is what it answers from, `spawns.json` is what the run asks it for.
        self.reviewer = Path(self.tmp.name, "reviewer")
        self.reviewer.mkdir()
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        git(self.workspace, "init", "-b", "main")
        for name, text in BASE_FILES.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.commit("base")
        base = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "store.py").write_text(HEAD_EDIT, encoding="utf-8")
        if self.migration:
            added = self.workspace / MIGRATION
            added.parent.mkdir(parents=True, exist_ok=True)
            added.write_text("create index items_id on items (id);\n", encoding="utf-8")
        self.commit("head")
        return base, git(self.workspace, "rev-parse", "HEAD")

    def commit(self, message):
        git(self.workspace, "add", "--all")
        git(
            self.workspace,
            "-c",
            "user.name=acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        )

    def write_config(self, sensitive_paths=(), question=QUESTION, deep_review=True):
        """The configuration the run reads; a control asks for a configuration the phase has not extended yet."""
        self.config.write_text(
            CONFIG.format(
                sensitive_paths=json.dumps(list(sensitive_paths)),
                question=question,
                deep_review=DEEP_REVIEW if deep_review else "",
            ),
            encoding="utf-8",
        )
        return self.config

    def write_screening_error(self, reason="the provider timed out"):
        """A canned screening failure, which is an incomplete execution rather than a clean verdict."""
        self.screening.write_text(json.dumps({"responses": [{"error": reason}]}), encoding="utf-8")
        return self.screening

    def write_screening(self, value=1.0, confidence=0.9):
        """One canned screening answer for the whole bank: the configured question, and nothing else."""
        self.screening.write_text(
            json.dumps(
                {
                    "responses": [
                        {
                            "model": "jev-1.14.0",
                            "answers": {QUESTION: score(value, confidence)},
                            "usage": {"input_tokens": 1000, "output_tokens": 20},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return self.screening

    def write_review(self, answer):
        """The reviewer's one canned answer; a `failure` key makes the review stop where that answer stopped."""
        (self.reviewer / ANSWERS).write_text(json.dumps({"answers": [answer]}), encoding="utf-8")
        return self.reviewer

    def spawns(self):
        """What the run asked the canned reviewer for, or nothing when it never spawned one."""
        path = self.reviewer / SPAWNS
        if not path.is_file():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def screen(self, *, harness=None, deep_review_fixture=True, extra=()):
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail("no `agent-review` on PATH: run the check under `uv run`")
        argv = [
            binary,
            "code",
            "--base",
            self.base,
            "--head",
            self.head,
            "--output",
            str(self.output),
            "--screening-fixture",
            str(self.screening),
            "--config",
            str(self.config),
        ]
        if harness is not None:
            argv += ["--deep-review", harness]
            if deep_review_fixture:
                argv += ["--deep-review-fixture", str(self.reviewer)]
        return subprocess.run(
            argv + list(extra),
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )

    def report(self):
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))

    def reasons(self):
        return [reason["rule"] for reason in self.report()["escalation"]["reasons"]]

    def disposition(self, finding=None, disposition="confirmed", evidence="store.py:2"):
        """A disposition of one fired signal: the reviewer confirms, dismisses or downgrades it."""
        reason = self.report()["escalation"]["reasons"][0]
        answer = {
            "signal": reason["rule"],
            "question": reason.get("question", ""),
            "disposition": disposition,
            "evidence": evidence,
        }
        if finding is not None:
            answer["finding"] = finding
        return answer

    def confirmed_finding(self, blocking=False):
        """The reviewer's finding on the critical question - blocking only if the reviewer says so."""
        return {
            "id": "F1",
            "classification": "critical",
            "path": MIGRATION,
            "line": 1,
            "requirement": QUESTION,
            "evidence": "the index is created without a way back",
            "correction": "add the down migration beside it",
            "blocking": blocking,
        }


class FixtureControlTests(DeepReviewFixture):
    """The change is what the scenarios say it is, and a screen with no deep review is only a screen."""

    def test_S1_control_the_head_edit_is_small_by_default(self):
        changed = git(self.workspace, "diff", "--name-only", f"{self.base}..{self.head}")
        self.assertIn("store.py", changed)
        self.assertNotIn(MIGRATION, changed)

    def test_S1_control_a_screen_with_no_deep_review_writes_its_report_and_spawns_nothing(self):
        self.write_config(deep_review=False)
        self.write_screening()
        self.write_review({"findings": [], "dispositions": []})
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.report()["verdict"], "screened")
        self.assertEqual(self.spawns(), [], "no --deep-review asks for no reviewer")


class FocusedReviewTests(DeepReviewFixture):
    """S1: one spawned reviewer investigates the flagged areas with the instruction sets that match them."""

    def test_S1_one_reviewer_is_spawned_for_the_signals_with_the_matching_instructions(self):
        self.write_config()
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review(
            {
                "findings": [self.confirmed_finding()],
                "dispositions": [
                    self.disposition(finding="F1", evidence="store.py:2 answers the question"),
                    self.disposition(evidence="store.py:2 answers the question"),
                ],
            }
        )
        result = self.screen(harness="pi")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        review = self.report()["deep_review"]
        self.assertEqual(
            review["scope"], "focused", "a calibrated signal is investigated where it points"
        )
        self.assertEqual(review["spawns"], 1, "one escalation is one spawned reviewer")
        self.assertEqual(review["capabilities"], ["read", "search"])
        self.assertEqual(
            review["instructions"],
            ["evidence-first", "contract-signal"],
            "the set matching the fired rule joins; the path set that matched no change does not",
        )
        self.assertEqual([signal["question"] for signal in review["signals"]], [QUESTION, QUESTION])
        spawned = self.spawns()
        self.assertEqual(len(spawned), 1, "exactly one reviewer was asked to investigate")
        self.assertEqual(spawned[0]["scope"], "focused")
        self.assertEqual(
            [entry["id"] for entry in spawned[0]["instructions"]], review["instructions"]
        )


class FullScopeTests(DeepReviewFixture):
    """S2: a sensitive path or a coverage gap is read whole, not where the screening pointed."""

    migration = True

    def test_S2_a_sensitive_path_is_read_whole_and_its_path_instruction_joins(self):
        self.write_config(sensitive_paths=("db/migrations/**",))
        self.write_screening()
        self.write_review(
            {"findings": [], "dispositions": [self.disposition(disposition="dismissed")]}
        )
        result = self.screen(harness="pi")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        review = self.report()["deep_review"]
        self.assertEqual(review["scope"], "full", "a sensitive path is read whole")
        self.assertEqual(review["instructions"], ["evidence-first", "migration-safety"])
        self.assertEqual(len(self.spawns()), 1)

    def test_S2_control_a_sensitive_path_escalates_the_screen(self):
        self.write_config(sensitive_paths=("db/migrations/**",), deep_review=False)
        self.write_screening()
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("protected-path", self.reasons())


class CapabilityTests(DeepReviewFixture):
    """S3: a harness that cannot enforce read/search-only is refused before anything is spawned."""

    def test_S3_a_harness_with_no_read_only_enforcement_is_refused_and_nothing_is_spawned(self):
        self.write_config()
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review({"findings": [], "dispositions": []})
        result = self.screen(harness="zsh")
        self.assertEqual(
            result.returncode, 2, "a refused harness screens nothing and reviews nothing"
        )
        self.assertIn("zsh", result.stderr, "the refusal names the harness it refused")
        self.assertIn(
            "read", result.stderr.lower(), "the refusal names the capability it cannot enforce"
        )
        self.assertIn("pi", result.stderr, "the refusal names a harness that can enforce it")
        self.assertFalse(
            (self.output / "report.json").is_file(),
            "a refusal writes no report to be read as this run's",
        )
        self.assertEqual(
            self.spawns(), [], "nothing was spawned with the controls it could not enforce"
        )

    def test_S3_control_a_screen_with_no_harness_resolves_and_refuses_nothing(self):
        self.write_config(deep_review=False)
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review({"findings": [], "dispositions": []})
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.report()["verdict"], "escalated")
        self.assertEqual(self.spawns(), [], "no harness is resolved when none is asked for")


class DispositionTests(DeepReviewFixture):
    """S4: every critical/high signal is answered on its own, and a contract violation cannot be downgraded."""

    def test_S4_each_fired_signal_carries_its_own_evidenced_disposition(self):
        self.write_config()
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review(
            {
                "findings": [self.confirmed_finding(blocking=False)],
                "dispositions": [
                    self.disposition(finding="F1", evidence="store.py:2 answers the threshold"),
                    self.disposition(evidence="store.py:2 answers the band"),
                ],
            }
        )
        result = self.screen(harness="pi")
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        review = report["deep_review"]
        self.assertEqual(len(review["signals"]), 2, "a value and a band both fired on this answer")
        self.assertEqual(
            [entry["signal"] for entry in review["dispositions"]],
            [signal["rule"] for signal in review["signals"]],
            "one disposition per fired signal, in the order they fired",
        )
        value, band = review["dispositions"]
        self.assertEqual(value["value"], 2.5, "the disposition carries the answered value")
        self.assertEqual(value["confidence"], 0.3, "and the confidence it came with")
        self.assertIn("value=1.5", value["threshold"], "and the declaration that fired")
        self.assertIn("confidence=0.6", band["threshold"])
        self.assertTrue(
            review["findings"][0]["blocking"],
            "a confirmed contract violation is blocking whichever the reviewer answered (D16)",
        )
        self.assertEqual(report["verdict"], "blocked")

    def test_S4_control_a_fired_signal_names_its_question_value_confidence_and_threshold(self):
        self.write_config(deep_review=False)
        self.write_screening(value=2.5, confidence=0.3)
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        reasons = self.report()["escalation"]["reasons"]
        self.assertEqual(
            [reason["rule"] for reason in reasons],
            ["value-above-threshold", "confidence-below-band"],
        )
        self.assertEqual(reasons[0]["question"], QUESTION)
        self.assertEqual(reasons[0]["value"], 2.5)
        self.assertEqual(reasons[1]["confidence"], 0.3)
        self.assertIn("value=1.5", reasons[0]["pattern"], "the declaration that fired travels")
        self.assertIn("confidence=0.6", reasons[1]["pattern"])


class IncompleteReviewTests(DeepReviewFixture):
    """S5: a review that stops is incomplete, keeps what it learned, and is never retried or fallen back from."""

    def test_S5_a_timeout_is_incomplete_keeping_the_judgments_and_findings_it_had(self):
        self.write_config()
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review(
            {
                "findings": [self.confirmed_finding()],
                "dispositions": [self.disposition(finding="F1")],
                "failure": "the reviewer timed out after 600 s",
            }
        )
        result = self.screen(harness="pi")
        self.assertNotEqual(
            result.returncode, 0, "an incomplete execution is never a clean verdict"
        )
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("timed out", report["failure"]["reason"])
        self.assertTrue(report["judgments"], "the screening it paid for is kept")
        review = report["deep_review"]
        self.assertEqual(review["spawns"], 1, "no retry and no model fallback")
        self.assertEqual([finding["id"] for finding in review["findings"]], ["F1"])

    def test_S5_a_signal_left_undisposed_is_unresolved_and_incomplete(self):
        self.write_config()
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review({"findings": [], "dispositions": []})
        result = self.screen(harness="pi")
        self.assertNotEqual(result.returncode, 0)
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        unanswered = [entry["signal"] for entry in report["deep_review"]["unresolved"]]
        self.assertEqual(
            unanswered,
            [signal["rule"] for signal in report["deep_review"]["signals"]],
            "every fired signal is named as unresolved, not read as dismissed",
        )
        self.assertEqual(report["deep_review"]["spawns"], 1)

    def test_S5_control_a_screening_failure_is_incomplete_rather_than_passed(self):
        self.write_config(deep_review=False)
        self.write_screening_error("the provider timed out")
        result = self.screen()
        self.assertNotEqual(result.returncode, 0, "a failed screening is never a clean verdict")
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["verdict"], "nothing was concluded, so the verdict is null")


class VerdictTests(DeepReviewFixture):
    """S6: `screened` says nothing escalated, `passed` says a deep review finished clean."""

    def test_S6_a_clean_deep_review_passes_while_a_screen_alone_is_only_screened(self):
        self.write_config()
        self.write_screening(value=1.0, confidence=0.9)
        screened = self.screen(harness="pi")
        self.assertEqual(screened.returncode, 0, screened.stderr[-2000:])
        self.assertEqual(self.report()["verdict"], "screened")
        self.assertIsNone(self.report()["deep_review"])
        self.write_screening(value=2.5, confidence=0.3)
        self.write_review(
            {"findings": [], "dispositions": [self.disposition(disposition="dismissed")]}
        )
        passed = self.screen(harness="pi")
        self.assertEqual(passed.returncode, 0, passed.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["verdict"], "passed", "a clean deep review is not merely screened")

    def test_S6_control_an_escalation_with_no_review_is_not_passed(self):
        self.write_config(deep_review=False)
        self.write_screening(value=2.5, confidence=0.3)
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(self.report()["verdict"], "escalated", "escalated is not passed")
