"""A change that only joins paths is decided by the safeguards and screened, never a crashed run.

00c-agent-review-safeguards/S2: a change that adds no destructive call carries no `target-outside-root`
reason, so the screening proceeds through the canned provider. A rule that raised while reading a
`commonpath(...)` test would decide nothing at all: the invocation exits nonzero with no verdict, which is
worse than a wrong one.

The run is the real one - two revisions of a scratch repository, the packaged question bank's configuration,
the canned provider and `report.json` - so the reading is exercised on the CLI's own path.
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

ANSWER = {
    "model": "jev-1.14.0",
    "answers": {"contract.error-handling": {"type": "noul", "noul": 0.9}},
    "usage": {"input_tokens": 40, "output_tokens": 5},
}

BASE_FILE = "def load(path):\n    return open(path).read()\n"

# The head adds a path join a `commonpath(...)` test would be written with, and deletes nothing.
HEAD_FILE = (
    "import os\n"
    "\n"
    "\n"
    "def load(path):\n"
    "    return open(path).read()\n"
    "\n"
    "\n"
    "def join(a, b):\n"
    "    return os.path.commonpath([str(a), str(b)])\n"
)


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


@pytest.fixture
def review(tmp_path, monkeypatch):
    """A scratch repository whose head adds a path join, a canned provider, and no credential in reach."""
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
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
    git(checkout, "init", "-b", "main")
    join = checkout / "paths.py"
    join.write_text(BASE_FILE, encoding="utf-8")
    git(checkout, "add", "--all")
    git(checkout, *commit, "base")
    base = git(checkout, "rev-parse", "HEAD")
    join.write_text(HEAD_FILE, encoding="utf-8")
    git(checkout, "add", "--all")
    git(checkout, *commit, "head")
    head = git(checkout, "rev-parse", "HEAD")
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    answers = tmp_path / "screening.json"
    answers.write_text(json.dumps({"responses": [ANSWER]}), encoding="utf-8")
    monkeypatch.chdir(checkout)
    return base, head, config, answers, tmp_path / "review"


def test_S2_a_change_that_joins_paths_is_screened_rather_than_crashing(review, capsys):
    base, head, config, answers, output = review
    code = main(
        [
            "code",
            "--base",
            base,
            "--head",
            head,
            "--output",
            str(output),
            "--config",
            str(config),
            "--screening-fixture",
            str(answers),
        ]
    )
    assert code == 0, capsys.readouterr().err
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "completed"
    assert report["verdict"] == "screened", (
        "the change adds no destructive call, so nothing escalates"
    )
    assert report["escalation"]["reasons"] == []
    assert report["usage"]["requests"] == 1, "the screening went through the canned provider"
