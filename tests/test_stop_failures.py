"""Stop and observed worker failures never strand later waits (hardening D1, D2, D9, D12, D13)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import test_event_wait as event_helpers
from test_mcp_server import SERVER
from test_tools import ToolCase

sys.path.insert(0, str(SERVER.parent))
from dispatch_store import Store


NOT_FOUND = json.dumps({"error": {"hint": "see `hc ls --all`", "kind": "not_found", "message": 'no terminal "w1"'}})


def put_task(store, assignment, worker, team, status="in_progress"):
    with store.transaction() as db:
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,'[]')", ("task1", team, "subject", status, worker, assignment))


def task_row(store):
    with store.transaction() as db:
        return dict(db.execute("SELECT status,owner,assignment FROM tasks WHERE id='task1'").fetchone())


class StopFailureTests(ToolCase):
    prepare = event_helpers.EventWaitTests.prepare
    spawn = event_helpers.EventWaitTests.spawn
    batch = event_helpers.EventWaitTests.batch

    def set_reply(self, key, reply):
        self.replies[key] = reply
        (self.dir / "replies").write_text(json.dumps(self.replies))

    def wait(self, timeout=0, **extra):
        result = self.rpc_call("wait", dict({"target": "all", "timeout": timeout}, **extra))
        return result["structuredContent"]

    def rpc_call(self, name, arguments):
        result = self.rpc.get(self.rpc.tool(name, arguments))["result"]  # prepare() gave the lead's stdout to rpc
        self.assertFalse(result["isError"], result)
        return result

    def test_S1_stop_of_a_vanished_terminal_succeeds_and_later_waits_exclude_it(self):
        """agent-runtime-hardening/S1"""
        self.prepare()
        self.set_reply("get:w1", {"rc": 1, "err": NOT_FOUND})
        self.rpc_call("stop", {"name": "w1"})
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])
        batch = self.wait()
        self.assertEqual([a["name"] for a in batch["assignments"]], ["w2"])
        self.assertNotEqual(batch["reason"], "failed")

    def test_S1_other_hc_errors_leave_the_record_active(self):
        """agent-runtime-hardening/S1"""
        self.prepare()
        for err in ("timeout talking to hc", "cannot run hc: [Errno 2] No such file or directory",
                    json.dumps({"error": {"kind": "io", "message": "no such socket"}})):
            self.set_reply("get:w1", {"rc": 1, "err": err})
            result = self.rpc.get(self.rpc.tool("stop", {"name": "w1"}))["result"]
            self.assertTrue(result["isError"], err)
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])
        self.assertEqual({a["name"] for a in self.wait()["assignments"]}, {"w1", "w2"})

    def test_S2_stop_of_a_name_owned_by_another_lead_touches_only_our_record(self):
        """agent-runtime-hardening/S2"""
        self.prepare()
        other = dict(self.rows[0], parent="t7", address="t8")
        self.set_reply("get:w1", {"out": json.dumps(other)})
        self.rpc_call("stop", {"name": "w1"})
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])
        self.assertEqual([a["name"] for a in self.wait()["assignments"]], ["w2"])

    def test_S3_observed_exit_persists_one_failure_and_ack_clears_it(self):
        """agent-runtime-hardening/S3"""
        self.prepare()
        self.rows.remove(self.rows[0])  # w1's terminal disappeared from hc ls
        self.set_reply("ls", {"out": json.dumps({"terminals": self.rows})})
        first = self.wait(3)
        self.assertEqual(first["reason"], "failed")
        failures = [m for m in first["messages"] if m["kind"] == "failed"]
        self.assertEqual(len(failures), 1)
        again = self.wait(3)  # unacknowledged: same single message, never a second one
        self.assertEqual([m["message_id"] for m in again["messages"]], [failures[0]["message_id"]])
        later = self.wait(0, receipt=again["receipt"])
        self.assertEqual([a["name"] for a in later["assignments"]], ["w2"])
        self.assertEqual(later["messages"], [])

    def test_S3_replaced_terminal_is_a_persisted_failure_too(self):
        """agent-runtime-hardening/S3"""
        self.prepare()
        self.rows[0]["parent"] = "t7"  # the address now belongs to another lead's terminal
        self.set_reply("ls", {"out": json.dumps({"terminals": self.rows})})
        first = self.wait(3)
        self.assertEqual(first["reason"], "failed")
        self.assertEqual([m["kind"] for m in first["messages"]], ["failed"])
        later = self.wait(0, receipt=first["receipt"])
        self.assertEqual([a["name"] for a in later["assignments"]], ["w2"])

    def test_S4_observed_failure_returns_the_linked_task_to_pending(self):
        """agent-runtime-hardening/S4"""
        self.prepare()
        store = Store(self.env)
        put_task(store, self.assignments[0]["assignment_id"], self.assignments[0]["worker_id"], self.assignments[0]["team_id"])
        self.rows.remove(self.rows[0])
        self.set_reply("ls", {"out": json.dumps({"terminals": self.rows})})
        self.assertEqual(self.wait(3)["reason"], "failed")
        self.assertEqual(task_row(store), {"status": "pending", "owner": None, "assignment": None})

    def test_S3_resumed_worker_exit_persists_one_message_for_the_current_assignment(self):
        """agent-runtime-hardening/S3"""
        self.prepare()
        old = self.assignments[0]
        self.assignments[0] = self.rpc_call("resume", {"name": "w1", "task": "again"})["structuredContent"]
        store = Store(self.env)
        put_task(store, old["assignment_id"], old["worker_id"], old["team_id"])
        self.rows.remove(self.rows[0])
        self.set_reply("ls", {"out": json.dumps({"terminals": self.rows})})
        first = self.wait(3)
        failures = [m for m in first["messages"] if m["kind"] == "failed"]
        self.assertEqual([m["assignment_id"] for m in failures], [self.assignments[0]["assignment_id"]])
        with store.transaction() as db:
            states = [r[0] for r in db.execute("SELECT state FROM assignments WHERE worker=?", (old["worker_id"],))]
        self.assertEqual(states, ["failed", "failed"])


class StoreTaskReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store({"HOME": self.temp.name})
        self.team = self.store.team("test", "t0", "lead")
        self.identity = self.store.register(self.team, "w1")
        put_task(self.store, self.identity["assignment_id"], self.identity["worker_id"], self.team)

    def test_S4_stopped_worker_releases_its_unfinished_task(self):
        """agent-runtime-hardening/S4"""
        self.store.bind(self.identity["assignment_id"], "t1", "test")
        self.store.stopped(self.team, "w1")
        self.assertEqual(task_row(self.store), {"status": "pending", "owner": None, "assignment": None})

    def test_S4_failed_spawn_releases_its_unfinished_task(self):
        """agent-runtime-hardening/S4"""
        self.store.fail(self.identity["assignment_id"])
        self.assertEqual(task_row(self.store), {"status": "pending", "owner": None, "assignment": None})

    def test_S4_stopped_worker_releases_its_blocked_task(self):
        """agent-runtime-hardening/S4"""
        self.store.bind(self.identity["assignment_id"], "t1", "test")
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET status='blocked'")
        self.store.stopped(self.team, "w1")
        self.assertEqual(task_row(self.store), {"status": "pending", "owner": None, "assignment": None})

    def test_S4_failure_after_a_blocked_report_keeps_the_task_owned(self):
        """agent-runtime-hardening/S4"""
        self.store.bind(self.identity["assignment_id"], "t1", "test")
        self.store.publish("test", "t1", "t0", self.identity["assignment_id"], "stuck", "blocked", caller_name="w1")
        before = task_row(self.store)
        self.store.fail(self.identity["assignment_id"])
        self.assertEqual(task_row(self.store), before)
        self.assertEqual(before["status"], "blocked")
        self.assertIsNotNone(before["owner"])

    def test_S4_stop_keeps_a_completed_task(self):
        """agent-runtime-hardening/S4"""
        with self.store.transaction() as db:
            db.execute("UPDATE tasks SET status='complete'")
        self.store.stopped(self.team, "w1")
        self.assertEqual(task_row(self.store)["status"], "complete")


if __name__ == "__main__":
    unittest.main()
