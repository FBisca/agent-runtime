"""The one reviewer an escalation spawns, and what it is asked to investigate (02-agent-review-deep-review/S1).

A calibrated signal - a value past its threshold, or a confidence below its band - is investigated where it
points: one reviewer is spawned, its scope is focused, and the instruction sets that match the fired rules
join the ones that always do (D12, D14, P2-D3, P2-D5). The report says what the review asked for, and the
canned reviewer records the same request, so a spawn is checked without reaching a model (P2-D1).
"""

import json
import subprocess

import pytest
from agent_review.cli import main

pytestmark = pytest.mark.integration

QUESTION = "repo.migration-safety"

CONFIG = """\
model: jev-latest
thresholds:
  {question}:
    value: 1.5
    confidence: 0.6
deep_review:
  timeout_seconds: 600
  instructions:
    - id: evidence-first
      text: Cite the changed line for every finding you report.
    - id: contract-signal
      text: Read the area of a question whose calibrated number fired, whole.
      applies:
        rules: [value-above-threshold, confidence-below-band]
    - id: payments
      text: Read the payment service with the change in hand.
      applies:
        paths: ["services/payments/**"]
questions:
  - id: {question}
    priority: critical
    type: score
    criteria: [none, minor, moderate, major, severe]
    ask: How severely does the change violate the migration contract
"""

BASE_FILES = {
    "store.py": "def load(path):\n    return open(path).read()\n",
    "docs/guide.md": "guide\n",
}

HEAD_EDIT = "def load(path):\n    with open(path) as stream:\n        return stream.read()\n"

COMMIT = (
    "-c",
    "user.name=agent-review",
    "-c",
    "user.email=agent-review@example.invalid",
    "-c",
    "commit.gpgsign=false",
    "commit",
    "-m",
)


def git(where, *args):
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr[-2000:]}"
    return result.stdout.strip()


class Spawn:
    """A repository whose head edit is small, a calibrated critical question, and a canned reviewer."""

    def __init__(self, tmp_path):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.screening = tmp_path / "screening.json"
        self.reviewer = tmp_path / "reviewer"
        self.reviewer.mkdir()
        self.config.write_text(CONFIG.format(question=QUESTION), encoding="utf-8")
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        git(self.checkout, "init", "-b", "main")
        for name, text in BASE_FILES.items():
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *COMMIT, "base")
        base = git(self.checkout, "rev-parse", "HEAD")
        (self.checkout / "store.py").write_text(HEAD_EDIT, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *COMMIT, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def write_screening(self, value=2.5, confidence=0.3):
        """The one canned answer the whole bank gets: a score above its threshold, with a low confidence."""
        self.screening.write_text(
            json.dumps(
                {
                    "responses": [
                        {
                            "model": "jev-1.14.0",
                            "answers": {
                                QUESTION: {
                                    "type": "score",
                                    "score": value,
                                    "confidence": confidence,
                                }
                            },
                            "usage": {"input_tokens": 900, "output_tokens": 20},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return self.screening

    def run(self, harness="pi"):
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
                str(self.screening),
                "--deep-review",
                harness,
                "--deep-review-fixture",
                str(self.reviewer),
            ]
        )

    def report(self):
        path = self.output / "report.json"
        assert path.is_file(), f"no report at {path}: an invocation writes one either way"
        return json.loads(path.read_text(encoding="utf-8"))

    def spawns(self):
        path = self.reviewer / "spawns.json"
        assert path.is_file(), f"the canned reviewer recorded no spawn at {path}"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def review(tmp_path, monkeypatch):
    where = Spawn(tmp_path)
    monkeypatch.chdir(where.checkout)
    return where


def test_S1_one_reviewer_is_spawned_focused_on_the_signals_with_the_matching_instructions(
    review, capsys
):
    review.write_screening()
    assert review.run() == 0, capsys.readouterr().err
    deep = review.report()["deep_review"]
    assert deep["spawns"] == 1, "one escalation is one spawned reviewer (D19, P2-D3)"
    assert deep["scope"] == "focused", "a calibrated signal is investigated where it points"
    assert deep["capabilities"] == ["read", "search"], (
        "the reviewer reads and searches, nothing else"
    )
    assert deep["instructions"] == ["evidence-first", "contract-signal"], (
        "the set that always joins and the set matching a fired rule; the path set matched no change"
    )
    assert [signal["rule"] for signal in deep["signals"]] == [
        "value-above-threshold",
        "confidence-below-band",
    ], "the flagged areas the reviewer investigates are the signals that fired"
    assert [signal["question"] for signal in deep["signals"]] == [QUESTION, QUESTION]
    spawned = review.spawns()
    assert len(spawned) == 1, "the reviewer was spawned exactly once"
    assert spawned[0]["harness"] == "pi"
    assert spawned[0]["scope"] == deep["scope"]
    assert [entry["id"] for entry in spawned[0]["instructions"]] == deep["instructions"], (
        "the reviewer is asked for what the report says it was asked for"
    )
    assert "deep review: focused" in capsys.readouterr().out, (
        "a person reads that a reviewer was spawned and what scope it was given"
    )
