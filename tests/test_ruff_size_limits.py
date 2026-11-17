"""The ruff PLR09 limits are the one structure gate, and a function over one fails the check."""

import json
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
    @pytest.mark.spec("Ruff size limits")
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
                    # Codes in JSON, not the text format: preview rules print their names there.
                    "--output-format",
                    "json",
                    "--select",
                    "PLR0912,PLR0913,PLR0915,PLR0904",
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
        # Each diagnostic sits on the line that defines the offender, where a suppression would go.
        lines = OVER_THE_LIMITS.splitlines()
        found = {
            diagnostic["code"]: lines[diagnostic["location"]["row"] - 1]
            for diagnostic in json.loads(done.stdout)
        }
        self.assertEqual(
            found,
            {
                "PLR0912": "def branchy(x):",
                "PLR0913": "def wide(a, b, c, d, e, f, g, h):",
                "PLR0915": "def long():",
                "PLR0904": "class Wide:",
            },
        )


if __name__ == "__main__":
    unittest.main()
