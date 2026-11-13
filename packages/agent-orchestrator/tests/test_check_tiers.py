"""A repo that declares check tiers briefs each ticket worker with the cheap tiers only: `fast` and `affected`
rendered for its ticket, never the whole suite (check-tiers/S2, S3)."""

import unittest

import pytest
from fixtures import RunFixture

pytestmark = pytest.mark.integration

CONFIG = """\
checks:
  fast: uv run pytest -q
  affected: uv run pytest --impacted --impacted-base-branch {base} {paths}
  full:
    run: uv run pytest -n 4
"""

AFFECTED = "uv run pytest --impacted --impacted-base-branch origin/main src/a/ 'src/my dir/'"


class CheckTiersBrief(RunFixture, unittest.TestCase):
    def declare_tiers(self):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(CONFIG, encoding="utf-8")

        def two_paths(data):
            # A second path with a space: `{paths}` is shell-quoted and space-joined.
            data["tickets"][0]["paths"] = ["src/a/", "src/my dir/"]

        self.set_plan(("T1",), mutate=two_paths)

    def brief(self):
        return (self.run_dir_of("demo") / "t1" / "task.md").read_text(encoding="utf-8")

    def required(self):
        return self.ledger()["tasks"]["t1"]["packet"]["checks"]

    def test_S2_a_worker_is_required_to_pass_fast_and_its_rendered_affected_check(self):
        self.declare_tiers()
        self.run_driver()
        self.assertEqual(
            self.required(),
            [
                "uv run pytest -q",
                AFFECTED,
            ],
        )

    def test_S2_the_brief_names_the_tiers_and_leaves_the_whole_suite_to_verify_and_ci(self):
        self.declare_tiers()
        self.run_driver()
        brief = self.brief()
        (run,) = [x for x in brief.splitlines() if x.startswith("Before you report COMPLETE, run:")]
        self.assertIn("uv run pytest -q", run)
        self.assertIn(AFFECTED, run)
        self.assertNotIn("pytest -n 4", run)
        self.assertNotIn("full CI gate", brief)
        (full,) = [x for x in brief.splitlines() if "uv run pytest -n 4" in x]
        self.assertIn("do not run", full)
        self.assertIn("verify", full)

    def test_S3_a_named_gate_overrides_the_declared_tiers(self):
        self.declare_tiers()
        self.run_driver("--gate", "scripts/checks.sh")
        self.assertEqual(self.required(), ["scripts/checks.sh"])

    def test_control_a_named_gate_without_a_config_is_the_required_check(self):
        self.set_plan(("T1",))
        self.run_driver("--gate", "scripts/checks.sh")
        self.assertEqual(self.required(), ["scripts/checks.sh"])
