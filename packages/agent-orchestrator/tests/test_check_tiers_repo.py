"""agent-runtime declares its own check tiers, so its workers are briefed with the cheap two."""

import tomllib
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import config
from pytest_impacted.file_map import parse_file_map
from pytest_impacted.strategies import matches_any_glob

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]


class AgentRuntimeTiersTests(unittest.TestCase):
    def test_S8_the_repo_config_declares_fast_affected_through_pytest_impacted_and_a_parallel_full(
        self,
    ):
        checks = config.load(REPO).checks
        # `plan check` warns exactly when the checks node is absent.
        self.assertIsNotNone(checks)
        self.assertEqual(checks.fast, "uv run pytest")
        self.assertIn("--impacted", checks.affected)
        self.assertIn("{base}", checks.affected)
        self.assertEqual(checks.full_run, "uv run pytest -n auto")
        self.assertTrue(checks.full_verify)


# The tests load server/ and hooks/ by path and the packages by their installed names, so
# pytest-impacted's import analysis selects no test for a source change here: the file map
# is what routes a change to its tests. Every tracked root must reach an existing test path.
ROOTS = ("server", "hooks", "bin", "scripts", "herdr", "packages/agent-orchestrator/src",
         "packages/agent-eval/src", "packages/agent-runtime/src")


def _source_files():
    yield REPO / "install.sh"
    for root in ROOTS:
        for path in (REPO / root).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and "node_modules" not in path.parts:
                yield path


class AffectedTierSelectsTests(unittest.TestCase):
    def setUp(self):
        with open(REPO / "pyproject.toml", "rb") as fh:
            self.ini = tomllib.load(fh)["tool"]["pytest"]["ini_options"]

    def test_the_affected_tier_names_a_module_so_pytest_impacted_runs(self):
        module = self.ini.get("impacted_module")
        self.assertTrue(module, "pytest-impacted refuses to run without impacted_module")
        self.assertTrue((REPO / module.replace(".", "/")).is_dir())

    def test_every_source_file_maps_to_an_existing_test_path(self):
        pairs = parse_file_map(self.ini.get("impacted_map", []))
        for target in {path for _, path in pairs}:
            self.assertTrue((REPO / target).exists(), f"impacted_map target {target} does not exist")
        unmapped = [
            str(path.relative_to(REPO))
            for path in _source_files()
            if not matches_any_glob(path.relative_to(REPO).as_posix(), [glob for glob, _ in pairs])
        ]
        self.assertEqual(unmapped, [], "a change to these would select no test under --impacted")
