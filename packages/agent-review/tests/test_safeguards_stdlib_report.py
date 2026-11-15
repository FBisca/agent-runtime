"""A declared standard-library-only member decided before the provider is asked (00c-agent-review-safeguards/S6, S7).

A member the configuration declares standard-library-only adds an import outside it: the invocation completes
with the verdict `escalated`, one `stdlib-only` reason naming the member's path and the imported module, and
`usage.requests: 0` because the provider was never asked (SG-D2, SG-D3). A standard-library import, or a
third-party one in a member no declaration covers, leaves no `stdlib-only` reason and the screening proceeds
through the canned provider.

These run in process over a scratch repository with a canned provider: the repository and the fixture are the
real boundaries, and no credential and no network are involved.
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
      - id: repo.error-handling
        priority: critical
        type: noul
        ask: Does the change keep the documented error-handling contract
    safeguards:
      stdlib_only:
        - plugin/
    """
)

RESPONSE = {
    "model": "jev-1.14.0",
    "answers": {"repo.error-handling": {"type": "noul", "noul": 0.9}},
    "usage": {"input_tokens": 900, "output_tokens": 20},
}

BASE_FILE = "import json\n\n\ndef send(payload):\n    return json.dumps(payload)\n"

STDLIB_FILE = "import json\nimport os\n\n\ndef send(payload):\n    return json.dumps(payload)\n"

THIRD_PARTY_FILE = (
    "import json\nimport requests\n\n\ndef send(payload):\n    return json.dumps(payload)\n"
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
    """Two revisions of a real repository changing `path`, a configuration, a fixture and no credential."""

    def __init__(self, tmp_path, path="plugin/tool.py"):
        self.checkout = tmp_path / "checkout"
        self.checkout.mkdir()
        self.output = tmp_path / "review"
        self.config = tmp_path / "config.yaml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.fixture = tmp_path / "screening.json"
        self.fixture.write_text(json.dumps({"responses": [RESPONSE]}), encoding="utf-8")
        self.path = path
        self.base, self.head = self.two_revisions()

    def two_revisions(self):
        """A repository whose head changes one file under the declared member; (base sha, head sha)."""
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
        target = self.checkout / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BASE_FILE, encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "base")
        base = git(self.checkout, "rev-parse", "HEAD")
        target.write_text(self.head_file(), encoding="utf-8")
        git(self.checkout, "add", "--all")
        git(self.checkout, *commit, "head")
        return base, git(self.checkout, "rev-parse", "HEAD")

    def head_file(self) -> str:
        """What the head revision writes at the changed path; a third-party import unless it says otherwise."""
        return THIRD_PARTY_FILE

    def screen(self):
        """One invocation with the canned provider named, so a screening that proceeds can complete."""
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


class StdlibOnlyReview(Review):
    """The head adds a standard-library import to a declared member."""

    def head_file(self) -> str:
        return STDLIB_FILE


class CommentedImportReview(Review):
    """The head adds a package to a declared member on a line a note and a noqa sit on."""

    def head_file(self) -> str:
        return (
            "import json\n\nimport requests  # HTTP client, behind the noqa: E402 it needs\n\n\n"
            "def send(payload):\n    return json.dumps(payload)\n"
        )


class TwoPackagesReview(Review):
    """The head adds two packages to a declared member, one as a from-import and one inside a function."""

    def head_file(self) -> str:
        return (
            "import json\n\nfrom yaml import safe_load\n\n\ndef load(path):\n"
            "    import dotenv\n\n    return safe_load(path)\n"
        )


@pytest.fixture
def review(tmp_path, monkeypatch):
    """The scratch screening, with no credential and no base URL in the environment."""
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = Review(tmp_path)
    monkeypatch.chdir(where.checkout)
    return where


def test_S6_a_declared_member_that_imports_outside_the_standard_library_escalates_without_a_request(
    review,
):
    assert review.screen() == 0, "a safeguard completes the execution, so the exit is 0 (D35)"
    report = review.report()
    assert report["status"] == "completed"
    assert report["verdict"] == "escalated"
    assert report["judgments"] == [], "nothing was judged: the provider was never asked"
    assert report["usage"]["requests"] == 0, "a stdlib-only hit spends no request (SG-D2)"
    assert report["escalation"]["reasons"] == [
        {
            "rule": "stdlib-only",
            "path": "plugin/tool.py",
            "pattern": "import requests",
            "source": "diff",
            "module": "requests",
        }
    ]


def test_S6_the_summary_names_the_member_the_module_and_the_way_out(review, capsys):
    assert review.screen() == 0
    summary = capsys.readouterr().out
    assert "stdlib-only" in summary, "the summary names the rule"
    assert "plugin/tool.py" in summary, "the summary names the member's path"
    assert "requests" in summary, "the summary names the module a person must act on"
    assert "standard library" in summary, "the summary says what a person should do about it"


def test_S6_a_commented_import_line_escalates_without_a_request(tmp_path, monkeypatch):
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = CommentedImportReview(tmp_path)
    monkeypatch.chdir(where.checkout)
    assert where.screen() == 0
    report = where.report()
    assert report["verdict"] == "escalated", (
        "a trailing comment cannot hide the module the member added"
    )
    assert report["usage"]["requests"] == 0, "no request is spent on a hit"
    assert [reason["module"] for reason in report["escalation"]["reasons"]] == ["requests"]
    assert report["escalation"]["reasons"][0]["path"] == "plugin/tool.py"


def test_S6_each_package_the_member_adds_is_its_own_reason_in_the_report(tmp_path, monkeypatch):
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = TwoPackagesReview(tmp_path)
    monkeypatch.chdir(where.checkout)
    assert where.screen() == 0
    report = where.report()
    assert report["verdict"] == "escalated"
    assert report["usage"]["requests"] == 0
    reasons = report["escalation"]["reasons"]
    assert [reason["module"] for reason in reasons] == ["yaml", "dotenv"], (
        "a from-import and a function-local import are imports too, and each names its module"
    )
    assert {reason["path"] for reason in reasons} == {"plugin/tool.py"}
    assert {reason["rule"] for reason in reasons} == {"stdlib-only"}


def test_S7_a_declared_member_that_adds_a_standard_library_import_is_screened(
    tmp_path, monkeypatch
):
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = StdlibOnlyReview(tmp_path)
    monkeypatch.chdir(where.checkout)
    assert where.screen() == 0
    report = where.report()
    assert report["verdict"] == "screened", "a standard-library import is not a dependency"
    assert report["escalation"]["reasons"] == []
    assert report["usage"]["requests"] == 1, "the screening proceeded through the canned provider"
    assert report["judgments"], "the provider's answer is in the report"


def test_S7_an_undeclared_member_that_adds_a_third_party_import_is_screened(tmp_path, monkeypatch):
    for variable in ("TYPESAFE_API_KEY", "AGENT_REVIEW_API_KEY", "AGENT_REVIEW_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    where = Review(tmp_path, path="lib/tool.py")
    monkeypatch.chdir(where.checkout)
    assert where.screen() == 0
    report = where.report()
    assert report["verdict"] == "screened", (
        "a member no declaration covers may import what it likes"
    )
    assert report["escalation"]["reasons"] == []
    assert report["usage"]["requests"] == 1
