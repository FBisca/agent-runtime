"""Check tiers in the worker's brief: required checks rendered from the repo config, `--gate` overriding."""

import json
import shlex
import unittest

import pytest
from test_gates import GateFixture

pytestmark = pytest.mark.integration

TIERS = (
    "checks:\n"
    "  fast: uv run ruff check\n"
    "  affected: uv run pytest --base {base} {paths}\n"
    "  full:\n"
    "    run: uv run pytest -n auto\n"
)


class TierBriefTests(GateFixture, unittest.TestCase):
    def declare(self, text=TIERS):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(text, encoding="utf-8")

    def brief(self, *extra):
        self.use_yaml_plan(paths=("src/a/", "docs/with space.md"))
        self.waits([self.worker_done(tests=True)])
        self.run_driver("--max-fix-rounds", "0", *extra)
        return (self.run_dir / "t1" / "task.md").read_text(encoding="utf-8")

    def packet(self):
        return self.ledger()["tasks"]["t1"]["packet"]

    def test_S2_the_brief_lists_fast_and_the_rendered_affected_and_names_full_as_not_run(self):
        self.declare()
        text = self.brief()
        affected = "uv run pytest --base origin/main src/a/ " + shlex.quote("docs/with space.md")
        self.assertEqual(self.packet()["checks"], ["uv run ruff check", affected])
        self.assertIn("Before you report COMPLETE, run: uv run ruff check; " + affected + ".", text)
        others = [line for line in text.splitlines() if "uv run pytest -n auto" in line]
        self.assertEqual(len(others), 1, text)
        self.assertIn("Do not run", others[0])
        self.assertNotIn("full CI gate", text)

    def test_S3_a_named_gate_is_the_required_checks_alone_and_the_tiers_are_not_applied(self):
        self.declare()
        text = self.brief("--gate", "scripts/checks.sh")
        self.assertEqual(self.packet()["checks"], ["scripts/checks.sh"])
        self.assertIn("Before you report COMPLETE, run: scripts/checks.sh.", text)
        self.assertNotIn("uv run ruff check", text)
        self.assertNotIn("uv run pytest -n auto", text)

    def test_control_without_tiers_the_brief_asks_for_the_full_gate(self):
        text = self.brief()
        self.assertEqual(self.packet()["checks"], [])
        self.assertIn("the repository's full CI gate", text)
        json.dumps(self.packet())
