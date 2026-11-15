"""Outcome oracle for the pi-lead-rhythm plan.

The rhythm predicate is one policy in the runtime package: the Claude and Codex Stop payloads and the
Pi dialect answer one store state with one reason. It drives the documented entry point,
`bin/agent-runtime-hook stop`, through `hook_harness.FakeCliProcess`, so successive runs share one
generation and the stop-guard budget behaves as it does in a session.

The control tests hold today and must keep holding: they prove the seeded state is really blocking and
the fixture is real, so a failing case fails because of the defect, not the harness.
"""

from pathlib import Path

import pytest
from agent_runtime import dispatch_store
from hook_harness import FakeCliProcess
from hook_helpers import HookStoreCase
from test_hooks_stop_guard import CodexHookTests

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / "bin" / "agent-runtime-hook"
CLAUDE_STOP = {"hook_event_name": "Stop", "stop_hook_active": False}


class RhythmDialectTests(HookStoreCase):
    """One fake harness process, one lead team, one unseen worker: the state the guard blocks on."""

    def setUp(self):
        super().setUp()
        self.cli = FakeCliProcess(self.env, self.root)
        self.addCleanup(self.cli.close)
        self.store = dispatch_store.Store(self.env)
        self.team_id = self.store.team("scope", "t0", self.cli.generation)
        self.worker(self.store, self.team_id)

    def shim(self, *args, payload=CLAUDE_STOP):
        """The documented entry point, as a host would run it."""
        out, err, code = self.cli.run([str(SHIM)], payload, args=("stop", *args))
        assert code == 0, err
        return out

    def test_control_the_seeded_state_blocks_the_claude_shape(self):
        answer = self.shim("--cli", "claude")
        assert answer is not None, "the seeded unseen worker must block"
        assert answer["decision"] == "block", answer
        assert "call wait(all)" in answer["reason"], answer

    def test_control_the_codex_payload_changes_nothing_about_the_block(self):
        answer = self.shim("--cli", "codex", payload=CodexHookTests.STOP)
        assert answer == self.shim("--cli", "claude"), "the two payload shapes answer alike"

    def test_S1_the_three_dialects_refuse_the_same_state_with_one_reason(self):
        claude = self.shim("--cli", "claude")
        codex = self.shim("--cli", "codex", payload=CodexHookTests.STOP)
        pi = self.shim("--cli", "pi", "--dialect", "json")
        assert pi is not None, "the Pi dialect must answer"
        assert pi.get("block") is True, pi
        assert "decision" not in pi
        assert claude["reason"] == codex["reason"] == pi["reason"]

    def test_S2_without_the_dialect_the_harness_output_is_unchanged(self):
        answer = self.shim("--cli", "codex")
        assert sorted(answer) == ["decision", "reason"], answer

    def test_S10_a_budget_spent_under_one_host_is_spent_for_the_next(self):
        spent = [self.shim("--cli", host) for host in ("claude", "codex", "claude")]
        assert all(answer is not None for answer in spent), "three blocks fit one work period"
        assert self.shim("--cli", "pi") is None, "a fourth block is not asked for"
