"""Acceptance for the smart screening CLI (01-agent-review-smart-screening/S1..S8).

The runner places this file at `packages/agent-review/acceptance/screening/`. It drives the `agent-review` console
script as a subprocess over a real repository, a configuration it writes, a canned screening provider and a price
table; it imports nothing from the package, so it collects cleanly before the phase exists and fails at base.

The contract it pins (brief.md, P1-D1..P1-D8):

  - a configured question extends the packaged baseline and cannot replace a baseline id; `sensitive_paths`,
    `thresholds`, `chunk.max_diff_chars`, `concurrency` and `cross_file.max_files` are the phase's configuration
  - the port is called once for whole-diff applicability, then once per chunk in chunk order; `usage.requests` is
    `1 + len(chunks)` on the completed path (P1-D2)
  - an uncertain applicability group is retained, an irrelevant one is not asked, and a failed applicability call
    falls back to every group while the execution stays `completed` (D10, P1-D3)
  - a changed path, or either side of a rename, matching a global or repository sensitive path sets
    `escalation.recommended` with a `sensitive-path` reason and its source (D13, P1-D4)
  - chunking is deterministic and total: `coverage.changed_regions == coverage.covered` on the completed path (D41)
  - a change wider than `cross_file.max_files` records `coverage.gap` and a `coverage-gap` reason (D42, D43)
  - escalation comes from declared priorities and thresholds, never from the model: `flagged`, `uncertain`,
    `critical`, `sensitive-path`, `coverage-gap`; advisory uncertainty is recorded without escalating (D07, D08, D27)
  - the report names the provider's resolved model beside the configured alias, and `usage.cost_usd` is computed from
    the declared table for the resolved model, unknown — never zero — for an unlisted one (P1-D8)

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

RESOLVED = "test-model-1.13.0"
CONFIGURED = "test-model-v1"
CRITICAL = "contract.requirement-traceability"
ADVISORY = "engineering.elegance"
ADVISORY_STRONG = "engineering.hard-decisions"
HIGH = "engineering.traceable-cause"
REPO_QUESTION = "repo.migration-safety"

CONFIG = """\
model: {model}
sensitive_paths: {sensitive_paths}
thresholds:
  critical: 0.75
  high: 0.6
  advisory: 0.9
chunk:
  max_diff_chars: {max_diff_chars}
concurrency: {concurrency}
cross_file:
  max_files: {max_files}
questions:
{questions}
"""

REPO_ENTRY = """\
  - id: {id}
    priority: {priority}
    type: boolean
    ask: {ask}
"""

CHOICE_CONFIG = """\
model: test-model-v1
questions:
  - id: repo.migration-strategy
    priority: high
    type: choice
    ask: Which migration strategy does this change take
    options: [additive, destructive]
