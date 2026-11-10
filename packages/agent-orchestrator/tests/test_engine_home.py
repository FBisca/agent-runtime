"""Where the engine lives, and what the CI runs.

The engine moved into this repository: the suite, the driver and the member's contracts are one
tree. These checks are the drift guard — a second copy of the engine, or a CI run that skips the
member's suite, would let the two copies diverge again without anything failing.
"""

import ast
import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
SKIPPED = {".git", ".venv", "__pycache__", "node_modules", ".ruff_cache", ".pytest_cache"}


def sources():
    """Every Python file of the repository, except the ones a tool generated."""
    for path in sorted(REPO.rglob("*.py")):
        if not any(part in SKIPPED for part in path.parts):
            yield path


class EngineHomeTests(unittest.TestCase):
    """R1: the suite drives the member, and the engine has one home."""

    def test_S1_the_suite_drives_the_member_driver(self):
        """orchestrator-engine/R1"""
        from agent_orchestrator import driver

        self.assertEqual(Path(driver.__file__).resolve().parent, MEMBER.resolve())

    def test_S1_the_repository_holds_one_copy_of_the_engine(self):
        """orchestrator-engine/R1"""
        copies = []
        for path in sources():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            if "parse_plan" in names and "check_plan" in names:
                copies.append(path.relative_to(REPO).as_posix())
        self.assertEqual(copies, ["packages/agent-orchestrator/src/agent_orchestrator/driver.py"])

    def test_S3_ci_runs_the_member_suite_and_the_tracked_budgets(self):
        """orchestrator-engine/R3"""
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("uv run pytest -n 4", workflow)
        for budget in ("RUFF_CHECK_BUDGET", "RUFF_FORMAT_BUDGET", "TY_BUDGET"):
            self.assertIn(budget, workflow)
        self.assertIn("ty check packages/", workflow)
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("packages/agent-orchestrator/tests", pyproject)

    def test_R3_the_format_budget_counts_a_single_unformatted_file(self):
        """orchestrator-engine/R3"""
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        step = workflow.split("name: ruff format (legacy budget)", 1)[1].split("- name:", 1)[0]
        patterns = re.findall(r"grep -oE '([^']+)'", step)
        self.assertTrue(patterns, "the format step parses no count from ruff's output")
        counting = re.compile(patterns[0])
        self.assertTrue(counting.search("1 file would be reformatted, 175 files already formatted"))
        self.assertTrue(
            counting.search("3 files would be reformatted, 173 files already formatted")
        )
        self.assertIsNone(counting.search("175 files already formatted"))


if __name__ == "__main__":
    unittest.main()
