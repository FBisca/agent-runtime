"""The event-wait rig, plus the waits whose single body outlives the function-length ceiling.

The rig (AsyncRPC, LazySessions, HcCase, EventRig) lives here because every event-wait module imports
it. The report batches, resume and stall attention, runtime status and the rig's own callers live in
their own modules beside this one (test_event_wait_batches, ..._resume, ..._stall, ..._runtime,
..._harness, ..._usage); the tests that stayed are the ones a move would have had to edit to fit the
budget, and a move must not edit a test.
"""

import functools
import json
import queue
import sqlite3
import sys
import threading
import time
from unittest import mock

from test_mcp_server import RUNTIME_SRC, Session
from tools_harness import FAKE, ToolCase, raw

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import dispatch_store, dispatch_wait, hc_tools
from agent_runtime.dispatch_events import endpoints
from agent_runtime.dispatch_store import Store

pytestmark = pytest.mark.integration


# The harness waits for the server's answer, so its budget must outlast the server's own deadline:
# a wait that runs to its deadline answers after it, and a late reply is still a reply. The old 3s
# budget gave up before a 5s wait could answer and reported an empty queue for a reply on its way.
CLIENT_BUDGET = 60
# A wait that must answer early carries a deadline far away and a bound the machine can still meet:
# the claim is "the wait answered before its deadline", not "the machine answered within a second".
EARLY_DEADLINE = 30
EARLY_BOUND = 15
# A wait that must take its deadline is bounded by the client's budget, not by a second.
DEADLINE_BOUND = 30


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
        """Stop the reader before the session closes its streams: a thread on a closed file raises.

        The reader sits on the server's stdout until the server exits, and the server exits when its stdin
        closes: end the input first, then the join is the reader finishing, not a timeout.
        """
        self.session.proc.stdin.close()
        self.thread.join(timeout=10)

    def send(self, method, params=None, notify=False):
        self.sequence += 1
        message = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            message["id"] = self.sequence
        self.session.proc.stdin.write(json.dumps(message) + "\n")
        self.session.proc.stdin.flush()
        return self.sequence

    def get(self, request_id, timeout=CLIENT_BUDGET):
        deadline = time.monotonic() + timeout
        while request_id not in self.pending:
            response = self.replies.get(timeout=max(0, deadline - time.monotonic()))
            if isinstance(response, Exception):
                raise response
            self.pending[response["id"]] = response
        return self.pending.pop(request_id)

    def tool(self, name, arguments):
        return self.send("tools/call", {"name": name, "arguments": arguments})


class LazySessions:
    """Sessions indexed like a list, each started the first time a test reaches for it."""

    def __init__(self, start):
        self.start, self.sessions = start, {}

    def __getitem__(self, index):
        if index not in self.sessions:
            self.sessions[index] = self.start(index)
        return self.sessions[index]


