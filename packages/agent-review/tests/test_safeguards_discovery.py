"""A repository's `.agent-review.yaml`, discovered without a flag, extends the mandatory baseline.

00c-agent-review-safeguards/S4, S5, S8, S9 (SG-D2, SG-D3, SG-D4, D30): the change is run through the CLI as
a person runs it - two revisions of a scratch repository, a configuration, no credential in reach - so the
discovery, the escalation the report carries, and the refusal that writes no report are exercised on the
tool's own path rather than on a helper's.

A hit names the rule, the path it matched and the pattern behind it; the report is `completed` with the
verdict `escalated`, no judgments and `usage.requests: 0`; the summary says what a person must do. A malformed
rule configuration is refused before a revision is resolved, so a change is never read for it.
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

# The repository's own list, discovered at the checkout's root: it adds to the baseline and replaces nothing.
DISCOVERED = CONFIG + "sensitive_paths:\n  - pkg/**\n"

ANSWER = {
    "model": "jev-1.14.0",
    "answers": {"contract.error-handling": {"type": "noul", "noul": 0.9}},
    "usage": {"input_tokens": 40, "output_tokens": 5},
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
    """Two revisions of a scratch repository, an output directory and the configuration handed to it."""

    def __init__(self, tmp_path):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.answers = tmp_path / "screening.json"
        self.base = self.head = ""
        self.before = ""
        self.after = ""

    def revisions(self, before, after, extra=None):
        """`before` as the base commit and `after` as the head, with any other files `extra` writes."""
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
        self.before, self.after = before, after
        for name, text in before.items():
            self.write(name, text)
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "base")
        self.base = git(self.checkout, "rev-parse", "HEAD")
        for name, text in after.items():
            self.write(name, text)
        if extra is not None:
            extra(self)
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "head")
        self.head = git(self.checkout, "rev-parse", "HEAD")
        return self

    def write(self, name, text):
        path = self.checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def screen(self, *, config=True, canned=False):
        """One invocation naming no provider unless `canned`: a run that asked would stop incomplete."""
        argv = [
            "code",
            "--base",
            self.base,
            "--head",
            self.head,
            "--output",
            str(self.output),
        ]
        if config:
            argv += ["--config", str(self.config)]
        if canned:
            argv += ["--screening-fixture", str(self.answers)]
        return main(argv)

    def report(self):
        path = self.output / "report.json"
        assert path.is_file(), f"no report at {path}: an invocation writes one either way"
        return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def review(tmp_path, monkeypatch):
    """A scratch checkout to build in, with no credential and no base URL in the environment."""
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = Review(tmp_path)
    where.config.write_text(CONFIG, encoding="utf-8")
    where.answers.write_text(json.dumps({"responses": [ANSWER]}), encoding="utf-8")
    monkeypatch.chdir(where.checkout)
    return where


def test_S5_a_discovered_configuration_extends_the_baseline_without_a_flag(review):
    review.revisions(
        {
            "pkg/service.py": "def load(path):\n    return path\n",
            "pyproject.toml": '[project]\nname = "scratch"\n',
        },
        {
            "pkg/service.py": "def load(path):\n    return path or '/'\n",
            "pyproject.toml": '[project]\nname = "scratch"\nversion = "1"\n',
            ".agent-review.yaml": DISCOVERED,
        },
    )
    assert review.screen(config=False) == 0, "a safeguard completes the execution"
    report = review.report()
    assert report["verdict"] == "escalated"
    assert report["usage"]["requests"] == 0, (
        "the repository config was discovered before a provider was built"
    )
    matched = {reason["path"]: reason for reason in report["escalation"]["reasons"]}
    assert sorted(matched) == ["pkg/service.py", "pyproject.toml"], (
        "the repository's list extends the mandatory baseline"
    )
    assert (matched["pkg/service.py"]["pattern"], matched["pkg/service.py"]["source"]) == (
        "pkg/**",
        "repository",
    )
    assert (matched["pyproject.toml"]["pattern"], matched["pyproject.toml"]["source"]) == (
        "pyproject.toml",
        "baseline",
    )


def test_S4_a_change_that_matches_no_pattern_is_screened(review):
    review.revisions(
        {"pkg/service.py": "def load(path):\n    return path\n"},
        {
            "pkg/service.py": "def load(path):\n    return path or '/'\n",
            ".github/ISSUE_TEMPLATE.md": "template\n",
        },
    )
    assert review.screen(config=False, canned=True) == 0
    report = review.report()
    assert report["escalation"]["reasons"] == [], (
        "a near miss under a matching directory matches nothing"
    )
    assert report["verdict"] == "screened"
    assert report["usage"]["requests"] == 1, "the screening went through the canned provider"


def test_S8_a_hit_writes_completed_escalated_and_names_what_a_person_should_do(review, capsys):
    review.revisions(
        {"server/agent_runtime.py": "import json\n"},
        {"server/extra.py": "import json\n"},
    )
    assert review.screen() == 0, "a hit completes the execution, so the exit is 0 (D35)"
    report = review.report()
    assert report["status"] == "completed"
    assert report["verdict"] == "escalated"
    assert report["judgments"] == [], "nothing was asked, so nothing was judged"
    assert report["usage"]["requests"] == 0
    assert report["input"] == {"base": review.base, "head": review.head}
    said = capsys.readouterr().out.lower()
    assert "protected-path" in said, "the summary names the rule"
    assert "server/extra.py" in said, "the summary names the path it matched"
    assert "deep review" in said, "a refusal names the way out"


def test_S9_a_malformed_rule_configuration_is_refused_before_any_change_is_read(
    review, tmp_path, capsys
):
    review.config.write_text(CONFIG + 'sensitive_paths: "hooks/**"\n', encoding="utf-8")
    code = main(
        [
            "code",
            "--base",
            "no-such-revision",
            "--head",
            "no-such-revision-either",
            "--output",
            str(review.output),
            "--config",
            str(review.config),
        ]
    )
    assert code == 2, "an invalid configuration is a refusal"
    assert "sensitive_paths" in capsys.readouterr().err, "the refusal names the field by its path"
    assert not (review.output / "report.json").exists(), "a refusal writes no report"


def test_S9_a_malformed_stdlib_only_declaration_is_refused_by_field_path(review, capsys):
    review.config.write_text(CONFIG + "safeguards:\n  stdlib_only: hooks/\n", encoding="utf-8")
    code = main(
        [
            "code",
            "--base",
            "no-such-revision",
            "--head",
            "no-such-revision-either",
            "--output",
            str(review.output),
            "--config",
            str(review.config),
        ]
    )
    assert code == 2
    assert "safeguards.stdlib_only" in capsys.readouterr().err
    assert not (review.output / "report.json").exists(), "a refusal writes no report"
