"""R248: the ruff PLR09 limits are the one structure gate, and a function over one fails the check."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]

# One definition per limit, each just over it: 13 branches, 8 arguments, 51 statements, 26 public methods.
OVER_THE_LIMITS = (
    "def branchy(x):\n"
    + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(13))
    + "\n\ndef wide(a, b, c, d, e, f, g, h):\n    return a\n"
    + "\n\ndef long():\n"
    + "".join(f"    v{i} = {i}\n" for i in range(51))
    + "\n\nclass Wide:\n"
    + "".join(f"    def m{i}(self):\n        return {i}\n" for i in range(26))
)


class RuffSizeLimitTests(unittest.TestCase):
    def test_S1_a_function_or_class_over_a_pylint_limit_fails_ruff_naming_its_rule_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "over_the_limits.py"
            fixture.write_text(OVER_THE_LIMITS, encoding="utf-8")
            # The repository's own config, so the limits under test are the ones CI enforces.
            done = subprocess.run(
                [
                    "uv",
                    "run",
                    "--frozen",
                    "ruff",
                    "check",
                    "--no-cache",
                    "--config",
                    str(ROOT / "pyproject.toml"),
                    str(fixture),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )
        self.assertNotEqual(done.returncode, 0, done.stdout + done.stderr)
        for code in ("PLR0912", "PLR0913", "PLR0915", "PLR0904"):
            self.assertIn(code, done.stdout)
        for name in ("branchy", "wide", "long", "Wide"):
            self.assertIn(name, done.stdout)


if __name__ == "__main__":
    unittest.main()