class HcCase(ToolCase):
    """ToolCase with the fake hc installable on its own: a test that drives hc_tools in process, or reads the
    store, needs the binary and its replies but no server process behind them."""

    def install_hc(self, replies=None, env=None):
        """Write the fake hc and its replies; return the environment a server (or a call) would run under."""
        replies = dict(replies or {})
        replies.setdefault("get:t0", {"out": raw("lead", None)})
        replies.setdefault("get:t1", {"out": raw("worker", "t0")})
        (self.dir / "replies").write_text(json.dumps(replies))
        hc = self.dir / "bin" / "hc"
        hc.write_text(
            FAKE
            % {
                "python": sys.executable,
                "log": str(self.log),
                "replies": str(self.dir / "replies"),
                "overlay": str(self.dir / "overlay"),
            }
        )
        hc.chmod(0o755)
        full = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "CODEX_THREAD_ID": "test-session",
        }
        full.update(env or {})
        return full

    def count_sessions(self):
        """The HC_TERMINAL_ID of every server session started from now on, in order."""
        started = []
        real = Session.__init__

        def counted(session, env, new_cli=False):
            started.append(env.get("HC_TERMINAL_ID"))
            real(session, env, new_cli)

        patcher = mock.patch.object(Session, "__init__", counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        return started


class EventRig(HcCase):
    """A lead with two workers registered through its server; a worker's own session starts when a test first
    talks to it, since most tests only read what the lead's server answers."""

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
        # Bound to EventRig, not to self: test_budgets, test_diagnostics and test_tasks borrow prepare on a plain
        # ToolCase that has no worker_session.
        self.workers = LazySessions(functools.partial(EventRig.worker_session, self))
        self.rpc = AsyncRPC(self.lead)
        self.addCleanup(self.rpc.close)

    def worker_session(self, index):
        address = ("t1", "t2")[index]
        session = Session(
            dict(self.env, HC_TERMINAL_ID=address, CODEX_THREAD_ID="worker-" + address),
            new_cli=True,
        )
        self.addCleanup(session.close)
        session.handshake()
        return session

    def spawn(self, session, name, assignment_id=None):
        arguments = {"kind": "worker", "name": name, "task": "work", "cwd": "/tmp"}
        if assignment_id:
            arguments["assignment_id"] = assignment_id
        result, text = self.call(session, "spawn", arguments)
        self.assertFalse(result["isError"], text)
        return result["structuredContent"]

    def publish(self, index, kind="complete", text="done"):
        result, body = self.call(
            self.workers[index],
            "report",
            {"text": text, "kind": kind, "assignment_id": self.assignments[index]["assignment_id"]},
        )
        self.assertFalse(result["isError"], body)
        return result["structuredContent"]

    def batch(self, rpc, request_id):
        result = rpc.get(request_id)["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(result["structuredContent"], json.loads(result["content"][0]["text"]))
        return result["structuredContent"]

    def wait_all(self, timeout: float = EARLY_DEADLINE):
        """Wait for everything at once. A row that reads an answer carries a deadline far away, so the
        probe it waits for answers; a row that asserts the timeout passes its own short one."""
        return self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": timeout}))

    def pending(self, rpc, request_id):
        self.assertEqual(rpc.get(rpc.send("ping"))["result"], {})
        with self.assertRaises(queue.Empty):
            rpc.get(request_id, timeout=0.03)

    def begin(self, rpc=None, target="all", timeout=5):
        """Start a wait and return its request id once the wait is inside its listener.

        The barrier is the wait's own listener socket: the wait registers it after the membership
        snapshot, so the socket's presence means the wait is watching, whatever the host's speed.
        The wait is only inside its listener once a socket name that was not there before shows
        up, so a socket that goes away never satisfies the barrier.
        The budget is the wait's own timeout: a wait that never listens answers "timeout" instead,
        which the guard below reports as a wait that returned before entering its listener.
        """
        rpc = rpc or self.rpc
        directory = endpoints(Store(self.env).directory)
        listening = {path.name for path in directory.glob("*.sock")}
        request_id = rpc.tool("wait", {"target": target, "timeout": timeout})
        deadline = time.monotonic() + timeout + 1
        while not ({path.name for path in directory.glob("*.sock")} - listening):
            self.assertNotIn(request_id, rpc.counts, "wait returned before entering its listener")
            self.assertLess(time.monotonic(), deadline, "wait never entered its listener")
            threading.Event().wait(0.005)
        self.pending(rpc, request_id)
        return request_id

    def resume_rig(self, mode="idle", turn=3, before_resume=0, fast_report=None):
        now = [1_000_000.0]
        self.rigs = getattr(self, "rigs", 0) + 1
        env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / ("resume%d" % self.rigs)),
            "HC_SCOPE": "events",
            "HC_TERMINAL_ID": "t0",
            "AGENT_RUNTIME_DISPATCH": "1",
        }
        row = {"name": "w1", "address": "t1", "parent": "t0", "status": mode, "turn": turn}
        self.install_hc({"get:w1": {"out": json.dumps(row)}}, env)
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
                store.publish(
                    "events",
                    "t1",
                    "t0",
                    assignment,
                    "started during send",
                    fast_report,
                    caller_name="w1",
                )
            return run_hc(argv, *args, **kwargs)

        with mock.patch.object(hc_tools, "hc", side_effect=send_with_report):
            result = hc_tools.resume({"name": "w1", "task": "next task"}, env, ctx)
        assignment = result["structuredContent"]["assignment_id"]
        state = {"runtime": mode, "turn": turn}

        def observe(*_):
            return {assignment: state["runtime"], (assignment, "turn"): state["turn"]}

        def wait(reopened=None):
            return dispatch_wait.wait(
                reopened or store, team, "t0", "all", 0.05, None, hc_tools.Context(), observe
            )

        return store, team, assignment, now, state, wait

    def prepare_mcp_resume(self, mode="idle"):
        self.prepare()
        self.rows[0].update(status=mode, turn=3)
        self.replies["get:w1"] = {"out": json.dumps(self.rows[0])}
        self.replies["ls"] = {"out": json.dumps({"terminals": self.rows})}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        self.publish(0)
        page = self.batch(self.rpc, self.rpc.tool("wait", {"target": "inbox", "timeout": 0}))
        request = self.rpc.tool(
            "resume", {"name": "w1", "task": "next task", "receipt": page["receipt"]}
        )
        resumed = self.batch(self.rpc, request)
        self.assignments[0] = resumed
        self.resume_store = Store(self.env)
        return resumed["assignment_id"]

    def age_mcp_resume(self, assignment, seconds):
        # Seed persisted dispatch time; server time and the MCP/IPC paths stay real.
        with self.resume_store.transaction() as db:
            db.execute(
                "UPDATE resume_marks SET dispatched_ms=? WHERE assignment=?",
                (int((time.time() - seconds) * 1000), assignment),
            )

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
            return dispatch_wait.wait(
                store, team, "t0", "all", 0.3, None, hc_tools.Context(), observe
            )

        return store, team, assignment, now, state, wait

    def stalled(self, batch):
        return [a for a in batch["assignments"] if a.get("attention") == "stalled"]

    def reported_for(self, store, assignment):
        with store.transaction() as db:
            return db.execute(
                "SELECT stall_reported_for FROM stall_progress WHERE assignment=?", (assignment,)
            ).fetchone()[0]

    def set_row(self, index, status, **extra):
        """Rewrite w1/w2's hc ls row and hc get reply."""
        row = dict(self.rows[index], status=status, **extra)
        self.rows[index] = row
        self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
        for key in (row["name"], row["address"]):
            self.replies["get:" + key] = {"out": json.dumps(row)}
        (self.dir / "replies").write_text(json.dumps(self.replies))

    def silent(self, index=0, age=60, **extra):
        self.set_row(
            index,
            None,
            state="live",
            status_ms=None,
            reported=False,
            turn=0,
            created_ms=int((time.time() - age) * 1000),
            exit_code=None,
            **extra,
        )


