"""The Markdown plan path is gone: `run` refuses a Markdown plan and names the YAML format and `plan check`."""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

import pytest
from fixtures import load_script

pytestmark = pytest.mark.integration

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "agent_orchestrator"


class MarkdownRemovedTests(unittest.TestCase):
    def test_S28_a_markdown_plan_passed_to_run_is_refused_naming_yaml_and_plan_check(self):
        spec = load_script()
        with tempfile.TemporaryDirectory() as temp:
            plan = Path(temp) / "demo.plan.md"
            plan.write_text(
                "# Plan: demo\n\nSlug: `demo`. Status: APPROVED 2026-11-06.\n", encoding="utf-8"
            )
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = spec.run_cli(["run", str(plan), "--state-dir", str(Path(temp) / "state")])
        self.assertEqual(code, 2, err.getvalue())
        self.assertIn("plan/1 YAML", err.getvalue())
        self.assertIn("plan check", err.getvalue())

    def test_S28_the_converge_adapter_and_the_plan_cell_writer_no_longer_exist(self):
        spec = load_script()
        self.assertFalse((PACKAGE / "converge.py").exists())
        self.assertFalse(hasattr(spec, "CONVERGE"))
        self.assertFalse(hasattr(spec, "set_status"))
        self.assertFalse(hasattr(spec, "parse_plan"))
        self.assertNotIn("--converge", (PACKAGE / "driver.py").read_text(encoding="utf-8"))


class WaitChecksRemovedTests(unittest.TestCase):
    def test_S5_the_wait_checks_module_does_not_exist_and_nothing_under_packages_names_it(self):
        name = "wait" + "_checks"
        self.assertIsNone(importlib.util.find_spec(f"agent_orchestrator.{name}"))
        self.assertFalse((PACKAGE / f"{name}.py").exists())
        packages = PACKAGE.parents[1]
        this_file = Path(__file__).resolve()
        named = [
            str(path.relative_to(packages.parent))
            for path in packages.rglob("*")
            if path.is_file()
            and path.resolve() != this_file
            and path.suffix not in {".pyc", ".so"}
            and name in path.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual(named, [], "wait_checks is removed; nothing under packages/ may name it")