"""

BASE_FILES = {
    "store.py": "def load(path):\n    return open(path).read()\n",
    "server/app.py": "def handle(request):\n    return request\n",
    "db/migrations/001_init.sql": "create table items (id int);\n",
    "docs/guide.md": "# Guide\n\nHow to load things.\n",
}

PIPELINE = "".join("def step_%d(value):\n    return value + %d\n\n\n" % (n, n) for n in range(40))


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure, not the tool's."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


class SmartScreeningFixture(unittest.TestCase):
    """A repository whose head change touches a global sensitive path by rename, a repository one, and many hunks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.workspace = Path(self.tmp.name, "checkout")
        self.workspace.mkdir()
        self.output = Path(self.tmp.name, "review")
        self.config = Path(self.tmp.name, "config.yaml")
        self.fixture = Path(self.tmp.name, "screening.json")
        self.pricing = Path(self.tmp.name, "pricing.yaml")
        self.base, self.head = self.two_revisions()

    # ---- the fixture repository ---------------------------------------------------------------------------

    def two_revisions(self):
        """Base writes four files; head adds error handling, a migration, a big module, and renames a doc into
        `server/` — a mandatory global sensitive path. (base sha, head sha)."""
        git(self.workspace, "init", "-b", "main")
        for name, text in BASE_FILES.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        self.commit("base")
        base = git(self.workspace, "rev-parse", "HEAD")

        (self.workspace / "store.py").write_text(
            "def load(path):\n"
            "    try:\n"
            "        return open(path).read()\n"
            "    except OSError:\n"
            "        return None\n",
            encoding="utf-8",
        )
        (self.workspace / "db/migrations/002_index.sql").write_text(
            "create index items_id on items (id);\n", encoding="utf-8"
        )
        (self.workspace / "pipeline.py").write_text(PIPELINE, encoding="utf-8")
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

    # ---- the fixtures the tool reads ----------------------------------------------------------------------

    def write_config(self, *, sensitive_paths=(), questions=(REPO_QUESTION,), **knobs):
        """A configuration with this phase's surface, its questions rendered from `questions`."""
        settings = {"max_diff_chars": 200000, "concurrency": 4, "max_files": 40}
        settings.update(knobs)
        rendered = "".join(
            REPO_ENTRY.format(
                id=name,
                priority="advisory" if name == ADVISORY else "critical",
                ask="Does the change keep the migration safe to apply",
            )
            for name in questions
        )
        self.config.write_text(
            CONFIG.format(
                model=settings.pop("model", CONFIGURED),
                sensitive_paths=json.dumps([str(path) for path in sensitive_paths]),
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
        """`{models: {<id>: {input_per_million, output_per_million}}}` as YAML the tool reads."""
        lines = [f"version: {version}", "models:"]
        for name, rates in models.items():
            lines.append(f"  {name}:")
            lines.append(f"    input_per_million: {rates[0]}")
            lines.append(f"    output_per_million: {rates[1]}")
        self.pricing.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self.pricing

    # ---- the invocation -----------------------------------------------------------------------------------

    def screen(self, *, config=True, pricing=False, base=None, output=None, extra=()):
        """One screening of the fixture change; the caller has written the fixture and the configuration."""
        binary = shutil.which("agent-review")
        if binary is None:
            self.fail(
                "no `agent-review` on PATH: run the check under `uv run`, which installs the workspace member"
            )
        argv = [
            binary,
            "code",
            "--base",
            base or self.base,
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
        """The report the invocation wrote, read as JSON."""
        path = self.output / "report.json"
        self.assertTrue(path.is_file(), f"no report at {path}: an invocation writes one either way")
        return json.loads(path.read_text(encoding="utf-8"))

    def judged(self, result):
        """The report of a screening expected to have completed, after checking it did."""
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = self.report()
        self.assertEqual(report["status"], "completed", report.get("failure"))
        self.assertEqual(report["verdict"], "screened")
        self.assertIsNone(report["failure"])
        return report

    # ---- the canned provider's answers --------------------------------------------------------------------

    def applies(self, contract="relevant", engineering="relevant"):
        """The applicability call: one verdict per question group, then its usage."""
        return {
            "applicability": {
                "groups": [
                    {"id": "contract", "verdict": contract},
                    {"id": "engineering", "verdict": engineering},
                ]
            },
            "usage": {"input_tokens": 200, "output_tokens": 20},
            "model": RESOLVED,
        }

    def answers(self, values):
        """A screening call's answer: `{question id: (value, confidence)}` in configuration-free order."""
        return {
            "judgments": [
                {"question": name, "value": value, "confidence": confidence}
                for name, (value, confidence) in values.items()
            ],
            "usage": {"input_tokens": 1000, "output_tokens": 500},
            "model": RESOLVED,
        }

    def chunk_answers(self, count, values=None):
        """`count` screening answers, each carrying the first chunk's judgments, so any chunking is answered."""
        values = values if values is not None else {CRITICAL: (True, 0.99), ADVISORY: (True, 0.99)}
        return [self.answers(values) for _ in range(count)]

    def reasons(self, report):
        """The escalation signals the report carries, as `{signal: [reason, ...]}`."""
        reasons = {}
        for reason in report["escalation"]["reasons"]:
            reasons.setdefault(reason["signal"], []).append(reason)
        return reasons

    @staticmethod
    def judgments_for(report, question):
        """Every judgment the report carries for one question: a question asked per chunk has one per scope."""
        return [judgment for judgment in report["judgments"] if judgment["question"] == question]

    def questions_asked(self, report):
        """The question ids that were actually screened, whichever chunk or scope answered them."""
        return {judgment["question"] for judgment in report["judgments"]}


class FixtureControlTests(SmartScreeningFixture):
    def test_control_the_head_change_renames_a_file_into_a_global_sensitive_path(self):
        status = git(self.workspace, "diff", "--name-status", self.base, self.head).splitlines()
        renamed = [line for line in status if line.startswith("R")]
        self.assertTrue(renamed, "the fixture must carry a rename")
        self.assertIn("server/guide.md", renamed[0], "the rename must land inside server/**")
        self.assertTrue((self.workspace / "server/guide.md").is_file())

    def test_control_the_head_change_has_hunks_across_many_files(self):
        diff = git(self.workspace, "diff", self.base, self.head)
        self.assertGreaterEqual(diff.count("\n@@ "), 3, "the fixture must have hunks to chunk")
        changed = git(self.workspace, "diff", "--name-only", self.base, self.head).splitlines()
        self.assertGreaterEqual(len(changed), 4, "the fixture must touch several files")


class AdditiveConfigurationTests(SmartScreeningFixture):
    def test_S1_a_repository_question_extends_the_baseline_bank(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        report = self.judged(self.screen())
        ids = [question["id"] for question in report["questions"]]
        self.assertIn(REPO_QUESTION, ids, "the repository's question is asked")
        self.assertEqual(ids[-1], REPO_QUESTION, "the addition follows the baseline")
        for baseline in (CRITICAL, ADVISORY, HIGH):
            self.assertIn(
                baseline, ids, "a repository question never disables a baseline question (D05)"
            )

    def test_S1_a_configured_question_cannot_replace_a_baseline_id(self):
        self.write_config(questions=(CRITICAL,))
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        result = self.screen()
        self.assertEqual(result.returncode, 2, "replacing a baseline question is a refusal (D05)")
        self.assertIn(CRITICAL, result.stderr, "the refusal names the id it refused")
        self.assertFalse((self.output / "report.json").is_file(), "a refusal screens nothing")

    def test_S1_an_empty_question_list_means_no_additions(self):
        self.write_config(questions=())
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        report = self.judged(self.screen())
        ids = [question["id"] for question in report["questions"]]
        self.assertIn(CRITICAL, ids, "an empty list of additions still asks the baseline bank")
        self.assertNotIn(REPO_QUESTION, ids)

    def test_S1_an_unknown_configuration_field_is_still_refused(self):
        self.config.write_text(
            f"model: {CONFIGURED}\nquestions:\n  - id: repo.x\n    priority: high\n"
            "    type: boolean\n    ask: Is it\n    weight: 3\n",
            encoding="utf-8",
        )
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        result = self.screen()
        self.assertEqual(result.returncode, 2)
        self.assertIn("weight", result.stderr, "the refusal names the field by its path")


class ApplicabilityTests(SmartScreeningFixture):
    def test_S2_an_irrelevant_group_is_not_asked(self):
        self.write_config()
        self.write_fixture(
            self.applies(contract="irrelevant", engineering="relevant"), *self.chunk_answers(8)
        )
        report = self.judged(self.screen())
        self.assertEqual(report["applicability"]["status"], "evaluated")
        verdicts = {group["id"]: group["verdict"] for group in report["applicability"]["groups"]}
        self.assertEqual(verdicts["contract"], "irrelevant")
        asked = self.questions_asked(report)
        self.assertNotIn(CRITICAL, asked, "an irrelevant group's questions are not screened")
        self.assertIn(ADVISORY, asked, "a relevant group's questions are")

    def test_S2_an_uncertain_group_is_retained(self):
        self.write_config()
        self.write_fixture(
            self.applies(contract="uncertain", engineering="irrelevant"), *self.chunk_answers(8)
        )
        report = self.judged(self.screen())
        verdicts = {group["id"]: group["verdict"] for group in report["applicability"]["groups"]}
        self.assertEqual(verdicts["contract"], "uncertain")
        asked = self.questions_asked(report)
        self.assertIn(CRITICAL, asked, "uncertain groups are retained and asked (D10)")
        self.assertNotIn(ADVISORY, asked)

    def test_S2_a_failed_applicability_call_evaluates_every_group(self):
        self.write_config()
        self.write_fixture({"error": "applicability timed out"}, *self.chunk_answers(8))
        report = self.judged(self.screen())
        self.assertEqual(
            report["applicability"]["status"], "fallback", "D10's fallback, not a failure"
        )
        self.assertEqual(
            [group["verdict"] for group in report["applicability"]["groups"]],
            ["relevant", "relevant"],
            "a total filter failure evaluates every group",
        )
        asked = self.questions_asked(report)
        self.assertIn(CRITICAL, asked)
        self.assertIn(ADVISORY, asked)


class SensitivePathTests(SmartScreeningFixture):
    def test_S3_a_global_sensitive_path_requires_deep_review(self):
        self.write_config(sensitive_paths=())
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        report = self.judged(self.screen())
        self.assertTrue(
            report["escalation"]["recommended"], "a global sensitive path forces deep review"
        )
        paths = [
            reason
            for reason in self.reasons(report).get("sensitive-path", [])
            if reason.get("source") == "global"
        ]
        self.assertTrue(paths, "the reason names the global baseline pattern")
        self.assertEqual(paths[0]["pattern"], "server/**")
        self.assertIn("server/guide.md", json.dumps(paths[0]))

    def test_S3_a_rename_into_a_sensitive_path_counts(self):
        self.write_config(sensitive_paths=())
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        report = self.judged(self.screen())
        detail = json.dumps(self.reasons(report).get("sensitive-path", []))
        self.assertIn("docs/guide.md", detail, "a rename is matched on both sides (D13)")

    def test_S3_a_repository_sensitive_path_requires_deep_review(self):
        self.write_config(sensitive_paths=("db/migrations/**",))
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        report = self.judged(self.screen())
        repository = [
            reason
            for reason in self.reasons(report).get("sensitive-path", [])
            if reason.get("source") == "repository"
        ]
        self.assertTrue(repository, "a repository pattern routes to deep review")
        self.assertEqual(repository[0]["pattern"], "db/migrations/**")
        global_ones = [
            reason
            for reason in self.reasons(report).get("sensitive-path", [])
            if reason.get("source") == "global"
        ]
        self.assertTrue(global_ones, "adding repository paths never removes a global one (D05)")


class ChunkingTests(SmartScreeningFixture):
    def test_S4_every_changed_region_is_covered_by_a_chunk(self):
        self.write_config(max_diff_chars=700)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        self.assertGreater(len(report["chunks"]), 1, "a small budget splits the change")
        coverage = report["coverage"]
        self.assertGreater(coverage["changed_regions"], 1)
        self.assertEqual(
            coverage["covered"],
            coverage["changed_regions"],
            "a hunk no chunk carries is a defect (D41)",
        )
        self.assertIsNone(coverage["gap"])
        carried = {path for chunk in report["chunks"] for path in chunk["files"]}
        changed = set(git(self.workspace, "diff", "--name-only", self.base, self.head).splitlines())
        self.assertTrue(
            changed <= carried, f"every changed path is in a chunk: missing {changed - carried}"
        )

    def test_S4_the_port_is_called_once_per_chunk(self):
        self.write_config(max_diff_chars=700)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        self.assertEqual(
            report["usage"]["requests"],
            1 + len(report["chunks"]),
            "one applicability call and one call per chunk (P1-D2)",
        )

    def test_S4_chunking_is_deterministic(self):
        self.write_config(max_diff_chars=700)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        first = self.judged(self.screen())["chunks"]
        shutil.rmtree(self.output)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        second = self.judged(self.screen())["chunks"]
        self.assertEqual(first, second, "the same frozen change chunks the same way (D41)")

    def test_S4_a_judgment_names_the_path_it_was_made_on(self):
        self.write_config(max_diff_chars=700)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        changed = set(git(self.workspace, "diff", "--name-only", self.base, self.head).splitlines())
        for judgment in report["judgments"]:
            self.assertTrue(judgment["scope"], "a judgment names its scope (D44)")
            self.assertIn(
                judgment["scope"].split("#")[0],
                changed | {"whole-diff"},
                f"scope {judgment['scope']!r} names a changed path",
            )


class CrossFileTests(SmartScreeningFixture):
    def test_S5_a_change_wider_than_the_budget_records_a_gap(self):
        self.write_config(max_files=1)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        self.assertIsNotNone(report["coverage"]["gap"], "the coverage limitation is recorded (D43)")
        self.assertTrue(
            report["escalation"]["recommended"], "a coverage gap forces full deep review"
        )
        self.assertTrue(self.reasons(report).get("coverage-gap"), "the reason names the gap")

    def test_S5_a_change_inside_the_budget_records_no_gap(self):
        self.write_config(max_files=40)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        self.assertIsNone(report["coverage"]["gap"])
        self.assertFalse(self.reasons(report).get("coverage-gap"))


class EscalationTests(SmartScreeningFixture):
    def test_S6_a_flagged_critical_question_escalates(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(40, {CRITICAL: (False, 0.99)}))
        report = self.judged(self.screen())
        flagged = self.reasons(report).get("flagged", [])
        self.assertTrue(flagged, "a failed critical question escalates")
        self.assertEqual(flagged[0]["question"], CRITICAL)
        self.assertEqual(flagged[0]["priority"], "critical")

    def test_S6_an_uncertain_critical_question_escalates(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(40, {CRITICAL: (True, 0.2)}))
        report = self.judged(self.screen())
        uncertain = self.reasons(report).get("uncertain", [])
        self.assertTrue(uncertain, "a critical judgment below its threshold escalates (D08)")
        self.assertEqual(uncertain[0]["question"], CRITICAL)
        confidences = [judgment["confidence"] for judgment in self.judgments_for(report, CRITICAL)]
        self.assertEqual(confidences, [0.2], "the confidence is recorded (D44)")

    def test_S6_advisory_uncertainty_is_recorded_without_escalating(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(40, {ADVISORY: (True, 0.05)}))
        report = self.judged(self.screen())
        named = [
            reason
            for reasons in self.reasons(report).values()
            for reason in reasons
            if reason.get("question") == ADVISORY
        ]
        self.assertFalse(named, "advisory uncertainty is recorded, not escalated (D08)")
        confidences = [judgment["confidence"] for judgment in self.judgments_for(report, ADVISORY)]
        self.assertEqual(confidences, [0.05])

    def test_S6_a_high_confidence_advisory_flag_escalates(self):
        self.write_config()
        self.write_fixture(
            self.applies(), *self.chunk_answers(40, {ADVISORY_STRONG: (False, 0.95)})
        )
        report = self.judged(self.screen())
        flagged = self.reasons(report).get("flagged", [])
        self.assertTrue(
            [reason for reason in flagged if reason["question"] == ADVISORY_STRONG],
            "a high-confidence advisory flag escalates (D27)",
        )


class AccountingTests(SmartScreeningFixture):
    def test_S7_the_report_names_the_resolved_model_beside_the_alias(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        self.assertEqual(
            report["model"], RESOLVED, "the report names the model that answered (D26)"
        )
        self.assertEqual(
            report["model_configured"], CONFIGURED, "and the alias the configuration asked for"
        )

    def test_S7_an_unpriced_run_carries_no_cost(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen())
        usage = report["usage"]
        self.assertIsNone(usage["cost_usd"], "no price table, no cost")
        self.assertIsNone(usage["cost_basis"])

    def test_S7_an_exhausted_provider_stops_the_run_and_counts_its_calls(self):
        self.write_config(max_diff_chars=400)
        self.write_fixture(self.applies(), self.answers({CRITICAL: (True, 0.99)}))
        result = self.screen()
        self.assertNotEqual(
            result.returncode, 0, "an unanswered chunk is not a clean verdict (D40, D47)"
        )
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["verdict"])
        self.assertEqual(
            report["usage"]["requests"], 2, "one applicability call and one screening call"
        )


class RefusalTests(SmartScreeningFixture):
    def test_S9_an_unworkable_output_directory_is_a_refusal_not_a_traceback(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        blocked = Path(self.tmp.name, "not-a-directory")
        blocked.write_text("occupied\n", encoding="utf-8")
        result = self.screen(output=blocked)
        self.assertEqual(result.returncode, 2, "an output that cannot be a directory is a refusal")
        self.assertNotIn("Traceback", result.stderr, "a refusal is not a stack trace")
        self.assertIn(str(blocked), result.stderr, "the refusal names the path it refused")
        self.assertIn("--output", result.stderr, "and the way out")

    def test_S11_a_refusal_names_the_report_it_left_standing(self):
        self.write_config()
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        self.judged(self.screen())
        result = self.screen(base="no-such-revision")
        self.assertEqual(result.returncode, 2, "an unknown revision is a refusal")
        self.assertIn("no-such-revision", result.stderr, "the refusal names the revision")
        self.assertIn(
            str(self.output / "report.json"),
            result.stderr,
            "and the report it left standing, so a reader cannot mistake it for this invocation's",
        )


class FailureReportTests(SmartScreeningFixture):
    def test_S10_an_incomplete_report_keeps_the_usage_and_the_valid_judgment(self):
        self.write_config()
        self.write_fixture(
            self.applies(),
            {
                "judgments": [
                    {"question": CRITICAL, "value": True, "confidence": 0.99},
                    {"question": ADVISORY, "value": "banana", "confidence": 0.99},
                ],
                "usage": {"input_tokens": 1234, "output_tokens": 12},
                "model": RESOLVED,
            },
        )
        result = self.screen()
        self.assertNotEqual(
            result.returncode, 0, "an invalid judgment is not a clean verdict (D40)"
        )
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(report["verdict"])
        self.assertIn(
            ADVISORY, report["failure"]["reason"], "the reason names the question it refused"
        )
        self.assertEqual(
            report["usage"]["input_tokens"], 1234, "the usage the execution paid for is kept (D54)"
        )
        self.assertEqual(report["usage"]["output_tokens"], 12)
        self.assertEqual(
            [judgment["question"] for judgment in report["judgments"]],
            [CRITICAL],
            "a valid judgment is kept beside the invalid one (D47)",
        )


class ChoiceOptionTests(SmartScreeningFixture):
    def test_S12_a_choice_judgment_outside_its_options_is_refused(self):
        self.config.write_text(CHOICE_CONFIG, encoding="utf-8")
        self.write_fixture(
            self.applies(),
            self.answers({REPO_QUESTION: (True, 0.99)})
            | {
                "judgments": [
                    {"question": "repo.migration-strategy", "value": "banana", "confidence": 0.9}
                ]
            },
        )
        result = self.screen()
        self.assertNotEqual(
            result.returncode, 0, "an unbounded choice value is not a clean verdict"
        )
        report = self.report()
        self.assertEqual(report["status"], "incomplete")
        reason = report["failure"]["reason"]
        self.assertIn("repo.migration-strategy", reason, "the refusal names the question")
        self.assertIn("additive", reason, "and the options it declares")

    def test_S12_a_choice_question_without_options_is_refused_at_configuration_time(self):
        self.config.write_text(
            "model: test-model-v1\nquestions:\n  - id: repo.migration-strategy\n"
            "    priority: high\n    type: choice\n    ask: Which strategy\n",
            encoding="utf-8",
        )
        self.write_fixture(self.applies(), *self.chunk_answers(8))
        result = self.screen()
        self.assertEqual(
            result.returncode, 2, "an unbounded choice question is a configuration refusal"
        )
        self.assertIn("options", result.stderr)
        self.assertFalse((self.output / "report.json").is_file())


class UsageCountTests(SmartScreeningFixture):
    def test_S13_a_boolean_usage_count_is_never_reported_as_tokens(self):
        self.write_config()
        counted = self.answers({CRITICAL: (True, 0.99)})
        counted["usage"] = {"input_tokens": True, "output_tokens": 0.5}
        self.write_fixture(self.applies(), counted)
        report = self.judged(self.screen())
        usage = report["usage"]
        for field in ("input_tokens", "output_tokens"):
            self.assertIsInstance(usage[field], int, f"{field} is a whole number of tokens")
            self.assertIsNot(usage[field], True)
            self.assertIsNot(usage[field], False)

    def test_S13_a_usage_count_that_is_refused_is_named(self):
        self.write_config()
        counted = self.answers({CRITICAL: (True, 0.99)})
        counted["usage"] = {"input_tokens": "many", "output_tokens": 0.5}
        self.write_fixture(self.applies(), counted)
        result = self.screen()
        report = self.report()
        if result.returncode == 0:
            self.assertIsInstance(
                report["usage"]["input_tokens"], int, "a string count is not a token count"
            )
        else:
            self.assertEqual(report["status"], "incomplete")
            self.assertIn(
                "input_tokens", report["failure"]["reason"], "a refusal names the usage field"
            )


class PricingTests(SmartScreeningFixture):
    def test_S8_a_listed_model_is_priced_from_the_declared_table(self):
        self.write_config()
        self.write_pricing({RESOLVED: (0.15, 0.6)})
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen(pricing=True))
        usage = report["usage"]
        expected = round(
            (usage["input_tokens"] * 0.15 + usage["output_tokens"] * 0.6) / 1_000_000, 6
        )
        self.assertEqual(
            usage["cost_usd"], expected, "the cost is the table applied to the usage (P1-D8)"
        )
        self.assertEqual(usage["cost_basis"]["version"], 1)
        self.assertTrue(usage["cost_basis"]["table"].endswith("pricing.yaml"))

    def test_S8_an_unlisted_model_is_unknown_never_zero(self):
        self.write_config()
        self.write_pricing({"some-other-model": (1.0, 2.0)})
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        report = self.judged(self.screen(pricing=True))
        usage = report["usage"]
        self.assertIsNone(usage["cost_usd"], "an unlisted model's cost is unknown, not zero")
        self.assertEqual(
            usage["cost_unknown"], RESOLVED, "the report names the model it could not price"
        )

    def test_S8_the_same_frozen_input_and_table_reproduce_the_cost(self):
        self.write_config()
        self.write_pricing({RESOLVED: (0.15, 0.6)})
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        first = self.judged(self.screen(pricing=True))["usage"]
        shutil.rmtree(self.output)
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        second = self.judged(self.screen(pricing=True))["usage"]
        self.assertEqual(first["cost_usd"], second["cost_usd"])
        self.assertEqual(first["cost_basis"], second["cost_basis"])

    def test_S8_a_malformed_price_table_is_refused(self):
        self.write_config()
        self.pricing.write_text(
            "version: 1\nmodels:\n  %s:\n    input_per_million: free\n" % RESOLVED, encoding="utf-8"
        )
        self.write_fixture(self.applies(), *self.chunk_answers(40))
        result = self.screen(pricing=True)
        self.assertEqual(
            result.returncode, 2, "a malformed table is a refusal that screens nothing"
        )
        self.assertIn("input_per_million", result.stderr, "the refusal names the field it refused")
        self.assertFalse((self.output / "report.json").is_file())
