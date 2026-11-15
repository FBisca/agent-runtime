"""Resume attention: a resumed assignment that never started is surfaced, not resent."""

import sys

from test_event_wait import (
    EventRig,
)
from test_mcp_server import RUNTIME_SRC

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import dispatch_wait, hc_tools
from agent_runtime.dispatch_store import Store

pytestmark = pytest.mark.integration


class ResumeAttentionTests(EventRig):
    def test_S12_swallowed_resume_returns_not_started_without_resending(self):
        """S12: an idle worker at the resume turn needs attention after 60 seconds."""
        store, team, assignment, now, state, wait = self.resume_rig()
        now[0] += 61
        batch = wait()
        self.assertEqual(batch["reason"], "attention")
        (member,) = batch["assignments"]
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
                self.assertTrue(
                    all(a.get("attention") != "not_started" for a in wait()["assignments"])
                )

    def test_S13_fast_report_during_resume_send_wins(self):
        """S13: a report before send returns binds once and suppresses not_started."""
        for kind in ("progress", "complete"):
            with self.subTest(kind=kind):
                store, team, assignment, now, state, wait = self.resume_rig(fast_report=kind)
                now[0] += 61
                batch = wait()
                self.assertEqual([m["kind"] for m in batch["messages"]], [kind])
                self.assertTrue(
                    all(a.get("attention") != "not_started" for a in batch["assignments"])
                )
                self.assertEqual(
                    store.assignment_state(assignment),
                    "working" if kind == "progress" else "complete",
                )

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
        store, team, assignment, now, state, wait = self.resume_rig(
            mode="working", fast_report="progress"
        )
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
        store, team, assignment, now, state, wait = self.resume_rig(
            mode="working", before_resume=3600
        )
        state["runtime"] = "idle"
        now[0] += 59
        self.assertEqual(wait()["reason"], "timeout")
        now[0] += 2
        (member,) = wait()["assignments"]
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
        (member,) = wait(reopened)["assignments"]
        self.assertEqual(member.get("attention"), "not_started")
        self.assertFalse(any(key.startswith("_") for key in member))
        with reopened.transaction() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            db.execute(
                "INSERT INTO assignments VALUES ('old-writer',?,?,'pending',0)",
                (team, member["worker_id"]),
            )
            self.assertEqual(
                db.execute("SELECT attention FROM last_wait WHERE team=?", (team,)).fetchone()[0], 1
            )

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

    def test_S13_mcp_pending_wait_wakes_for_resume_report(self):
        """S13: a report wakes the real IPC wait and suppresses overdue-resume attention after ack."""
        assignment = self.prepare_mcp_resume()
        request = self.begin()
        self.publish(0, kind="progress", text="new task started")
        batch = self.batch(self.rpc, request)
        self.assertEqual(batch["reason"], "messages")
        self.assertEqual([m["assignment_id"] for m in batch["messages"]], [assignment])
        self.age_mcp_resume(assignment, 61)
        after = self.batch(
            self.rpc,
            self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": batch["receipt"]}),
        )
        self.assertEqual(after["reason"], "timeout")
        self.assertTrue(all("attention" not in a for a in after["assignments"]))
