"""Acceptance for the smart screening CLI (01-agent-review-smart-screening/S1..S6).

It drives the `agent-review` console
script as a subprocess over a real repository, a configuration and a price table it writes, and a canned System One
provider; it imports nothing from the package, so it collects cleanly before the phase exists and fails at base.

This file replaces the version approved before the Jev evidence: the applicability pre-pass is gone (fan-out), the
chunk budget is tokens, and escalation is a calibrated threshold or a confidence band. The contract it pins
(brief.md, P1-D1..P1-D6):

  - a repository question extends the packaged baseline and never replaces a baseline id; a score question carries its
    levels; an empty additions list asks the baseline alone; weights, thresholds and calibration sit beside the bank
  - a changed path, or either side of a rename, matching a baseline or repository protected path recommends full
    review with a `protected-path` reason naming the pattern and whether it came from the baseline or the repository
  - chunks are built inside the token budget in path order, each chunk is screened once with the whole bank —
    `usage.requests == len(chunks)`, never `1 + len(chunks)` — and `coverage.changed_regions == coverage.covered`
  - a change wider than `cross_file.max_files` records `coverage.gap` and a `coverage-gap` reason
  - escalation is decided by the tool: a noul below its `value` threshold, a score above it, or any judgment whose
    confidence is below its band; advisory uncertainty is recorded and never escalates alone
  - `usage.cost_usd` is computed from the declared table against the model the provider served, and is unknown — never
    zero — for an unlisted one

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

SERVED = "jev-1.14.0"
CONFIGURED = "jev-latest"
CRITICAL = "contract.error-handling"
HIGH = "engineering.hidden-state"
ADVISORY = "repo.style"
REPO_QUESTION = "repo.migration-safety"

CONFIG = """\
model: {model}
sensitive_paths: {sensitive_paths}
chunk:
  max_diff_tokens: {max_diff_tokens}
cross_file:
  max_files: {max_files}
weights:
  contract.error-handling: 3
  engineering.hidden-state: 2
thresholds:
  contract.error-handling:
    value: 1.5
    confidence: 0.6
  engineering.hidden-state:
    value: 0.5
    confidence: 0.4
  repo.style:
    value: 1.5
    confidence: 0.9
questions:
{questions}
"""

REPO_ENTRY = """\
  - id: {id}
    priority: {priority}
    type: noul
    ask: Does the change keep the migration safe to apply
"""

REPO_SCORE_ENTRY = """\
  - id: {id}
    priority: advisory
    type: score
    criteria: [none, minor, moderate, major, severe]
    ask: How severely does the change violate the repository's style
