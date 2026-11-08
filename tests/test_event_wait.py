"""Event waits through real MCP processes; fake hc, explicit reconciliation barriers."""
import json
import queue
import sqlite3
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
import dispatch_store
from dispatch_events import endpoints
from dispatch_store import Store

import pytest

pytestmark = pytest.mark.integration



class AsyncRPC:
    def __init__(self, session):
        self.session, self.pending, self.counts = session, {}, {}
        self.replies = queue.Queue()
        self.sequence = 100
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()

    def read(self):
        try:
            for line in self.session.proc.stdout:
                try:
                    response = json.loads(line)
                    self.counts[response["id"]] = self.counts.get(response["id"], 0) + 1
                    self.replies.put(response)
                except (ValueError, KeyError) as error:
                    self.replies.put(error)
        except (ValueError, OSError):  # the session closed its stdout under this thread
            return

    def close(self):
        """Stop the reader before the session closes its streams: a thread on a closed file raises."""
        self.thread.join(timeout=2)

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
            session = Session(dict(self.env, HC_TERMINAL_ID=address, CODEX_THREAD_ID="worker-" + address), new_cli=True)
            self.addCleanup(session.close)
            session.handshake()
            self.workers.append(session)
        self.rpc = AsyncRPC(self.lead)
        self.addCleanup(self.rpc.close)

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

    def test_S3_final_wait_records_reason_and_fingerprint_atomically(self):
        """agent-runtime/R109
        S3: a partial "messages" result leaves the last-wait row untouched."""
        store = Store({"HOME": str(self.dir / "home")})
        cases = (("timeout", None, {}, 0),
                 ("attention", "blocked", {}, 1),
                 ("attention", "decision", {}, 1),
                 ("attention", None, {"runtime": "blocked"}, 1),
                 ("attention", None, {"runtime": "unreported"}, 1),
                 ("attention", None, {"runtime": "error"}, 1),
                 ("attention", None, {"runtime": "idle", "turn": 1}, 1),
                 ("attention", "blocked", {"second_runtime": "idle", "turn": 0}, 1),
                 ("attention", "blocked", {"second_runtime": "done", "turn": 1}, 2),
                 ("failed", None, {"runtime": "exited"}, 0),
                 ("completed", "complete", {}, 0),
                 ("empty", None, {}, 0))
        for index, (expected, kind, observation, count) in enumerate(cases):
            with self.subTest(reason=expected, kind=kind, observation=observation):
                team = store.team("events", "t0", str(index))
                assignment = None
                if expected != "empty":
                    assignment = store.register(team, "w1")["assignment_id"]
                    store.bind(assignment, "t1", "events", turn=0)
                    if kind and "second_runtime" not in observation:
                        store.publish("events", "t1", "t0", assignment, kind, kind)
                observations = {}
                if "runtime" in observation:
                    observations[assignment] = observation["runtime"]
                    observations[(assignment, "turn")] = observation.get("turn")
                if "second_runtime" in observation:
                    second = store.register(team, "w2")["assignment_id"]
                    store.bind(second, "t2", "events", turn=0)
                    observations[second] = observation["second_runtime"]
                    observations[(second, "turn")] = observation["turn"]
                def observe(*_):
                    if "second_runtime" in observation:
                        store.publish("events", "t1", "t0", assignment, kind, kind)
                    return observations
                batch = dispatch_wait.wait(store, team, "t0", "all", 0 if not observation else 1,
                                           None, hc_tools.Context(), observe)
                self.assertEqual(batch["reason"], expected)
                self.assertNotIn("_attention", batch)
                with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
                    row = db.execute("SELECT reason,attention,fingerprint FROM last_wait WHERE team=?", (team,)).fetchone()
                    self.assertEqual(row, (expected, count, dispatch_store.wait_fingerprint(db, team)))
                    if batch["receipt"]:
                        ids = db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[0]
                        self.assertEqual(json.loads(ids), [m["message_id"] for m in batch["messages"]])
                        self.assertEqual(db.execute("SELECT SUM(acked) FROM messages").fetchone()[0], 0)
        # S3: a partial result (an undelivered report while other work is open) never records the row.
        team = store.team("events", "t0", "partial")
        first = store.register(team, "w1")["assignment_id"]
        store.bind(first, "t1", "events")
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events")
        dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})  # timeout
        with store.transaction() as db:
            original = tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone())
        store.publish("events", "t1", "t0", first, "half done", "progress")
        batch = dispatch_wait.wait(store, team, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {})
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["unfinished"], [first, second])
        with store.transaction() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()), original)
        team = store.team("events", "t0", "narrow")
        first = store.register(team, "w1")["assignment_id"]
        store.bind(first, "t1", "events")
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events")
        dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
        with store.transaction() as db:
            original = tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone())
        store.publish("events", "t1", "t0", first, "done", "complete")
        for target in ("any", "w1", "absent"):
            dispatch_wait.wait(store, team, "t0", target, 0, None, hc_tools.Context(), lambda *_: {})
            with store.transaction() as db:
                self.assertEqual(tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()), original)
        store.read(team, "t0", "all", ready=lambda batch: None)
        store.read(team, "t0", "all")
        ctx = hc_tools.Context()
        def cancel(*_):
            ctx.cancelled.set()
            return {}
        with self.assertRaises(InterruptedError):
            dispatch_wait.wait(store, team, "t0", "all", 1, None, ctx, cancel)
        with store.transaction() as db:
            self.assertEqual(tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()), original)
            before = db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            db.execute("CREATE TRIGGER reject_wait BEFORE INSERT ON last_wait "
                       "BEGIN SELECT RAISE(ABORT, 'reject wait'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "reject wait"):
            dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], before)
            self.assertEqual(tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()), original)

    def test_S4_read_only_fingerprint_changes_with_messages_states_and_turns(self):
        """agent-runtime/R110"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events", turn=0)
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events", turn=0)
        batch = dispatch_wait.wait(store, team, "t0", "all", 0, None,
                                   hc_tools.Context(), lambda *_: {})
        self.assertEqual(batch["reason"], "timeout")
        def fingerprint():
            with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
                return dispatch_store.wait_fingerprint(db, team)
        stored = fingerprint()
        other = store.team("events", "other", "lead")
        other_assignment = store.register(other, "other")["assignment_id"]
        store.bind(other_assignment, "t3", "events", turn=9)
        self.assertEqual(fingerprint(), stored, "other teams must not change this fingerprint")
        lead = store.actor("events", "t0", None, "lead")
        worker = store.actor("events", "t1", "t0", "worker", assignment)
        store.peer(lead, "w1", "outbound")
        self.assertEqual(fingerprint(), stored, "outbound messages are not lead inbox changes")
        store.peer(worker, "w2", "peer")
        self.assertEqual(fingerprint(), stored, "worker peer messages are not lead inbox changes")
        store.peer(worker, "lead", "lead inbox")
        message_fingerprint = fingerprint()
        self.assertNotEqual(message_fingerprint, stored)
        with store.transaction() as db:
            db.execute("UPDATE assignments SET state='blocked' WHERE id=?", (assignment,))
        state_fingerprint = fingerprint()
        self.assertNotEqual(state_fingerprint, message_fingerprint)
        with store.transaction() as db:
            db.execute("UPDATE dispatch_turns SET turn=1 WHERE assignment=?", (assignment,))
        self.assertNotEqual(fingerprint(), state_fingerprint)
        with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
            statements = []
            db.set_trace_callback(statements.append)
            self.assertEqual(dispatch_store.last_wait_state(db, team), ("timeout", 0, stored, fingerprint()))
            self.assertFalse(db.in_transaction)
            self.assertIn("BEGIN", statements)
            self.assertIsNone(dispatch_store.last_wait_state(db, other))
            db.execute("BEGIN")
            dispatch_store.last_wait_state(db, team)
            self.assertTrue(db.in_transaction, "helper must preserve caller's read transaction")
            db.rollback()
        with store.transaction() as db:
            db.execute("DROP TABLE last_wait")
        with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
            self.assertIsNone(dispatch_store.last_wait_state(db, team))
            self.assertFalse(db.in_transaction)

    def resume_rig(self, mode="idle", turn=3, before_resume=0, fast_report=None):
        now = [1_000_000.0]
        self.rigs = getattr(self, "rigs", 0) + 1
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
               "HOME": str(self.dir / ("resume%d" % self.rigs)), "HC_SCOPE": "events",
               "HC_TERMINAL_ID": "t0", "AGENT_RUNTIME_DISPATCH": "1"}
        row = {"name": "w1", "address": "t1", "parent": "t0", "status": mode, "turn": turn}
        self.start({"get:w1": {"out": json.dumps(row)}}, env)
        store = Store(env, clock=lambda: now[0])
        team = store.team("events", "t0", "resume")
        old = store.register(team, "w1")["assignment_id"]
        store.bind(old, "t1", "events", turn=turn)
        store.publish("events", "t1", "t0", old, "previous task done", "complete")
        receipt = store.read(team, "t0", "all")["receipt"]
        store.read(team, "t0", "all", receipt=receipt)
        ctx = hc_tools.Context()
        ctx.store, ctx.owned_team, ctx.actor, ctx.ack = store, team, {"scope": "events"}, None
        now[0] += before_resume
        run_hc = hc_tools.hc
        def send_with_report(argv, *args, **kwargs):
            if argv[0] == "send" and fast_report:
                assignment = argv[2].split("Use assignment_id=", 1)[1].split()[0]
                store.publish("events", "t1", "t0", assignment, "started during send", fast_report, caller_name="w1")
            return run_hc(argv, *args, **kwargs)
        with mock.patch.object(hc_tools, "hc", side_effect=send_with_report):
            result = hc_tools.resume({"name": "w1", "task": "next task"}, env, ctx)
        assignment = result["structuredContent"]["assignment_id"]
        state = {"runtime": mode, "turn": turn}
        def observe(*_):
            return {assignment: state["runtime"], (assignment, "turn"): state["turn"]}
        def wait(reopened=None):
            return dispatch_wait.wait(reopened or store, team, "t0", "all", 0.05, None, hc_tools.Context(), observe)
        return store, team, assignment, now, state, wait

    def test_S12_swallowed_resume_returns_not_started_without_resending(self):
        """S12: an idle worker at the resume turn needs attention after 60 seconds."""
        store, team, assignment, now, state, wait = self.resume_rig()
        now[0] += 61
        batch = wait()
        self.assertEqual(batch["reason"], "attention")
        member, = batch["assignments"]
        self.assertEqual(member["assignment_id"], assignment)
        self.assertEqual(member["attention"], "not_started")
        self.assertEqual(member["state"], "working")
        self.assertEqual(batch["unfinished"], [assignment])
        for word in ("resume", "collect", "stop", "never resends"):
            self.assertIn(word, member["next"])
        self.assertEqual(batch["messages"], [])
        self.assertEqual(wait()["reason"], "attention")
        self.assertEqual(len(self.runs("send")), 1)
        self.assertEqual(store.assignment_state(assignment), "working")

    def test_S13_turn_advance_or_report_suppresses_resume_attention(self):
        """S13: progress proves the resumed assignment started, including an acked report."""
        for progress in ("turn", "progress", "complete", "blocked", "decision"):
            with self.subTest(progress=progress):
                store, team, assignment, now, state, wait = self.resume_rig()
                now[0] += 61
                self.assertEqual(wait()["assignments"][0].get("attention"), "not_started")
                store, team, assignment, now, state, wait = self.resume_rig()
                now[0] += 30
                if progress == "turn":
                    state["turn"], state["runtime"] = 4, "working"
                    self.assertEqual(wait()["reason"], "timeout")
                else:
                    store.publish("events", "t1", "t0", assignment, "started", progress)
                    page = store.read(team, "t0", "all")
                    store.read(team, "t0", "all", receipt=page["receipt"])
                now[0] += 31
                self.assertTrue(all(a.get("attention") != "not_started" for a in wait()["assignments"]))

    def test_S13_fast_report_during_resume_send_wins(self):
        """S13: a report before send returns binds once and suppresses not_started."""
        for kind in ("progress", "complete"):
            with self.subTest(kind=kind):
                store, team, assignment, now, state, wait = self.resume_rig(fast_report=kind)
                now[0] += 61
                batch = wait()
                self.assertEqual([m["kind"] for m in batch["messages"]], [kind])
                self.assertTrue(all(a.get("attention") != "not_started" for a in batch["assignments"]))
                self.assertEqual(store.assignment_state(assignment), "working" if kind == "progress" else "complete")

    def test_S12_zero_timeout_observes_overdue_resume_before_returning(self):
        """S12: an immediate wait still reconciles a resume whose grace elapsed."""
        store, team, assignment, now, state, wait = self.resume_rig()
        now[0] += 61
        observations = []
        def observe(*_):
            observations.append(assignment)
            return {assignment: "idle", (assignment, "turn"): 3}
        batch = dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), observe)
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(batch["assignments"][0].get("attention"), "not_started")
        self.assertEqual(observations, [assignment])

    def test_S13_fast_mid_turn_progress_keeps_the_resume_baseline(self):
        """S13: progress during send must not turn the pre-existing turn into premature attention."""
        store, team, assignment, now, state, wait = self.resume_rig(mode="working", fast_report="progress")
        state["runtime"], state["turn"] = "idle", 4
        now[0] += 1
        batch = wait()
        self.assertEqual(batch["reason"], "messages")
        store.read(team, "t0", "all", receipt=batch["receipt"])
        self.assertEqual(wait()["reason"], "timeout")
        state["turn"] = 5
        self.assertEqual(wait()["reason"], "attention")

    def test_S14_mid_turn_resume_clock_starts_at_dispatch(self):
        """S14: a mid-turn resume gets a fresh 60 seconds even after a long old turn."""
        store, team, assignment, now, state, wait = self.resume_rig(mode="working", before_resume=3600)
        state["runtime"] = "idle"
        now[0] += 59
        self.assertEqual(wait()["reason"], "timeout")
        now[0] += 2
        member, = wait()["assignments"]
        self.assertEqual(member.get("attention"), "not_started")
        state["turn"] = 4  # The pre-existing turn ended; do not call this a swallowed resume.
        self.assertNotEqual(wait()["assignments"][0].get("attention"), "not_started")

    def test_S15_resume_grace_boundary_keeps_assignment_working(self):
        """S15: before the grace boundary there is no attention or assignment mutation."""
        store, team, assignment, now, state, wait = self.resume_rig()
        for elapsed in (0, 59.999):
            now[0] = 1_000_000 + elapsed
            batch = wait()
            self.assertEqual(batch["reason"], "timeout")
            self.assertNotIn("attention", batch["assignments"][0])
            self.assertEqual(batch["assignments"][0]["state"], "working")
        now[0] = 1_000_060
        self.assertEqual(wait()["assignments"][0].get("attention"), "not_started")

    def test_S12_resume_mark_survives_store_restart_and_old_writers(self):
        """S12: reopening the store preserves the resume clock without changing assignment shape."""
        store, team, assignment, now, state, wait = self.resume_rig()
        now[0] += 61
        reopened = Store({"XDG_STATE_HOME": str(store.directory.parent)}, clock=lambda: now[0])
        member, = wait(reopened)["assignments"]
        self.assertEqual(member.get("attention"), "not_started")
        self.assertFalse(any(key.startswith("_") for key in member))
        with reopened.transaction() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            db.execute("INSERT INTO assignments VALUES ('old-writer',?,?,'pending',0)",
                       (team, member["worker_id"]))
            self.assertEqual(db.execute("SELECT attention FROM last_wait WHERE team=?", (team,)).fetchone()[0], 1)

    def test_S12_failure_precedes_not_started(self):
        """S12: exit, interruption and replacement keep their durable failure behavior."""
        for runtime in dispatch_wait.FAILURE_STATES:
            with self.subTest(runtime=runtime):
                store, team, assignment, now, state, wait = self.resume_rig()
                now[0] += 61
                state["runtime"] = runtime
                batch = wait()
                self.assertEqual(batch["reason"], "failed")
                self.assertNotIn("attention", batch["assignments"][0])
                self.assertEqual(store.assignment_state(assignment), "failed")
                self.assertEqual(len(self.runs("send")), self.rigs)

    def test_S13_missing_turn_fails_safe_and_working_recovery_clears_attention(self):
        """S13: no turn evidence or an actively working worker cannot prove a swallowed resume."""
        for baseline in (None, 3):
            with self.subTest(baseline=baseline):
                store, team, assignment, now, state, wait = self.resume_rig(turn=baseline)
                now[0] += 61
                state["turn"] = None
                self.assertEqual(wait()["reason"], "timeout")
                state["turn"] = 3
                if baseline is None:
                    self.assertNotEqual(wait()["assignments"][0].get("attention"), "not_started")
                else:
                    self.assertEqual(wait()["assignments"][0].get("attention"), "not_started")
                state["runtime"] = "working"
                self.assertEqual(wait()["reason"], "timeout")
                self.assertEqual(store.assignment_state(assignment), "working")

    def test_S15_cancelled_wait_keeps_resume_assignment_and_never_resends(self):
        """S15: cancelling a pending wait changes no resume state and creates no receipt."""
        store, team, assignment, now, state, wait = self.resume_rig()
        ctx = hc_tools.Context()
        def observe(*_):
            ctx.cancelled.set()
            return {assignment: "idle", (assignment, "turn"): 3}
        with self.assertRaises(InterruptedError):
            dispatch_wait.wait(store, team, "t0", "all", 1, None, ctx, observe)
        self.assertEqual(store.assignment_state(assignment), "working")
        self.assertEqual(len(self.runs("send")), 1)
        now[0] += 61
        self.assertEqual(wait()["assignments"][0].get("attention"), "not_started")

    def prepare_mcp_resume(self, mode="idle", fast_progress=False):
        self.prepare()
        self.rows[0].update(status=mode, turn=3)
        self.replies["get:w1"] = {"out": json.dumps(self.rows[0])}
        self.replies["ls"] = {"out": json.dumps({"terminals": self.rows})}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        self.publish(0)
        page = self.batch(self.rpc, self.rpc.tool("wait", {"target": "inbox", "timeout": 0}))
        if fast_progress:
            # Hold the external send response until a real worker MCP report binds the pending assignment.
            script = self.dir / "bin" / "hc"
            barrier = (
                "if argv[0] == 'send':\n"
                "    from pathlib import Path\n"
                "    root = Path(%r)\n"
                "    root.joinpath('sending').write_text(argv[2].split('Use assignment_id=', 1)[1].split()[0])\n"
                "    deadline = time.monotonic() + 5\n"
                "    while not root.joinpath('release-send').exists() and time.monotonic() < deadline:\n"
                "        time.sleep(0.005)\n" % str(self.dir))
            source = script.read_text()
            script.write_text(source.replace('sys.stdout.write(reply.get("out", ""))',
                                             barrier + 'sys.stdout.write(reply.get("out", ""))'))
        request = self.rpc.tool("resume", {
            "name": "w1", "task": "next task", "receipt": page["receipt"]})
        if fast_progress:
            deadline = time.monotonic() + 3
            while not (self.dir / "sending").exists():
                self.assertLess(time.monotonic(), deadline, "resume never reached send")
                threading.Event().wait(0.005)
            assignment = (self.dir / "sending").read_text()
            try:
                result, text = self.call(self.workers[0], "report", {
                    "assignment_id": assignment, "kind": "progress", "text": "started during send"})
                self.assertFalse(result["isError"], text)
            finally:
                (self.dir / "release-send").touch()
        resumed = self.batch(self.rpc, request)
        self.assignments[0] = resumed
        self.resume_store = Store(self.env)
        return resumed["assignment_id"]

    def age_mcp_resume(self, assignment, seconds):
        # Seed persisted dispatch time; server time and the MCP/IPC paths stay real.
        with self.resume_store.transaction() as db:
            db.execute("UPDATE resume_marks SET dispatched_ms=? WHERE assignment=?",
                       (int((time.time() - seconds) * 1000), assignment))

    def test_S12_mcp_zero_timeout_surfaces_overdue_resume(self):
        """S12: real MCP resume and fake-hc observation expose the swallowed send at timeout zero."""
        assignment = self.prepare_mcp_resume()
        self.age_mcp_resume(assignment, 61)
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual(batch["reason"], "attention")
        member = next(a for a in batch["assignments"] if a["assignment_id"] == assignment)
        self.assertEqual(member.get("attention"), "not_started")
        self.assertEqual(member["state"], "working")
        self.assertEqual(len(self.runs("send")), 1)

    def test_S13_mcp_pending_wait_wakes_for_resume_report(self):
        """S13: a report wakes the real IPC wait and suppresses overdue-resume attention after ack."""
        assignment = self.prepare_mcp_resume()
        request = self.begin()
        self.publish(0, kind="progress", text="new task started")
        batch = self.batch(self.rpc, request)
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual([m["assignment_id"] for m in batch["messages"]], [assignment])
        self.age_mcp_resume(assignment, 61)
        after = self.batch(self.rpc, self.rpc.tool("wait", {
            "target": "all", "timeout": 0, "receipt": batch["receipt"]}))
        self.assertEqual(after["reason"], "timeout")
        self.assertTrue(all("attention" not in a for a in after["assignments"]))

    def test_S13_mcp_turn_advance_suppresses_resume_attention(self):
        """S13: a real runtime observation of the next working turn suppresses not_started."""
        assignment = self.prepare_mcp_resume()
        self.rows[0].update(status="working", turn=4)
        self.replies["ls"] = {"out": json.dumps({"terminals": self.rows})}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        self.age_mcp_resume(assignment, 61)
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual(batch["reason"], "timeout")
        self.assertTrue(all("attention" not in a for a in batch["assignments"]))
        self.assertGreaterEqual(len(self.runs("ls")), 1)

    def test_S13_mcp_fast_report_preserves_mid_turn_baseline(self):
        """S13: a worker report before the real resume response preserves old-turn suppression."""
        assignment = self.prepare_mcp_resume(mode="working", fast_progress=True)
        self.rows[0].update(status="idle", turn=4)
        self.replies["ls"] = {"out": json.dumps({"terminals": self.rows})}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        # The resume response already delivered the fast report in its inbox piggyback.
        delivery = self.assignments[0]["inbox"]
        self.assertEqual([m["assignment_id"] for m in delivery["messages"]], [assignment])
        after = self.batch(self.rpc, self.rpc.tool("wait", {
            "target": "all", "timeout": 0.2, "receipt": delivery["receipt"]}))
        self.assertEqual(after["reason"], "timeout")
        self.assertTrue(all("attention" not in a for a in after["assignments"]))

    def test_S14_mcp_mid_turn_resume_uses_dispatch_clock(self):
        """S14: a mid-turn resume is quiet within grace and surfaces when the same turn stays idle."""
        assignment = self.prepare_mcp_resume(mode="working")
        self.rows[0]["status"] = "idle"
        self.replies["ls"] = {"out": json.dumps({"terminals": self.rows})}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        self.age_mcp_resume(assignment, 59)
        before = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0.2}))
        self.assertEqual(before["reason"], "timeout")
        self.age_mcp_resume(assignment, 61)
        after = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual(after["reason"], "attention")
        member = next(a for a in after["assignments"] if a["assignment_id"] == assignment)
        self.assertEqual(member.get("attention"), "not_started")

    def test_S15_mcp_resume_within_grace_stays_working(self):
        """S15: real resume/wait keeps an idle worker working before grace elapses."""
        assignment = self.prepare_mcp_resume()
        self.age_mcp_resume(assignment, 59)
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0.2}))
        self.assertEqual(batch["reason"], "timeout")
        member = next(a for a in batch["assignments"] if a["assignment_id"] == assignment)
        self.assertEqual(member["state"], "working")
        self.assertNotIn("attention", member)
        self.assertEqual(len(self.runs("send")), 1)

    def stall_rig(self, start=1_000_000.0):
        now = [start]
        self.rigs = getattr(self, "rigs", 0) + 1
        store = Store({"HOME": str(self.dir / ("stall%d" % self.rigs))}, clock=lambda: now[0])
        team = store.team("events", "t0", "stall")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events", turn=0)
        state = {"runtime": "working", "turn": 0}
        def observe(*_):
            return {assignment: state["runtime"], (assignment, "turn"): state["turn"]}
        def wait():
            return dispatch_wait.wait(store, team, "t0", "all", 0.3, None, hc_tools.Context(), observe)
        return store, team, assignment, now, state, wait

    def stalled(self, batch):
        return [a for a in batch["assignments"] if a.get("attention") == "stalled"]

    def test_idle_working_assignment_returns_stalled_once(self):
        """agent-runtime/R111"""
        store, team, assignment, now, state, wait = self.stall_rig()
        self.assertEqual(wait()["reason"], "timeout")
        now[0] += 14 * 60
        self.assertEqual(wait()["reason"], "timeout")
        now[0] += 60
        batch = wait()
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual([a["assignment_id"] for a in self.stalled(batch)], [assignment])
        self.assertEqual(self.stalled(batch)[0]["state"], "working")
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT attention FROM last_wait WHERE team=?", (team,)).fetchone()[0], 1)
        now[0] += 3600
        again = wait()
        self.assertEqual(again["reason"], "timeout")
        self.assertEqual(self.stalled(again), [])

    def test_progress_ends_the_episode_and_a_new_idle_period_stalls_again(self):
        """agent-runtime/R112
        S1: an undelivered progress report returns at once; the stall episode still ends with it."""
        for progress in ("publish", "turn"):
            with self.subTest(progress=progress):
                store, team, assignment, now, state, wait = self.stall_rig()
                wait()
                now[0] += 15 * 60
                self.assertEqual(wait()["reason"], "attention")
                now[0] += 10 * 60
                if progress == "publish":
                    store.publish("events", "t1", "t0", assignment, "step", "progress")
                    self.assertEqual(wait()["reason"], "messages")  # the report is delivered at once
                else:
                    state["turn"] = 1
                self.assertEqual(wait()["reason"], "timeout")
                now[0] += 14 * 60
                self.assertEqual(wait()["reason"], "timeout")
                now[0] += 60
                batch = wait()
                self.assertEqual(batch["reason"], "attention")
                self.assertEqual(len(self.stalled(batch)), 1)

    def test_clock_starts_at_first_observation_and_failure_beats_stalled(self):
        """agent-runtime/R113"""
        store, team, assignment, now, state, wait = self.stall_rig(start=5_000_000.0)
        # Existing before the upgrade: bind wrote no side-table row, so the first wait starts the clock.
        self.assertEqual(wait()["reason"], "timeout")
        self.assertEqual(wait()["reason"], "timeout")
        with store.transaction() as db:
            row = db.execute("SELECT last_progress_ms FROM stall_progress WHERE assignment=?", (assignment,)).fetchone()
        self.assertEqual(row[0], 5_000_000_000)
        now[0] += 20 * 60
        state["runtime"] = "exited"
        batch = wait()
        self.assertEqual(batch["reason"], "failed")
        self.assertEqual(self.stalled(batch), [])
        self.assertNotIn("complete", {a["state"] for a in batch["assignments"]})
        store2, team2, assignment2, now2, state2, wait2 = self.stall_rig()
        wait2()
        now2[0] += 3600
        self.assertEqual(wait2()["reason"], "attention")
        with store2.transaction() as db:
            self.assertEqual(db.execute("SELECT state FROM assignments WHERE id=?", (assignment2,)).fetchone()[0], "working")

    def reported_for(self, store, assignment):
        with store.transaction() as db:
            return db.execute("SELECT stall_reported_for FROM stall_progress WHERE assignment=?", (assignment,)).fetchone()[0]

    def test_needs_person_runtime_keeps_its_own_next_and_is_not_stalled(self):
        """agent-runtime/R111"""
        from agent_runtime import ERROR_NEXT, UNREPORTED_NEXT
        for runtime, expected in (("unreported", UNREPORTED_NEXT), ("error", ERROR_NEXT)):
            with self.subTest(runtime=runtime):
                store, team, assignment, now, state, wait = self.stall_rig()
                state["runtime"] = runtime
                wait()
                now[0] += 3600
                batch = wait()
                self.assertEqual(batch["reason"], "attention")
                self.assertEqual(self.stalled(batch), [])
                self.assertEqual(batch["assignments"][0]["next"], expected)
                self.assertIsNone(self.reported_for(store, assignment))

    def test_nonfinal_read_does_not_mark_the_stall_reported(self):
        """agent-runtime/R111"""
        store, team, assignment, now, state, wait = self.stall_rig()
        wait()
        now[0] += 15 * 60
        store.read(team, "t0", "all", ready=lambda batch: None)
        self.assertIsNone(self.reported_for(store, assignment))
        self.assertEqual(wait()["reason"], "attention")
        self.assertIsNotNone(self.reported_for(store, assignment))

    def test_stalled_worker_alongside_blocked_worker_is_not_reported_early(self):
        """agent-runtime/R111"""
        store, team, assignment, now, state, wait = self.stall_rig()
        other = store.register(team, "w2")["assignment_id"]
        store.bind(other, "t2", "events", turn=0)
        store.publish("events", "t2", "t0", other, "need input", "blocked")
        now[0] += 3600
        batch = wait()  # The blocked report returns at once, before any observation can start a stall clock.
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(self.stalled(batch), [])
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT attention FROM last_wait WHERE team=?", (team,)).fetchone()[0], 1)
            self.assertIsNone(db.execute("SELECT 1 FROM stall_progress WHERE assignment=?", (assignment,)).fetchone())

    def test_unchanged_polls_take_no_write_lock(self):
        """agent-runtime/R111"""
        store, team, assignment, now, state, wait = self.stall_rig()
        wait()
        locker = sqlite3.connect(store.path, isolation_level=None)
        locker.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            store.track_progress({assignment: "working", (assignment, "turn"): 0})
            self.assertLess(time.monotonic() - started, 1)
        finally:
            locker.execute("ROLLBACK")
            locker.close()

    def test_all_buffers_progress_and_partial_completion_with_fixed_membership(self):
        """agent-runtime/R38
        S1: wait(all) returns the first undelivered report, keeps its frozen membership and the caller
        continues from the receipt the batch carried."""
        self.prepare()
        request_id = self.begin()
        store = Store(self.env)
        late = store.register(self.assignments[0]["team_id"], "later")
        store.bind(late["assignment_id"], "t4", "events")
        progress = self.publish(0, "progress", "running")
        batch = self.batch(self.rpc, request_id)
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["messages"], [progress])
        # The membership froze when the wait started: the late worker is not part of it.
        self.assertEqual([a["assignment_id"] for a in batch["assignments"]],
                         [self.assignments[0]["assignment_id"], self.assignments[1]["assignment_id"]])
        self.assertEqual(batch["unfinished"], [self.assignments[0]["assignment_id"], self.assignments[1]["assignment_id"]])
        # The receipt drives the continuation: the acknowledged report is not delivered again.
        first = self.publish(0)
        store.stopped(self.assignments[0]["team_id"], "later")  # the late worker is gone
        following = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0,
                                                               "receipt": batch["receipt"]}))
        self.assertEqual(following["messages"], [first])
        self.assertEqual(following["reason"], "messages")
        self.assertEqual(following["unfinished"], [self.assignments[1]["assignment_id"]])

    def test_S1_wait_all_returns_the_first_undelivered_report(self):
        """S1: wait(all) returns the first undelivered report while another assignment is still working."""
        self.prepare()
        request_id = self.begin()
        report = self.publish(0)
        batch = self.batch(self.rpc, request_id)
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["messages"], [report])
        self.assertEqual([a["assignment_id"] for a in batch["assignments"]],
                         [self.assignments[0]["assignment_id"], self.assignments[1]["assignment_id"]])
        self.assertEqual([a["state"] for a in batch["assignments"]], ["complete", "working"])
        self.assertEqual(batch["unfinished"], [self.assignments[1]["assignment_id"]])
        store = Store(self.env)
        with store.transaction() as db:
            ids = db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[0]
        self.assertEqual(json.loads(ids), [report["message_id"]])

    def test_one_wait_returns_all_completed_reports_without_collect(self):
        """agent-runtime/R39
        S4: both completions published before the wait still yield one completed batch (the early-delivery
        case is test_S1_wait_all_returns_the_first_undelivered_report)."""
        self.prepare()
        messages = [self.publish(i) for i in (0, 1)]
        request_id = self.rpc.tool("wait", {"target": "all", "timeout": 5})
        batch = self.batch(self.rpc, request_id)
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], messages)
        self.assertEqual(batch["unfinished"], [])
        self.assertEqual(self.runs("read"), [])
        self.pending(self.rpc, request_id)  # Exactly one final result.
        self.assertEqual(self.rpc.counts[request_id], 1)

    def test_S2_a_delivered_report_stays_readable_until_its_receipt_is_passed(self):
        """S2: a report delivered in an earlier batch stays readable and may reappear with the same message ID."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        report = store.publish("events", "t1", "t0", assignment, "half done", "progress")
        first = dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
        self.assertEqual(first["reason"], "messages")
        self.assertEqual(first["messages"], [report])
        # The receipt was not passed: the report stays readable, with the same message ID.
        again = dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
        self.assertEqual([m["message_id"] for m in again["messages"]], [report["message_id"]])
        self.assertEqual(again["reason"], "timeout")  # already delivered: the wait blocks for the deadline
        # Passing the receipt acknowledges it.
        third = dispatch_wait.wait(store, team, "t0", "all", 0, first["receipt"], hc_tools.Context(), lambda *_: {})
        self.assertEqual(third["messages"], [])

    def test_S1_new_report_behind_unacknowledged_replay_page(self):
        """S1/S2: a full replay page cannot hide a new report; old reports remain readable."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        old = [store.publish("events", "t1", "t0", assignment, str(i), "progress") for i in range(2)]
        with mock.patch.object(dispatch_store, "MAX_MESSAGES", 2):
            first = dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
            self.assertEqual(first["messages"], old)
            new = store.publish("events", "t1", "t0", assignment, "new", "progress")
            batch = dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
            self.assertEqual(batch["reason"], "messages")
            self.assertIn(new, batch["messages"])
            with store.transaction() as db:
                ids = json.loads(db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[0])
            self.assertEqual(ids, [m["message_id"] for m in batch["messages"]])
            replay = store.read(team, "t0", "all", issue_receipt=False)
            self.assertEqual(replay["messages"], old)

    def test_S4_wait_all_returns_completed_with_the_reports_and_writes_last_wait(self):
        """S4: every selected assignment complete -> completed, the reports, an empty unfinished list and the
        last_wait row written in the same transaction as the receipt."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        first = store.register(team, "w1")["assignment_id"]
        store.bind(first, "t1", "events")
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events")
        reports = [store.publish("events", "t1", "t0", first, "done", "complete"),
                   store.publish("events", "t2", "t0", second, "done", "complete")]
        batch = dispatch_wait.wait(store, team, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {})
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], reports)
        self.assertEqual(batch["unfinished"], [])
        with store.transaction() as db:
            row = tuple(db.execute("SELECT reason,attention,fingerprint FROM last_wait WHERE team=?", (team,)).fetchone())
            self.assertEqual(row, ("completed", 0, dispatch_store.wait_fingerprint(db, team)))
            ids = db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[0]
        self.assertEqual(json.loads(ids), [m["message_id"] for m in reports])
        # The row and the receipt commit together: a rejected row leaves no receipt behind.
        other = Store({"HOME": str(self.dir / "other")})
        team2 = other.team("events", "t0", "lead")
        third = other.register(team2, "w1")["assignment_id"]
        other.bind(third, "t1", "events")
        other.publish("events", "t1", "t0", third, "done", "complete")
        with other.transaction() as db:
            db.execute("CREATE TRIGGER reject_wait BEFORE INSERT ON last_wait "
                       "BEGIN SELECT RAISE(ABORT, 'reject wait'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "reject wait"):
            dispatch_wait.wait(other, team2, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {})
        with other.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)

    def test_S5_attention_and_failed_win_over_a_pending_report(self):
        """S5: a blocked, failed or person-needing assignment wins over a pending report."""
        def rig():
            self.rigs = getattr(self, "rigs", 0) + 1
            store = Store({"HOME": str(self.dir / ("s5-%d" % self.rigs))})
            team = store.team("events", "t0", "lead")
            first = store.register(team, "w1")["assignment_id"]
            store.bind(first, "t1", "events")
            second = store.register(team, "w2")["assignment_id"]
            store.bind(second, "t2", "events")
            return store, team, first, second
        store, team, first, second = rig()
        store.publish("events", "t1", "t0", first, "half done", "progress")
        store.publish("events", "t1", "t0", first, "need input", "blocked")
        batch = dispatch_wait.wait(store, team, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {})
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(batch["unfinished"], [first, second])
        for runtime, expected in (("exited", "failed"), ("unreported", "attention"), ("error", "attention")):
            with self.subTest(runtime=runtime):
                store, team, first, second = rig()
                store.publish("events", "t1", "t0", first, "half done", "progress")
                def observe(*_):
                    return {first: runtime, (first, "turn"): 0}
                batch = dispatch_wait.wait(store, team, "t0", "all", 1, None, hc_tools.Context(), observe)
                self.assertEqual(batch["reason"], expected)
                self.assertEqual(batch["unfinished"], [first, second])
        # A stalled assignment wins over a pending report too.
        store, team, assignment, now, state, wait = self.stall_rig()
        store.publish("events", "t1", "t0", assignment, "half done", "progress")
        now[0] += 16 * 60
        batch = wait()
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual([a["assignment_id"] for a in self.stalled(batch)], [assignment])

    def test_S6_wait_all_times_out_when_no_report_is_undelivered(self):
        """S6: no undelivered report and nothing finished -> the wait stays pending until the deadline."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        started = time.monotonic()
        batch = dispatch_wait.wait(store, team, "t0", "all", 0.3, None, hc_tools.Context(), lambda *_: {})
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        self.assertEqual(batch["reason"], "timeout")
        self.assertEqual(batch["messages"], [])
        self.assertEqual(batch["unfinished"], [assignment])

    def test_reports_and_runtime_failures_need_attention_and_ended_turns_need_attention(self):
        """agent-runtime/R40
        S1: an undelivered progress report returns at once; the attention report still wins over it."""
        self.prepare()
        for state in ("idle", "done", "blocked", "interrupted", "exited", "removed"):
            with self.subTest(runtime_state=state):
                self.rows[0]["status"] = state
                self.rows[0]["turn"] = 1  # a finished turn since dispatch
                self.replies["ls"]["out"] = json.dumps({"terminals": self.rows[1:] if state == "removed" else self.rows})
                tmp = self.dir / "next-replies"
                tmp.write_text(json.dumps(self.replies))
                tmp.replace(self.dir / "replies")
                request_id = self.rpc.tool("wait", {"target": "all", "timeout": 0.3})
                batch = self.batch(self.rpc, request_id)
                self.assertEqual(batch["reason"], "attention" if state in ("idle", "done", "blocked") else "failed")
                self.assertEqual(len(batch["unfinished"]), 2)
                if batch["reason"] == "failed":  # D2: persisted, so a fresh assignment replaces the failed one
                    self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": batch["receipt"]}))
                    self.rows[0]["status"] = "working"
                    self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
                    (self.dir / "replies").write_text(json.dumps(self.replies))
                    self.assignments[0] = self.rpc.get(self.rpc.tool("spawn", {
                        "kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"}))["result"]["structuredContent"]
        self.rows[0]["status"] = "working"
        self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
        (self.dir / "replies").write_text(json.dumps(self.replies))
        for kind in ("blocked", "decision"):
            self.publish(0, "progress")
            request_id = self.rpc.tool("wait", {"target": "all", "timeout": 0.3})
            first = self.batch(self.rpc, request_id)
            self.assertEqual(first["reason"], "messages")  # the progress report is delivered at once
            event = self.publish(0, kind)
            batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0.3}))
            self.assertEqual(batch["reason"], "attention")
            self.assertIn(event, batch["messages"])
            self.assertEqual(len(batch["unfinished"]), 2)
        self.publish(0)
        self.publish(1)
        self.assertEqual(self.batch(self.rpc, self.rpc.tool("wait", {"target": "all"}))["reason"], "completed")

    def test_any_batches_eligible_events_and_named_wait_excludes_old_assignment(self):
        """agent-runtime/R41"""
        self.prepare()
        old = self.publish(0)
        # Reuse the name; the prior completion stays out of the named wait but, being unacknowledged (D26),
        # is still delivered to wait(any).
        replacement = self.rpc.tool("spawn", {"kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"})
        self.assignments[0] = self.rpc.get(replacement)["result"]["structuredContent"]
        first, second = self.publish(0), self.publish(1, "progress")
        named = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w1", "timeout": 0}))
        self.assertEqual(named["messages"], [first])
        self.assertEqual([a["assignment_id"] for a in named["assignments"]], [first["assignment_id"]])
        any_batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any"}))
        self.assertEqual(any_batch["messages"], [old, first, second])
        self.assertEqual(any_batch["reason"], "messages")

    def test_deadline_empty_and_cancel_preserve_replay_and_workers(self):
        """agent-runtime/R42
        S6: a queued report returns at once; a genuinely empty queue waits for the deadline."""
        self.prepare()
        progress = self.publish(0, "progress")
        started = time.monotonic()
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 2}))
        self.assertLess(time.monotonic() - started, 1)  # the undelivered report returns at once
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["messages"], [progress])
        started = time.monotonic()
        deadline = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w2", "timeout": 0.2}))
        self.assertGreaterEqual(time.monotonic() - started, 0.18)  # an empty queue takes the deadline
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(deadline["reason"], "timeout")
        self.assertEqual(deadline["messages"], [])
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

    def test_commit_then_process_exit_without_hint_recovers_within_bound(self):
        """agent-runtime/R43"""
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

    def test_publication_between_snapshot_and_listener_is_rechecked(self):
        """agent-runtime/R43"""
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

    def test_cross_process_ipc_wakes_without_waiting_for_recovery_timer(self):
        """agent-runtime/R43"""
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

    def test_wait_keeps_same_process_ping_report_and_cancellation_responsive(self):
        """agent-runtime/R44"""
        self.prepare()
        child = self.spawn(self.workers[0], "child", assignment_id=self.assignments[0]["assignment_id"])
        worker_rpc = AsyncRPC(self.workers[0])
        self.addCleanup(worker_rpc.close)
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

    def test_attention_cleared_during_finalization_cannot_become_early_timeout(self):
        """agent-runtime/R38 agent-runtime/R40
        agent-runtime-dispatch/S5; agent-runtime-dispatch/S7"""
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

    def test_concurrent_ack_during_finalization_keeps_batch_and_receipt_consistent(self):
        """agent-runtime/R41
        agent-runtime-dispatch/S8"""
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

    def test_queued_progress_and_replay_surface_exited_or_interrupted_worker(self):
        """agent-runtime/R60
        agent-runtime-dispatch/S27"""
        self.prepare()
        for state in ("exited", "interrupted"):
            progress = self.publish(0, "progress")
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
                    # D2: the observed failure is persisted once; a replay re-reads it without probing again.
                    self.assertEqual([(m["kind"], m["assignment_id"]) for m in batch["messages"]],
                                     [("progress", progress["assignment_id"]), ("failed", progress["assignment_id"])])
                    self.assertEqual(batch["messages"][0], progress)
                    self.assertEqual(len(batch["unfinished"]), 2)
                    if not replay:
                        w1 = [a for a in batch["assignments"] if a["name"] == "w1"][0]
                        self.assertEqual(w1["runtime_state"], state)
                        self.assertEqual(len(self.runs("ls")), before + 1)
            receipt = batch["receipt"]
            self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": receipt}))
            # The failed assignment is cleared; a fresh one for the same name continues the scenario.
            self.rows[0]["status"] = "working"
            self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
            (self.dir / "replies").write_text(json.dumps(self.replies))
            self.assignments[0] = self.rpc.get(self.rpc.tool(
                "spawn", {"kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"}))["result"]["structuredContent"]
        self.publish(0)
        self.publish(1)
        complete = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.assertEqual(complete["reason"], "completed")
        self.assertEqual(complete["unfinished"], [])

    def test_completion_committed_during_bounded_probe_overrides_observed_exit(self):
        """agent-runtime/R60
        agent-runtime-dispatch/S27"""
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

    def set_row(self, index, status, **extra):
        """Rewrite w1/w2's hc ls row and hc get reply."""
        row = dict(self.rows[index], status=status, **extra)
        self.rows[index] = row
        self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
        for key in (row["name"], row["address"]):
            self.replies["get:" + key] = {"out": json.dumps(row)}
        (self.dir / "replies").write_text(json.dumps(self.replies))

    def silent(self, index=0, age=60, **extra):
        self.set_row(index, None, state="live", status_ms=None, reported=False, turn=0,
                     created_ms=int((time.time() - age) * 1000), exit_code=None, **extra)

    def wait_all(self):
        return self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0.3}))

    def test_never_reported_worker_past_grace_needs_attention(self):
        """agent-runtime/R100"""
        self.prepare()
        self.silent(0, age=60)
        batch = self.wait_all()
        self.assertEqual(batch["reason"], "attention")
        silent = next(a for a in batch["assignments"] if a["name"] == "w1")
        self.assertEqual(silent["runtime_state"], "unreported")
        self.assertEqual(silent["state"], "working")
        for verb in ("collect", "ask the user", "hc keys", "stop"):
            self.assertIn(verb, silent["next"])
        rows = hc_tools.lib.hc_next("w1", "unreported")
        self.assertTrue(silent["next"].endswith("; ".join(r["do"] for r in rows)))

    def test_never_reported_worker_inside_grace_keeps_working(self):
        """agent-runtime/R101"""
        self.prepare()
        self.silent(0, age=5)
        batch = self.wait_all()
        self.assertEqual(batch["reason"], "timeout")
        self.assertNotIn("unreported", json.dumps(batch["assignments"]))

    def test_worker_that_reported_before_is_not_unreported(self):
        """agent-runtime/R102"""
        self.prepare()
        self.set_row(0, None, state="live", status_ms=1000, reported=True, turn=2,
                     created_ms=int((time.time() - 60) * 1000), exit_code=None)
        batch = self.wait_all()
        self.assertEqual(batch["reason"], "timeout")
        self.assertNotIn("unreported", json.dumps(batch["assignments"]))

    def test_exited_never_reported_worker_fails_not_unreported(self):
        """agent-runtime/R103"""
        for state in ("exited", "interrupted", "replaced"):
            with self.subTest(state=state):
                self.prepare()
                self.silent(0, age=60)
                if state == "replaced":
                    self.set_row(0, None, name="other")
                else:
                    self.set_row(0, None, state=state)
                batch = self.wait_all()
                self.assertEqual(batch["reason"], "failed")
                self.assertNotIn("unreported", json.dumps(batch))

    def test_failed_hc_get_falls_back_to_unknown(self):
        """agent-runtime/R100"""
        self.prepare()
        self.silent(0, age=60)
        for reply in ({"rc": 1, "err": "boom"}, {"rc": 0, "out": "not json"}):
            with self.subTest(reply=reply):
                self.replies["get:t1"] = reply
                self.replies["get:w1"] = reply
                (self.dir / "replies").write_text(json.dumps(self.replies))
                batch = self.wait_all()
                self.assertEqual(batch["reason"], "timeout")
                self.assertNotIn("unreported", json.dumps(batch["assignments"]))

    def test_rows_missing_reported_or_created_ms_are_never_flagged(self):
        """agent-runtime/R100"""
        for missing in ("reported", "created_ms"):
            with self.subTest(missing=missing):
                self.prepare()
                self.silent(0, age=60)
                row = json.loads(self.replies["get:t1"]["out"])
                del row[missing]
                self.replies["get:t1"] = self.replies["get:w1"] = {"out": json.dumps(row)}
                (self.dir / "replies").write_text(json.dumps(self.replies))
                batch = self.wait_all()
                self.assertEqual(batch["reason"], "timeout")

    def test_member_that_reported_once_costs_no_repeated_get(self):
        """agent-runtime/R106"""
        self.prepare()
        self.set_row(0, None, state="live", status_ms=1000, reported=True, turn=2,
                     created_ms=int((time.time() - 60) * 1000), exit_code=None)
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 2}))
        self.assertEqual(batch["reason"], "timeout")
        self.assertLessEqual(len([c for c in self.runs("get") if c[1] == "t1"]), 1)

    def test_hc_error_status_needs_attention(self):
        """agent-runtime/R104"""
        self.prepare()
        self.set_row(0, "error")
        batch = self.wait_all()
        self.assertEqual(batch["reason"], "attention")
        silent = next(a for a in batch["assignments"] if a["name"] == "w1")
        self.assertEqual(silent["runtime_state"], "error")
        self.assertIn("ask the user", silent["next"])

    def test_all_statuses_non_null_runs_no_hc_get(self):
        """agent-runtime/R106"""
        self.prepare()
        workers = lambda: [c for c in self.runs("get") if c[1] in ("w1", "w2", "t1", "t2")]
        before = len(workers())
        self.wait_all()
        self.assertEqual(len(workers()), before)


class WaitDescriptionTests(ToolCase):
    def test_S7_wait_description_names_the_early_return_and_the_word_budget(self):
        """S7: the wait description says it returns as soon as a report is undelivered, that unfinished lists
        the work still open and that the caller waits again for the rest; all descriptions stay within 400 words."""
        session = self.start()
        tools = session.send("tools/list")["result"]["tools"]
        description = next(t for t in tools if t["name"] == "wait")["description"]
        self.assertIn("undelivered", description)
        self.assertIn("unfinished", description)
        self.assertIn("wait again", description)
        words = 0
        for tool in tools:
            words += len(tool["description"].split())
            words += sum(len(p.get("description", "").split()) for p in tool["inputSchema"]["properties"].values())
        self.assertLessEqual(words, 400)
