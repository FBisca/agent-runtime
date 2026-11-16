"""The one reviewer a sensitive path spawns, over the whole change (02-agent-review-deep-review/S2).

A sensitive path is about the change itself, so the reviewer reads the whole change rather than the flagged
areas, and the instruction sets whose paths the change matched join the ones that always do (D12, D14,
P2-D3, P2-D5). The rule that decides the scope from the reasons alone is checked in `test_deep_review_rules`.
"""

import json
import subprocess

import pytest
from agent_review.cli import main
from agent_review.deep_review import FULL

pytestmark = pytest.mark.integration

QUESTION = "repo.migration-safety"
MIGRATION = "db/migrations/002_index.sql"

CONFIG = """\
model: jev-latest
sensitive_paths: ["db/migrations/**"]
thresholds:
  {question}:
    value: 1.5
    confidence: 0.6
deep_review:
  timeout_seconds: 600
  instructions:
    - id: evidence-first
      text: Cite the changed line for every finding you report.
    - id: migration-safety
      text: Read the migration beside the schema it changes.
      applies:
        paths: ["db/migrations/**"]
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


class Scope:
    """A repository whose head adds a migration under a sensitive path, with a canned reviewer."""

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
        added = self.checkout / MIGRATION
        added.parent.mkdir(parents=True, exist_ok=True)
        added.write_text("create index items_id on items (id);\n", encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *COMMIT, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def write_screening(self, value=1.0, confidence=0.9):
        """An answer that fires no threshold: the sensitive path alone decides this review."""
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
    where = Scope(tmp_path)
    monkeypatch.chdir(where.checkout)
    return where


def test_S2_a_sensitive_path_is_read_whole_and_its_path_instruction_joins(review):
    """A focused review would read the flags; a full one reads the change, and its path sets join."""
    review.write_screening()
    assert review.run() == 0
    report = review.report()
    deep = report["deep_review"]
    assert [reason["rule"] for reason in report["escalation"]["reasons"]] == ["protected-path"]
    assert deep["scope"] == FULL, "a sensitive path is read whole, not where a screen pointed"
    assert deep["instructions"] == ["evidence-first", "migration-safety"], (
        "the set whose paths the change matched joins; the one matching nothing does not"
    )
    assert deep["spawns"] == 1
    assert len(review.spawns()) == 1
    assert review.spawns()[0]["scope"] == FULL
