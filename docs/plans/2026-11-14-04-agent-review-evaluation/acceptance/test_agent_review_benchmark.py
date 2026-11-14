"""Acceptance for the frozen historical evaluation (04-agent-review-evaluation/S1..S4).

The runner places this file at `packages/agent-review/acceptance/evaluation/`. It drives a `agent-review bench`
invocation as a subprocess over a real repository with two frozen cases, a canned provider and a price table; it
imports nothing from the package, so it collects cleanly before the phase exists and fails at base.

The contract it pins (brief.md, P4-D1..P4-D4):

  - a benchmark run reports per case the escalation decision, the case's kind and the judgments behind the decision,
    and a summary that names the split it measured (S1)
  - a bank, threshold or calibration that moved after the freeze is refused, naming the digest, never measured (S2)
  - acceptance is decided on the escalation action: a missed bad case fails the run and is named; a run with no miss
    reports the clean changes it still escalated (S3)
  - every case carries its requests, tokens, cost from the declared table and latency (S4)

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

CASES = """\
- name: known-bad
  base: {base}
  head: {bad}
  kind: bad
  split: calibration
- name: known-clean
  base: {base}
  head: {clean}
  kind: clean
  split: calibration
"""

CONFIG = """\
model: jev-latest
thresholds:
  contract.error-handling:
    value: 1.5
    confidence: 0.6
questions:
  - id: contract.error-handling
    priority: critical
    type: score
    criteria: [none, minor, moderate, major, severe]
    ask: How severely does the change violate error handling
"""


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def answers(severity=0.0, served="jev-1.14.0"):
    return {
        "model": served,
        "answers": {
            "contract.error-handling": {"type": "score", "score": severity, "confidence": 0.9}
        },
        "usage": {"input_tokens": 1000, "output_tokens": 20},
    }


class BenchmarkFixture(unittest.TestCase):
    """A repository with three revisions: a base, a known-bad change that is escalated, a clean one that is not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "bench")
        self.cases = Path(self.tmp.name, "cases.yaml")
        self.config = Path(self.tmp.name, "bank.yaml")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.pricing = Path(self.tmp.name, "pricing.yaml")
        self.frozen = Path(self.tmp.name, "frozen.json")
        self.base, self.bad, self.clean = self.three_revisions()
        self.cases.write_text(
            CASES.format(base=self.base, bad=self.bad, clean=self.clean), encoding="utf-8"
        )
        self.config.write_text(CONFIG, encoding="utf-8")
        self.pricing.write_text(
            "version: 1\nmodels:\n  jev-1.14.0:\n    input_per_million: 0.042\n"
            "    output_per_million: 0.0\n",
            encoding="utf-8",
        )

    def three_revisions(self):
        git(self.workspace, "init", "-b", "main")
        (self.workspace / "store.py").write_text(
            "def load(path):\n    return open(path).read()\n", encoding="utf-8"
        )
        self.commit("base")
        base = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "checkout.py").write_text(
            "def charge(card):\n    return card.charge()\n", encoding="utf-8"
        )
        self.commit("bad")
        bad = git(self.workspace, "rev-parse", "HEAD")
        (self.workspace / "README.md").write_text("docs\n", encoding="utf-8")
        self.commit("clean")
        return base, bad, git(self.workspace, "rev-parse", "HEAD")

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

    def write_fixture(self, *responses):
        self.fixture.write_text(json.dumps({"responses": list(responses)}), encoding="utf-8")
        return self.fixture

    def bench(self, *, extra=(), cases=None):
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail("no `agent-review` on PATH: run the check under `uv run`")
        argv = [
            binary,
            "bench",
            "--cases",
            str(cases or self.cases),
            "--config",
            str(self.config),
            "--pricing",
            str(self.pricing),
            "--screening-fixture",
            str(self.fixture),
            "--output",
            str(self.output),
        ]
        return subprocess.run(
            argv + list(extra),
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )

    def report(self):
        path = self.output / "benchmark.json"
        self.assertTrue(path.is_file(), f"no benchmark at {path}")
        return json.loads(path.read_text(encoding="utf-8"))


class FixtureControlTests(BenchmarkFixture):
    def test_control_the_fixture_has_one_bad_and_one_clean_case(self):
        self.assertNotEqual(self.bad, self.clean)
        self.assertTrue(self.cases.is_file())


class ReportingTests(BenchmarkFixture):
    """S1: per-case decisions and a summary that names its split."""

    def test_S1_each_case_reports_its_decision_kind_and_judgments(self):
        self.write_fixture(answers(severity=2.5), answers(severity=0.0))
        result = self.bench()
        report = self.report()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        cases = {c["name"]: c for c in report["cases"]}
        self.assertEqual(set(cases), {"known-bad", "known-clean"})
        self.assertTrue(cases["known-bad"]["escalated"], "severity 2.5 is above its 1.5 threshold")
        self.assertFalse(cases["known-clean"]["escalated"])
        self.assertEqual(cases["known-bad"]["kind"], "bad")
        self.assertTrue(
            cases["known-bad"]["judgments"], "the judgments behind the decision travel with it"
        )
        self.assertEqual(report["split"]["calibration"]["bad"], 1)
        self.assertEqual(report["split"]["calibration"]["clean"], 1)


class FreezeTests(BenchmarkFixture):
    """S2: a moved bank is refused, not measured."""

    def test_S2_a_bank_that_moved_after_the_freeze_is_refused_naming_the_digest(self):
        self.write_fixture(answers(severity=2.5), answers(severity=0.0))
        self.assertEqual(self.bench(extra=["--freeze", str(self.frozen)]).returncode, 0)
        self.config.write_text(CONFIG.replace("value: 1.5", "value: 2.5"), encoding="utf-8")
        result = self.bench(extra=["--frozen", str(self.frozen)])
        self.assertEqual(result.returncode, 2, result.stderr[-2000:])
        self.assertIn("digest", result.stderr.lower())


class AcceptanceTests(BenchmarkFixture):
    """S3: the action decides — a miss fails, a clean escalation is reported."""

    def test_S3_a_missed_bad_case_fails_acceptance_and_is_named(self):
        self.write_fixture(answers(severity=0.0), answers(severity=0.0))
        result = self.bench()
        self.assertNotEqual(result.returncode, 0, "a missed known-bad case must fail the run")
        report = self.report()
        self.assertFalse(report["summary"]["accepted"])
        self.assertEqual([c["name"] for c in report["summary"]["missed"]], ["known-bad"])

    def test_S3_no_miss_passes_and_still_reports_the_clean_changes_it_escalated(self):
        self.write_fixture(answers(severity=2.5), answers(severity=2.5))
        result = self.bench()
        report = self.report()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertTrue(report["summary"]["accepted"])
        self.assertFalse(report["summary"]["missed"])
        self.assertEqual(
            [c["name"] for c in report["summary"]["false_escalations"]], ["known-clean"]
        )


class CostTests(BenchmarkFixture):
    """S4: requests, tokens, cost and latency per case."""

    def test_S4_each_case_reports_requests_tokens_cost_and_latency(self):
        self.write_fixture(answers(severity=2.5), answers(severity=0.0))
        result = self.bench()
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        case = self.report()["cases"][0]
        self.assertGreaterEqual(case["usage"]["requests"], 1)
        self.assertEqual(case["usage"]["input_tokens"], 1000)
        self.assertAlmostEqual(case["usage"]["cost_usd"], 0.000042, places=9)
        self.assertIn("latency_s", case)
