"""Tasks link to workers on spawn and move on resume (hardening D8, D9, D11)."""
import json
import sys
import tempfile
import threading
import unittest

from test_tools import ToolCase, raw
from test_mcp_server import SERVER

sys.path.insert(0, str(SERVER.parent))
from dispatch_store import Store, StoreError

ENV = {"CODEX_THREAD_ID": "lead-session", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
       "AGENT_RUNTIME_DISPATCH": "1"}
SPAWN = {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"}


def add_task(store, team, task_id="t1", status="pending", deps=(), owner=None, assignment=None):
    with store.transaction() as db:
        db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?)",
                   (task_id, team, "subject", status, owner, assignment, json.dumps(list(deps))))


def task_row(store, task_id="t1"):
    with store.transaction() as db:
        return dict(db.execute("SELECT status,owner,assignment FROM tasks WHERE id=?", (task_id,)).fetchone())


class StoreLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store({"HOME": self.temp.name})
        self.team = self.store.team("test", "t0", "lead")

    def test_S13_register_links_a_ready_task_to_the_new_worker_and_assignment(self):
        """agent-runtime-hardening/S13"""
        add_task(self.store, self.team, "dep", "complete")
        add_task(self.store, self.team, deps=["dep"])
        identity = self.store.register(self.team, "w1", "t1")
        self.assertEqual(identity["task_id"], "t1")
        self.assertEqual(task_row(self.store), {"status": "in_progress", "owner": identity["worker_id"],
                                                "assignment": identity["assignment_id"]})
        self.store.bind(identity["assignment_id"], "t1", "test")
        self.store.publish("test", "t1", "t0", identity["assignment_id"], "done", "complete", caller_name="w1")
        self.assertEqual(task_row(self.store)["status"], "complete")

    def test_S14_invalid_tasks_are_refused_and_register_nothing(self):
        """agent-runtime-hardening/S14"""
        other = self.store.team("test", "t7", "other-lead")
        add_task(self.store, other, "foreign")
        add_task(self.store, self.team, "dep")
        add_task(self.store, self.team, "blocked-by-dep", deps=["dep"])
        add_task(self.store, self.team, "taken", "in_progress", owner="x", assignment="y")
        for task_id in ("missing", "foreign", "blocked-by-dep", "taken"):
            with self.subTest(task_id=task_id):
                with self.assertRaises(StoreError):
                    self.store.register(self.team, "w1", task_id)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workers").fetchone()[0], 0)
        self.assertEqual(task_row(self.store, "foreign")["status"], "pending")

    def test_S14_concurrent_claims_have_exactly_one_owner(self):
        """agent-runtime-hardening/S14"""
        add_task(self.store, self.team)
        outcomes = []

        def claim(name):
            try:
                outcomes.append(self.store.register(self.team, name, "t1"))
            except StoreError as error:
                outcomes.append(error)

        threads = [threading.Thread(target=claim, args=("w%d" % n,)) for n in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        winners = [o for o in outcomes if isinstance(o, dict)]
        self.assertEqual(len(winners), 1)
        self.assertEqual(task_row(self.store)["owner"], winners[0]["worker_id"])

    def test_S15_resume_moves_an_owned_task_to_the_fresh_assignment(self):
        """agent-runtime-hardening/S15"""
        first = self.store.register(self.team, "w1")
        self.store.bind(first["assignment_id"], "t1", "test")
        for status in ("in_progress", "blocked"):
            with self.subTest(status=status):
                add_task(self.store, self.team, "own-" + status, status, owner=first["worker_id"], assignment=first["assignment_id"])
                second = self.store.register(self.team, "w1", "own-" + status, transfer=True)
                self.assertEqual(task_row(self.store, "own-" + status),
                                 {"status": "in_progress", "owner": first["worker_id"],
                                  "assignment": second["assignment_id"]})
        add_task(self.store, self.team, "ready")
        third = self.store.register(self.team, "w1", "ready", transfer=True)
        self.assertEqual(task_row(self.store, "ready")["assignment"], third["assignment_id"])

    def test_S15_resume_refuses_another_workers_task_and_unmet_dependencies(self):
        """agent-runtime-hardening/S15"""
        add_task(self.store, self.team, "dep")
        add_task(self.store, self.team, "gated", deps=["dep"])
        add_task(self.store, self.team, "theirs", "in_progress", owner="someone-else", assignment="other-assignment")
        for task_id in ("gated", "theirs"):
            with self.assertRaises(StoreError):
                self.store.register(self.team, "w1", task_id, transfer=True)

    def test_S15_resume_without_task_id_leaves_links_unchanged(self):
        """agent-runtime-hardening/S15"""
        first = self.store.register(self.team, "w1", None)
        add_task(self.store, self.team, status="in_progress", owner=first["worker_id"], assignment=first["assignment_id"])
        before = task_row(self.store)
        self.store.register(self.team, "w1", transfer=True)
        self.assertEqual(task_row(self.store), before)


class ToolLinkTests(ToolCase):
    def lead(self, extra=None):
        replies = {"get:w1": {"out": raw("w1", "t0")}}
        replies.update(extra or {})
        return self.start(replies, ENV)

    def make_task(self, lead, **arguments):
        result, text = self.call(lead, "task", dict({"action": "create", "subject": "do it"}, **arguments))
        self.assertFalse(result["isError"], text)
        return result["structuredContent"]["task_id"]

    def store(self):
        return Store({"HOME": str(self.dir / "home")})

    def test_S13_spawn_with_task_id_returns_it_and_the_complete_report_finishes_the_task(self):
        """agent-runtime-hardening/S13"""
        lead = self.lead()
        task_id = self.make_task(lead)
        result, text = self.call(lead, "spawn", dict(SPAWN, task_id=task_id))
        self.assertFalse(result["isError"], text)
        identity = result["structuredContent"]
        self.assertEqual(identity["task_id"], task_id)
        store = self.store()
        self.assertEqual(task_row(store, task_id)["assignment"], identity["assignment_id"])
        store.publish("/hc/sock", "t9", "t0", identity["assignment_id"], "done", "complete", caller_name="w1")
        self.assertEqual(task_row(store, task_id)["status"], "complete")

    def test_S14_spawn_with_an_unready_task_starts_no_terminal(self):
        """agent-runtime-hardening/S14"""
        lead = self.lead()
        dep = self.make_task(lead)
        gated = self.make_task(lead, depends_on=[dep])
        for task_id in ("missing", gated):
            result, _ = self.call(lead, "spawn", dict(SPAWN, task_id=task_id))
            self.assertTrue(result["isError"], task_id)
        self.assertEqual(self.runs("run"), [])
        self.assertEqual(task_row(self.store(), gated)["status"], "pending")

    def test_S14_failed_spawn_releases_the_linked_task(self):
        """agent-runtime-hardening/S14"""
        lead = self.lead({"run": {"out": json.dumps({"first_message": {"sent": False}})}})
        task_id = self.make_task(lead)
        result, _ = self.call(lead, "spawn", dict(SPAWN, task_id=task_id))
        self.assertTrue(result["isError"])
        self.assertEqual(task_row(self.store(), task_id), {"status": "pending", "owner": None, "assignment": None})

    def test_S15_resume_with_task_id_moves_the_task_and_returns_it(self):
        """agent-runtime-hardening/S15"""
        lead = self.lead()
        task_id = self.make_task(lead)
        result, text = self.call(lead, "spawn", dict(SPAWN, task_id=task_id))
        first = result["structuredContent"]
        result, text = self.call(lead, "resume", {"name": "w1", "task": "again", "task_id": task_id})
        self.assertFalse(result["isError"], text)
        second = result["structuredContent"]
        self.assertEqual(second["task_id"], task_id)
        store = self.store()
        self.assertEqual(task_row(store, task_id)["assignment"], second["assignment_id"])
        store.publish("/hc/sock", "t9", "t0", second["assignment_id"], "done", "complete", caller_name="w1")
        self.assertEqual(task_row(store, task_id)["status"], "complete")
        self.assertNotEqual(first["assignment_id"], second["assignment_id"])

    def test_S15_failed_resume_restores_the_task_to_the_live_assignment(self):
        """agent-runtime-hardening/S15"""
        lead = self.lead()
        task_id = self.make_task(lead)
        first = self.call(lead, "spawn", dict(SPAWN, task_id=task_id))[0]["structuredContent"]
        replies = json.loads((self.dir / "replies").read_text())
        replies["send"] = {"out": json.dumps({"sent": False})}
        (self.dir / "replies").write_text(json.dumps(replies))
        result, _ = self.call(lead, "resume", {"name": "w1", "task": "again", "task_id": task_id})
        self.assertTrue(result["isError"])
        store = self.store()
        self.assertEqual(task_row(store, task_id), {"status": "in_progress", "owner": first["worker_id"],
                                                    "assignment": first["assignment_id"]})
        store.publish("/hc/sock", "t9", "t0", first["assignment_id"], "done", "complete", caller_name="w1")
        self.assertEqual(task_row(store, task_id)["status"], "complete")

    def test_S15_refused_resume_sends_nothing(self):
        """agent-runtime-hardening/S15"""
        lead = self.lead()
        dep = self.make_task(lead)
        gated = self.make_task(lead, depends_on=[dep])
        self.call(lead, "spawn", dict(SPAWN, task_id=self.make_task(lead)))
        for task_id in ("missing", gated):
            result, _ = self.call(lead, "resume", {"name": "w1", "task": "again", "task_id": task_id})
            self.assertTrue(result["isError"], task_id)
        self.assertEqual(self.runs("send"), [])

    def test_S15_resume_without_task_id_keeps_the_task_on_the_earlier_assignment(self):
        """agent-runtime-hardening/S15"""
        lead = self.lead()
        task_id = self.make_task(lead)
        first = self.call(lead, "spawn", dict(SPAWN, task_id=task_id))[0]["structuredContent"]
        result, text = self.call(lead, "resume", {"name": "w1", "task": "again"})
        self.assertFalse(result["isError"], text)
        self.assertNotIn("task_id", result["structuredContent"])
        self.assertEqual(task_row(self.store(), task_id)["assignment"], first["assignment_id"])
