"""Event batches through real MCP processes: what one wait delivers, freezes, replays and wakes for."""

import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from test_event_wait import (
    DEADLINE_BOUND,
    EARLY_BOUND,
    EARLY_DEADLINE,
    AsyncRPC,
    EventRig,
)
from test_mcp_server import RUNTIME_SRC

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import dispatch_store, dispatch_wait, hc_tools
from agent_runtime.dispatch_events import endpoints
from agent_runtime.dispatch_store import Store

pytestmark = pytest.mark.integration


class EventBatchTests(EventRig):
    @pytest.mark.spec("Wait all")
    def test_all_buffers_progress_and_partial_completion_with_fixed_membership(self):
        """S1: wait(all) returns the first undelivered report, keeps its frozen membership and the caller
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
        self.assertEqual(
            [a["assignment_id"] for a in batch["assignments"]],
            [self.assignments[0]["assignment_id"], self.assignments[1]["assignment_id"]],
        )
        self.assertEqual(
            batch["unfinished"],
            [self.assignments[0]["assignment_id"], self.assignments[1]["assignment_id"]],
        )
        # The receipt drives the continuation: the acknowledged report is not delivered again.
        first = self.publish(0)
        store.stopped(self.assignments[0]["team_id"], "later")  # the late worker is gone
        following = self.batch(
            self.rpc,
            self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": batch["receipt"]}),
        )
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
        self.assertEqual(
            [a["assignment_id"] for a in batch["assignments"]],
            [self.assignments[0]["assignment_id"], self.assignments[1]["assignment_id"]],
        )
        self.assertEqual([a["state"] for a in batch["assignments"]], ["complete", "working"])
        self.assertEqual(batch["unfinished"], [self.assignments[1]["assignment_id"]])
        store = Store(self.env)
        with store.transaction() as db:
            ids = db.execute("SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)).fetchone()[
                0
            ]
        self.assertEqual(json.loads(ids), [report["message_id"]])

    @pytest.mark.spec("Wait all")
    def test_one_wait_returns_all_completed_reports_without_collect(self):
        """S4: both completions published before the wait still yield one completed batch (the early-delivery
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
        first = dispatch_wait.wait(
            store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(first["reason"], "messages")
        self.assertEqual(first["messages"], [report])
        # The receipt was not passed: the report stays readable, with the same message ID.
        again = dispatch_wait.wait(
            store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual([m["message_id"] for m in again["messages"]], [report["message_id"]])
        self.assertEqual(
            again["reason"], "timeout"
        )  # already delivered: the wait blocks for the deadline
        # Passing the receipt acknowledges it.
        third = dispatch_wait.wait(
            store, team, "t0", "all", 0, first["receipt"], hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(third["messages"], [])

    def test_S1_new_report_behind_unacknowledged_replay_page(self):
        """S1/S2: a full replay page cannot hide a new report; old reports remain readable."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        old = [
            store.publish("events", "t1", "t0", assignment, str(i), "progress") for i in range(2)
        ]
        with mock.patch.object(dispatch_store, "MAX_MESSAGES", 2):
            first = dispatch_wait.wait(
                store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
            )
            self.assertEqual(first["messages"], old)
            new = store.publish("events", "t1", "t0", assignment, "new", "progress")
            batch = dispatch_wait.wait(
                store, team, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
            )
            self.assertEqual(batch["reason"], "messages")
            self.assertIn(new, batch["messages"])
            with store.transaction() as db:
                ids = json.loads(
                    db.execute(
                        "SELECT ids FROM receipts WHERE id=?", (batch["receipt"],)
                    ).fetchone()[0]
                )
            self.assertEqual(ids, [m["message_id"] for m in batch["messages"]])
            replay = store.read(team, "t0", "all", issue_receipt=False)
            self.assertEqual(replay["messages"], old)

    def test_S6_wait_all_times_out_when_no_report_is_undelivered(self):
        """S6: no undelivered report and nothing finished -> the wait stays pending until the deadline."""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "events")
        started = time.monotonic()
        batch = dispatch_wait.wait(
            store, team, "t0", "all", 0.3, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertGreaterEqual(time.monotonic() - started, 0.25)
        self.assertEqual(batch["reason"], "timeout")
        self.assertEqual(batch["messages"], [])
        self.assertEqual(batch["unfinished"], [assignment])

    @pytest.mark.spec("Wait any and named")
    def test_any_batches_eligible_events_and_named_wait_excludes_old_assignment(self):
        self.prepare()
        old = self.publish(0)
        # The wait page delivers the report, so the server lets the lead reuse the name; the report stays
        # unacknowledged (D26) and is still delivered to wait(any).
        self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        replacement = self.rpc.tool(
            "spawn", {"kind": "worker", "name": "w1", "task": "again", "cwd": "/tmp"}
        )
        self.assignments[0] = self.rpc.get(replacement)["result"]["structuredContent"]
        first, second = self.publish(0), self.publish(1, "progress")
        named = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w1", "timeout": 0}))
        self.assertEqual(named["messages"], [first])
        self.assertEqual(
            [a["assignment_id"] for a in named["assignments"]], [first["assignment_id"]]
        )
        any_batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "any"}))
        self.assertEqual(any_batch["messages"], [old, first, second])
        self.assertEqual(any_batch["reason"], "messages")

    @pytest.mark.spec("Wait deadline and cancellation")
    def test_deadline_empty_and_cancel_preserve_replay_and_workers(self):
        """S6: a queued report returns at once; a genuinely empty queue waits for the deadline."""
        self.prepare()
        progress = self.publish(0, "progress")
        started = time.monotonic()
        batch = self.batch(
            self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": EARLY_DEADLINE})
        )
        self.assertLess(
            time.monotonic() - started, EARLY_BOUND
        )  # the undelivered report returns at once
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual(batch["messages"], [progress])
        started = time.monotonic()
        deadline = self.batch(self.rpc, self.rpc.tool("wait", {"target": "w2", "timeout": 0.2}))
        self.assertGreaterEqual(
            time.monotonic() - started, 0.18
        )  # an empty queue takes the deadline
        self.assertLess(time.monotonic() - started, DEADLINE_BOUND)
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

    @pytest.mark.spec("Wait recovers missed events")
    def test_commit_then_process_exit_without_hint_recovers_within_bound(self):
        self.prepare()
        request_id = self.begin(target="w1")
        assignment = self.assignments[0]
        envelope = dict(
            assignment,
            message_id="lost-hint",
            sender=assignment["worker_id"],
            recipient="t0",
            kind="complete",
            text="committed before crash",
        )
        script = """import json, os, sys
sys.path.insert(0, sys.argv[1])
from agent_runtime.dispatch_store import Store
store = Store({"HOME": sys.argv[2]})
message = json.loads(sys.argv[3])
with store.transaction() as db:
    db.execute("INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
               (message["message_id"],message["team_id"],"t0",message["assignment_id"],json.dumps(message,separators=(",",":"))))
    db.execute("UPDATE assignments SET state='complete' WHERE id=?", (message["assignment_id"],))
os._exit(0)
"""
        started = time.monotonic()
        subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(RUNTIME_SRC),
                self.env["HOME"],
                json.dumps(envelope),
            ],
            check=True,
        )
        batch = self.batch(self.rpc, request_id)
        # The recovery is the wait's own 0.5s recheck: ten of them are a finite bound, and the old
        # one-second bound measured the machine instead of the recovery.
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["messages"], [envelope])

    @pytest.mark.spec("Wait recovers missed events")
    def test_publication_between_snapshot_and_listener_is_rechecked(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("events", "t0", "lead")
        assignment = store.register(team, "w1")
        store.bind(assignment["assignment_id"], "t1", "events")
        snapshot = getattr(store, "snapshot", None)

        def race(*args, **kwargs):
            selection = snapshot(*args, **kwargs)
            store.publish("events", "t1", "t0", assignment["assignment_id"], "race", "complete")
            return selection

        with (
            mock.patch.object(hc_tools, "lead_store", return_value=(store, team)),
            mock.patch.object(store, "snapshot", side_effect=race, create=True),
        ):
            result = hc_tools.wait(
                {"target": "all", "timeout": 1},
                {"HC_TERMINAL_ID": "t0", "AGENT_RUNTIME_DISPATCH": "1"},
                hc_tools.Context(),
            )
        self.assertEqual(result["structuredContent"]["reason"], "completed")

    @pytest.mark.spec("Wait recovers missed events")
    def test_cross_process_ipc_wakes_without_waiting_for_recovery_timer(self):
        self.prepare()
        store = Store(self.env)
        observed = threading.Event()

        def observe(*_):
            observed.set()
            return {}

        with (
            mock.patch.object(dispatch_wait, "RECONCILE_INTERVAL", 30),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(
                dispatch_wait.wait,
                store,
                self.assignments[0]["team_id"],
                "t0",
                "w1",
                2,
                None,
                hc_tools.Context(),
                observe,
            )
            self.assertTrue(observed.wait(1), "listener never registered")
            message = self.publish(0)
            batch = future.result(timeout=1)
        self.assertEqual(batch["messages"], [message])
        self.assertEqual(batch["reason"], "completed")

    @pytest.mark.spec("Responsive MCP calls")
    def test_wait_keeps_same_process_ping_report_and_cancellation_responsive(self):
        self.prepare()
        child = self.spawn(
            self.workers[0], "child", assignment_id=self.assignments[0]["assignment_id"]
        )
        worker_rpc = AsyncRPC(self.workers[0])
        self.addCleanup(worker_rpc.close)
        request_id = self.begin(worker_rpc)
        report_id = worker_rpc.tool(
            "report",
            {
                "assignment_id": self.assignments[0]["assignment_id"],
                "kind": "complete",
                "text": "parent report",
            },
        )
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
