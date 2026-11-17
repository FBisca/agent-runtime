"""The rhythm policy in process: one capability row per harness and the stop predicate hooks format.

Pure logic: no subprocess, no server, no store. The boundary tests that drive the hook entry live in
`tests/test_hooks_stop_guard.py`.
"""

import sys
import unittest
from pathlib import Path

RUNTIME_SRC = Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))
import pytest  # noqa: E402
from agent_runtime import agent_runtime as app  # noqa: E402
from agent_runtime import rhythm  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.mark.spec("Harness rhythm capabilities")
class CapabilityTests(unittest.TestCase):
    def test_S9_every_harness_the_runtime_resolves_has_a_rhythm_row(self):
        """one table, one row per resolvable harness."""
        self.assertLessEqual(set(app.HARNESSES), set(rhythm.CAPABILITIES))
        for cli in app.HARNESSES:
            row = rhythm.capabilities(cli)
            self.assertTrue(row.can_refuse_stop, cli)
            self.assertEqual(row.request_cap, rhythm.REQUEST_CAP, cli)
        self.assertTrue(rhythm.capabilities("pi").can_push_wake)
        self.assertFalse(rhythm.capabilities("claude").can_push_wake)
        self.assertFalse(rhythm.capabilities("codex").can_push_wake)
        self.assertTrue(rhythm.capabilities("claude").can_park_wait)
        self.assertFalse(rhythm.capabilities("codex").can_park_wait)
        with self.assertRaises(KeyError):
            rhythm.capabilities("gemini")

    def test_S9_the_runtime_reports_the_row_of_the_harness_it_resolves(self):
        """the harness `resolve` names reports its row from the one table."""
        for cli in app.HARNESSES:
            resolved = app.resolve({}, cli)
            self.assertEqual(resolved["cli"], cli)
            row = rhythm.capabilities(resolved["cli"])
            self.assertEqual(row, rhythm.CAPABILITIES[cli], cli)
        # a resolution with no flag also lands on a row, never on a gap: the harnesses the `--cli` choices
        # offer are exactly the rows, so `resolve` cannot name one the table lacks
        for choice in app.CLI_CHOICES:
            self.assertIn(choice, rhythm.CAPABILITIES)


class StopPredicateTests(unittest.TestCase):
    """The one predicate the Claude and Codex hooks and the Pi dialect share."""

    def needs(self, **overrides):
        state = {"undelivered": 0, "working": 0, "unfinished": 0, "seen": False, "attention": 0}
        state.update(overrides)
        return state

    def test_the_refusal_reason_is_the_wait_instruction(self):
        self.assertEqual(
            rhythm.stop_refusal(self.needs(undelivered=2, working=1, unfinished=4)),
            "agent-runtime: 4 unfinished, 2 undelivered — call wait(all) before ending the turn",
        )

    def test_undelivered_messages_always_block(self):
        self.assertIsNotNone(rhythm.stop_refusal(self.needs(undelivered=1)))

    def test_working_work_the_lead_has_seen_does_not_block(self):
        self.assertIsNone(rhythm.stop_refusal(self.needs(working=1, unfinished=1, seen=True)))
        self.assertIsNotNone(rhythm.stop_refusal(self.needs(working=1, unfinished=1)))

    def test_no_team_has_nothing_to_block_on(self):
        self.assertIsNone(rhythm.stop_refusal(None))
