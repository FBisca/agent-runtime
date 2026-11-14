"""A repo that declares check tiers briefs each ticket worker with the cheap tiers only: `fast` and `affected`
rendered for its ticket, never the whole suite (check-tiers/S2, S3)."""

import unittest

import pytest
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration

CONFIG = """\
checks:
  fast: uv run pytest -q
  affected: uv run pytest --impacted --impacted-base-branch {base} {paths}
  full:
    run: uv run pytest -n 4
"""

AFFECTED = "uv run pytest --impacted --impacted-base-branch origin/main src/a/ 'src/my dir/'"
AFFECTED_ONE_PATH = "uv run pytest --impacted --impacted-base-branch origin/main src/a/"
# Every tier renders the same command: one requirement, and nothing to tell the worker not to run.
OVERLAPPING = """\
checks:
  fast: uv run pytest -q
  affected: uv run pytest -q
  full:
    run: uv run pytest -q
"""
# The same command in three launcher spellings: still one requirement, still never forbidden.
ONE_COMMAND = """\
checks:
  fast: uv run pytest -q
  affected: python3 -m pytest -q
  full:
    run: pytest -q
"""


def report(checks):
    return {
        "acceptance_met": True,
        "remaining": [],
        "review_rounds": 0,
        "usage": "unknown",
        "checks": checks,
    }


class CheckTiersBrief(RunFixture, unittest.TestCase):
    def declare(self, config, mutate=None):
        path = self.repo / ".orchestrate" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(config, encoding="utf-8")
        self.set_plan(("T1",), mutate=mutate)

    def declare_tiers(self):
        def two_paths(data):
            # A second path with a space: `{paths}` is shell-quoted and space-joined.
            data["tickets"][0]["paths"] = ["src/a/", "src/my dir/"]

        self.declare(CONFIG, two_paths)

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

    def test_S3_the_brief_names_the_proxy_and_asks_for_the_command_the_worker_ran(self):
        self.declare_tiers()
        self.run_driver()
        brief = self.brief()
        # The required-check line is the ticket's own: it stays as it is.
        self.assertEqual(
            [x for x in brief.splitlines() if x.startswith("Before you report COMPLETE, run:")],
            ["Before you report COMPLETE, run: uv run pytest -q; " + AFFECTED + "."],
        )
        proxy = [x for x in brief.splitlines() if "rtk proxy" in x]
        self.assertTrue(proxy, brief)
        self.assertTrue(any("run" in x.lower() for x in proxy), brief)
        self.assertTrue(
            any("report" in x.lower() and "command" in x.lower() for x in brief.splitlines()),
            brief,
        )

    def test_S3_a_named_gate_overrides_the_declared_tiers(self):
        self.declare_tiers()
        self.run_driver("--gate", "scripts/checks.sh")
        self.assertEqual(self.required(), ["scripts/checks.sh"])

    def test_S1_overlapping_tiers_require_the_command_once_and_never_forbid_it(self):
        self.declare(OVERLAPPING)
        self.run_driver()
        self.assertEqual(self.required(), ["uv run pytest -q"])
        brief = self.brief()
        (run,) = [x for x in brief.splitlines() if x.startswith("Before you report COMPLETE, run:")]
        self.assertEqual(run.count("uv run pytest -q"), 1)
        self.assertEqual(
            [x for x in brief.splitlines() if "do not run" in x],
            [],
        )
        # The required set and COMPLETE validation are the same set: passed once is enough, absent is not.
        task = self.ledger()["tasks"]["t1"]
        load_script().require_completable(
            task, report([{"command": "uv run pytest -q", "outcome": "passed"}])
        )
        with self.assertRaisesRegex(ValueError, "required checks"):
            load_script().require_completable(task, report([]))

    def test_S1_launcher_spellings_of_one_command_are_one_required_assignment(self):
        self.declare(ONE_COMMAND)
        self.run_driver()
        self.assertEqual(self.required(), ["uv run pytest -q"])
        self.assertEqual([x for x in self.brief().splitlines() if "do not run" in x], [])
        load_script().require_completable(
            self.ledger()["tasks"]["t1"],
            report([{"command": "python3 -m pytest -q", "outcome": "passed"}]),
        )

    def test_S2_fast_and_affected_are_kept_when_only_full_is_distinct(self):
        self.declare(CONFIG)
        self.run_driver()
        self.assertEqual(self.required(), ["uv run pytest -q", AFFECTED_ONE_PATH])
        packet = self.ledger()["tasks"]["t1"]["packet"]
        self.assertEqual(packet["affected"], AFFECTED_ONE_PATH)
        self.assertEqual(packet["full"], "uv run pytest -n 4")

    def test_control_a_named_gate_without_a_config_is_the_required_check(self):
        self.set_plan(("T1",))
        self.run_driver("--gate", "scripts/checks.sh")
        self.assertEqual(self.required(), ["scripts/checks.sh"])
