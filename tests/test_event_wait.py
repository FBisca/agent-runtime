"""Event waits through real MCP processes; fake hc, explicit reconciliation barriers."""
import json
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from test_mcp_server import SERVER, Session
from test_tools import ToolCase

sys.path.insert(0, str(SERVER.parent))
import hc_tools
import dispatch_wait
from dispatch_events import endpoints
from dispatch_store import Store


class AsyncRPC:
    def __init__(self, session):
        self.session, self.pending, self.counts = session, {}, {}
        self.replies = queue.Queue()
        self.sequence = 100
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.session.proc.stdout:
            try:
                response = json.loads(line)
                self.counts[response["id"]] = self.counts.get(response["id"], 0) + 1
                self.replies.put(response)
            except (ValueError, KeyError) as error:
                self.replies.put(error)

    def send(self, method, params=None, notify=False):
        self.sequence += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            message["id"] = self.sequence
        self.session.proc.stdin.write(json.dumps(message) + "\n")
        self.session.proc.stdin.flush()
        return self.sequence

    def get(self, request_id, timeout=3):
        deadline = time.monotonic() + timeout
        while request_id not in self.pending:
            response = self.replies.get(timeout=max(0, deadline - time.monotonic()))
            if isinstance(response, Exception):
                raise response
            self.pending[response["id"]] = response
        return self.pending.pop(request_id)

    def tool(self, name, arguments):
        return self.send("tools/call", {"name": name, "arguments": arguments})


