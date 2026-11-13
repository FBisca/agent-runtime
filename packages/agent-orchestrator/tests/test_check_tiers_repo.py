"""agent-runtime declares its own check tiers, so its workers are briefed with the cheap two."""

import unittest
from pathlib import Path

import pytest
from agent_orchestrator import config

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
