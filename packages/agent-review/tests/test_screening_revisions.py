"""`agent-review code` over two Git revisions, screened through the port.

00-agent-review-screening-cli/S1: a caller invokes `agent-review code` on two Git revisions, and the tool
evaluates the curated global question bank through the screening port. No check here reaches a real
provider: that is the plan's human evidence, which needs a credential and a charge.
"""

import json
import subprocess
import textwrap

import pytest
from agent_review import questions
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
    """
)

RESPONSE = {
    "judgments": [
        {"question": "contract.error-handling", "value": True},
        {"question": "engineering.names-meaning", "value": False},
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


class Revisions:
    """Two revisions of a real repository, a configuration, an output directory and a canned answer."""

    def __init__(self, tmp_path):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.fixture = tmp_path / "screening.json"
        self.fixture.write_text(json.dumps({"responses": [RESPONSE]}), encoding="utf-8")
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

    def args(self, **overrides):
        """The command line the plan fixes (P0-D1), with `overrides` replacing named options."""
        options = {
            "--base": self.base,
            "--head": self.head,
            "--output": str(self.output),
            "--config": str(self.config),
            "--screening-fixture": str(self.fixture),
        }
        options.update(overrides)
        argv = ["code"]
        for name, value in options.items():
            if value is not None:
                argv.extend([name, value])
        return argv


@pytest.fixture
def revisions(tmp_path, monkeypatch):
    checkout = Revisions(tmp_path)
    monkeypatch.chdir(checkout.checkout)
    return checkout


def answer(capsys):
    """The screening answer the invocation printed, read as JSON."""
    return json.loads(capsys.readouterr().out)


def test_S1_the_cli_screens_the_frozen_diff_between_two_revisions(revisions, capsys):
    assert main(revisions.args()) == 0
    screened = answer(capsys)
    assert screened["input"] == {"base": revisions.base, "head": revisions.head}
    assert [question["id"] for question in screened["questions"]] == [
        "contract.error-handling",
        "engineering.names-meaning",
    ], "the configured bank is what the run evaluates"
    assert {judgment["question"]: judgment["value"] for judgment in screened["judgments"]} == {
        "contract.error-handling": True,
        "engineering.names-meaning": False,
    }, "the native judgments the port returned are what the run reports"
    assert screened["usage"]["requests"] == 1, "the port is called once per screening (D40)"


def test_S1_the_packaged_baseline_bank_is_evaluated_without_a_configuration(revisions, capsys):
    revisions.fixture.write_text(json.dumps({"responses": [{"judgments": []}]}), encoding="utf-8")
    assert main(revisions.args(**{"--config": None})) == 0
    screened = answer(capsys)
    assert [question["id"] for question in screened["questions"]] == [
        question.id for question in questions.baseline().questions
    ], "an absent configuration evaluates the mandatory global baseline (D30)"
    assert screened["model"] == questions.baseline().model


def test_S1_a_configuration_that_asks_nothing_is_refused_before_screening(revisions, capsys):
    revisions.config.write_text("model: test-model-v1\nquestions: []\n", encoding="utf-8")
    assert main(revisions.args()) == 2, "a screening that asks nothing must not exit 0"
    refused = capsys.readouterr()
    assert "questions" in refused.err
    assert refused.out == "", "a refusal writes no screening answer"


def test_S1_an_unknown_revision_is_refused_naming_it(revisions, capsys):
    assert main(revisions.args(**{"--base": "deadbeef"})) == 2
    refused = capsys.readouterr()
    assert "deadbeef" in refused.err
    assert refused.out == "", "a refusal writes no screening answer"


def test_S1_a_screening_failure_exits_nonzero_naming_the_reason(revisions, capsys):
    revisions.fixture.write_text(
        json.dumps({"responses": [{"error": "provider answered 500"}]}), encoding="utf-8"
    )
    assert main(revisions.args()) != 0, "a screening failure never exits 0 (D35)"
    assert "500" in capsys.readouterr().err


def test_the_fixture_answer_is_the_only_thing_that_differs_between_runs(revisions, capsys):
    assert main(revisions.args()) == 0
    first = answer(capsys)
    assert main(revisions.args()) == 0
    assert answer(capsys) == first, "the same frozen input and configuration screen the same way"
