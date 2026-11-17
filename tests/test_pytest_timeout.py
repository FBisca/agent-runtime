"""R249: a single test past the configured timeout fails, by pytest-timeout and no script of ours."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

SLEEPER = "import time\n\n\ndef test_sleeps_past_the_timeout():\n    time.sleep(10)\n"


class PytestTimeoutTests(unittest.TestCase):
    def test_S6_a_test_past_the_timeout_fails_naming_the_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "test_sleeper.py"
            fixture.write_text(SLEEPER, encoding="utf-8")
            # `-c /dev/null` keeps the repo's addopts out; `--timeout=1` stands in for the 18 of the config.
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-c",
                    "/dev/null",
                    "--rootdir",
                    tmp,
                    "--timeout=1",
                    str(fixture),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 1, result.stdout[-2000:])
        self.assertIn("Timeout", result.stdout)

    def test_S6_the_configured_timeout_is_eighteen_seconds(self):
        import tomllib

        root = Path(__file__).resolve().parents[1]
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["tool"]["pytest"]["ini_options"]["timeout"], 18)
