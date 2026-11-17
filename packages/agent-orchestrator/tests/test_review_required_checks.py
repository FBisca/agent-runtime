"""Worker check ownership at the generated assignment/report boundary."""

import unittest

import pytest
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration


class RequiredChecksAcceptance(RunFixture, unittest.TestCase):
    def test_S1_overlapping_tiers_require_once_without_forbidding_the_command(self):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "checks:\n  fast: uv run pytest -q\n  affected: uv run pytest -q\n"
            "  full:\n    run: uv run pytest -q\n"
        )
        self.set_plan(("T1",))
        self.run_driver()
        task = self.ledger()["tasks"]["t1"]
        self.assertEqual(task["packet"]["checks"], ["uv run pytest -q"])
        brief = (self.run_dir_of() / "t1" / "task.md").read_text()
        required = [
            line for line in brief.splitlines() if line.startswith("Before you hand in, run:")
        ]
        self.assertEqual(len(required), 1)
        self.assertEqual(required[0].count("uv run pytest -q"), 1)
        self.assertFalse(
            any("uv run pytest -q" in line and "do not run" in line for line in brief.splitlines())
        )
        report = {
            "acceptance_met": True,
            "review_rounds": 0,
            "usage": "unknown",
            "checks": [{"command": "uv run pytest -q", "outcome": "passed"}],
        }
        load_script().require_completable(task, report)

    def test_control_named_gate_is_still_required(self):
        self.set_plan(("T1",))
        self.run_driver("--gate", "scripts/checks.sh")
        self.assertEqual(self.ledger()["tasks"]["t1"]["packet"]["checks"], ["scripts/checks.sh"])

    def test_S1_whitespace_spellings_of_one_command_share_one_required_assignment(self):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            'checks:\n  fast: uv run pytest -q\n  affected: "uv run  pytest -q"\n'
            '  full:\n    run: "uv run pytest   -q"\n'
        )
        self.set_plan(("T1",))
        self.run_driver()
        task = self.ledger()["tasks"]["t1"]
        self.assertEqual(task["packet"]["checks"], ["uv run pytest -q"])
        brief = (self.run_dir_of() / "t1" / "task.md").read_text()
        self.assertFalse(
            any("pytest -q" in line and "do not run" in line for line in brief.splitlines())
        )
        load_script().require_completable(
            task,
            {
                "acceptance_met": True,
                "review_rounds": 0,
                "usage": "unknown",
                "checks": [{"command": "uv run pytest  -q", "outcome": "passed"}],
            },
        )
