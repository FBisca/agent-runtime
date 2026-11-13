"""The capture CLI's pure logic (refusals, the help) runs in process; one subprocess test per boundary
still shows the refusal crossing the CLI (agent-eval/S13)."""

import subprocess
import sys
from unittest import mock

import pytest
from claude_fixture import CaptureCase

pytestmark = pytest.mark.integration

UNKNOWN = "99999999-0000-4000-8000-000000000000"
REAL_RUN = subprocess.run


def no_cli_spawn(args, *rest, **kwargs):
    """Git may still run; the agent-eval CLI itself must not be started as a subprocess."""
    if args and args[0] == sys.executable:
        raise AssertionError("spawned the agent-eval CLI")
    return REAL_RUN(args, *rest, **kwargs)


class InProcessRefusalTests(CaptureCase):
    def test_S13_an_unknown_ref_is_refused_in_process_without_spawning_a_cli(self):
        with mock.patch("subprocess.run", side_effect=no_cli_spawn):
            result = self.capture("--session", UNKNOWN)
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(self.claude), result.stderr)
        self.assertIn(str(self.tmp / "codex"), result.stderr)

    def test_S13_the_capture_help_is_printed_in_process(self):
        with mock.patch("subprocess.run", side_effect=no_cli_spawn):
            result = self.run_cli("capture", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--session", result.stdout)


class SubprocessBoundaryTests(CaptureCase):
    def test_S13_a_refusal_crosses_the_subprocess_boundary_with_exit_2(self):
        result = self.run_cli_subprocess("capture", "--session", UNKNOWN)
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(self.claude), result.stderr)
