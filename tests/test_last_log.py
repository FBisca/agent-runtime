"""Every pytest run leaves its outcome and its failures in one file, so nobody re-runs a suite to read them."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"

TESTS = """
def test_passes():
    pass

def test_fails():
    print("captured line")
    assert 1 == 2, "the failure detail"
"""


class LastLogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copy(ROOT_CONFTEST, self.root / "conftest.py")
        (self.root / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (self.root / "test_sample.py").write_text(TESTS, encoding="utf-8")

    def run_pytest(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return (self.root / ".pytest" / "last.log").read_text(encoding="utf-8"), result

    def test_a_run_writes_its_counts_failures_and_captured_output_to_the_last_log(self):
        log, result = self.run_pytest("-q", "--tb=line")
        self.assertIn("1 failed, 1 passed", log)
        self.assertIn("FAILED test_sample.py::test_fails", log)
        self.assertIn("the failure detail", log)
        self.assertIn("captured line", log)
        self.assertIn(".pytest/last.log", result.stdout)

    def test_a_green_run_replaces_the_failures_of_the_last_one(self):
        self.run_pytest("-q")
        log, _ = self.run_pytest("-q", "test_sample.py::test_passes")
        self.assertIn("1 passed", log)
        self.assertNotIn("FAILED", log)
