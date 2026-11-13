"""Diagnostics (status, collect, list) survive store and identity failures; authorization failures stay errors."""

import json
import sqlite3

import pytest
import test_event_wait as event_helpers
from agent_runtime.dispatch_store import Store
from test_event_wait import LazySessions
from test_mcp_server import InProcessSession, Session
from test_tools import InProcessToolCase

pytestmark = pytest.mark.integration


SCREEN = "screen says finished"


class InProcessRPC:
    """The lead's tool calls answered in process: what `AsyncRPC` gives a test, with no server behind it."""

    def __init__(self, session):
        self.session, self.replies, self.sequence = session, {}, 0

    def tool(self, name, arguments):
        self.sequence += 1
        self.replies[self.sequence] = self.session.send(
            "tools/call", {"name": name, "arguments": arguments}
        )
        return self.sequence

    def get(self, request_id):
        return self.replies.pop(request_id)


class Rig(InProcessToolCase):
    """A lead with two workers registered through its tools, every session in process: diagnostics prove what
    the tools decide, so the server process is left to the boundary tests."""

    # The helpers come through the module: binding the class here would collect it a second time in
    # this file, and the whole class would run twice in every parallel run.
    spawn = event_helpers.EventWaitTests.spawn
    publish = event_helpers.EventWaitTests.publish

    def prepare(self):
        def terminal(name, address, parent="t0", state="working"):
            return {"name": name, "address": address, "parent": parent, "status": state}

        self.rows = [terminal("w1", "t1"), terminal("w2", "t2"), terminal("child", "t3", "t1")]
        self.replies = {
            "get:t0": {"out": json.dumps(terminal("lead", "t0", None))},
            "ls": {"out": json.dumps({"terminals": self.rows})},
        }
        for row in self.rows:
            for key in (row["name"], row["address"]):
                self.replies["get:" + key] = {"out": json.dumps(row)}
        self.env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_SCOPE": "events",
            "CODEX_THREAD_ID": "event-lead",
            "HC_TERMINAL_ID": "t0",
            "AGENT_RUNTIME_DISPATCH": "1",
        }
        self.lead = self.start(self.replies, self.env)
        self.assignments = [self.spawn(self.lead, name) for name in ("w1", "w2")]
        self.workers = LazySessions(self.worker_session)
        self.rpc = InProcessRPC(self.lead)

    def worker_session(self, index):
        address = ("t1", "t2")[index]
        session = InProcessSession(
            dict(self.env, HC_TERMINAL_ID=address, CODEX_THREAD_ID="worker-" + address)
        )
        session.handshake()
        return session


