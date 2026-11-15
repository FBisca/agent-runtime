"""A lead that has not read its reports cannot change its team: the server refuses every mutating verb."""

import os
from unittest import mock

import pytest
import test_event_wait as event_helpers
from agent_runtime import hc_tools
from agent_runtime.dispatch_store import Store
from tools_harness import raw

pytestmark = pytest.mark.integration


class RhythmAuthorityTests(event_helpers.EventRig):
    VERBS = (
        ("spawn", {"kind": "worker", "name": "w3", "task": "work", "cwd": "/tmp"}),
        ("message", {"to": "w1", "text": "hi"}),
        ("resume", {"name": "w1", "task": "again"}),
        ("stop", {"name": "w1"}),
    )

    def answer(self, name, arguments):
        return self.rpc.get(self.rpc.tool(name, arguments))["result"]

    def refused(self, name, arguments):
        result = self.answer(name, arguments)
        self.assertTrue(result["isError"], result)
        return result["content"][0]["text"]

    def test_S6_lead_with_undelivered_reports_is_refused_every_mutating_verb(self):
        self.prepare()
        self.publish(0)  # one report the lead has not read
        started = self.runs("run")
        for name, arguments in self.VERBS:
            with self.subTest(verb=name):
                text = self.refused(name, arguments)
                self.assertIn("1 undelivered report", text)
                self.assertIn("wait(all)", text)
        self.assertEqual(self.runs("run"), started)  # no worker starts
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])  # nor is one stopped

    def test_S6_wait_all_delivers_and_the_verbs_are_allowed_again(self):
        self.prepare()
        self.publish(0)
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual([message["kind"] for message in batch["messages"]], ["complete"])
        # The receipt acknowledges on the next read: wait(all) is the way out the refusal names.
        self.batch(
            self.rpc,
            self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": batch["receipt"]}),
        )
        sent = self.answer("message", {"to": "w1", "text": "hi"})
        self.assertFalse(sent["isError"], sent)


class ForcedLeadTests(event_helpers.HcCase):
    """The dispatch loop calls through `agent-runtime call --lead`; a session a nudge reaches has no such path."""

    def setUp(self):
        super().setUp()
        self.env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "HC_SCOPE": "authority",
            "AGENT_RUNTIME_DISPATCH": "1",
            "CODEX_THREAD_ID": "authority-lead",
        }
        # The identity lookup runs the hc binary with the process PATH, not the call's env.
        patched = mock.patch.dict(os.environ, {"PATH": self.env["PATH"]})
        patched.start()
        self.addCleanup(patched.stop)
        self.install_hc({"get:w1": {"out": raw("w1", "t0")}}, self.env)
        self.store = Store(self.env)
        scope, generation = hc_tools.dispatch_identity(self.env)[:2]
        self.team = self.store.team(scope, "t0", generation)
        worker = self.store.register(self.team, "w1")
        self.store.bind(worker["assignment_id"], "t9", scope)
        self.report = self.store.publish(
            scope, "t9", "t0", worker["assignment_id"], "done", "complete"
        )

    def message(self):
        out = hc_tools.call("message", {"to": "w1", "text": "hi"}, self.env, hc_tools.Context())
        return out, out["content"][0]["text"]

    def test_S6_the_dispatch_loops_forced_lead_call_is_not_refused_and_the_report_is_kept(self):
        """S6: the driver's `agent-runtime call --lead` path is not a nudge's target; its loop wait reads the
        report, so the server must not turn its routine race into a parked unit. An in-host lead cannot take
        this path: the ancestry gate refuses an LLM-run `agent-runtime call`
        (test_ancestry_S1_claude_or_codex_below_the_server_is_refused_naming_pid_and_name) and the CLI's
        live-lead gate refuses another generation of a terminal whose lead holds an unacknowledged report
        (test_S7_other_generation_is_refused_while_the_live_one_has_an_unacknowledged_report)."""
        result, text = self.message()
        self.assertTrue(result["isError"], "a nudge-reachable session was not refused")
        self.assertIn("1 undelivered report", text)
        with mock.patch.object(hc_tools.lib, "FORCED_LEAD", True):
            result, text = self.message()
        self.assertFalse(result["isError"], text)
        # Left unacknowledged: the dispatch loop's own wait page is what reads it.
        with self.store.transaction() as db:
            acked = db.execute(
                "SELECT acked FROM messages WHERE id=?", (self.report["message_id"],)
            ).fetchone()[0]
        self.assertEqual(acked, 0)
