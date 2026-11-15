"""Runtime status in a wait: what the hc probe observes, and which rows it costs."""

import json
import sys
import time
from unittest import mock

from test_event_wait import (
    EventRig,
)
from test_mcp_server import RUNTIME_SRC

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import dispatch_wait, hc_tools
from agent_runtime.dispatch_store import Store

pytestmark = pytest.mark.integration


class RuntimeStatusTests(EventRig):
    def test_a_slow_status_probe_does_not_hide_the_failed_worker(self):
        """agent-runtime/R166
        agent-runtime-dispatch/S27: the probe is what sees the exit; a probe that misses its budget
        would answer with the queued report instead of the failure."""
        self.prepare()
        self.rows[0]["status"] = "exited"
        replies = json.loads((self.dir / "replies").read_text())
        replies["ls"] = {"out": json.dumps({"terminals": self.rows}), "sleep": 0.5}
        (self.dir / "replies").write_text(json.dumps(replies))
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 5}))
        self.assertEqual(batch["reason"], "failed")

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
        batch = self.wait_all(timeout=0.3)
        self.assertEqual(batch["reason"], "timeout")
        self.assertNotIn("unreported", json.dumps(batch["assignments"]))

    def test_worker_that_reported_before_is_not_unreported(self):
        """agent-runtime/R102"""
        self.prepare()
        self.set_row(
            0,
            None,
            state="live",
            status_ms=1000,
            reported=True,
            turn=2,
            created_ms=int((time.time() - 60) * 1000),
            exit_code=None,
        )
        batch = self.wait_all(timeout=0.3)
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
                batch = self.wait_all(timeout=0.3)
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
                batch = self.wait_all(timeout=0.3)
                self.assertEqual(batch["reason"], "timeout")

    def test_member_that_reported_once_costs_no_repeated_get(self):
        """agent-runtime/R106"""
        self.prepare()
        self.set_row(
            0,
            None,
            state="live",
            status_ms=1000,
            reported=True,
            turn=2,
            created_ms=int((time.time() - 60) * 1000),
            exit_code=None,
        )
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

        def workers():
            return [c for c in self.runs("get") if c[1] in ("w1", "w2", "t1", "t2")]

        before = len(workers())
        self.wait_all(
            timeout=0.3
        )  # nothing to report: the wait takes its deadline, so keep it short
        self.assertEqual(len(workers()), before)
