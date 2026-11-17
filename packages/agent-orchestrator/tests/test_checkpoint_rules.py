"""Pure `checkpoint()` COMPLETE rules over a dict ledger: check-name normalisation and `not_run` handling."""

import unittest
from typing import Any
from unittest import mock

import pytest
from agent_orchestrator import driver as app

pytestmark = pytest.mark.unit


class CheckpointRulesTests(unittest.TestCase):
    def setUp(self):
        # checkout_snapshot reads git; these rules are about the ledger, not the tree.
        patcher = mock.patch.object(app, "checkout_snapshot", return_value={"head": "abc"})
        self.snapshot = patcher.start()
        self.addCleanup(patcher.stop)
        self.packet = {
            "task_id": "validation",
            "worker": "api",
            "checkout": "/nonexistent",
            "checks": ["scripts/checks.sh"],
        }
        self.data: dict[str, Any] = {
            "version": 1,
            "tasks": {
                "validation": {
                    "status": "ACTIVE",
                    "runtime_id": "child",
                    "packet": self.packet,
                }
            },
        }

    def report(self, checks, **overrides):
        base = {
            "task_id": "validation",
            "status": "COMPLETE",
            "summary": "Implemented validation",
            "files": ["src/validation/input.py"],
            "acceptance_met": True,
            "decisions": [],
            "interfaces": [],
            "remaining": [],
            "checks": checks,
        }
        base.update(overrides)
        return base

    def passed(self, command):
        return {"command": command, "outcome": "passed"}

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S1_the_required_spelling_matches_whitespace_apart(self):
        for command in ("scripts/checks.sh", "  scripts/checks.sh ", "scripts/checks.sh\t"):
            with self.subTest(command=command):
                done = app.checkpoint(self.data, "validation", self.report([self.passed(command)]))
                self.assertEqual(done["status"], "COMPLETE")
                self.data["tasks"]["validation"]["status"] = "ACTIVE"

    @pytest.mark.spec("COMPLETE needs the required checks")
    @pytest.mark.spec("Required checks are matched by spelling")
    def test_S1a_a_launcher_around_the_required_check_is_another_command(self):
        # The driver works in any repository behind any shell: it strips no launcher it would have to know.
        for required, reported in (
            ("scripts/checks.sh", "bash scripts/checks.sh"),
            ("scripts/checks.sh", "./scripts/checks.sh"),
            ("pytest", "uv run python -m pytest"),
            ("uv run pytest -q", "rtk proxy uv run pytest -q"),
        ):
            with self.subTest(reported=reported):
                self.packet["checks"] = [required]
                with self.assertRaises(ValueError) as caught:
                    app.checkpoint(self.data, "validation", self.report([self.passed(reported)]))
                self.assertIn(required, str(caught.exception))
                self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    @pytest.mark.spec("Required checks are matched by spelling")
    def test_S4_the_contract_states_the_spelling_rule_and_names_no_launcher(self):
        self.assertIn("spelled as the task gives it", app.CONTRACT)
        for launcher in ("rtk", "uv run", "python -m", "`bash`", "--durations"):
            with self.subTest(launcher=launcher):
                self.assertNotIn(launcher, app.CONTRACT)

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S2_another_or_an_absent_check_is_refused(self):
        for checks in (
            [self.passed("echo scripts/checks.sh")],
            [self.passed("scripts/checks.sh --fast")],
            [],
        ):
            with self.subTest(checks=checks):
                with self.assertRaises(ValueError) as caught:
                    app.checkpoint(self.data, "validation", self.report(checks))
                self.assertIn("scripts/checks.sh", str(caught.exception))
                self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S3_not_run_with_reason_on_a_non_required_check_is_accepted(self):
        checks = [
            self.passed("scripts/checks.sh"),
            {"command": "slow-benchmark", "outcome": "not_run", "reason": "no GPU here"},
        ]
        done = app.checkpoint(self.data, "validation", self.report(checks))
        self.assertEqual(done["status"], "COMPLETE")

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S3a_not_run_without_reason_is_refused(self):
        checks = [
            self.passed("scripts/checks.sh"),
            {"command": "slow-benchmark", "outcome": "not_run"},
        ]
        with self.assertRaises(ValueError):
            app.checkpoint(self.data, "validation", self.report(checks))
        self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S3b_required_check_reported_not_run_is_refused(self):
        checks = [
            {"command": "scripts/checks.sh", "outcome": "not_run", "reason": "skipped"},
        ]
        with self.assertRaises(ValueError) as caught:
            app.checkpoint(self.data, "validation", self.report(checks))
        self.assertIn("scripts/checks.sh", str(caught.exception))

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S4_any_failed_check_refuses_complete(self):
        checks = [
            self.passed("scripts/checks.sh"),
            {"command": "slow-benchmark", "outcome": "failed", "reason": "timeout"},
        ]
        with self.assertRaises(ValueError):
            app.checkpoint(self.data, "validation", self.report(checks))
        self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    @pytest.mark.spec("COMPLETE needs the required checks")
    def test_S8a_a_partial_checkpoint_raises_when_the_checkout_is_missing(self):
        self.snapshot.side_effect = FileNotFoundError("/nonexistent")
        with self.assertRaises(FileNotFoundError):
            app.checkpoint(
                self.data,
                "validation",
                self.report([], status="PARTIAL", remaining=["more work"]),
            )

    @pytest.mark.spec("Required checks are matched by spelling")
    def test_contract_states_the_check_rules_and_normalisation(self):
        self.assertIn("not_run", app.CONTRACT)
        self.assertIn("reason", app.CONTRACT)
        self.assertIn("required", app.CONTRACT)
        self.assertIn("whitespace apart", app.CONTRACT)