class EventWaitTests(ToolCase):
    def prepare(self):
        def terminal(name, address, parent="t0", state="working"):
            return {"name": name, "address": address, "parent": parent, "status": state}
        self.rows = [terminal("w1", "t1"), terminal("w2", "t2"), terminal("child", "t3", "t1")]
        self.replies = {"get:t0": {"out": json.dumps(terminal("lead", "t0", None))},
                        "ls": {"out": json.dumps({"terminals": self.rows})}}
        for row in self.rows:
            for key in (row["name"], row["address"]):
                self.replies["get:" + key] = {"out": json.dumps(row)}
        self.env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"),
                    "HC_SCOPE": "events", "CODEX_THREAD_ID": "event-lead", "HC_TERMINAL_ID": "t0",
                    "AGENT_RUNTIME_DISPATCH": "1"}
        self.lead = self.start(self.replies, self.env)
        self.assignments = [self.spawn(self.lead, name) for name in ("w1", "w2")]
        self.workers = []
        for address in ("t1", "t2"):
            session = Session(dict(self.env, HC_TERMINAL_ID=address, CODEX_THREAD_ID="worker-" + address))
            self.addCleanup(session.close)
            session.handshake()
            self.workers.append(session)
        self.rpc = AsyncRPC(self.lead)

    def spawn(self, session, name, assignment_id=None):
        arguments = {"kind": "worker", "name": name, "task": "work", "cwd": "/tmp"}
        if assignment_id:
            arguments["assignment_id"] = assignment_id
        result, text = self.call(session, "spawn", arguments)
        self.assertFalse(result["isError"], text)
        return result["structuredContent"]

    def publish(self, index, kind="complete", text="done"):
        result, body = self.call(self.workers[index], "report", {
            "text": text, "kind": kind, "assignment_id": self.assignments[index]["assignment_id"]})
        self.assertFalse(result["isError"], body)
        return result["structuredContent"]

    def batch(self, rpc, request_id):
        result = rpc.get(request_id)["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
        return result["structuredContent"]

    def pending(self, rpc, request_id):
        self.assertEqual(rpc.get(rpc.send("ping"))["result"], {})
        with self.assertRaises(queue.Empty):
            rpc.get(request_id, timeout=0.03)

    def begin(self, rpc=None, target="all", timeout=5):
        rpc = rpc or self.rpc
        before = len(self.runs("ls"))
        request_id = rpc.tool("wait", {"target": target, "timeout": timeout})
        deadline = time.monotonic() + 2
        # Reconciliation starts only after membership snapshot and listener registration.
        while len(self.runs("ls")) == before:
            self.assertNotIn(request_id, rpc.counts, "wait returned before entering its listener")
            self.assertLess(time.monotonic(), deadline, "wait never reconciled")
            threading.Event().wait(0.005)
        self.pending(rpc, request_id)
        return request_id

    def test_S5_all_buffers_progress_and_partial_completion_with_fixed_membership(self):
        self.prepare()
        request_id = self.begin()
        store = Store(self.env)
        late = store.register(self.assignments[0]["team_id"], "later")
        store.bind(late["assignment_id"], "t4", "events")
        progress = self.publish(0, "progress", "running")
        self.pending(self.rpc, request_id)
        first = self.publish(0)
        self.pending(self.rpc, request_id)
        second = self.publish(1)
        batch = self.batch(self.rpc, request_id)
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], [progress, first, second])
        self.assertEqual(len(batch["assignments"]), 2)
        self.assertEqual(batch["unfinished"], [])

    def test_S6_one_wait_returns_all_completed_reports_without_collect(self):
        self.prepare()
        request_id = self.begin()
        messages = [self.publish(i) for i in (0, 1)]
        batch = self.batch(self.rpc, request_id)
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], messages)
        self.assertEqual(batch["unfinished"], [])
        self.assertEqual(self.runs("read"), [])
        self.pending(self.rpc, request_id)  # Exactly one final result.
        self.assertEqual(self.rpc.counts[request_id], 1)

    def test_S7_reports_and_runtime_failures_need_attention_but_idle_done_are_not_success(self):
        self.prepare()
        for state in ("idle", "done", "blocked", "interrupted", "exited", "removed"):
            with self.subTest(runtime_state=state):
                self.rows[0]["status"] = state
                self.replies["ls"]["out"] = json.dumps({"terminals": self.rows[1:] if state == "removed" else self.rows})
                tmp = self.dir / "next-replies"
                tmp.write_text(json.dumps(self.replies))
                tmp.replace(self.dir / "replies")
                request_id = self.rpc.tool("wait", {"target": "all", "timeout": 0.3})
                batch = self.batch(self.rpc, request_id)
                self.assertEqual(batch["reason"], "timeout" if state in ("idle", "done") else
                                 "attention" if state == "blocked" else "failed")
                self.assertEqual(len(batch["unfinished"]), 2)
        self.rows[0]["status"] = "working"
        self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
        (self.dir / "replies").write_text(json.dumps(self.replies))
        for kind in ("blocked", "decision"):
            self.publish(0, "progress")
            request_id = self.begin()
            event = self.publish(0, kind)
            batch = self.batch(self.rpc, request_id)
            self.assertEqual(batch["reason"], "attention")
            self.assertIn(event, batch["messages"])
            self.assertEqual(len(batch["unfinished"]), 2)
        self.publish(0)
        self.publish(1)
        self.assertEqual(self.batch(self.rpc, self.rpc.tool("wait", {"target": "all"}))["reason"], "completed")

    def test_S8_any_batches_eligible_events_and_named_wait_excludes_old_assignment(self):
        self.prepare()
        self.publish(0)
        # Reuse the name; prior unacknowledged completion must not enter this selection.
        replacement = self.rpc.tool("spawn", {"kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"})
        self.assignments[0] = self.rpc.get(replacement)["result"]["structuredContent"]
        first, second = self.publish(0), self.publish(1, "progress")
        named = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w1", "timeout": 0}))
        self.assertEqual(named["messages"], [first])
        self.assertEqual([a["assignment_id"] for a in named["assignments"]], [first["assignment_id"]])
        any_batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any"}))
        self.assertEqual(any_batch["messages"], [first, second])
        self.assertEqual(any_batch["reason"], "messages")

    def test_S9_deadline_empty_and_cancel_preserve_replay_and_workers(self):
        self.prepare()
        progress = self.publish(0, "progress")
        started = time.monotonic()
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0.2}))
        self.assertGreaterEqual(time.monotonic() - started, 0.18)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(batch["reason"], "timeout")
        self.assertEqual(batch["messages"], [progress])
        empty = self.batch(self.rpc, self.rpc.tool("wait", {"target": "absent", "timeout": 5}))
        self.assertEqual(empty["reason"], "empty")
        request_id = self.begin()
        store = Store(self.env)
        with store.transaction() as db:
            receipts = db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
        directory = endpoints(store.directory)
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for path in directory.glob("*.sock"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.rpc.send("notifications/cancelled", {"requestId": request_id}, notify=True)
        self.pending(self.rpc, request_id)
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], receipts)
        self.assertEqual(list(directory.glob("*.sock")), [])
        replay = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual(replay["messages"], [progress])
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])

    def test_S10_commit_then_process_exit_without_hint_recovers_within_bound(self):
        self.prepare()
        request_id = self.begin(target="w1")
        assignment = self.assignments[0]
        envelope = dict(assignment, message_id="lost-hint", sender=assignment["worker_id"],
                        recipient="t0", kind="complete", text="committed before crash")
        script = '''import json, os, sys
sys.path.insert(0, sys.argv[1])
from dispatch_store import Store
store = Store({"HOME": sys.argv[2]})
message = json.loads(sys.argv[3])
with store.transaction() as db:
    db.execute("INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
               (message["message_id"],message["team_id"],"t0",message["assignment_id"],json.dumps(message,separators=(",",":"))))
    db.execute("UPDATE assignments SET state='complete' WHERE id=?", (message["assignment_id"],))
os._exit(0)
'''
        started = time.monotonic()
        subprocess.run([sys.executable, "-c", script, str(SERVER.parent), self.env["HOME"], json.dumps(envelope)], check=True)
        batch = self.batch(self.rpc, request_id)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], [envelope])

    def test_S10_publication_between_snapshot_and_listener_is_rechecked(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")
        store.bind(assignment["assignment_id"], "t1", "events")
        snapshot = getattr(store, "snapshot", None)
        def race(*args, **kwargs):
            selection = snapshot(*args, **kwargs)
            store.publish("events", "t1", "t0", assignment["assignment_id"], "race", "complete")
            return selection
        with mock.patch.object(hc_tools, "lead_store", return_value=(store, team)), \
             mock.patch.object(store, "snapshot", side_effect=race, create=True):
            result = hc_tools.wait({"target": "all", "timeout": 1},
                                   {"HC_TERMINAL_ID": "t0", "AGENT_RUNTIME_DISPATCH": "1"}, hc_tools.Context())
        self.assertEqual(result["structuredContent"]["reason"], "completed")

    def test_S10_cross_process_ipc_wakes_without_waiting_for_recovery_timer(self):
        self.prepare()
        store = Store(self.env)
        observed = threading.Event()
        def observe(*_):
            observed.set()
            return {}
        with mock.patch.object(dispatch_wait, "RECONCILE_INTERVAL", 30), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(dispatch_wait.wait, store, self.assignments[0]["team_id"], "t0",
                                 "w1", 2, None, hc_tools.Context(), observe)
            self.assertTrue(observed.wait(1), "listener never registered")
            message = self.publish(0)
            batch = future.result(timeout=1)
        self.assertEqual(batch["messages"], [message])
        self.assertEqual(batch["reason"], "completed")

    def test_S11_wait_keeps_same_process_ping_report_and_cancellation_responsive(self):
        self.prepare()
        child = self.spawn(self.workers[0], "child", assignment_id=self.assignments[0]["assignment_id"])
        worker_rpc = AsyncRPC(self.workers[0])
        request_id = self.begin(worker_rpc)
        report_id = worker_rpc.tool("report", {"assignment_id": self.assignments[0]["assignment_id"],
                                              "kind": "complete", "text": "parent report"})
        self.assertFalse(worker_rpc.get(report_id)["result"]["isError"])
        self.pending(worker_rpc, request_id)
        worker_rpc.send("notifications/cancelled", {"requestId": request_id}, notify=True)
        self.pending(worker_rpc, request_id)
        store = Store(self.env)
        store.publish("events", "t3", "t1", child["assignment_id"], "child done", "complete")
        completed_id = worker_rpc.tool("wait", {"target": "all", "timeout": 0})
        self.assertEqual(self.batch(worker_rpc, completed_id)["reason"], "completed")
        self.pending(worker_rpc, completed_id)
        self.assertEqual(worker_rpc.counts.get(request_id, 0), 0)
        self.assertEqual(worker_rpc.counts[completed_id], 1)

    def test_S5_S7_attention_cleared_during_finalization_cannot_become_early_timeout(self):
        """agent-runtime-dispatch/S5; agent-runtime-dispatch/S7"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        blocked = store.publish("events", "t1", "t0", assignment, "needs help", "blocked")
        original_read, raced = store.read, False
        def read_then_progress(*args, **kwargs):
            nonlocal raced
            batch = original_read(*args, **kwargs)
            if not raced and batch["assignments"][0]["state"] == "blocked":
                raced = True
                store.publish("events", "t1", "t0", assignment, "working again", "progress")
            return batch
        def finish(*_):
            store.publish("events", "t1", "t0", assignment, "done", "complete")
            return {}
        with mock.patch.object(store, "read", side_effect=read_then_progress):
            batch = dispatch_wait.wait(store, team, "t0", "all", 3, None, hc_tools.Context(), finish)
        self.assertTrue(raced)
        self.assertIn(batch["reason"], ("attention", "completed"))
        if batch["reason"] == "attention":
            self.assertEqual(batch["assignments"][0]["state"], "blocked")
            self.assertEqual(batch["messages"], [blocked])
        else:
            self.assertEqual(batch["unfinished"], [])
        with store.transaction() as db:
            receipts = db.execute("SELECT ids FROM receipts").fetchall()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(json.loads(receipts[0][0]), [m["message_id"] for m in batch["messages"]])

    def test_S8_concurrent_ack_during_finalization_keeps_batch_and_receipt_consistent(self):
        """agent-runtime-dispatch/S8"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        progress = store.publish("events", "t1", "t0", assignment, "progress", "progress")
        prior = store.read(team, "t0", "any")
        original_read, raced = store.read, False
        def read_then_ack(*args, **kwargs):
            nonlocal raced
            batch = original_read(*args, **kwargs)
            if not raced and batch["messages"]:
                raced = True
                with store.transaction() as db:
                    store.acknowledge(db, team, "t0", prior["receipt"])
            return batch
        def publish_next(*_):
            store.publish("events", "t1", "t0", assignment, "next progress", "progress")
            return {}
        with mock.patch.object(store, "read", side_effect=read_then_ack):
            batch = dispatch_wait.wait(store, team, "t0", "any", 3, None, hc_tools.Context(), publish_next)
        self.assertTrue(raced)
        self.assertEqual(batch["reason"], "messages")
        self.assertTrue(batch["messages"])
        self.assertIn(batch["messages"][0]["text"], (progress["text"], "next progress"))
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 2)
            ids = json.loads(db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[0])
        self.assertEqual(ids, [m["message_id"] for m in batch["messages"]])

    def test_S27_queued_progress_and_replay_surface_exited_or_interrupted_worker(self):
        """agent-runtime-dispatch/S27"""
        self.prepare()
        progress = self.publish(0, "progress")
        for state in ("exited", "interrupted"):
            self.rows[0]["status"] = state
            self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
            tmp = self.dir / "next-replies"
            tmp.write_text(json.dumps(self.replies))
            tmp.replace(self.dir / "replies")
            for replay in range(2):
                with self.subTest(state=state, replay=replay):
                    before = len(self.runs("ls"))
                    batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0 if replay else 1}))
                    self.assertEqual(batch["reason"], "failed")
                    self.assertEqual(batch["messages"], [progress])
                    self.assertEqual(len(batch["unfinished"]), 2)
                    self.assertEqual(batch["assignments"][0]["runtime_state"], state)
                    self.assertEqual(len(self.runs("ls")), before + 1)
        self.publish(0)
        self.publish(1)
        complete = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.assertEqual(complete["reason"], "completed")
        self.assertEqual(complete["unfinished"], [])
        self.assertEqual(complete["messages"][0], progress)

    def test_S27_completion_committed_during_bounded_probe_overrides_observed_exit(self):
        """agent-runtime-dispatch/S27"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        progress = store.publish("events", "t1", "t0", assignment, "progress", "progress")
        def complete_during_probe(members, timeout):
            self.assertEqual([m["assignment_id"] for m in members], [assignment])
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, dispatch_wait.OBSERVE_TIMEOUT)
            store.publish("events", "t1", "t0", assignment, "done", "complete")
            return {assignment: "exited"}
        observe = mock.Mock(side_effect=complete_during_probe)
        batch = dispatch_wait.wait(store, team, "t0", "any", 1, None, hc_tools.Context(), observe)
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["unfinished"], [])
        self.assertEqual(batch["messages"][0], progress)
        observe.assert_called_once()
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)
