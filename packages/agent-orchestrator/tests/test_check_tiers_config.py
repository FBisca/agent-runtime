"""`plan check` in a repo that declares no `checks` node: one warning on stderr, exit 0."""

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

import pytest
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration


class ChecksWarningTests(RunFixture, unittest.TestCase):
    def check(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    "check",
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                ]
            )
        return code, err.getvalue()

    def test_S4_no_checks_node_prints_one_warning_naming_the_fields_and_exits_zero(self):
        code, err = self.check()
        self.assertEqual(code, 0, err)
        lines = [line for line in err.splitlines() if ".orchestrate/config.yaml" in line]
        self.assertEqual(len(lines), 1, err)
        for field in ("checks.fast", "checks.affected", "checks.full"):
            self.assertIn(field, lines[0])

    def test_S4_declared_checks_node_prints_no_warning(self):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(exist_ok=True)
        config.write_text("checks:\n  fast: pytest -q\n", encoding="utf-8")
        code, err = self.check()
        self.assertEqual(code, 0, err)
        self.assertNotIn("checks.fast", err)
