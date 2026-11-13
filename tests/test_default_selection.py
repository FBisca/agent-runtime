"""The default run is the fast suite: only a named path or node id hands the selection to the caller. An option's
value (`-p no:cacheprovider`, `-k name`) names no test, so it must not widen the run to the whole suite."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CONFTEST = Path(__file__).with_name("conftest.py")

INI = """[pytest]
markers =
    unit: in process
    integration: a real boundary
"""

TESTS = """import pytest

@pytest.mark.unit
def test_fast():
    pass

@pytest.mark.integration
def test_slow():
    pass
"""


class DefaultSelection(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "pytest.ini").write_text(INI, encoding="utf-8")
        shutil.copy(CONFTEST, self.root / "conftest.py")
        (self.root / "test_mixed.py").write_text(TESTS, encoding="utf-8")

    def collected(self, *args):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return [line.split("::")[-1] for line in result.stdout.splitlines() if "::" in line]

    def test_a_bare_run_is_the_fast_suite(self):
        self.assertEqual(self.collected(), ["test_fast"])

    def test_a_plugin_option_value_is_not_a_test_path(self):
        self.assertEqual(self.collected("-p", "no:cacheprovider"), ["test_fast"])

    def test_a_keyword_value_is_not_a_test_path(self):
        self.assertEqual(self.collected("-k", "test_"), ["test_fast"])

    def test_control_a_named_file_runs_every_test_in_it(self):
        self.assertEqual(self.collected("test_mixed.py"), ["test_fast", "test_slow"])