class WaitRecordTests(EventRig):
    @pytest.mark.spec("Lead wait record")
    def test_S3_final_wait_records_reason_and_fingerprint_atomically(self):  # noqa: PLR0915
        """S3: a partial "messages" result leaves the last-wait row untouched."""
        store = Store({"HOME": str(self.dir / "home")})
        cases = (
            ("timeout", None, {}, 0),
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
            ("empty", None, {}, 0),
        )
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

                batch = dispatch_wait.wait(
                    store,
                    team,
                    "t0",
                    "all",
                    0 if not observation else 1,
                    None,
                    hc_tools.Context(),
                    observe,
                )
                self.assertEqual(batch["reason"], expected)
                self.assertNotIn("_attention", batch)
                with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as db:
                    row = db.execute(
                        "SELECT reason,attention,fingerprint FROM last_wait WHERE team=?", (team,)
                    ).fetchone()
                    self.assertEqual(
                        row, (expected, count, dispatch_store.wait_fingerprint(db, team))
                    )
                    if batch["receipt"]:
                        ids = db.execute(
                            "SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)
                        ).fetchone()[0]
                        self.assertEqual(
                            json.loads(ids), [m["message_id"] for m in batch["messages"]]
                        )
                        self.assertEqual(
                            db.execute("SELECT SUM(acked) FROM messages").fetchone()[0], 0
                        )
        # S3: a partial result (an undelivered report while other work is open) never records the row.
        team = store.team("events", "t0", "partial")
        first = store.register(team, "w1")["assignment_id"]
        store.bind(first, "t1", "events")
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events")
        dispatch_wait.wait(
            store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
        )  # timeout
        with store.transaction() as db:
            original = tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone())
        store.publish("events", "t1", "t0", first, "half done", "progress")
        batch = dispatch_wait.wait(
            store, team, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["unfinished"], [first, second])
        with store.transaction() as db:
            self.assertEqual(
                tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()),
                original,
            )
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
            dispatch_wait.wait(
                store, team, "t0", target, 0, None, hc_tools.Context(), lambda *_: {}
            )
            with store.transaction() as db:
                self.assertEqual(
                    tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()),
                    original,
                )
        store.read(team, "t0", "all", ready=lambda batch: None)
        store.read(team, "t0", "all")
        ctx = hc_tools.Context()

        def cancel(*_):
            ctx.cancelled.set()
            return {}

        with self.assertRaises(InterruptedError):
            dispatch_wait.wait(store, team, "t0", "all", 1, None, ctx, cancel)
        with store.transaction() as db:
            self.assertEqual(
                tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()),
                original,
            )
            before = db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
            db.execute(
                "CREATE TRIGGER reject_wait BEFORE INSERT ON last_wait "
                "BEGIN SELECT RAISE(ABORT, 'reject wait'); END"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "reject wait"):
            dispatch_wait.wait(store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {})
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], before)
            self.assertEqual(
                tuple(db.execute("SELECT * FROM last_wait WHERE team=?", (team,)).fetchone()),
                original,
            )

    @pytest.mark.spec("Lead wait record")
    def test_S4_read_only_fingerprint_changes_with_messages_states_and_turns(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events", turn=0)
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events", turn=0)
        batch = dispatch_wait.wait(
            store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
        )
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
            self.assertEqual(
                dispatch_store.last_wait_state(db, team), ("timeout", 0, stored, fingerprint())
            )
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

    def test_S4_wait_all_returns_completed_with_the_reports_and_writes_last_wait(self):
        """S4: every selected assignment complete -> completed, the reports, an empty unfinished list and the
        last_wait row written in the same transaction as the receipt."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        first = store.register(team, "w1")["assignment_id"]
        store.bind(first, "t1", "events")
        second = store.register(team, "w2")["assignment_id"]
        store.bind(second, "t2", "events")
        reports = [
            store.publish("events", "t1", "t0", first, "done", "complete"),
            store.publish("events", "t2", "t0", second, "done", "complete"),
        ]
        batch = dispatch_wait.wait(
            store, team, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], reports)
        self.assertEqual(batch["unfinished"], [])
        with store.transaction() as db:
            row = tuple(
                db.execute(
                    "SELECT reason,attention,fingerprint FROM last_wait WHERE team=?", (team,)
                ).fetchone()
            )
            self.assertEqual(row, ("completed", 0, dispatch_store.wait_fingerprint(db, team)))
            ids = db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[
                0
            ]
        self.assertEqual(json.loads(ids), [m["message_id"] for m in reports])
        # The row and the receipt commit together: a rejected row leaves no receipt behind.
        other = Store({"HOME": str(self.dir / "other")})
        team2 = other.team("events", "t0", "lead")
        third = other.register(team2, "w1")["assignment_id"]
        other.bind(third, "t1", "events")
        other.publish("events", "t1", "t0", third, "done", "complete")
        with other.transaction() as db:
            db.execute(
                "CREATE TRIGGER reject_wait BEFORE INSERT ON last_wait "
                "BEGIN SELECT RAISE(ABORT, 'reject wait'); END"
            )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "reject wait"):
            dispatch_wait.wait(
                other, team2, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {}
            )
        with other.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 0)

    @pytest.mark.spec("Wait attention and failure")
    @pytest.mark.spec("Wait all")
    def test_attention_cleared_during_finalization_cannot_become_early_timeout(self):
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
            batch = dispatch_wait.wait(
                store, team, "t0", "all", 3, None, hc_tools.Context(), finish
            )
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

    @pytest.mark.spec("Wait any and named")
    def test_concurrent_ack_during_finalization_keeps_batch_and_receipt_consistent(self):
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
            batch = dispatch_wait.wait(
                store, team, "t0", "any", 3, None, hc_tools.Context(), publish_next
            )
        self.assertTrue(raced)
        self.assertEqual(batch["reason"], "messages")
        self.assertTrue(batch["messages"])
        self.assertIn(batch["messages"][0]["text"], (progress["text"], "next progress"))
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 2)
            ids = json.loads(
                db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[0]
            )
        self.assertEqual(ids, [m["message_id"] for m in batch["messages"]])


class ReasonPrecedenceTests(EventRig):
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
        batch = dispatch_wait.wait(
            store, team, "t0", "all", 1, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(batch["unfinished"], [first, second])
        for runtime, expected in (
            ("exited", "failed"),
            ("unreported", "attention"),
            ("error", "attention"),
        ):
            with self.subTest(runtime=runtime):
                store, team, first, second = rig()
                store.publish("events", "t1", "t0", first, "half done", "progress")

                def observe(*_):
                    return {first: runtime, (first, "turn"): 0}

                batch = dispatch_wait.wait(
                    store, team, "t0", "all", 1, None, hc_tools.Context(), observe
                )
                self.assertEqual(batch["reason"], expected)
                self.assertEqual(batch["unfinished"], [first, second])
        # A stalled assignment wins over a pending report too.
        store, team, assignment, now, state, wait = self.stall_rig()
        store.publish("events", "t1", "t0", assignment, "half done", "progress")
        now[0] += 16 * 60
        batch = wait()
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual([a["assignment_id"] for a in self.stalled(batch)], [assignment])

    @pytest.mark.spec("Wait attention and failure")
    def test_reports_and_runtime_failures_need_attention_and_ended_turns_need_attention(self):
        """S1: an undelivered progress report returns at once; the attention report still wins over it."""
        self.prepare()
        for state in ("idle", "done", "blocked", "interrupted", "exited", "removed"):
            with self.subTest(runtime_state=state):
                self.rows[0]["status"] = state
                self.rows[0]["turn"] = 1  # a finished turn since dispatch
                self.replies["ls"]["out"] = json.dumps(
                    {"terminals": self.rows[1:] if state == "removed" else self.rows}
                )
                tmp = self.dir / "next-replies"
                tmp.write_text(json.dumps(self.replies))
                tmp.replace(self.dir / "replies")
                batch = self.wait_all()
                self.assertEqual(
                    batch["reason"],
                    "attention" if state in ("idle", "done", "blocked") else "failed",
                )
                self.assertEqual(len(batch["unfinished"]), 2)
                if (
                    batch["reason"] == "failed"
                ):  # D2: persisted, so a fresh assignment replaces the failed one
                    self.batch(
                        self.rpc,
                        self.rpc.tool(
                            "wait", {"target": "all", "timeout": 0, "receipt": batch["receipt"]}
                        ),
                    )
                    self.rows[0]["status"] = "working"
                    self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
                    (self.dir / "replies").write_text(json.dumps(self.replies))
                    self.assignments[0] = self.rpc.get(
                        self.rpc.tool(
                            "spawn",
                            {"kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"},
                        )
                    )["result"]["structuredContent"]
        self.rows[0]["status"] = "working"
        self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
        (self.dir / "replies").write_text(json.dumps(self.replies))
        for kind in ("blocked", "decision"):
            self.publish(0, "progress")
            first = self.wait_all()
            self.assertEqual(
                first["reason"], "messages"
            )  # the progress report is delivered at once
            event = self.publish(0, kind)
            batch = self.wait_all()
            self.assertEqual(batch["reason"], "attention")
            self.assertIn(event, batch["messages"])
            self.assertEqual(len(batch["unfinished"]), 2)
        self.publish(0)
        self.publish(1)
        self.assertEqual(
            self.batch(self.rpc, self.rpc.tool("wait", {"target": "all"}))["reason"], "completed"
        )

    @pytest.mark.spec("Wait attention and failure")
    def test_queued_progress_and_replay_surface_exited_or_interrupted_worker(self):
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
                    batch = self.batch(
                        self.rpc,
                        self.rpc.tool("wait", {"target": "any", "timeout": 0 if replay else 1}),
                    )
                    self.assertEqual(batch["reason"], "failed")
                    # D2: the observed failure is persisted once; a replay re-reads it without probing again.
                    self.assertEqual(
                        [(m["kind"], m["assignment_id"]) for m in batch["messages"]],
                        [
                            ("progress", progress["assignment_id"]),
                            ("failed", progress["assignment_id"]),
                        ],
                    )
                    self.assertEqual(batch["messages"][0], progress)
                    self.assertEqual(len(batch["unfinished"]), 2)
                    if not replay:
                        w1 = [a for a in batch["assignments"] if a["name"] == "w1"][0]
                        self.assertEqual(w1["runtime_state"], state)
                        self.assertEqual(len(self.runs("ls")), before + 1)
            receipt = batch["receipt"]
            self.batch(
                self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": receipt})
            )
            # The failed assignment is cleared; a fresh one for the same name continues the scenario.
            self.rows[0]["status"] = "working"
            self.replies["ls"]["out"] = json.dumps({"terminals": self.rows})
            (self.dir / "replies").write_text(json.dumps(self.replies))
            self.assignments[0] = self.rpc.get(
                self.rpc.tool(
                    "spawn", {"kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"}
                )
            )["result"]["structuredContent"]
        self.publish(0)
        self.publish(1)
        complete = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any", "timeout": 0}))
        self.assertEqual(complete["reason"], "completed")
        self.assertEqual(complete["unfinished"], [])
