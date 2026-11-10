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
        patcher.start()
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

    def test_S1_bash_and_dot_slash_prefixes_match_the_required_check(self):
        """orchestrator-engine/R2"""
        for command in (
            "bash scripts/checks.sh",
            "./scripts/checks.sh",
            "sh ./scripts/checks.sh",
        ):
            with self.subTest(command=command):
                done = app.checkpoint(self.data, "validation", self.report([self.passed(command)]))
                self.assertEqual(done["status"], "COMPLETE")
                self.data["tasks"]["validation"]["status"] = "ACTIVE"

    def test_S1a_uv_run_python_m_prefix_matches_pytest(self):
        """orchestrator-engine/R2"""
        self.packet["checks"] = ["pytest"]
        done = app.checkpoint(
            self.data, "validation", self.report([self.passed("uv run python -m pytest")])
        )
        self.assertEqual(done["status"], "COMPLETE")

    def test_S2_prefix_off_the_strip_list_or_absent_check_is_refused(self):
        """orchestrator-engine/R2"""
        for checks in (
            [self.passed("echo scripts/checks.sh")],
            [self.passed("python scripts/checks.sh")],
            [self.passed("run scripts/checks.sh")],
            [self.passed("-m scripts/checks.sh")],
            [self.passed("uv scripts/checks.sh")],
            [],
        ):
            with self.subTest(checks=checks):
                with self.assertRaises(ValueError) as caught:
                    app.checkpoint(self.data, "validation", self.report(checks))
                self.assertIn("scripts/checks.sh", str(caught.exception))
                self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    def test_S3_not_run_with_reason_on_a_non_required_check_is_accepted(self):
        """orchestrator-engine/R2"""
        checks = [
            self.passed("scripts/checks.sh"),
            {"command": "slow-benchmark", "outcome": "not_run", "reason": "no GPU here"},
        ]
        done = app.checkpoint(self.data, "validation", self.report(checks))
        self.assertEqual(done["status"], "COMPLETE")

    def test_S3a_not_run_without_reason_is_refused(self):
        """orchestrator-engine/R2"""
        checks = [
            self.passed("scripts/checks.sh"),
            {"command": "slow-benchmark", "outcome": "not_run"},
        ]
        with self.assertRaises(ValueError):
            app.checkpoint(self.data, "validation", self.report(checks))
        self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    def test_S3b_required_check_reported_not_run_is_refused(self):
        """orchestrator-engine/R2"""
        checks = [
            {"command": "scripts/checks.sh", "outcome": "not_run", "reason": "skipped"},
        ]
        with self.assertRaises(ValueError) as caught:
            app.checkpoint(self.data, "validation", self.report(checks))
        self.assertIn("scripts/checks.sh", str(caught.exception))

    def test_S4_any_failed_check_refuses_complete(self):
        """orchestrator-engine/R2"""
        checks = [
            self.passed("scripts/checks.sh"),
            {"command": "slow-benchmark", "outcome": "failed", "reason": "timeout"},
        ]
        with self.assertRaises(ValueError):
            app.checkpoint(self.data, "validation", self.report(checks))
        self.assertEqual(self.data["tasks"]["validation"]["status"], "ACTIVE")

    def test_contract_states_the_check_rules_and_normalisation(self):
        """orchestrator-engine/R2"""
        self.assertIn("not_run", app.CONTRACT)
        self.assertIn("reason", app.CONTRACT)
        self.assertIn("required", app.CONTRACT)
        self.assertIn("bash", app.CONTRACT)
