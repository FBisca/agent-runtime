"""The fast suite selects by marker alone (test-cost/S5) and its tests stay in process (S3)."""

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]

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


def selected(*args):
    """How many tests a pytest command line selects, from `--collect-only`'s per-file counts."""
    result = pytest_run("--collect-only", "-q", *args)
    counts = [line.rsplit(": ", 1) for line in result.stdout.splitlines() if ": " in line]
    return sum(int(count) for _, count in counts if count.strip().isdigit())


class FastSuiteTests(unittest.TestCase):
    def test_control_the_fast_suite_selects_fewer_tests_than_the_whole_suite(self):
        self.assertLess(selected(), selected("-m", "unit or integration or not unit"))

    def test_S5_an_option_value_keeps_the_fast_suite(self):
        self.assertEqual(selected("-p", "no:cacheprovider"), selected())

    def test_S3_no_unit_test_starts_a_process_or_opens_a_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "unit_guard.py").write_text(GUARD, encoding="utf-8")
            env = dict(
                os.environ,
                PYTHONPATH=os.pathsep.join(filter(None, [directory, os.environ.get("PYTHONPATH")])),
            )
            # The fast suite as `uv run pytest` selects it: an `integration` mark overrides a module's `unit`.
            result = pytest_run("-p", "unit_guard", "-q", env=env)
        self.assertEqual(result.returncode, 0, result.stdout[-4000:])
