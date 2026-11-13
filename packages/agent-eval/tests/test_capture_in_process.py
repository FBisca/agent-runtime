"""The capture CLI's pure logic (refusals, the help) runs in process; one subprocess test per boundary
still shows the refusal crossing the CLI (agent-eval/S13). A test deleted by a ticket keeps its name
in the file that replaces it (agent-eval/S6)."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from claude_fixture import CaptureCase

pytestmark = pytest.mark.integration

UNKNOWN = "99999999-0000-4000-8000-000000000000"
REAL_RUN = subprocess.run
HERE = Path(__file__).parent

# The 19 tests that test_capture_named.py held before it was deleted, each kept by the same name.
DELETED_NAMED_TESTS = (
    "test_R228_a_named_pi_session_is_captured_with_its_harness_and_cwd",
    "test_R228_a_named_session_brings_no_subagent_and_nothing_else",
    "test_R228_a_named_codex_session_is_captured_with_its_later_rollouts",
    "test_R228_a_named_session_is_captured_under_a_unique_prefix_of_its_id",
    "test_R228_a_named_codex_session_is_captured_under_a_unique_prefix_of_its_id",
    "test_R228_the_capture_help_names_every_harness",
    "test_R228_an_unknown_ref_is_refused_naming_every_harness_it_looked_in",
    "test_R228_an_ambiguous_prefix_inside_one_harness_is_refused_naming_both",
    "test_R228_a_prefix_that_matches_two_harnesses_is_refused_naming_both",
    "test_R228_the_same_session_under_two_harnesses_is_refused",
    "test_R228_the_first_named_session_is_the_root_and_names_the_case",
    "test_R228_a_named_session_that_is_also_a_run_worker_joins_once",
    "test_R228_a_named_session_can_be_excluded_and_is_listed",
    "test_R229_a_named_session_records_its_cwd_and_assignment_id",
    "test_R229_a_session_captured_by_name_and_as_a_worker_records_the_same_fields",
    "test_R229_a_chain_member_records_its_own_cwd_and_assignment_id",
    "test_R229_a_session_without_a_marker_records_a_null_assignment_id",
)


def no_cli_spawn(args, *rest, **kwargs):
    """Git may still run; the agent-eval CLI itself must not be started as a subprocess."""
    if args and args[0] == sys.executable:
        raise AssertionError("spawned the agent-eval CLI")
    return REAL_RUN(args, *rest, **kwargs)


@pytest.fixture
def c():
    case = CaptureCase()
    case.setUp()
    yield case
    case.doCleanups()


def test_S6_every_deleted_named_capture_test_keeps_its_name_in_the_outcome_file(c):
    assert not (HERE / "test_capture_named.py").exists()
    outcome = (HERE / "test_capture_named_outcome.py").read_text(encoding="utf-8")
    missing = [name for name in DELETED_NAMED_TESTS if f"def {name}(" not in outcome]
    assert missing == []


def test_S13_an_unknown_ref_is_refused_in_process_without_spawning_a_cli(c):
    with mock.patch("subprocess.run", side_effect=no_cli_spawn):
        result = c.capture("--session", UNKNOWN)
    assert result.returncode == 2
    assert str(c.claude) in result.stderr
    assert str(c.tmp / "codex") in result.stderr


def test_S13_the_capture_help_is_printed_in_process(c):
    with mock.patch("subprocess.run", side_effect=no_cli_spawn):
        result = c.run_cli("capture", "--help")
    assert result.returncode == 0, result.stderr
    assert "--session" in result.stdout


def test_S13_a_refusal_crosses_the_subprocess_boundary_with_exit_2(c):
    result = c.run_cli_subprocess("capture", "--session", UNKNOWN)
    assert result.returncode == 2
    assert str(c.claude) in result.stderr
