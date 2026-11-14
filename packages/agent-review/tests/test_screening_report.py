"""`report.json`: the screening's verdict, its provenance and the execution's own health.

00-agent-review-screening-cli/S2, S3, S4: a completed screening is `screened` and keeps the provider's
native judgments attributed to their question, priority, scope and revision; a provider failure or an
answer the question's type does not allow is an incomplete execution with an observable reason, never a
clean verdict; and the report names the revisions, the question configuration, the model and the usage,
so the same frozen input and configuration reproduce it (D34, D39, D40, D44, D53). The screening runs
through the fixture provider: no check here reaches a real provider.
"""

import json
import subprocess
import textwrap

import pytest
from agent_review.cli import main

pytestmark = pytest.mark.integration

CONFIG = textwrap.dedent(
    """\
    model: test-model-v1
    questions:
      - id: contract.error-handling
        priority: critical
        type: boolean
        ask: Does the change keep the documented error-handling contract
      - id: engineering.names-meaning
        priority: advisory
        type: boolean
        ask: Do the new names say what they mean
      - id: design.one-reason
        priority: high
        type: score
        ask: How much does the change keep one reason to change
    """
)

# Answered in the reverse of the configuration's order: the report aggregates deterministically (D46).
RESPONSE = {
    "judgments": [
        {"question": "design.one-reason", "value": 0.75},
        {"question": "engineering.names-meaning", "value": False},
        {"question": "contract.error-handling", "value": True},
    ]
}

BASE_FILE = "def load(path):\n    return open(path).read()\n"

HEAD_FILE = (
    "def load(path):\n"
    "    try:\n"
    "        return open(path).read()\n"
    "    except OSError:\n"
    "        return None\n"
)


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


class Review:
    """Two revisions of a real repository, a configuration, an output directory and a canned answer."""

    def __init__(self, tmp_path):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.fixture = tmp_path / "screening.json"
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        commit = (
            "-c",
            "user.name=agent-review",
            "-c",
            "user.email=agent-review@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
        )
        git(self.checkout, "init", "-b", "main")
        store = self.checkout / "store.py"
        store.write_text(BASE_FILE, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "base")
        base = git(self.checkout, "rev-parse", "HEAD")
        store.write_text(HEAD_FILE, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def screen(self, response):
        """One screening of the fixture, with `response` as the provider's only answer."""
        self.fixture.write_text(json.dumps({"responses": [response]}), encoding="utf-8")
        return main(
            [
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
            ]
        )

    def report(self):
        """The report the invocation wrote, read as JSON."""
        path = self.output / "report.json"
        assert path.is_file(), f"no report at {path}: an invocation writes one either way"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def review(tmp_path, monkeypatch):
    where = Review(tmp_path)
    monkeypatch.chdir(where.checkout)
    return where


def test_S2_a_completed_screening_is_screened_with_native_attributed_judgments(review, capsys):
    assert review.screen(RESPONSE) == 0
    report = review.report()
    assert report["status"] == "completed"
    assert report["verdict"] == "screened", "nothing escalates in P0 (D39)"
    assert report["failure"] is None
    assert report["judgments"] == [
        {
            "question": "contract.error-handling",
            "priority": "critical",
            "scope": "whole-diff",
            "revision": f"{review.base}..{review.head}",
            "value": True,
        },
        {
            "question": "engineering.names-meaning",
            "priority": "advisory",
            "scope": "whole-diff",
            "revision": f"{review.base}..{review.head}",
            "value": False,
        },
        {
            "question": "design.one-reason",
            "priority": "high",
            "scope": "whole-diff",
            "revision": f"{review.base}..{review.head}",
            "value": 0.75,
        },
    ], "native values, configuration order, and each judgment's provenance (D07, D44, D46)"
    summary = capsys.readouterr().out
    assert "screened" in summary and str(review.output / "report.json") in summary


def test_S2_the_terminal_summary_reads_the_judgments_without_the_report(review, capsys):
    assert review.screen(RESPONSE) == 0
    summary = capsys.readouterr().out
    assert "contract.error-handling" in summary and "critical" in summary
    assert "true" in summary
    with pytest.raises(json.JSONDecodeError):
        json.loads(summary)


def test_S3_a_provider_error_is_incomplete_with_an_observable_reason(review, capsys):
    assert review.screen({"error": "provider answered 500"}) != 0, "an incomplete run never exits 0"
    report = review.report()
    assert report["status"] == "incomplete"
    assert report["verdict"] is None, "an incomplete execution has no verdict (D34, D40)"
    assert "500" in report["failure"]["reason"]
    assert report["usage"]["requests"] == 1, "the provider was asked again (D40 retries)"
    assert "500" in capsys.readouterr().err, "the reason is observable without reading the report"


def test_S3_an_invalid_judgment_is_incomplete_naming_the_question(review):
    assert (
        review.screen(
            {
                "judgments": [
                    {"question": "contract.error-handling", "value": "banana"},
                    {"question": "engineering.names-meaning", "value": False},
                ]
            }
        )
        != 0
    ), "an invalid judgment must not exit 0"
    report = review.report()
    assert report["status"] == "incomplete"
    assert report["verdict"] is None
    assert "contract.error-handling" in report["failure"]["reason"]
    assert report["usage"]["requests"] == 1


def test_S3_a_judgment_on_a_question_the_run_did_not_ask_is_incomplete(review):
    assert review.screen({"judgments": [{"question": "contract.not-asked", "value": True}]}) != 0, (
        "a judgment the run cannot attribute is not a screening result"
    )
    report = review.report()
    assert report["status"] == "incomplete"
    assert report["judgments"] == [], "nothing unattributable lands in the report"
    assert "contract.not-asked" in report["failure"]["reason"]


def test_S4_the_report_names_revisions_questions_model_and_usage(review):
    assert review.screen(RESPONSE) == 0
    report = review.report()
    assert report["input"] == {"base": review.base, "head": review.head}
    assert report["questions"] == [
        {"id": "contract.error-handling", "priority": "critical", "type": "boolean"},
        {"id": "engineering.names-meaning", "priority": "advisory", "type": "boolean"},
        {"id": "design.one-reason", "priority": "high", "type": "score"},
    ], "the report names the question configuration it evaluated (D26, D51)"
    assert report["model"] == "test-model-v1"
    assert report["usage"]["requests"] == 1
    assert isinstance(report["usage"]["input_tokens"], int)
    assert isinstance(report["usage"]["output_tokens"], int)


def test_S4_the_same_frozen_input_and_configuration_produce_the_same_report(review):
    assert review.screen(RESPONSE) == 0
    first = review.report()
    assert review.screen(RESPONSE) == 0
    assert review.report() == first
