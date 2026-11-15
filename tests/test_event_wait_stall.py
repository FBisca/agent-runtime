"""Stall attention: a working assignment that stops reporting is surfaced once per episode."""

import sqlite3
import sys
import time

from test_event_wait import (
    EventRig,
)
from test_mcp_server import RUNTIME_SRC

sys.path.insert(0, str(RUNTIME_SRC))
import pytest

pytestmark = pytest.mark.integration


class StallAttentionTests(EventRig):
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
            self.assertEqual(
                db.execute("SELECT attention FROM last_wait WHERE team=?", (team,)).fetchone()[0], 1
            )
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
                    self.assertEqual(
                        wait()["reason"], "messages"
                    )  # the report is delivered at once
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
            row = db.execute(
                "SELECT last_progress_ms FROM stall_progress WHERE assignment=?", (assignment,)
            ).fetchone()
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
            self.assertEqual(
                db.execute("SELECT state FROM assignments WHERE id=?", (assignment2,)).fetchone()[
                    0
                ],
                "working",
            )

    def test_needs_person_runtime_keeps_its_own_next_and_is_not_stalled(self):
        """agent-runtime/R111"""
        from agent_runtime.agent_runtime import ERROR_NEXT, UNREPORTED_NEXT

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
        batch = (
            wait()
        )  # The blocked report returns at once, before any observation can start a stall clock.
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(self.stalled(batch), [])
        with store.transaction() as db:
            self.assertEqual(
                db.execute("SELECT attention FROM last_wait WHERE team=?", (team,)).fetchone()[0], 1
            )
            self.assertIsNone(
                db.execute(
                    "SELECT 1 FROM stall_progress WHERE assignment=?", (assignment,)
                ).fetchone()
            )

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
