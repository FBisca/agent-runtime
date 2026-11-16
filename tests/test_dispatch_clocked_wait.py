"""The dispatch wait on an injected clock: a wait that runs to its deadline in no real seconds,
and the turn evidence that turns a quiet worker into attention."""

import json
import os
import sys
import time
from types import SimpleNamespace
from unittest import mock

from test_mcp_server import RUNTIME_SRC
from test_operations import FakeHc
from tools_harness import hc_in_process, raw

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import dispatch_wait, hc_tools, mcp_server

pytestmark = pytest.mark.integration


class ClockedWaitTests(FakeHc):
    """agent-runtime/R63: how a lead's `wait` reads a worker's turns, with the wait's clock injected.

    The server's call path runs in this process with the fake hc answered in process, and the wait's monotonic clock
    and listener are replaced: the seconds a wait would sit in its listener pass on the injected clock at once, so a
    `timeout` the test asks for costs no real time. What the wait answers is the signal the code produced; the real
    server over stdio is held by the wait tests of test_event_wait_batches and by DispatchTests."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        test = self

        class InjectedListener(dispatch_wait.Listener):
            def wait(
                self, seconds
            ):  # nothing publishes in process: the time a real wait takes just passes
                test.now += seconds

        self.enterContext(
            mock.patch.object(dispatch_wait, "time", SimpleNamespace(monotonic=lambda: test.now))
        )
        self.enterContext(mock.patch.object(dispatch_wait, "Listener", InjectedListener))
        self.session = {}
        self.env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "CODEX_THREAD_ID": "l",
            "HC_SCOPE": "lead-token",
            "HC_TERMINAL_SOCKET": str(self.socket_path),
            "AGENT_RUNTIME_DISPATCH": "1",
        }

    def lead(self, replies):
        """A lead whose fake hc answers `replies`, with the one worker w1 spawned; returns its assignment."""
        self.replies = dict(replies, **{"get:t0": {"out": raw("lead", None)}})
        self.install(self.replies)
        self.enterContext(hc_in_process(self.dir / "bin" / "hc"))
        result, text = self.call_tool(
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(result["isError"], text)
        return json.loads(text)

    def change(self, **replies):
        self.replies.update(replies)
        (self.dir / "replies").write_text(json.dumps(self.replies))

    def call_tool(self, tool, arguments):
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            result = mcp_server.call_tool(tool, arguments, self.env, hc_tools.Context(self.session))
        return result, result["content"][0]["text"]

    def wait(self, timeout):
        started = self.now
        result, text = self.call_tool("wait", {"target": "all", "timeout": timeout})
        self.assertFalse(result["isError"], text)
        return result["structuredContent"], self.now - started

    @staticmethod
    def terminals(turn, state="done"):
        row = {
            "address": "t9",
            "name": "w1",
            "parent": "t0",
            "turn": turn,
            "status": {"state": state},
        }
        return {"out": json.dumps({"terminals": [row]})}

    @staticmethod
    def worker(turn, state="idle"):
        row = json.loads(raw("w1", "t0", state))
        row["turn"] = turn
        return {"out": json.dumps(row)}

    def test_S7_a_wait_with_nothing_to_report_answers_timeout_when_the_injected_clock_reaches_it(
        self,
    ):
        """agent-runtime/R63"""
        real = time.monotonic()
        self.lead({"get:w1": self.worker(0), "ls": self.terminals(0, "idle")})
        batch, elapsed = self.wait(30)
        self.assertEqual(batch["reason"], "timeout")
        self.assertGreaterEqual(
            elapsed, 30, "the wait did not run to its timeout on the injected clock"
        )
        self.assertTrue(
            self.runs("ls"), "the wait never looked at the terminals it was waiting for"
        )
        self.assertLess(time.monotonic() - real, 10, "the 30 seconds were waited in real time")

    def test_fresh_spawn_idle_before_first_turn_is_not_attention(self):
        """agent-runtime/R63"""
        self.lead({"get:w1": self.worker(0), "ls": self.terminals(0, "idle")})
        batch, _ = self.wait(1)
        self.assertEqual(batch["reason"], "timeout")  # CLI still starting up
        self.assertTrue(self.runs("ls"), "the wait never looked at the terminal")
        self.change(ls=self.terminals(1))
        batch, _ = self.wait(2)
        self.assertEqual(batch["reason"], "attention")  # first turn ended, no report

    def test_resume_then_immediate_wait_ignores_the_old_done_turn(self):
        """agent-runtime/R63"""
        self.lead({"get:w1": self.worker(1, "idle"), "ls": self.terminals(1)})
        result, text = self.call_tool("resume", {"name": "w1", "task": "again"})
        self.assertFalse(result["isError"], text)
        batch, _ = self.wait(1)
        self.assertEqual(batch["reason"], "timeout")  # still the previous turn's done
        self.assertTrue(self.runs("ls"), "the wait never looked at the terminal")
        self.change(ls=self.terminals(2))
        batch, _ = self.wait(2)
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(len(batch["unfinished"]), 1)

    def test_resume_of_a_mid_turn_worker_ignores_the_old_turn_ending(self):
        """agent-runtime/R63"""
        self.lead({"get:w1": self.worker(3, "working"), "ls": self.terminals(3)})
        result, text = self.call_tool(
            "resume", {"name": "w1", "task": "again"}
        )  # recorded: working, turn 3
        self.assertFalse(result["isError"], text)
        self.change(ls=self.terminals(4))  # the turn that was already running ends
        batch, _ = self.wait(1)
        self.assertEqual(batch["reason"], "timeout")
        self.change(ls=self.terminals(5))  # the resumed turn ends
        batch, _ = self.wait(2)
        self.assertEqual(batch["reason"], "attention")
