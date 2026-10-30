"""Concurrent dispatch, peer inboxes and receipt transactions over fake-hc MCP."""
import json
import threading
import time

from test_event_wait import AsyncRPC, Session
import test_event_wait as event_helpers
from test_tools import ToolCase
from dispatch_events import endpoints
from dispatch_store import Store


class PeerDispatchTests(ToolCase):
    prepare = event_helpers.EventWaitTests.prepare
    spawn = event_helpers.EventWaitTests.spawn
    publish = event_helpers.EventWaitTests.publish
    batch = event_helpers.EventWaitTests.batch
    pending = event_helpers.EventWaitTests.pending
    begin = event_helpers.EventWaitTests.begin

    def tool(self, tool_name, **arguments):
        result = self.rpc.get(self.rpc.tool(tool_name, arguments))["result"]
        self.assertFalse(result["isError"], result)
        return result

    def test_mixed_workers_resume_promptly_with_fresh_ids_and_preserve_failed_dispatch(self):
        """agent-runtime/R45"""
        self.prepare()
        self.assignments[1] = self.tool("spawn", kind="worker", cli="codex", name="w2",
                                        task="codex work", cwd="/tmp")["structuredContent"]
        started = time.monotonic()
        resumed = [self.tool("resume", name=name, task="continue")["structuredContent"] for name in ("w1", "w2")]
        self.assertLess(time.monotonic() - started, 1)
        for before, after in zip(self.assignments, resumed):
            self.assertNotEqual(before["assignment_id"], after["assignment_id"])
            self.assertEqual(before["worker_id"], after["worker_id"])
        for argv, identity in zip(self.runs("send"), resumed):
            self.assertNotIn("--wait", argv)
            self.assertNotIn("--force", argv)
            self.assertIn(identity["assignment_id"], argv[-1])
            self.assertIn("first runtime call", argv[-1])
        for argv in self.runs("run"):
            self.assertIn("first runtime call", argv[-1])
        self.assertIn("codex", self.runs("run")[-1])
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual(set(batch["unfinished"]), {a["assignment_id"] for a in resumed})
        self.replies["send"] = {"rc": 1, "err": "permission dialog"}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        failed = self.rpc.get(self.rpc.tool("resume", {"name": "w1", "task": "blocked dispatch"}))["result"]
        self.assertTrue(failed["isError"])
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w1", "timeout": 0}))
        self.assertEqual(batch["unfinished"], [resumed[0]["assignment_id"]])
        old, _ = self.call(self.workers[0], "report", {"text": "late", "kind": "complete",
                                                     "assignment_id": self.assignments[0]["assignment_id"]})
        self.assertTrue(old["isError"])

    def test_peer_idle_and_active_inboxes_deliver_true_sender_without_hc_injection(self):
        """agent-runtime/R47"""
        self.prepare()
        first, text = self.call(self.workers[0], "message", {"to": "w2", "text": "idle data",
                                                          "assignment_id": self.assignments[0]["assignment_id"]})
        self.assertFalse(first["isError"], text)
        event = first["structuredContent"]
        self.assertEqual(event["sender"], self.assignments[0]["worker_id"])
        self.assertEqual(self.runs("send"), [])
        self.assignments[1] = self.tool("resume", name="w2", task="continue work")["structuredContent"]
        resume_sends = self.runs("send")
        self.assertNotIn("idle data", resume_sends[0][-1])
        result, text = self.call(self.workers[1], "status", {"name": "w2", "assignment_id": self.assignments[1]["assignment_id"]})
        self.assertFalse(result["isError"], text)
        inbox = result["structuredContent"]["inbox"]
        self.assertEqual(inbox["messages"], [event])
        empty, text = self.call(self.workers[1], "wait", {"target": "inbox", "timeout": 0, "receipt": inbox["receipt"]})
        self.assertFalse(empty["isError"], text)
        self.assertEqual(empty["structuredContent"]["messages"], [])
        self.publish(0)  # Completed workers must still block for future inbox data.
        delivered = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.tool("status", name="w1", receipt=delivered["receipt"])
        worker_rpc = AsyncRPC(self.workers[0])
        request_id = worker_rpc.tool("wait", {"target": "any", "timeout": 3})
        directory = endpoints(Store(self.env).directory)
        deadline = time.monotonic() + 2
        while not list(directory.glob("*.sock")):
            self.assertNotIn(request_id, worker_rpc.counts, "worker inbox returned before a message")
            self.assertLess(time.monotonic(), deadline)
            threading.Event().wait(0.005)
        self.pending(worker_rpc, request_id)
        reply, text = self.call(self.workers[1], "message", {"to": "w1", "text": "peer reply"})
        self.assertFalse(reply["isError"], text)
        batch = self.batch(worker_rpc, request_id)
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["messages"], [reply["structuredContent"]])
        self.assertEqual(batch["messages"][0]["sender"], self.assignments[1]["worker_id"])
        self.assertEqual(batch["assignments"], [])
        to_lead = worker_rpc.get(worker_rpc.tool("message", {"to": "lead", "text": "lead data"}))["result"]
        self.assertFalse(to_lead["isError"])
        received = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.assertEqual(received["messages"][0]["message_id"], to_lead["structuredContent"]["message_id"])
        self.assertEqual(to_lead["structuredContent"]["inbox"]["messages"], batch["messages"])
        self.assertEqual(self.runs("send"), resume_sends)
        denied, _ = self.call(self.workers[1], "message", {"to": "foreign-worker-id", "text": "no"})
        self.assertTrue(denied["isError"])

    def test_stale_worker_session_cannot_route_into_reused_team_generation(self):
        """agent-runtime/R47"""
        self.prepare()
        old, text = self.call(self.workers[0], "message", {"to": "w2", "text": "old team",
                                                        "assignment_id": self.assignments[0]["assignment_id"]})
        self.assertFalse(old["isError"], text)
        replacement = Session(dict(self.env, CODEX_THREAD_ID="replacement-lead"))
        self.addCleanup(replacement.close)
        replacement.handshake()
        fresh = [self.spawn(replacement, name) for name in ("w1", "w2")]
        denied, _ = self.call(self.workers[0], "message", {"to": "w2", "text": "stale caller"})
        self.assertTrue(denied["isError"])
        new_worker = Session(dict(self.env, HC_TERMINAL_ID="t1", CODEX_THREAD_ID="replacement-worker"))
        self.addCleanup(new_worker.close)
        new_worker.handshake()
        sent, text = self.call(new_worker, "message", {"to": "w2", "text": "new team", "assignment_id": fresh[0]["assignment_id"]})
        self.assertFalse(sent["isError"], text)
        self.assertEqual(sent["structuredContent"]["team_id"], fresh[0]["team_id"])
        store = Store(self.env)
        received = store.read(fresh[1]["team_id"], fresh[1]["worker_id"], "any", assignment_ids=[], inbox=True)
        self.assertEqual([m["text"] for m in received["messages"]], ["new team"])

    def test_worker_can_message_its_own_child_without_crossing_peer_team(self):
        """agent-runtime/R47"""
        self.prepare()
        child = self.spawn(self.workers[0], "child", assignment_id=self.assignments[0]["assignment_id"])
        sent, text = self.call(self.workers[0], "message", {"to": "child", "text": "child data"})
        self.assertFalse(sent["isError"], text)
        self.assertEqual(sent["structuredContent"]["team_id"], child["team_id"])
        self.assertNotEqual(child["team_id"], self.assignments[0]["team_id"])
        self.assertEqual(sent["structuredContent"]["recipient"], child["worker_id"])
        self.assertEqual(self.runs("send"), [])

    def replace_team_without_contacting_old_workers(self):
        replacement = Session(dict(self.env, CODEX_THREAD_ID="replacement-lead"))
        self.addCleanup(replacement.close)
        replacement.handshake()
        return replacement, [self.spawn(replacement, name) for name in ("w1", "w2")]

    def test_first_message_requires_assignment_proof_before_generation_pin(self):
        """agent-runtime/R47"""
        self.prepare()
        _, fresh = self.replace_team_without_contacting_old_workers()
        for proof in (None, self.assignments[0]["assignment_id"]):
            with self.subTest(proof=proof):
                arguments = {"to": "w2", "text": "stale first message"}
                if proof:
                    arguments["assignment_id"] = proof
                denied, _ = self.call(self.workers[0], "message", arguments)
                self.assertTrue(denied["isError"])
        store = Store(self.env)
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM callers").fetchone()[0], 0)
        self.assertEqual(store.read(fresh[1]["team_id"], fresh[1]["worker_id"], "any", inbox=True)["messages"], [])

    def test_first_inbox_wait_cannot_adopt_reused_address_or_read_new_team(self):
        """agent-runtime/R47"""
        self.prepare()
        replacement, fresh = self.replace_team_without_contacting_old_workers()
        sent, text = self.call(replacement, "message", {"to": "w1", "text": "new team private data"})
        self.assertFalse(sent["isError"], text)
        for proof in (None, self.assignments[0]["assignment_id"]):
            with self.subTest(proof=proof):
                arguments = {"target": "inbox", "timeout": 0}
                if proof:
                    arguments["assignment_id"] = proof
                denied, text = self.call(self.workers[0], "wait", arguments)
                self.assertTrue(denied["isError"], text)
        new_env = dict(self.env, HC_TERMINAL_ID="t1", CODEX_THREAD_ID="replacement-worker")
        for restart in (False, True):
            worker = Session(new_env)
            worker.handshake()
            try:
                arguments = {"target": "inbox", "timeout": 0}
                if not restart:
                    arguments["assignment_id"] = fresh[0]["assignment_id"]
                received, text = self.call(worker, "wait", arguments)
                self.assertFalse(received["isError"], text)
                self.assertEqual(received["structuredContent"]["messages"], [sent["structuredContent"]])
            finally:
                worker.close()

    def test_failed_pending_report_cannot_establish_first_use_proof(self):
        """agent-runtime/R47"""
        self.prepare()
        store = Store(self.env)
        team = self.assignments[0]["team_id"]
        old_pending = store.register(team, "w1")
        store.register(team, "w1")
        denied, _ = self.call(self.workers[0], "report", {"assignment_id": old_pending["assignment_id"],
                                                        "kind": "complete", "text": "stale pending"})
        self.assertTrue(denied["isError"])
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM callers").fetchone()[0], 0)
        denied, _ = self.call(self.workers[0], "message", {"to": "w2", "text": "unproven"})
        self.assertTrue(denied["isError"])

    def test_successful_fast_report_proves_session_in_publication_transaction(self):
        """agent-runtime/R47"""
        self.prepare()
        store = Store(self.env)
        pending = store.register(self.assignments[0]["team_id"], "w1")
        result, text = self.call(self.workers[0], "report", {"assignment_id": pending["assignment_id"],
                                                          "kind": "progress", "text": "fast report"})
        self.assertFalse(result["isError"], text)
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM callers").fetchone()[0], 1)
        result, text = self.call(self.workers[0], "message", {"to": "w2", "text": "proof retained"})
        self.assertFalse(result["isError"], text)
        self.assertEqual(result["structuredContent"]["assignment_id"], pending["assignment_id"])

    def test_piggyback_ack_is_atomic_and_invalid_or_foreign_receipts_do_not_act(self):
        """agent-runtime/R52"""
        self.prepare()
        self.publish(0, "progress", "first")
        first = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.tool("status", name="w1", receipt=first["receipt"])
        self.assertEqual(self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))["messages"], [])
        progress = self.publish(0, "progress", "second")
        second = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.tool("message", to="w1", text="original", message_id="retry")
        conflict = self.rpc.get(self.rpc.tool("message", {"to": "w1", "text": "changed", "message_id": "retry",
                                                         "receipt": second["receipt"]}))["result"]
        self.assertTrue(conflict["isError"])
        self.assertEqual(self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))["messages"], [progress])
        denied, _ = self.call(self.workers[1], "message", {"to": "w1", "text": "foreign receipt", "receipt": second["receipt"],
                                                        "assignment_id": self.assignments[1]["assignment_id"]})
        self.assertTrue(denied["isError"])
        denied = self.rpc.get(self.rpc.tool("message", {"to": "w1", "text": "invalid receipt", "receipt": "invalid"}))["result"]
        self.assertTrue(denied["isError"])
        self.assertEqual(self.runs("send"), [])
        final = self.tool("message", to="w2", text="valid ack", receipt=second["receipt"])
        self.assertTrue(final["structuredContent"]["message_id"])
        self.assertEqual(self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))["messages"], [])

    def test_stop_wakes_wait_preserves_reports_and_keeps_accepted_completion(self):
        """agent-runtime/R54"""
        self.prepare()
        progress = self.publish(0, "progress")
        request_id = self.begin()
        self.tool("stop", name="w1")
        batch = self.batch(self.rpc, request_id)
        self.assertEqual(batch["reason"], "failed")
        self.assertEqual(batch["messages"], [progress])
        self.assertEqual(len(batch["unfinished"]), 2)
        replay = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w1", "timeout": 0}))
        self.assertEqual(replay["messages"], [progress])
        complete = self.publish(1)
        self.tool("stop", name="w2")
        completed = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w2", "timeout": 0}))
        self.assertEqual(completed["reason"], "completed")
        self.assertEqual(completed["messages"], [complete])
        self.assertEqual(completed["unfinished"], [])
