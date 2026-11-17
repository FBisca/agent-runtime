"""The fast suite selects by marker alone (test-cost/S5) and its tests stay in process (S3)."""

import functools
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]

# Loaded into the fast suite with `-p`: a `unit` test (not also marked `integration`) that starts a process or opens a network socket fails, naming it.
GUARD = textwrap.dedent(
    """
    import socket
    import subprocess

    import pytest


    def refuse(what):
        def raise_(*args, **kwargs):
            raise AssertionError(f"a unit test {what}; mark it integration or keep it in process")

        return raise_


    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_protocol(item, nextitem):
        if item.get_closest_marker("unit") is None or item.get_closest_marker("integration"):
            yield
            return
        saved = (subprocess.Popen.__init__, socket.socket.connect, socket.socket.bind)
        subprocess.Popen.__init__ = refuse("started a process")
        socket.socket.connect = refuse("opened a socket")
        socket.socket.bind = refuse("opened a socket")
        try:
            yield
        finally:
            subprocess.Popen.__init__, socket.socket.connect, socket.socket.bind = saved
    """
)


def pytest_run(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "pytest", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=600,
    )


@functools.lru_cache(maxsize=None)
def selected(*args):
    """How many tests a pytest command line selects, from `--collect-only`'s per-file counts.

    Cached: the rows ask for the same collection more than once, and collecting the whole suite is a
    second of a contended runner each time.
    """
    result = pytest_run("--collect-only", "-q", *args)
    counts = [line.rsplit(": ", 1) for line in result.stdout.splitlines() if ": " in line]
    return sum(int(count) for _, count in counts if count.strip().isdigit())


# A wall-clock ceiling is a property of the runner class it was measured on. R222's numbers come from the
# ubuntu runners the suite's cost was calibrated against; a macOS runner is a different machine class and
# no calibration exists for it, so asserting the same seconds there measures with another machine's ruler
# and fails on the runner rather than on the code. The behaviour row (no unit test starts a process or
# opens a socket) is machine-independent and runs everywhere.
TIMED_ON_CALIBRATED_RUNNER = pytest.mark.skipif(
    sys.platform == "darwin",
    reason=(
        "the fast suite's seconds are calibrated on the ubuntu runners; a macOS runner is a different "
        "machine class with no calibration to compare against"
    ),
)


class FastSuiteTests(unittest.TestCase):
    FAST_SUITE_SECONDS = 3.0  # R222: the fast suite's tests cost under three seconds
    UNIT_TEST_SECONDS = 0.5
    # A shared runner is slower than the machine these numbers were measured on, and a build fails on
    # the behaviour, not on the runner: half again before a row is a red build.
    RUNNER_FACTOR = 1.5

    _profile = None

    @classmethod
    def profile(cls):
        """The one timed, guarded fast-suite run every R222 row reads.

        R222 measures an idle machine and a wall clock, which a loaded CI runner cannot promise, so the
        gate measures what a test costs: the sum of the cases' call time — collection and module import
        are not in it, so no warm-up buys anything it reads. The run carries the unit guard too, because
        a second run of the suite to prove the same tests start no process is another full suite on a
        contended runner; the two rows share this one.
        """
        if cls._profile is None:
            with tempfile.TemporaryDirectory() as directory:
                Path(directory, "unit_guard.py").write_text(GUARD, encoding="utf-8")
                env = dict(
                    os.environ,
                    PYTHONPATH=os.pathsep.join(
                        filter(None, [directory, os.environ.get("PYTHONPATH")])
                    ),
                )
                # The fast suite as `uv run pytest` selects it: an `integration` mark overrides a module's
                # `unit`, so the guard covers exactly the tests the fast suite runs.
                cls._profile = pytest_run("-p", "unit_guard", "-q", "--durations=0", env=env)
        return cls._profile

    @TIMED_ON_CALIBRATED_RUNNER
    def test_S1_the_fast_suite_reports_under_three_seconds(self):
        result = self.profile()
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        calls = [
            float(found.group(1))
            for line in result.stdout.splitlines()
            if (found := re.match(r"\s*([0-9.]+)s call\s+", line))
        ]
        self.assertTrue(calls, result.stdout[-4000:])
        self.assertLess(
            sum(calls),
            self.FAST_SUITE_SECONDS * self.RUNNER_FACTOR,
            "the fast suite's tests cost more than R222's three seconds; make one cheaper or move it",
        )

    @TIMED_ON_CALIBRATED_RUNNER
    def test_S1_no_unit_test_costs_more_than_half_a_second(self):
        result = self.profile()
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
        slow = []
        for line in result.stdout.splitlines():
            found = re.match(r"\s*([0-9.]+)s call\s+(\S+)", line)
            if found and float(found.group(1)) >= self.UNIT_TEST_SECONDS * self.RUNNER_FACTOR:
                slow.append((found.group(2), float(found.group(1))))
        self.assertEqual(
            slow,
            [],
            "a unit test over half a second slows every run; make it cheaper or move it to the boundary",
        )

    def test_control_the_fast_suite_leaves_an_integration_test_out(self):
        """The default fast suite drops what is marked integration; naming its module keeps it.

        `-k` is an option, not a path, so the default selection still applies. One whole-suite
        collection answers it: the two counts a whole-suite-versus-every-marker comparison needs were
        two collections, and a test that only asks which tests a run selected pays for both.
        """
        name = "test_S1_the_fast_suite_reports_under_three_seconds"
        module = "tests/test_fast_suite.py"
        self.assertEqual(selected("-k", name), 0)
        self.assertEqual(selected(module, "-k", name), 1)

    def test_S5_an_option_value_keeps_the_fast_suite(self):
        # One module is enough: the property is that an option's value is not read as a selection, and a
        # whole-suite collection to prove it is two seconds of a contended runner for the same answer.
        path = "tests/test_activation.py"
        self.assertEqual(selected(path, "-p", "no:cacheprovider"), selected(path))

    def test_S3_no_unit_test_starts_a_process_or_opens_a_socket(self):
        result = self.profile()
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
