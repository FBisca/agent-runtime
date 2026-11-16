"""A store failure in a tool handler rolls back what is reversible and names what is not (S7, S8).

The words a spawn, resume, message, task, report or inbox read owes when the store fails beneath it,
the state it leaves behind and the retry it names. The spawn cleanup that runs before the store is
reached stays in test_spawn_cleanup.py, beside this module."""

import json
import os
import sqlite3
import sys
from unittest import mock

import test_event_wait as event_helpers
from spawn_harness import ARGS, ENV
from test_mcp_server import RUNTIME_SRC
from tools_harness import raw

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import hc_tools
from agent_runtime.dispatch_store import Store

pytestmark = pytest.mark.integration


LOCKED = sqlite3.OperationalError("database is locked")


class StoreFailureTests(event_helpers.HcCase):
    """A store failure in a tool handler rolls back what is reversible and names what is not (S7, S8)."""

    def setUp(self):
        super().setUp()
        self.env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
        }
        self.env.update(ENV)
        # The identity lookup runs the hc binary with the process PATH, not the call's env.
        patched = mock.patch.dict(os.environ, {"PATH": self.env["PATH"]})
        patched.start()
        self.addCleanup(patched.stop)

    def lead(self, replies=None, task=False):
        """A lead with one live, bound worker w1 at t9; `task` links a task t1 to its assignment."""
        self.install_hc(dict({"get:w1": {"out": raw("w1", "t0")}}, **(replies or {})), ENV)
        self.store = Store(self.env)
        self.scope, generation = hc_tools.dispatch_identity(self.env)[:2]
        self.team = self.store.team(self.scope, "t0", generation)
        self.first = self.store.register(self.team, "w1")
        self.store.bind(self.first["assignment_id"], "t9", self.scope)
        if task:
            with self.store.transaction() as db:
                db.execute(
                    "INSERT INTO tasks VALUES ('t1',?,'s','in_progress',?,?,'[]')",
                    (self.team, self.first["worker_id"], self.first["assignment_id"]),
                )

    def call_tool(self, name, arguments, env=None):
        out = hc_tools.call(name, arguments, env or self.env, hc_tools.Context())
        return out, out["content"][0]["text"]

    def states(self):
        with self.store.transaction() as db:
            return [r[0] for r in db.execute("SELECT state FROM assignments ORDER BY rowid")]

    def task_row(self):
        with self.store.transaction() as db:
            return dict(db.execute("SELECT status,owner,assignment FROM tasks").fetchone())

    def test_S11_a_store_failure_driven_in_process_starts_no_server(self):
        """S11: the tool handler reads the fake binary and the store; no server answers for it."""
        started = self.count_sessions()
        self.lead()
        self.call_tool("message", {"to": "w1", "text": "hi"})
        self.assertEqual(started, [])

    def test_S7_resume_store_failure_before_the_send_fails_the_pending_assignment(self):
        """agent-runtime/R246"""
        self.lead(task=True)
        with mock.patch.object(Store, "mark_resume", side_effect=LOCKED):
            result, text = self.call_tool(
                "resume", {"name": "w1", "task": "again", "task_id": "t1"}
            )
        self.assertTrue(result["isError"])
        self.assertIn("store failure", text)
        self.assertIn("database is locked", text)
        self.assertIn("retry the resume", text)
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(self.states(), ["working", "failed"])
        self.assertEqual(
            self.task_row(),
            {
                "status": "in_progress",
                "owner": self.first["worker_id"],
                "assignment": self.first["assignment_id"],
            },
        )

    def test_S8_resume_store_failure_registering_sends_nothing_and_names_the_way_out(self):
        """agent-runtime/R247"""
        self.lead()
        with mock.patch.object(Store, "register", side_effect=LOCKED):
            result, text = self.call_tool("resume", {"name": "w1", "task": "again"})
        self.assertTrue(result["isError"])
        self.assertIn("store failure", text)
        self.assertIn("nothing was sent", text)
        self.assertIn("retry the resume", text)
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(self.states(), ["working"])

    def test_S8_resume_store_failure_after_the_send_names_the_send_and_the_pending_assignment(self):
        """agent-runtime/R247"""
        self.lead()
        with mock.patch.object(Store, "bind", side_effect=LOCKED):
            result, text = self.call_tool("resume", {"name": "w1", "task": "again"})
        self.assertTrue(result["isError"])
        self.assertEqual(len(self.runs("send")), 1)
        self.assertIn("was sent", text)
        self.assertIn("database is locked", text)
        with self.store.transaction() as db:
            newest = db.execute("SELECT id,state FROM assignments ORDER BY rowid DESC").fetchone()
        self.assertEqual(newest["state"], "pending")  # the worker's report still binds it
        self.assertIn(newest["id"], text)
        self.assertIn("pending", text)

    def test_S8_resume_store_failure_after_the_send_keeps_a_report_that_landed(self):
        """agent-runtime/R247"""
        self.lead()
        real_bind = Store.bind

        def bind(self_, assignment, *rest, **more):
            self_.publish(self.scope, "t9", "t0", assignment, "done", "complete", caller_name="w1")
            return real_bind(self_, assignment, *rest, **more)

        with mock.patch.object(Store, "bind", bind):
            result, text = self.call_tool("resume", {"name": "w1", "task": "again"})
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.states(), ["working", "complete"])

    def test_S8_resume_rollback_that_also_fails_names_the_assignment_left_pending(self):
        """agent-runtime/R247"""
        self.lead()
        with (
            mock.patch.object(Store, "mark_resume", side_effect=LOCKED),
            mock.patch.object(
                Store, "fail", side_effect=sqlite3.OperationalError("disk I/O error")
            ),
        ):
            result, text = self.call_tool("resume", {"name": "w1", "task": "again"})
        self.assertTrue(result["isError"])
        self.assertIn("database is locked", text)  # the original failure is not masked
        self.assertIn("disk I/O error", text)
        self.assertIn("pending", text)
        self.assertIn("nothing was sent", text)
        self.assertEqual(self.runs("send"), [])

    def test_S8_spawn_store_failure_registering_starts_nothing(self):
        """agent-runtime/R247"""
        self.lead()
        with mock.patch.object(Store, "register", side_effect=LOCKED):
            result, text = self.call_tool("spawn", dict(ARGS, name="w2"))
        self.assertTrue(result["isError"])
        self.assertIn("store failure", text)
        self.assertIn("nothing was started", text)
        self.assertIn("retry the spawn", text)
        self.assertEqual(self.runs("run"), [])

    def test_S8_spawn_bind_store_failure_names_the_removed_terminal(self):
        """agent-runtime/R247"""
        self.lead({"get:w2": {"out": raw("w2", "t0")}})
        with mock.patch.object(Store, "bind", side_effect=LOCKED):
            result, text = self.call_tool("spawn", dict(ARGS, name="w2"))
        self.assertTrue(result["isError"])
        self.assertIn("database is locked", text)
        self.assertIn("terminal was removed", text)
        self.assertIn("spawn it again", text)
        self.assertEqual(
            [c[:2] for c in self.runs() if c[0] in ("kill", "rm")], [["kill", "w2"], ["rm", "w2"]]
        )
        self.assertEqual(self.states(), ["working", "failed"])

    def test_S8_spawn_rollback_that_also_fails_removes_the_terminal_and_names_the_assignment(self):
        """agent-runtime/R247"""
        self.lead({"get:w2": {"out": raw("w2", "t0")}})
        with (
            mock.patch.object(Store, "bind", side_effect=LOCKED),
            mock.patch.object(
                Store, "fail", side_effect=sqlite3.OperationalError("disk I/O error")
            ),
        ):
            result, text = self.call_tool("spawn", dict(ARGS, name="w2"))
        self.assertTrue(result["isError"])
        self.assertIn("database is locked", text)
        self.assertIn("disk I/O error", text)
        self.assertIn("pending", text)
        self.assertIn("terminal was removed", text)
        self.assertEqual(
            [c[:2] for c in self.runs() if c[0] in ("kill", "rm")], [["kill", "w2"], ["rm", "w2"]]
        )

    def test_S8_spawn_refusal_keeps_its_reason_when_the_rollback_fails(self):
        """agent-runtime/R247"""
        self.lead({"run": {"rc": 1, "err": "name taken by another terminal"}})
        with mock.patch.object(
            Store, "fail", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            result, text = self.call_tool("spawn", dict(ARGS, name="w2"))
        self.assertTrue(result["isError"])
        self.assertIn("name taken by another terminal", text)
        self.assertIn("disk I/O error", text)
        self.assertIn("pending", text)

    def test_S8_message_store_failure_says_nothing_was_delivered(self):
        """agent-runtime/R247"""
        self.lead()
        with mock.patch.object(Store, "peer", side_effect=LOCKED):
            result, text = self.call_tool("message", {"to": "w1", "text": "hi"})
        self.assertTrue(result["isError"])
        self.assertIn("database is locked", text)
        self.assertIn("not delivered", text)
        self.assertIn("retry the message", text)

    def test_S8_task_store_failure_says_nothing_changed(self):
        """agent-runtime/R247"""
        self.lead()
        with mock.patch.object(Store, "task", side_effect=LOCKED):
            result, text = self.call_tool("task", {"action": "create", "subject": "s"})
        self.assertTrue(result["isError"])
        self.assertIn("database is locked", text)
        self.assertIn("not changed", text)
        self.assertIn("retry the task", text)

    def test_S8_report_store_failure_says_the_report_was_not_recorded(self):
        """agent-runtime/R247"""
        self.lead({"get:t9": {"out": raw("w1", "t0")}})
        env = dict(self.env, HC_TERMINAL_ID="t9")
        with mock.patch.object(Store, "publish", side_effect=LOCKED):
            result, text = self.call_tool(
                "report",
                {"text": "done", "kind": "complete", "assignment_id": self.first["assignment_id"]},
                env,
            )
        self.assertTrue(result["isError"])
        self.assertIn("database is locked", text)
        self.assertIn("not recorded", text)
        self.assertIn("retry the report", text)

    def inbox_read_fails(self, name, arguments, env=None):
        """The call's effect is committed when the inbox it piggybacks fails: the result must still be a success."""
        with mock.patch.object(Store, "read", side_effect=LOCKED):
            result, text = self.call_tool(name, arguments, env)
        self.assertFalse(result["isError"], text)

    def count(self, table, where="1"):
        with self.store.transaction() as db:
            return db.execute("SELECT COUNT(*) FROM %s WHERE %s" % (table, where)).fetchone()[0]

    def test_S8_a_committed_task_survives_an_inbox_read_failure(self):
        """agent-runtime/R247"""
        self.lead()
        self.inbox_read_fails("task", {"action": "create", "subject": "s"})
        self.assertEqual(self.count("tasks"), 1)

    def test_S8_a_committed_message_survives_an_inbox_read_failure(self):
        """agent-runtime/R247"""
        self.lead()
        self.inbox_read_fails("message", {"to": "w1", "text": "hi"})
        self.assertEqual(self.count("messages", "assignment IS NULL"), 1)

    def test_S8_a_committed_report_survives_an_inbox_read_failure(self):
        """agent-runtime/R247: a committed report is never discarded."""
        self.lead({"get:t9": {"out": raw("w1", "t0")}})
        self.inbox_read_fails(
            "report",
            {"text": "done", "kind": "complete", "assignment_id": self.first["assignment_id"]},
            dict(self.env, HC_TERMINAL_ID="t9"),
        )
        self.assertEqual(self.count("messages", "assignment IS NOT NULL"), 1)
        self.assertEqual(self.states(), ["complete"])

    def test_S8_a_started_worker_survives_an_inbox_read_failure(self):
        """agent-runtime/R247"""
        other = json.dumps({"address": "t8", "name": "w2", "parent": "t0", "status": "idle"})
        self.lead({"get:w2": {"out": other}})
        self.inbox_read_fails("spawn", dict(ARGS, name="w2"))
        self.assertEqual(self.states(), ["working", "working"])
