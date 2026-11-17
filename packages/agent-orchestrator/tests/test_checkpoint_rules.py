"""Pure `checkpoint()` rules over a dict ledger: no check outcome is held against COMPLETE."""

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
            "checks": checks,
        }
        base.update(overrides)
        return base

    def passed(self, command):
        return {"command": command, "outcome": "passed"}

    @pytest.mark.spec("A checkpoint needs its checkout")
    def test_failed_and_unrun_checks_do_not_refuse_complete(self):
        """CI judges the tests: the driver compares no reported command with the brief."""
        for checks in (
            [],
            [{"command": "bash scripts/checks.sh", "outcome": "passed"}],
            [{"command": "scripts/checks.sh", "outcome": "failed", "reason": "flaky"}],
            [{"command": "scripts/checks.sh", "outcome": "not_run", "reason": "no test"}],
        ):
            with self.subTest(checks=checks):
                self.data["tasks"]["validation"]["status"] = "ACTIVE"
                done = app.checkpoint(self.data, "validation", self.report(checks))
                self.assertEqual(done["status"], "COMPLETE")

    @pytest.mark.spec("A checkpoint needs its checkout")
    def test_a_partial_checkpoint_raises_when_the_checkout_is_missing(self):
        self.snapshot.side_effect = FileNotFoundError("/nonexistent")
        with self.assertRaises(FileNotFoundError):
            app.checkpoint(self.data, "validation", self.report([], status="PARTIAL"))
