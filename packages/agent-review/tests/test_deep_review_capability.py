"""The read/search-only answer a review's harness gets (02-agent-review-deep-review/S3).

A deep review is read-only by contract: it may read the source, its references and its tests, and it may
not modify a file, run a command or run a test (D18). A prompt saying so is not a control, so the answer
comes from the runtime that owns the harness families: each family either answers with the arguments its
own CLI enforces, or with a refusal naming what it cannot enforce (D38, P2-D2). The review resolves that
answer before it screens anything, so a harness that cannot hold its reviewer is refused and never spawned
unrestricted (S3).
"""

import sys

import pytest
from agent_review import readonly
from agent_review.cli import main
from agent_runtime import profiles

pytestmark = pytest.mark.unit


def test_S3_every_family_the_runtime_knows_answers_with_controls_or_a_named_refusal():
    """The capability answer is total: the module iterates the families the runtime knows, never a list here."""
    enforcing = readonly.enforcing()
    assert enforcing, "no family could be held to read/search-only, so no deep review could run"
    for family in profiles.BUILTINS:
        try:
            controls = readonly.controls(family)
        except readonly.ReadOnlyError as refusal:
            reason = str(refusal)
            assert family in reason, "the refusal names the harness it refused"
            assert "read" in reason, "the refusal names the capability it cannot enforce"
            assert any(name in reason for name in enforcing), (
                "the refusal names a family that can enforce it, so the way out is readable"
            )
        else:
            assert family in enforcing
            assert controls, "a family that can enforce it answers with the arguments that hold it"


def test_S3_an_unknown_harness_is_refused_naming_the_capability_and_a_family_that_enforces_it():
    with pytest.raises(readonly.ReadOnlyError) as refused:
        readonly.controls("zsh")
    reason = str(refused.value)
    assert "zsh" in reason, "the refusal names the harness it refused"
    assert "read" in reason, "the refusal names the capability it cannot enforce"
    assert "pi" in reason, "the refusal names a family that can enforce it"


def test_S3_a_harness_that_cannot_enforce_read_only_is_refused_before_anything_runs(
    tmp_path, capsys
):
    """No revision is resolved and no report is written: the refusal comes before the run, not inside it."""
    output = tmp_path / "review"
    status = main(
        [
            "code",
            "--base",
            "no-such-revision",
            "--head",
            "no-such-revision",
            "--output",
            str(output),
            "--deep-review",
            "zsh",
        ]
    )
    assert status == 2, "a refused harness is a refusal, not an incomplete review"
    stderr = capsys.readouterr().err
    assert "zsh" in stderr
    assert "read" in stderr
    assert "pi" in stderr, "the way out is another harness, named"
    assert not (output / "report.json").is_file(), (
        "a refusal writes no report to be read as this run's"
    )


def test_S3_a_review_the_runtime_cannot_answer_for_is_refused_rather_than_crashing(
    tmp_path, capsys, monkeypatch
):
    """The answer has one home in the runtime: an environment without it holds no family, so nothing spawns."""
    monkeypatch.setitem(sys.modules, "agent_runtime.read_only", None)
    output = tmp_path / "review"
    status = main(
        [
            "code",
            "--base",
            "no-such-revision",
            "--head",
            "no-such-revision",
            "--output",
            str(output),
            "--deep-review",
            "pi",
        ]
    )
    assert status == 2, "a review nothing can hold to reading is a refusal, not a crash"
    stderr = capsys.readouterr().err
    assert "agent-runtime" in stderr, "the refusal names the install that would answer it"
    assert not (output / "report.json").is_file(), (
        "a refusal writes no report to be read as this run's"
    )


def test_S3_a_canned_reviewer_that_no_harness_asks_is_refused_rather_than_ignored(tmp_path, capsys):
    """A fixture the run would never consult is an input nothing reads, so the invocation refuses it."""
    status = main(
        [
            "code",
            "--base",
            "no-such-revision",
            "--head",
            "no-such-revision",
            "--output",
            str(tmp_path / "review"),
            "--deep-review-fixture",
            str(tmp_path),
        ]
    )
    assert status == 2
    assert "deep-review-fixture" in capsys.readouterr().err


@pytest.mark.parametrize(
    "family", [name for name in profiles.BUILTINS if name not in readonly.enforcing()]
)
def test_S3_every_family_that_cannot_be_held_is_refused_before_a_reviewer_is_spawned(
    family, tmp_path, capsys
):
    """A family the runtime knows but cannot hold is refused by name, with a family that can as the way out."""
    output = tmp_path / "review"
    status = main(
        [
            "code",
            "--base",
            "no-such-revision",
            "--head",
            "no-such-revision",
            "--output",
            str(output),
            "--deep-review",
            family,
        ]
    )
    assert status == 2, "a family that cannot hold its reviewer screens nothing"
    stderr = capsys.readouterr().err
    assert family in stderr, "the refusal names the family it refused"
    assert readonly.cannot(family), (
        f"the runtime knows {family} and says nothing about what it cannot enforce"
    )
    assert readonly.cannot(family) in stderr, (
        "the refusal names what the family cannot enforce, in the runtime's own words"
    )
    assert any(name in stderr for name in readonly.enforcing()), (
        "the refusal names a family that can enforce it"
    )
    assert not (output / "report.json").is_file(), (
        "a refusal writes no report to be read as this run's"
    )
