"""Where the engine lives, and what the CI runs.

The engine moved into this repository: the suite, the driver and the member's contracts are one
tree. These checks are the drift guard — a second copy of the engine, or a CI run that skips the
member's suite, would let the two copies diverge again without anything failing.
"""

import ast
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
# The directories that can ship the engine. Tooling trees (`.pi`, `.claude`, `docs`) cannot hold a
# second copy a suite would import, and walking them cost seconds for nothing.
SOURCE_ROOTS = ("packages", "server", "hooks", "tests", "acceptance", "bin", "scripts", "herdr")


def sources():
    """Every Python file of the engine's own tree, except the ones a tool generated."""
    for top in SOURCE_ROOTS:
        root = REPO / top
        if root.is_dir():
            yield from sorted(root.rglob("*.py"))
    yield from sorted(REPO.glob("*.py"))


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
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # A second copy must define one of these names: skip the parse for every other file.
            if "parse_plan" not in source and "check_plan" not in source:
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            if "parse_plan" in names and "check_plan" in names:
                copies.append(path.relative_to(REPO).as_posix())
        self.assertEqual(copies, [])  # the Markdown plan reader is gone: no source defines it

    def test_S3_ci_runs_the_member_suite_and_the_tracked_budgets(self):
        """orchestrator-engine/R3"""
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("uv run pytest -n 4", workflow)
        self.assertIn("suite_budget.py", workflow)
        # The structure budget is the same kind of check: the tree, measured against its baseline.
        self.assertIn(
            "python3 scripts/structure_budget.py --root . "
            "--baseline acceptance/structure/structure_budget.json",
            " ".join(workflow.split()),
        )
        for budget in ("RUFF_CHECK_BUDGET", "RUFF_FORMAT_BUDGET", "TY_BUDGET"):
            self.assertIn(budget, workflow)
        self.assertIn("ty check packages/", workflow)
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("packages/agent-orchestrator/tests", pyproject)


if __name__ == "__main__":
    unittest.main()
