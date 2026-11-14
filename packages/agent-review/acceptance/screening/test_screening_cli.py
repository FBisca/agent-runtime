"""Acceptance for the screening CLI (00-agent-review-screening-cli/S2, S3, S4).

The runner places this file at `packages/agent-review/acceptance/`. It drives the `agent-review` console script as a
subprocess over a real two-revision git repository, a config it writes and a canned screening response; it imports
nothing from the package, so it collects cleanly before the package exists. The contract it pins (`brief.md`,
P0-D1..P0-D4):

  - `agent-review code --base <rev> --head <rev> --output <dir> --config <path> --screening-fixture <path>`
  - exit 0 means the invocation completed, and the verdict lives in `report.json`; a failure to screen exits nonzero
    with `status: incomplete`, never with a clean verdict (D34, D35, D40)
  - a completed screening with nothing to escalate is `screened`, its judgments native and attributed to the
    question, the priority, the scope and the revision they were made on (D39, D44)
  - the port is called once: `usage.requests` is 1 on both the error and the invalid-judgment path, so a retry or a
    model fallback shows as a second request (D40)
  - the report names both revisions, the question configuration, the model and the usage, and the same frozen input
    and configuration produce the same report (S4, D26, D53)

Each class has a control case that passes at base and after.
"""

import json
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CONFIG = textwrap.dedent(
    """\
    model: test-model-v1
    questions:
      - id: contract.error-handling
        priority: critical
        type: boolean
        ask: Does the change keep the documented error-handling contract
      - id: design.naming
        priority: advisory
        type: boolean
        ask: Do the new names say what they mean
    """
)

RESPONSE = {
    "judgments": [
        {"question": "contract.error-handling", "value": True},
        {"question": "design.naming", "value": False},
    ]
}

ANSWERED = "contract.error-handling"

BASE_FILE = "def load(path):\n    return open(path).read()\n"

HEAD_FILE = (
    "def load(path):\n"
    "    try:\n"
    "        return open(path).read()\n"
    "    except OSError:\n"
    "        return None\n"
)


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure, not the tool's."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


class ScreeningFixture(unittest.TestCase):
    """Two revisions of a real repository, a config, an output directory and a canned screening response."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.config.write_text(CONFIG, encoding="utf-8")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        """A repository whose head adds error handling to the file the base wrote; (base sha, head sha)."""
        git(self.workspace, "init", "-b", "main")
        commit = (
            "-c",
            "user.name=acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
        )
        store = self.workspace / "store.py"
        store.write_text(BASE_FILE, encoding="utf-8")
        git(self.workspace, "add", "--all")
        git(self.workspace, *commit, "base")
        base = git(self.workspace, "rev-parse", "HEAD")
        store.write_text(HEAD_FILE, encoding="utf-8")
        git(self.workspace, "add", "--all")
        git(self.workspace, *commit, "head")
        return base, git(self.workspace, "rev-parse", "HEAD")

    def screen(self, response):
        """One screening of the fixture, with `response` as the port's only answer."""
        self.fixture.write_text(json.dumps({"responses": [response]}), encoding="utf-8")
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail(
                "no `agent-review` on PATH: run the check under `uv run`, which installs the workspace member"
            )
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
        )

    def report(self):
        """The report the invocation wrote, read as JSON."""
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))


class CompletedScreeningTests(ScreeningFixture):
    def test_control_the_fixture_carries_a_two_revision_diff(self):
        self.assertNotEqual(self.base, self.head)
        self.assertIn("except OSError", git(self.workspace, "diff", self.base, self.head))

    def test_S2_a_completed_screening_is_screened_with_native_judgments(self):
        result = self.screen(RESPONSE)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["verdict"], "screened", "nothing was escalated in P0 (D39)")
        self.assertIsNone(report["failure"])
        judgments = {judgment["question"]: judgment for judgment in report["judgments"]}
        self.assertEqual(set(judgments), {"contract.error-handling", "design.naming"})
        self.assertIs(judgments[ANSWERED]["value"], True, "a boolean judgment is native (D44)")
        self.assertIs(judgments["design.naming"]["value"], False)
        self.assertEqual(
            judgments[ANSWERED]["priority"], "critical", "priority comes from config (D07)"
        )
        for judgment in judgments.values():
            self.assertTrue(judgment["scope"], "a judgment names its scope (D44)")
            self.assertIn(self.head, judgment["revision"], "a judgment names its revision (D44)")


class IncompleteScreeningTests(ScreeningFixture):
    def test_S3_a_provider_error_is_incomplete_and_never_retried(self):
        result = self.screen({"error": "provider answered 500"})
        self.assertNotEqual(result.returncode, 0, "an incomplete screening must not exit 0 (D35)")
        self.assertIn("500", result.stderr, "the reason is observable without reading the report")
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["verdict"], "an incomplete screening has no verdict (D34)")
        self.assertIn("500", report["failure"]["reason"])
        self.assertEqual(report["usage"]["requests"], 1, "the port was called again (D40 retries)")

    def test_S3_an_invalid_judgment_is_incomplete_and_never_retried(self):
        result = self.screen(
            {
                "judgments": [
                    {"question": ANSWERED, "value": "banana"},
                    {"question": "design.naming", "value": False},
                ]
            }
        )
        self.assertNotEqual(result.returncode, 0, "an invalid judgment must not exit 0 (D35)")
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["verdict"], "an invalid judgment is not a clean verdict (D40)")
        self.assertIn(
            ANSWERED, report["failure"]["reason"], "the refusal names the question it refused"
        )
        self.assertEqual(report["usage"]["requests"], 1, "the port was called again (D40 retries)")


class ReportProvenanceTests(ScreeningFixture):
    def test_S4_the_report_names_revisions_questions_model_and_usage(self):
        result = self.screen(RESPONSE)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["input"]["base"], self.base)
        self.assertEqual(report["input"]["head"], self.head)
        self.assertEqual(
            [question["id"] for question in report["questions"]],
            ["contract.error-handling", "design.naming"],
            "the report names the question configuration it evaluated",
        )
        self.assertEqual(report["model"], "test-model-v1", "the report names the model (D26)")
        self.assertGreaterEqual(report["usage"]["requests"], 1)
        for key in ("input_tokens", "output_tokens"):
            self.assertIsInstance(report["usage"][key], int)

    def test_S4_the_same_frozen_input_and_configuration_produce_the_same_report(self):
        self.screen(RESPONSE)
        first = self.frozen(self.report())
        shutil.rmtree(self.output)
        self.screen(RESPONSE)
        self.assertEqual(self.frozen(self.report()), first, "the same frozen input is reproducible")

    @staticmethod
    def frozen(report):
        """The report's frozen projection: what the same input and configuration must reproduce."""
        keys = ("status", "verdict", "input", "questions", "model", "judgments")
        return {key: report[key] for key in keys}
