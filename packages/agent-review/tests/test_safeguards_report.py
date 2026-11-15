"""`report.json` when a safeguard decides the change (00c-agent-review-safeguards/S1, S8).

A hit completes the execution - `status: completed` and exit 0 - with the verdict `escalated`, no
judgments, and `usage.requests: 0`: the provider was never asked, which the run proves by holding no
credential and naming no fixture at all. The report carries one reason per rule and path, and the terminal
summary names the rule, the path it matched and what a person should do about it (SG-D2, SG-D3, D35, D39).

These run in process over a scratch repository: no subprocess and no provider, so the check is offline and
needs none of the history the rule was learned from.
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
        type: noul
        ask: Does the change keep the documented error-handling contract
    """
)

BASE_FILE = "def discard(folder):\n    return folder\n"

HEAD_FILE = "import shutil\n\n\ndef discard(folder):\n    shutil.rmtree(folder)\n"


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


class Review:
    """Two revisions of a real repository, a configuration, an output directory and no provider."""

    def __init__(self, tmp_path):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        """A repository whose head deletes a folder it computes, as the archive driver did."""
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
        driver = self.checkout / "driver.py"
        driver.write_text(BASE_FILE, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "base")
        base = git(self.checkout, "rev-parse", "HEAD")
        driver.write_text(HEAD_FILE, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def screen(self):
        """One invocation naming no fixture: a run that reached for a provider would stop incomplete."""
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
            ]
        )

    def report(self):
        """The report the invocation wrote, read as JSON."""
        path = self.output / "report.json"
        assert path.is_file(), f"no report at {path}: an invocation writes one either way"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def review(tmp_path, monkeypatch):
    """The scratch screening, with no credential and no base URL in the environment."""
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = Review(tmp_path)
    monkeypatch.chdir(where.checkout)
    return where


def test_S1_the_report_carries_one_reason_naming_the_rule_the_path_and_where_it_came_from(review):
    assert review.screen() == 0, "a safeguard completes the execution, so the exit is 0 (D35)"
    report = review.report()
    assert report["status"] == "completed"
    assert report["verdict"] == "escalated"
    assert report["judgments"] == [], "nothing was judged: no provider was asked"
    assert report["usage"]["requests"] == 0, "a safeguard is decided before any request (SG-D2)"
    assert report["escalation"]["reasons"] == [
        {
            "rule": "target-outside-root",
            "path": "driver.py",
            "pattern": "shutil.rmtree(folder)",
            "source": "diff",
        }
    ]


def test_S8_a_safeguard_writes_a_completed_escalated_report_and_names_the_way_out(review, capsys):
    assert review.screen() == 0
    report = review.report()
    assert report["status"] == "completed"
    assert report["verdict"] == "escalated"
    assert report["judgments"] == []
    summary = capsys.readouterr().out
    assert "target-outside-root" in summary, "the summary names the rule"
    assert "driver.py" in summary, "the summary names the path it matched"
    assert "is_relative_to" in summary, "the summary says what a person should do about it"
    assert str(review.output / "report.json") in summary, "the summary names the report it wrote"
