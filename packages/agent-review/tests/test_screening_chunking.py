"""Chunking at the boundary: one request per chunk with the whole bank, and the coverage a report claims.

01-agent-review-smart-screening/S3, S4 (D41, D42, D43). These drive the `agent-review code` invocation over a
real two-revision repository, a configuration that sets the budgets, and a canned System One provider: the
report counts the requests it actually made, every changed region is carried by exactly one chunk, each
chunk is asked the whole bank, and a change wider than the cross-file limit records the coverage gap and
recommends a full deep review instead of reading as a clean screen. A screening runs through the fixture
provider: no check here reaches a real provider.
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
    chunk:
      max_diff_tokens: {max_diff_tokens}
    cross_file:
      max_files: {max_files}
    questions:
      - id: repo.error-handling
        priority: critical
        type: noul
        ask: Does the change keep the documented error-handling contract
      - id: repo.names-meaning
        priority: advisory
        type: noul
        ask: Do the new names say what they mean
    """
)

QUESTIONS = ("repo.error-handling", "repo.names-meaning")
BASE_FILES = {"alpha.py": "one\ntwo\n", "beta.py": "one\ntwo\n"}


def answer(value=0.9):
    """One canned answer to the whole bank, as the provider would answer one chunk."""
    return {
        "model": "jev-1.14.0",
        "answers": {
            "repo.error-handling": {"type": "noul", "noul": value},
            "repo.names-meaning": {"type": "noul", "noul": 0.1},
        },
        "usage": {"input_tokens": 300, "output_tokens": 20},
    }


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


class Review:
    """Two revisions that add a line to each of two files, a configuration, and a canned provider."""

    def __init__(self, tmp_path, *, max_diff_tokens=60, max_files=40):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.config.write_text(
            CONFIG.format(max_diff_tokens=max_diff_tokens, max_files=max_files), encoding="utf-8"
        )
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
        for name, text in BASE_FILES.items():
            (self.checkout / name).write_text(text, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "base")
        base = git(self.checkout, "rev-parse", "HEAD")
        for name, text in BASE_FILES.items():
            (self.checkout / name).write_text(f"{text}three\n", encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def screen(self, *responses):
        """One screening of the fixture, with `responses` as the provider's answers in order."""
        self.fixture.write_text(json.dumps({"responses": list(responses)}), encoding="utf-8")
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


@pytest.mark.spec("Deterministic chunking")
def test_S3_a_change_larger_than_the_budget_is_screened_once_per_chunk_with_the_whole_bank(
    review, capsys
):
    assert review.screen(answer(), answer()) == 0, capsys.readouterr().err
    report = review.report()
    chunks = report["chunks"]
    assert [chunk["files"] for chunk in chunks] == [["alpha.py"], ["beta.py"]], (
        "chunks are built in path order inside the budget, one file each here"
    )
    assert all(chunk["within_budget"] for chunk in chunks)
    assert report["usage"]["requests"] == len(chunks) == 2, (
        "one request per chunk, and the report counts the requests it made (D41)"
    )
    coverage = report["coverage"]
    assert coverage["changed_regions"] == coverage["covered"] == 2, (
        "every changed region is carried by exactly one chunk (D41)"
    )
    assert coverage["gap"] is None, "two files are inside the cross-file limit"
    scopes = {judgment["scope"] for judgment in report["judgments"]}
    assert len(scopes) == 2, "each chunk's judgments name the chunk they were made over (D44)"
    assert any("alpha.py" in scope for scope in scopes)
    assert any("beta.py" in scope for scope in scopes)
    assert {(judgment["question"], judgment["scope"]) for judgment in report["judgments"]} == {
        (question, scope) for question in QUESTIONS for scope in scopes
    }, "each chunk is screened with the whole bank, not a subset of it"
    assert report["verdict"] == "screened"


@pytest.mark.spec("Deterministic chunking")
def test_S3_a_missing_chunk_answer_is_incomplete_rather_than_a_short_request_count(review):
    assert review.screen(answer()) != 0, "a chunk the provider never answered is not a clean screen"
    report = review.report()
    assert report["status"] == "incomplete"
    assert report["verdict"] is None, "an incomplete execution has no verdict (D34, D40)"
    assert report["usage"]["requests"] == 2, "the request that had no answer was still made"
    assert [judgment["question"] for judgment in report["judgments"]] == list(QUESTIONS), (
        "what the first chunk answered stays in the report (D47)"
    )


@pytest.mark.spec("Cross-file coverage gap")
def test_S4_a_change_wider_than_the_cross_file_limit_records_the_gap(tmp_path, monkeypatch):
    where = Review(tmp_path, max_diff_tokens=200_000, max_files=1)
    monkeypatch.chdir(where.checkout)

    assert where.screen(answer()) == 0
    report = where.report()
    assert len(report["chunks"]) == 1, "the whole change travelled in one request"
    gap = report["coverage"]["gap"]
    assert gap is not None, (
        "a change wider than the cross-file limit reports its coverage gap (D43)"
    )
    assert gap["changed_files"] == 2 and gap["max_files"] == 1
    assert "cross_file.max_files" in gap["reason"]
    reasons = report["escalation"]["reasons"]
    assert [reason["rule"] for reason in reasons] == ["coverage-gap"]
    assert report["escalation"]["recommended"], (
        "a coverage gap recommends a full deep review instead of a clean screen (D43)"
    )
    assert report["verdict"] == "escalated"


@pytest.mark.spec("Cross-file coverage gap")
def test_S4_a_change_inside_the_cross_file_limit_records_no_gap(tmp_path, monkeypatch):
    where = Review(tmp_path, max_diff_tokens=200_000, max_files=2)
    monkeypatch.chdir(where.checkout)

    assert where.screen(answer()) == 0
    report = where.report()
    assert report["coverage"]["gap"] is None, "a change at the limit is not wider than it"
    assert report["escalation"]["reasons"] == []
    assert report["verdict"] == "screened"
