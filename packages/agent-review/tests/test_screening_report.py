"""`report.json`: the screening's verdict, its provenance and the execution's own health.

00-agent-review-screening-cli/S2, S3, S4: a completed screening is `screened` and keeps the provider's
native judgments attributed to their question, priority, scope and revision; a provider failure or an
answer the question's type does not allow is an incomplete execution with an observable reason, never a
clean verdict; and the report names the revisions, the question configuration, the model and the usage,
so the same frozen input and configuration reproduce it (D34, D39, D40, D44, D53). The screening runs
through the fixture provider: no check here reaches a real provider.
"""

import json
import re
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
        type: noul
        ask: Does the change keep the documented error-handling contract
      - id: engineering.names-meaning
        priority: advisory
        type: noul
        ask: Do the new names say what they mean
      - id: design.one-reason
        priority: high
        type: score
        criteria:
          - the change keeps one reason to change
          - the change names a second reason but keeps them apart
          - the change mixes two reasons into one module
          - the change has no single reason a reader could name
        ask: How much does the change keep one reason to change
    """
)

# Answered in the reverse of the configuration's order: the report aggregates deterministically (D46).
RESPONSE = {
    "model": "jev-1.14.0",
    "answers": {
        "design.one-reason": {"type": "score", "score": 2.0, "confidence": 0.8},
        "engineering.names-meaning": {"type": "noul", "noul": 0.2},
        "contract.error-handling": {"type": "noul", "noul": 0.9},
    },
    "usage": {"input_tokens": 700, "output_tokens": 30},
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

    def screen(self, response, long_context=False, output=None):
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
                str(output or self.output),
                "--config",
                str(self.config),
                "--screening-fixture",
                str(self.fixture),
                *(["--long-context"] if long_context else []),
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
            "value": 0.9,
        },
        {
            "question": "engineering.names-meaning",
            "priority": "advisory",
            "scope": "whole-diff",
            "revision": f"{review.base}..{review.head}",
            "value": 0.2,
        },
        {
            "question": "design.one-reason",
            "priority": "high",
            "scope": "whole-diff",
            "revision": f"{review.base}..{review.head}",
            "value": 2.0,
            "confidence": 0.8,
        },
    ], (
        "native values, their confidence, configuration order, and each judgment's provenance (D07, D44, D46)"
    )
    summary = capsys.readouterr().out
    assert "screened" in summary and str(review.output / "report.json") in summary


def test_S2_the_terminal_summary_reads_the_judgments_without_the_report(review, capsys):
    assert review.screen(RESPONSE) == 0
    summary = capsys.readouterr().out
    assert "contract.error-handling" in summary and "critical" in summary
    assert "0.9" in summary, "the summary reads the native value as it was answered"
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
                "answers": {
                    "contract.error-handling": {"type": "noul", "noul": "banana"},
                    "engineering.names-meaning": {"type": "noul", "noul": 0.2},
                }
            }
        )
        != 0
    ), "an invalid judgment must not exit 0"
    report = review.report()
    assert report["status"] == "incomplete"
    assert report["verdict"] is None
    assert "contract.error-handling" in report["failure"]["reason"]
    assert report["usage"]["requests"] == 1


def test_S1_a_score_between_two_rungs_completes_with_the_providers_own_number(review):
    assert (
        review.screen(
            {
                "answers": {
                    "design.one-reason": {"type": "score", "score": 1.22, "confidence": 0.8},
                    "contract.error-handling": {"type": "noul", "noul": 0.9},
                }
            }
        )
        == 0
    ), "1.22 sits on a four-level ladder (0..3) and is a screening answer, not a refusal"
    report = review.report()
    assert report["status"] == "completed"
    assert report["verdict"] == "screened"
    assert report["judgments"][1]["value"] == 1.22, (
        "the judgment carries the number as the provider answered it, never a rung it was snapped to"
    )


def test_S3_a_score_outside_its_declared_levels_is_incomplete_naming_the_question(review):
    assert (
        review.screen(
            {
                "answers": {
                    "design.one-reason": {"type": "score", "score": 4.0, "confidence": 0.9},
                    "contract.error-handling": {"type": "noul", "noul": 0.9},
                }
            }
        )
        != 0
    ), "4.0 is above the last rung of this question's four-level ladder, so it is refused"
    report = review.report()
    assert report["status"] == "incomplete"
    assert report["verdict"] is None, "a refused answer is not a screening result (D34, D40)"
    assert "design.one-reason" in report["failure"]["reason"], (
        "the refusal names the question the answer could not be attributed to"
    )


def test_S3_a_judgment_on_a_question_the_run_did_not_ask_is_incomplete(review):
    assert review.screen({"answers": {"contract.not-asked": {"type": "noul", "noul": 0.9}}}) != 0, (
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
        {"id": "contract.error-handling", "priority": "critical", "type": "noul"},
        {"id": "engineering.names-meaning", "priority": "advisory", "type": "noul"},
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


def test_S5_the_report_records_the_limits_in_force_and_whether_long_context_was_requested(review):
    assert review.screen(RESPONSE) == 0
    assert review.report()["limits"] == {
        "long_context": False,
        "per_question": 32000,
        "per_request": 64000,
    }, "the default budget is what was in force"
    assert review.screen(RESPONSE, long_context=True) == 0
    assert review.report()["limits"] == {
        "long_context": True,
        "per_question": 262000,
        "per_request": 262000,
    }, "the long-context budget is recorded when it was asked for"


def test_S4_the_report_names_the_provider_served_model_limits_and_bank_digest(review):
    assert review.screen(RESPONSE) == 0
    report = review.report()
    assert report["provider"] == f"fixture:{review.fixture}", (
        "the report names where the screening came from, so a canned one is never read as a provider's"
    )
    assert report["model"] == "test-model-v1", "the configured alias stays, as the caller named it"
    assert report["resolved_model"] == "jev-1.14.0", (
        "the model the provider served stands beside the alias, not in place of it"
    )
    assert report["limits"] == {"long_context": False, "per_question": 32000, "per_request": 64000}
    assert re.fullmatch(r"[0-9a-f]{64}", report["bank_digest"]), (
        "the report digests the bank it screened, so a frozen report is recognisable (D26)"
    )


def test_S4_the_bank_digest_follows_the_bank_and_not_the_run(review):
    assert review.screen(RESPONSE) == 0
    digest = review.report()["bank_digest"]
    assert review.screen(RESPONSE) == 0
    assert review.report()["bank_digest"] == digest, "the same bank digests the same, run after run"
    review.config.write_text(
        CONFIG.replace("the change keeps one reason to change", "the change keeps a reason"),
        encoding="utf-8",
    )
    assert review.screen(RESPONSE) == 0
    assert review.report()["bank_digest"] != digest, (
        "a level reworded is a different bank, and the digest says so"
    )


def test_S6_an_incomplete_run_keeps_the_usage_and_the_judgments_it_learned(review):
    assert (
        review.screen(
            {
                "model": "jev-1.14.0",
                "answers": {
                    "contract.error-handling": {"type": "noul", "noul": 0.9},
                    "engineering.names-meaning": {"type": "noul", "noul": 1.4},
                },
                "usage": {"input_tokens": 321, "output_tokens": 9},
            }
        )
        != 0
    ), "an answer the question does not allow is not a clean verdict"
    report = review.report()
    assert report["status"] == "incomplete"
    assert "engineering.names-meaning" in report["failure"]["reason"], (
        "the refusal still names the question it refused"
    )
    assert report["usage"]["input_tokens"] == 321, (
        "the usage the provider charged for is the only record of a screening that was paid for"
    )
    assert [judgment["question"] for judgment in report["judgments"]] == [
        "contract.error-handling"
    ], "the judgment learned before the refusal is kept, and the refused one is not"


def test_S6_a_refusal_names_the_report_it_leaves_standing(review, capsys):
    assert review.screen(RESPONSE) == 0
    capsys.readouterr()
    standing = review.report()
    review.config.write_text("questions: []\n", encoding="utf-8")
    assert review.screen(RESPONSE) == 2, "a configuration with no model is a refusal"
    err = capsys.readouterr().err
    assert str(review.output / "report.json") in err, (
        "the refusal names the report an earlier invocation left in --output"
    )
    assert standing["input"]["head"][:7] in err, "and the revision that report screened"
    assert review.report() == standing, "a refusal writes no report of its own"


def test_S6_an_output_that_is_not_a_directory_is_a_refusal_naming_the_path(review, capsys):
    blocked = review.output.parent / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    assert review.screen(RESPONSE, output=blocked) == 2, (
        "an output that cannot be written is a refusal, never a traceback"
    )
    err = capsys.readouterr().err
    assert str(blocked) in err, "the refusal names the path it could not write"
    assert review.head[:7] in err, "and the change it refused to screen"
    assert not (review.output / "report.json").exists(), "a refusal writes no report"


def test_S6_an_existing_output_that_denies_writing_is_refused_before_the_provider_is_asked(
    review, capsys, request
):
    review.output.mkdir()
    review.output.chmod(0o555)
    request.addfinalizer(lambda: review.output.chmod(0o755))
    assert review.screen(RESPONSE) == 2, (
        "a directory that exists and denies writing is a refusal, never a paid screening lost to a "
        "traceback"
    )
    err = capsys.readouterr().err
    assert str(review.output) in err, "the refusal names the path it could not write"
    assert review.head[:7] in err, "and the change it refused to screen"
    assert "Traceback" not in err
    assert not (review.output / "report.json").exists(), "a refusal writes no report"


def test_S6_a_report_that_cannot_be_written_is_a_refusal_that_keeps_what_was_screened(
    review, capsys, monkeypatch
):
    def deny(_report, _directory):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("agent_review.report.Report.write", deny)
    assert review.screen(RESPONSE) == 2, "a write that fails is a refusal, never a traceback"
    captured = capsys.readouterr()
    assert str(review.output) in captured.err, "the refusal names the path it could not write"
    assert review.head[:7] in captured.err, "and the change that was screened"
    assert "Traceback" not in captured.err
    assert "contract.error-handling" in captured.out, (
        "the screening was paid for, so the summary keeps what it learned when the report cannot be "
        "written"
    )