"""

BASE_FILES = {
    "store.py": "def load(path):\n    return open(path).read()\n",
    "docs/guide.md": "guide\n",
    "db/migrations/001_init.sql": "create table items (id int);\n",
}

PIPELINE = "".join(f"def step_{n}(value):\n    return value + {n}\n\n\n" for n in range(220))


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def score(value, confidence=0.9):
    return {"type": "score", "score": value, "confidence": confidence}


def noul(value):
    """A noul answer carries one number: the probability that the change satisfies the question."""
    return {"type": "noul", "noul": value}


class SmartScreeningFixture(unittest.TestCase):
    """A repository whose head edit adds a migration and a big module, and by default renames a doc into `server/`."""

    # A protected path answers before a request is made ("Protected path safeguard", "Safeguard escalation report"), so only the scenarios about that
    # answer keep the rename; the screening scenarios need a change that reaches a request at all.
    protected_path = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.pricing = Path(self.tmp.name, "pricing.yaml")
        self.base, self.head = self.two_revisions(protected=self.protected_path)

    def two_revisions(self, *, protected=True):
        git(self.workspace, "init", "-b", "main")
        for name, text in BASE_FILES.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.commit("base")
        base = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "db/migrations/002_index.sql").write_text(
            "create index items_id on items (id);\n", encoding="utf-8"
        )
        (self.workspace / "pipeline.py").write_text(PIPELINE, encoding="utf-8")
        if protected:
            # `git mv` will not create the destination directory, and `server/` is new in this head edit.
            (self.workspace / "server").mkdir(parents=True, exist_ok=True)
            git(self.workspace, "mv", "docs/guide.md", "server/guide.md")
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

    def write_config(self, *, sensitive_paths=(), questions=(REPO_QUESTION,), **knobs):
        settings = {"max_diff_tokens": 200000, "max_files": 40}
        settings.update(knobs)
        rendered = "".join(
            (REPO_SCORE_ENTRY if name == ADVISORY else REPO_ENTRY).format(
                id=name, priority="critical"
            )
            for name in questions
        )
        self.config.write_text(
            CONFIG.format(
                model=settings.pop("model", CONFIGURED),
                sensitive_paths=json.dumps([str(p) for p in sensitive_paths]),
                questions=rendered or "  []",
                **settings,
            ),
            encoding="utf-8",
        )
        return self.config

    def write_fixture(self, *responses):
        self.fixture.write_text(json.dumps({"responses": list(responses)}), encoding="utf-8")
        return self.fixture

    def write_pricing(self, models, version=1):
        lines = [f"version: {version}", "models:"]
        for name, (inp, out) in models.items():
            lines += [
                f"  {name}:",
                f"    input_per_million: {inp}",
                f"    output_per_million: {out}",
            ]
        self.pricing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.pricing

    def screen(self, *, config=True, pricing=False, output=None, extra=()):
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
            str(output or self.output),
            "--screening-fixture",
            str(self.fixture),
        ]
        if config:
            argv += ["--config", str(self.config)]
        if pricing:
            argv += ["--pricing", str(self.pricing)]
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
        return [r["rule"] for r in self.report().get("escalation", {}).get("reasons", [])]

    def answers(self, values, served=SERVED):
        """One canned screening answer for the whole bank, `{question: (value, confidence)}`."""

        def typed(name, value, confidence):
            if name in (HIGH, REPO_QUESTION):
                return noul(value)
            return score(value, confidence)

        return {
            "model": served,
            "answers": {
                name: typed(name, value, confidence) for name, (value, confidence) in values.items()
            },
            "usage": {"input_tokens": 1000, "output_tokens": 20},
        }


class FixtureControlTests(SmartScreeningFixture):
    def test_control_the_head_change_renames_into_a_global_sensitive_path_and_adds_a_big_module(
        self,
    ):
        changed = git(self.workspace, "diff", "--name-status", f"{self.base}..{self.head}")
        self.assertIn("server/guide.md", changed)
        self.assertTrue((self.workspace / "pipeline.py").stat().st_size > 5000)


class AdditiveConfigurationTests(SmartScreeningFixture):
    """S1: repository configuration extends the baseline, and its ambiguous shapes are refused."""

    def test_S1_a_repository_question_extends_the_baseline_bank(self):
        self.write_config()
        self.write_fixture(
            self.answers({CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9), REPO_QUESTION: (0.8, 0.9)})
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        asked = [q["id"] for q in self.report()["questions"]]
        self.assertIn(REPO_QUESTION, asked)
        self.assertIn(CRITICAL, asked, "the baseline is still asked")

    def test_S1_a_question_that_repeats_a_baseline_id_is_refused_by_name(self):
        self.write_config(questions=(CRITICAL,))
        self.write_fixture(self.answers({CRITICAL: (1.0, 0.9)}))
        result = self.screen()
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn(CRITICAL, result.stderr)

    def test_S1_an_empty_additions_list_asks_the_baseline_alone(self):
        self.write_config(questions=())
        self.write_fixture(self.answers({CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9)}))
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertNotIn(REPO_QUESTION, [q["id"] for q in self.report()["questions"]])


class SensitivePathTests(SmartScreeningFixture):
    """S2: a baseline and a repository pattern each route to full review, renames included."""

    def test_S2_a_rename_into_a_global_sensitive_path_recommends_full_review(self):
        self.write_config()
        self.write_fixture(
            self.answers({CRITICAL: (0.0, 0.9), HIGH: (0.9, 0.9), REPO_QUESTION: (0.9, 0.9)})
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertTrue(report["escalation"]["recommended"])
        matched = [r for r in report["escalation"]["reasons"] if r["rule"] == "protected-path"]
        self.assertTrue(matched, "the rename into server/ is a protected path")
        self.assertEqual(matched[0]["source"], "baseline")
        self.assertIn("server/", matched[0]["pattern"])

    def test_S2_a_repository_pattern_is_named_with_its_source(self):
        self.write_config(sensitive_paths=("db/migrations/**",))
        self.write_fixture(
            self.answers({CRITICAL: (0.0, 0.9), HIGH: (0.9, 0.9), REPO_QUESTION: (0.9, 0.9)})
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        matched = [
            r for r in self.report()["escalation"]["reasons"] if r["rule"] == "protected-path"
        ]
        self.assertTrue(
            any(r["source"] == "repository" and "db/migrations" in r["pattern"] for r in matched)
        )


class ChunkingTests(SmartScreeningFixture):
    """S3: one request per chunk with the whole bank, total coverage, no applicability call."""

    protected_path = False

    def test_S3_two_chunks_are_two_requests_and_every_region_is_covered(self):
        self.write_config(max_diff_tokens=400)
        self.write_fixture(
            self.answers({CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9), REPO_QUESTION: (0.8, 0.9)}),
            self.answers({CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9), REPO_QUESTION: (0.8, 0.9)}),
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        chunks = report["chunks"]
        self.assertEqual(len(chunks), 2, "a 400-token budget splits this change in two")
        self.assertEqual(
            report["usage"]["requests"], len(chunks), "one request per chunk, no applicability call"
        )
        self.assertEqual(report["coverage"]["changed_regions"], report["coverage"]["covered"])

    def test_S3_a_missing_chunk_answer_is_incomplete_rather_than_a_short_request_count(self):
        self.write_config(max_diff_tokens=400)
        self.write_fixture(
            self.answers({CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9), REPO_QUESTION: (0.8, 0.9)})
        )
        result = self.screen()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.report()["status"], "incomplete")


class CoverageGapTests(SmartScreeningFixture):
    """S4: a change wider than the cross-file limit records the gap instead of a clean screen."""

    protected_path = False

    def test_S4_a_change_wider_than_the_cross_file_limit_records_the_gap(self):
        self.write_config(max_files=1)
        self.write_fixture(
            self.answers({CRITICAL: (0.0, 0.9), HIGH: (0.9, 0.9), REPO_QUESTION: (0.9, 0.9)})
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertIsNotNone(report["coverage"]["gap"], "a wide change reports its coverage gap")
        self.assertIn("coverage-gap", [r["rule"] for r in report["escalation"]["reasons"]])


class EscalationTests(SmartScreeningFixture):
    """S5: a calibrated threshold or a confidence band decides, not the model."""

    protected_path = False

    def test_S5_a_score_above_its_threshold_escalates(self):
        self.write_config()
        self.write_fixture(
            self.answers({CRITICAL: (2.0, 0.9), HIGH: (0.9, 0.9), REPO_QUESTION: (0.9, 0.9)})
        )
        self.screen()
        self.assertIn("value-above-threshold", self.reasons())

    def test_S5_a_noul_below_its_threshold_escalates(self):
        self.write_config()
        self.write_fixture(
            self.answers({CRITICAL: (0.0, 0.9), HIGH: (0.2, 0.9), REPO_QUESTION: (0.9, 0.9)})
        )
        self.screen()
        self.assertIn("value-below-threshold", self.reasons())

    def test_S5_a_confidence_below_the_band_escalates_even_when_the_value_passes(self):
        self.write_config()
        self.write_fixture(
            self.answers({CRITICAL: (0.0, 0.2), HIGH: (0.9, 0.9), REPO_QUESTION: (0.9, 0.9)})
        )
        self.screen()
        self.assertIn("confidence-below-band", self.reasons())

    def test_S5_advisory_uncertainty_is_recorded_without_escalating(self):
        self.write_config(questions=(ADVISORY,))
        self.write_fixture(
            self.answers({CRITICAL: (0.0, 0.9), HIGH: (0.9, 0.9), ADVISORY: (0.9, 0.1)})
        )
        result = self.screen()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        judged = {j["question"]: j for j in report["judgments"]}
        self.assertAlmostEqual(judged[ADVISORY]["confidence"], 0.1)
        self.assertNotIn(
            "confidence-below-band", self.reasons(), "advisory uncertainty never escalates alone"
        )


class CostTests(SmartScreeningFixture):
    """S6: cost from the declared table against the served model, unknown for an unlisted one."""

    protected_path = False

    def test_S6_cost_is_computed_from_the_table_for_the_served_model(self):
        self.write_config()
        self.write_fixture(
            self.answers(
                {CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9), REPO_QUESTION: (0.8, 0.9)}, served=SERVED
            )
        )
        self.write_pricing({SERVED: (0.042, 0.0)})
        result = self.screen(pricing=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        usage = self.report()["usage"]
        self.assertAlmostEqual(usage["cost_usd"], 0.000042, places=9)
        self.assertIn("pricing", json.dumps(usage.get("cost_basis", {})).lower())

    def test_S6_an_unlisted_served_model_is_unknown_not_zero(self):
        self.write_config()
        self.write_fixture(
            self.answers(
                {CRITICAL: (1.0, 0.9), HIGH: (0.8, 0.9), REPO_QUESTION: (0.8, 0.9)},
                served="jev-9.9.9",
            )
        )
        self.write_pricing({SERVED: (0.042, 0.0)})
        result = self.screen(pricing=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        usage = self.report()["usage"]
        self.assertIsNone(usage["cost_usd"])
        self.assertIn("jev-9.9.9", json.dumps(usage.get("cost_unknown", "")))
