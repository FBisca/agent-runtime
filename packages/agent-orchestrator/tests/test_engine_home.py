"""Where the engine lives, and what the CI runs.

The engine moved into this repository: the suite, the driver and the member's contracts are one
tree. These checks are the drift guard — a second copy of the engine, or a CI run that skips the
member's suite, would let the two copies diverge again without anything failing.
"""

import ast
import importlib
import tomllib
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
# The directories that can ship the engine. Tooling trees (`.pi`, `.claude`, `docs`) cannot hold a
# second copy a suite would import, and walking them cost seconds for nothing.
SOURCE_ROOTS = ("packages", "server", "hooks", "tests", "bin", "scripts", "herdr")


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

        # The engine is a package inside the member's source directory, so its home is named by
        # that directory rather than by the parent of a flat module, and the flat module is gone.
        self.assertEqual(Path(driver.__file__).resolve().parent.parent, MEMBER.resolve())
        self.assertFalse((MEMBER / "driver.py").exists())

    def test_S10_the_driver_is_a_package_every_entry_point_still_reaches(self):
        import agent_orchestrator
        from agent_orchestrator import driver

        # The engine is a package, not the flat module it was: the split that follows needs a
        # directory to put its modules in, and flattening it back loses that home.
        self.assertTrue(hasattr(driver, "__path__"), "agent_orchestrator.driver is not a package")
        self.assertEqual(Path(driver.__file__).name, "__init__.py")
        # The names the package, the suite and the entry points ask for still resolve.
        self.assertTrue(callable(driver.main))
        self.assertTrue(callable(driver.run_cli))
        self.assertTrue(callable(agent_orchestrator.main))
        # `python -m agent_orchestrator` calls the package's own run_cli, so it drives the same
        # commands with the same arguments; so does the installed console script's target.
        entry = importlib.import_module("agent_orchestrator.__main__")
        self.assertIs(entry.run_cli, driver.run_cli)
        target = tomllib.loads((MEMBER.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["scripts"]["agent-orchestrator"]
        module, _, attribute = target.partition(":")
        self.assertIs(getattr(importlib.import_module(module), attribute), driver.run_cli)

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

    def test_S3_ci_runs_the_member_suite_and_the_plain_gates(self):
        """orchestrator-engine/R3"""
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("uv run pytest -n 4", workflow)
        # The checkout must hold the history: a
        # depth-1 checkout resolves neither a pull request's base nor the tip a push replaces.
        after_checkout = workflow.split("uses: actions/checkout@v4", 1)[1]
        self.assertIn("fetch-depth: 0", after_checkout.split("\n      - ", 1)[0])
        workflow = " ".join(workflow.split())
        self.assertIn("uv run ruff check .", workflow)
        self.assertIn("uv run ruff format --check .", workflow)
        self.assertIn("uv run ty check packages/ tests/test_workspace_isolation.py", workflow)
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("packages/agent-orchestrator/tests", pyproject)


if __name__ == "__main__":
    unittest.main()
