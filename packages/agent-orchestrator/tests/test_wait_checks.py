"""The CI gate's wait: the module the driver imports, answered by a fake `gh`."""

import contextlib
import io
import json
import subprocess
import unittest
from unittest import mock

import fixtures  # noqa: F401  (puts the driver's modules on the path)
import pytest
from agent_orchestrator import wait_checks

pytestmark = pytest.mark.unit


class FakeGh:
    """A fake `gh`: it answers the checks of one pull request the way the CLI does.

    `failing` makes the watch fail, `watch_error` makes it raise (a timeout), `registering` says how many times the
    checks have not registered yet."""

    def __init__(
        self, failing=False, buckets=None, log="boom at line 3", registering=0, watch_error=None
    ):
        self.failing = failing
        self.watch_error = watch_error
        self.buckets = (
            buckets
            if buckets is not None
            else (
                [
                    {
                        "name": "unit tests",
                        "bucket": "fail",
                        "link": "https://github.com/o/r/actions/runs/42",
                    }
                ]
                if failing
                else [
                    {
                        "name": "unit tests",
                        "bucket": "pass",
                        "link": "https://github.com/o/r/actions/runs/42",
                    }
                ]
            )
        )
        self.log = log
        self.registering = registering
        self.calls = []

    def __call__(self, argv, timeout=None):
        self.calls.append(argv)
        if "--watch" in argv:
            if self.watch_error:
                raise self.watch_error
            return (1, "", "some checks failed") if self.failing else (0, "", "")
        if argv[0] == "run":
            return (0, self.log, "")
        if "--json" in argv:
            fields = argv[argv.index("--json") + 1]
            rows = [{key: row.get(key) for key in fields.split(",")} for row in self.buckets]
            return (0, json.dumps(rows), "")
        if self.registering > 0:
            self.registering -= 1
            return (0, "no checks reported", "")
        return (0, "", "")


class WaitChecksTests(unittest.TestCase):
    def wait(self, gh, ref="o/r#7", **kwargs):
        """The wait, answered by the fake `gh`."""
        with mock.patch.object(wait_checks, "gh", gh):
            return wait_checks.wait(ref, **kwargs)

    def test_a_passing_pr_answers_pass(self):
        """orchestrator-engine/R2"""
        result = self.wait(FakeGh())
        self.assertEqual(
            (result.returncode, result.stdout, result.timed_out), (0, "PASS o/r#7\n", False)
        )

    def test_a_failing_pr_answers_fail_with_the_failing_check_and_the_log_tail(self):
        """orchestrator-engine/R2"""
        result = self.wait(FakeGh(failing=True))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stdout.startswith("FAIL o/r#7\n"))
        self.assertIn("  unit tests: fail https://github.com/o/r/actions/runs/42", result.stdout)
        self.assertIn("  boom at line 3", result.stdout)

    def test_the_log_tail_is_kept_to_the_last_lines(self):
        """orchestrator-engine/R2"""
        result = self.wait(
            FakeGh(failing=True, log="\n".join("line %d" % n for n in range(10))), log_lines=3
        )
        self.assertIn("  line 9", result.stdout)
        self.assertNotIn("line 5", result.stdout)

    def test_a_wait_that_runs_out_of_time_is_not_a_failure(self):
        """orchestrator-engine/R2"""
        result = self.wait(FakeGh(watch_error=subprocess.TimeoutExpired("gh", 60)))
        self.assertTrue(result.timed_out)
        self.assertEqual(result.returncode, 1)

    def test_checks_that_have_not_registered_yet_are_waited_for(self):
        """orchestrator-engine/R2"""
        gh = FakeGh(registering=1)
        with (
            mock.patch.object(wait_checks, "gh", gh),
            mock.patch.object(wait_checks.time, "sleep") as sleep,
        ):
            result = wait_checks.wait("o/r#7")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(sleep.call_args[0][0], wait_checks.REGISTER_INTERVAL)

    def test_a_ref_that_names_no_pull_request_is_refused(self):
        """orchestrator-engine/R2"""
        with self.assertRaises(ValueError):
            wait_checks.resolve("o/r#seven")

    def test_the_command_line_prints_nothing_for_no_ref(self):
        """orchestrator-engine/R2"""
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wait_checks.main([]), 2)