class DiagnosticTests(Rig):
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
        for name, arguments in (
            ("status", {"name": "w1"}),
            ("collect", {"name": "w1"}),
            ("list", {}),
        ):
            reply = caller(name, arguments)
            payload = self.assert_degraded(reply)
            if name == "collect":
                self.assertIn(SCREEN, payload["result"])
            if name == "status":
                self.assertEqual(payload["name"], "w1")

    def test_store_corruption_degrades_status_collect_and_list(self):
        """agent-runtime/R70"""
        Store(self.env).path.write_bytes(b"not a database")
        self.all_three(self.lead_call)

    def test_store_schema_or_ownership_errors_degrade_status_collect_and_list(self):
        """agent-runtime/R70"""
        with sqlite3.connect(Store(self.env).path) as db:
            db.execute("PRAGMA user_version=99")
        self.all_three(self.lead_call)

    def test_unresolvable_identity_degrades_status_collect_and_list(self):
        """agent-runtime/R70"""
        env = {
            k: v for k, v in self.env.items() if k not in ("CODEX_THREAD_ID", "CLAUDE_SESSION_ID")
        }
        env["PATH"] = str(
            self.dir / "bin"
        )  # no ps: the CLI ancestry (and so the session generation) is invisible
        # S13: the process ancestry is the boundary here, so this one keeps the server process
        session = Session(env)
        self.addCleanup(session.close)
        session.handshake()
        self.all_three(lambda name, arguments: self.call(session, name, arguments)[0])

    def test_hc_role_lookup_failure_degrades_status_collect_and_list(self):
        """agent-runtime/R70"""
        self.replies["get:t0"] = {"rc": 1, "out": "", "err": "timed out"}
        self.save()
        session = InProcessSession(dict(self.env, CODEX_THREAD_ID="other"))
        session.handshake()
        self.all_three(lambda name, arguments: self.call(session, name, arguments)[0])

    def test_forged_or_unproven_callers_get_authorization_errors_and_no_hc_data(self):
        """agent-runtime/R71"""
        other = self.assignments[1]["assignment_id"]
        for arguments in ({}, {"assignment_id": other}, {"assignment_id": "forged"}):
            for name, extra in (
                ("status", {"name": "w1"}),
                ("collect", {"name": "w1"}),
                ("list", {}),
            ):
                reply, text = self.call(self.workers[0], name, dict(extra, **arguments))
                self.assertTrue(reply["isError"], (name, arguments, text))
                self.assertNotIn("delivery_error", text)
                self.assertNotIn(SCREEN, text)
                self.assertNotIn("inbox", text)
        self.assertEqual(self.runs("read"), [])
        with sqlite3.connect(Store(self.env).path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM callers").fetchone()[0], 0)

    def test_degraded_diagnostics_never_reach_terminals_that_are_not_the_callers_children(self):
        """agent-runtime/R71"""
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
            lead_foreign = self.lead_call(
                name, {"name": "child"}
            )  # grandchild, not the lead's child
            self.assertTrue(lead_foreign["isError"], lead_foreign)
        listed, _ = self.call(self.workers[1], "list", {})  # w2 is nobody's parent
        self.assertEqual(self.assert_degraded(listed)["result"], [])
        self.assertEqual(self.runs("read"), [r for r in self.runs("read") if r[1] == "child"])


class ReceiptGatedInboxTests(Rig):
    def read_calls(self):
        return (
            ("status", {"name": "w1"}),
            ("list", {}),
            ("collect", {"name": "w1"}),
            ("task", {"action": "list"}),
        )

    def unacked(self):
        with sqlite3.connect(Store(self.env).path) as db:
            return (
                db.execute("SELECT COUNT(*) FROM messages WHERE acked=0").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0],
                db.execute("SELECT COUNT(*) FROM callers").fetchone()[0],
            )

    def test_receipt_free_reads_carry_no_inbox_and_acknowledge_nothing(self):
        """agent-runtime/R83"""
        self.prepare()
        self.publish(0, kind="progress", text="pending for lead")
        before = self.unacked()
        for name, arguments in self.read_calls():
            reply = self.rpc.get(self.rpc.tool(name, arguments))["result"]
            self.assertFalse(reply["isError"], reply)
            self.assertNotIn("inbox", reply["content"][0]["text"])
        proof = {"assignment_id": self.assignments[1]["assignment_id"]}
        for (
            name,
            arguments,
        ) in self.read_calls():  # a worker's first-contact reads write no pin either
            reply, text = self.call(self.workers[1], name, dict(arguments, **proof))
            self.assertFalse(reply["isError"], text)
            self.assertNotIn("inbox", text)
        messages, receipts, pins = self.unacked()
        self.assertEqual((messages, receipts), before[:2])
        self.assertEqual(
            pins, before[2] + 1
        )  # first contact still pins the worker's session (once)

    def test_first_contact_receipt_free_read_still_pins_the_worker(self):
        """agent-runtime/R83"""
        self.prepare()
        proof = {"assignment_id": self.assignments[1]["assignment_id"]}
        for name, arguments in (
            ("task", {"action": "list"}),
            ("status", {"name": "w2"}),
            ("list", {}),
            ("collect", {"name": "w2"}),
        ):
            if name != "task":  # fresh session per read tool: a new generation has no pin yet
                session = Session(
                    dict(self.env, HC_TERMINAL_ID="t2", CODEX_THREAD_ID="fresh-" + name),
                    new_cli=True,
                )
                self.addCleanup(session.close)
                session.handshake()
            else:
                session = self.workers[1]
            reply, text = self.call(session, name, dict(arguments, **proof))
            self.assertFalse(reply["isError"], text)
            bare, text = self.call(session, "wait", {"target": "inbox", "timeout": 0})
            self.assertFalse(bare["isError"], text)

    def test_valid_receipt_returns_pending_inbox_as_before(self):
        """agent-runtime/R83"""
        self.prepare()
        self.publish(0, kind="progress", text="pending for lead")
        batch = self.rpc.get(self.rpc.tool("wait", {"target": "any", "timeout": 0}))["result"][
            "structuredContent"
        ]
        self.publish(0, kind="progress", text="second")
        for name, arguments in self.read_calls():
            reply = self.rpc.get(self.rpc.tool(name, dict(arguments, receipt=batch["receipt"])))[
                "result"
            ]
            self.assertFalse(reply["isError"], reply)
            payload = reply["structuredContent"]
            self.assertEqual([m["text"] for m in payload["inbox"]["messages"]], ["second"], name)
            batch = {"receipt": payload["inbox"]["receipt"]}
            self.publish(0, kind="progress", text="second")
