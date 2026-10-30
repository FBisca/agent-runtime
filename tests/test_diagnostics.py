"""Diagnostics (status, collect, list) survive store and identity failures; authorization failures stay errors."""
import json
import sqlite3

from dispatch_store import Store
from test_event_wait import EventWaitTests, Session
from test_tools import ToolCase

SCREEN = "screen says finished"


class DiagnosticTests(ToolCase):
    prepare = EventWaitTests.prepare
    spawn = EventWaitTests.spawn
    publish = EventWaitTests.publish

    def setUp(self):
        super().setUp()
        self.prepare()
        self.replies["read"] = {"out": SCREEN}
        self.save()

    def save(self):
        (self.dir / "replies").write_text(json.dumps(self.replies))

    def lead_call(self, name, arguments):
        return self.rpc.get(self.rpc.tool(name, arguments))["result"]

    def assert_degraded(self, reply):
        self.assertFalse(reply["isError"], reply)
        payload = reply["structuredContent"]
        self.assertIn("delivery_error", payload)
        self.assertNotIn("inbox", payload)
        return payload

    def all_three(self, caller):
        for name, arguments in (("status", {"name": "w1"}), ("collect", {"name": "w1"}), ("list", {})):
            reply = caller(name, arguments)
            payload = self.assert_degraded(reply)
            if name == "collect":
                self.assertIn(SCREEN, payload["result"])
            if name == "status":
                self.assertEqual(payload["name"], "w1")

    def test_S5_store_corruption_degrades_status_collect_and_list(self):
        """agent-runtime/R55"""
        Store(self.env).path.write_bytes(b"not a database")
        self.all_three(self.lead_call)

    def test_S5_store_schema_or_ownership_errors_degrade_status_collect_and_list(self):
        """agent-runtime/R55"""
        with sqlite3.connect(Store(self.env).path) as db:
            db.execute("PRAGMA user_version=99")
        self.all_three(self.lead_call)

    def test_S5_unresolvable_identity_degrades_status_collect_and_list(self):
        """agent-runtime/R55"""
        env = {k: v for k, v in self.env.items() if k not in ("CODEX_THREAD_ID", "CLAUDE_SESSION_ID")}
        env["PATH"] = str(self.dir / "bin")  # no ps: the CLI ancestry (and so the session generation) is invisible
        session = Session(env)
        self.addCleanup(session.close)
        session.handshake()
        self.all_three(lambda name, arguments: self.call(session, name, arguments)[0])

    def test_S5_hc_role_lookup_failure_degrades_status_collect_and_list(self):
        """agent-runtime/R55"""
        self.replies["get:t0"] = {"rc": 1, "out": "", "err": "timed out"}
        self.save()
        session = Session(dict(self.env, CODEX_THREAD_ID="other"))
        self.addCleanup(session.close)
        session.handshake()
        self.all_three(lambda name, arguments: self.call(session, name, arguments)[0])

    def test_S6_forged_or_unproven_callers_get_authorization_errors_and_no_hc_data(self):
        """agent-runtime/R55"""
        other = self.assignments[1]["assignment_id"]
        for arguments in ({}, {"assignment_id": other}, {"assignment_id": "forged"}):
            for name, extra in (("status", {"name": "w1"}), ("collect", {"name": "w1"}), ("list", {})):
                reply, text = self.call(self.workers[0], name, dict(extra, **arguments))
                self.assertTrue(reply["isError"], (name, arguments, text))
                self.assertNotIn("delivery_error", text)
                self.assertNotIn(SCREEN, text)
                self.assertNotIn("inbox", text)
        self.assertEqual(self.runs("read"), [])
        with sqlite3.connect(Store(self.env).path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM callers").fetchone()[0], 0)

    def test_S6_degraded_diagnostics_never_reach_terminals_that_are_not_the_callers_children(self):
        """agent-runtime/R55"""
        with sqlite3.connect(Store(self.env).path) as db:
            db.execute("PRAGMA user_version=99")
        for name in ("status", "collect"):
            # w1 is the lead's child and w2 is w1's sibling: neither belongs to worker w1's own subtree.
            foreign, text = self.call(self.workers[0], name, {"name": "w2"})
            self.assertTrue(foreign["isError"], (name, text))
            self.assertIn("not your child", text)
            self.assertNotIn(SCREEN, text)
            own, _ = self.call(self.workers[0], name, {"name": "child"})  # t3's parent is t1
            payload = self.assert_degraded(own)
            if name == "collect":
                self.assertIn(SCREEN, payload["result"])
            lead_foreign = self.lead_call(name, {"name": "child"})  # grandchild, not the lead's child
            self.assertTrue(lead_foreign["isError"], lead_foreign)
        listed, _ = self.call(self.workers[1], "list", {})  # w2 is nobody's parent
        self.assertEqual(self.assert_degraded(listed)["result"], [])
        self.assertEqual(self.runs("read"), [r for r in self.runs("read") if r[1] == "child"])
