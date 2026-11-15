"""Acceptance for the deterministic safeguards (00c-agent-review-safeguards/S1, S2, S8).

The runner places this file at `packages/agent-review/acceptance/safeguards/`. It drives the `agent-review`
console script as a subprocess over this repository's own history, so the case each rule was learned from is
the real change rather than a restatement of it (SG-D1, SG-D5):

  - `429625b` added `shutil.rmtree(folder)` on a folder the change computes and confined nothing, so the run
    is `escalated` with one `target-outside-root` reason naming the driver, no judgments and no request
  - `109c98b` added no destructive call at all, so no reason appears and the screening goes through the
    canned provider
  - a safeguard hit writes `report.json` with `status: completed` and `verdict: escalated`, exits 0, and the
    summary names the rule, the path and what a person should do about it

The two historical runs pin those commits, so the check needs a checkout with that history: a shallow clone
fails here, loudly, instead of skipping the case it cannot reach.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

DEFECT = "429625b"  # the archive driver deletes a plan folder nothing confined (S1)
FIX = "109c98b"  # the same driver deletes only directly under plans.dir, and adds no delete (S2)
DRIVER = "packages/agent-orchestrator/src/agent_orchestrator/driver.py"

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

# The provider's only answer: an error, so a run that asked it could never pass as escalated (S1).
NOT_ASKED = {"error": "the safeguard must decide this change without a request"}

ANSWERED = {
    "model": "jev-1.14.0",
    "answers": {"contract.error-handling": {"type": "noul", "noul": 0.9}},
    "usage": {"input_tokens": 900, "output_tokens": 20},
}


def repository():
    """The checkout this file lives in, found upwards; a build that cannot find it fails loudly."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() and (parent / "packages").is_dir():
            return parent
    raise AssertionError(
        f"no repository above {here}: the safeguards' historical cases need this checkout's history"
    )


def git(where, *args):
    """`git <args>` in `where`, its stdout stripped; a failing git is the fixture's failure, not the tool's."""
    result = subprocess.run(
        ["git", *args], cwd=where, capture_output=True, text=True, timeout=120, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr[-2000:]}")
    return result.stdout.strip()


def added_lines(repo, commit):
    """The lines a commit adds, as its diff spells them."""
    shown = git(repo, "show", "--format=", "--unified=0", commit)
    return [
        line[1:]
        for line in shown.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]


@pytest.fixture
def screening(tmp_path):
    """One invocation of the tool over a pair of revisions, and the report it wrote."""
    repo = repository()
    for commit in (DEFECT, FIX):
        if not git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"):
            raise AssertionError(
                f"this checkout does not have {commit}: the safeguards' historical cases need the full "
                "history, so fetch it (a shallow clone fails here rather than skipping the case)"
            )
    output = tmp_path / "review"
    config = tmp_path / "config.yaml"
    config.write_text(CONFIG, encoding="utf-8")
    fixture = tmp_path / "screening.json"

    def run(base, head, response):
        fixture.write_text(json.dumps({"responses": [response]}), encoding="utf-8")
        binary = shutil.which("agent-review")
        if binary is None:
            raise AssertionError(
                "no `agent-review` on PATH: run the check under `uv run`, which installs the workspace member"
            )
        result = subprocess.run(
            [
                binary,
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
                str(fixture),
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        path = output / "report.json"
        assert path.is_file(), (
            f"no report at {path}: an invocation writes one either way\n{result.stderr}"
        )
        return result, json.loads(path.read_text(encoding="utf-8"))

    return run


def escalated(screening):
    """The defect commit screened: the run, its report and its summary, decided once for the two scenarios."""
    return screening(f"{DEFECT}^", DEFECT, NOT_ASKED)


def test_control_the_two_historical_commits_carry_the_shapes_the_rules_are_learned_from():
    repo = repository()
    assert DRIVER in git(repo, "show", "--name-only", "--pretty=format:", DEFECT)
    assert any("shutil.rmtree(" in line for line in added_lines(repo, DEFECT)), (
        "the defect adds the delete the rule is learned from"
    )
    assert [line for line in added_lines(repo, FIX) if "rmtree(" in line] == [], (
        "the fix adds no delete of its own: it refuses the folder it may not delete"
    )


def test_S1_the_defect_escalates_as_target_outside_root_with_no_request_spent(screening):
    result, report = escalated(screening)
    assert result.returncode == 0, result.stderr[-2000:]
    assert report["status"] == "completed"
    assert report["verdict"] == "escalated", (
        "a safeguard decides the change before the model is asked"
    )
    assert report["judgments"] == [], "nothing was judged: the provider was never asked"
    assert report["usage"]["requests"] == 0, "the provider was never asked (SG-D2)"
    reasons = report["escalation"]["reasons"]
    assert [reason["rule"] for reason in reasons] == ["target-outside-root"] * len(reasons)
    assert [reason["path"] for reason in reasons if reason["path"] == DRIVER] == [DRIVER], (
        "exactly one reason names the rule and the changed path the delete it is learned from is in"
    )
    assert len({(reason["rule"], reason["path"]) for reason in reasons}) == len(reasons), (
        "one reason per rule and path, however many calls that path adds"
    )


def test_S2_the_fix_adds_no_destructive_call_and_is_screened_through_the_provider(screening):
    result, report = screening(f"{FIX}^", FIX, ANSWERED)
    assert result.returncode == 0, result.stderr[-2000:]
    assert report["status"] == "completed"
    assert report["verdict"] == "screened", "no safeguard fires on the change that fixed the defect"
    assert [
        reason
        for reason in report["escalation"]["reasons"]
        if reason["rule"] == "target-outside-root"
    ] == []
    assert report["usage"]["requests"] == 1, "the screening went through the canned provider"
    assert [judgment["question"] for judgment in report["judgments"]] == ["contract.error-handling"]


def test_S8_a_safeguard_writes_a_completed_escalated_report_and_names_the_way_out(screening):
    result, report = escalated(screening)
    assert result.returncode == 0, "an execution that completed exits 0, whatever the verdict (D35)"
    assert report["status"] == "completed"
    assert report["verdict"] == "escalated"
    assert report["judgments"] == []
    summary = result.stdout
    assert "target-outside-root" in summary, "the summary names the rule"
    assert DRIVER in summary, "the summary names the path it matched"
    assert "is_relative_to" in summary, "the summary says what a person should do about it"
