"""Spawn failures after hc run end the terminal; invalid spawns start none (hardening D4, D9)."""
import json
import sys
import time
from unittest import mock

from test_tools import ToolCase, raw
from test_mcp_server import SERVER

sys.path.insert(0, str(SERVER.parent))
import hc_tools
from dispatch_store import Store, StoreError

ENV = {"CODEX_THREAD_ID": "lead-session", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
       "AGENT_RUNTIME_DISPATCH": "1"}
ARGS = {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"}


class SpawnCleanupTests(ToolCase):
    def removed(self):
        return [c[:2] for c in self.runs() if c[0] in ("kill", "rm")]

    def assert_failed_and_inactive(self, expect_worker=True):
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            states = [r[0] for r in db.execute("SELECT state FROM assignments")]
            active = [r[0] for r in db.execute("SELECT active FROM workers")]
        self.assertEqual(states, ["failed"])
        self.assertEqual(active, [0])

    def test_unsent_first_message_removes_the_terminal_and_fails_the_assignment(self):
        """agent-runtime/R72"""
        lead = self.start({"run": {"out": json.dumps({"first_message": {"sent": False}})}}, ENV)
        result, _ = self.call(lead, "spawn", ARGS)
        self.assertTrue(result["isError"])
        self.assertEqual(self.removed(), [["kill", "w1"], ["rm", "w1"]])
        self.assert_failed_and_inactive()

    def test_unreadable_address_removes_the_terminal(self):
        """agent-runtime/R72"""
        lead = self.start({"get:w1": {"rc": 1, "err": "boom"}}, ENV)
        result, _ = self.call(lead, "spawn", ARGS)
        self.assertTrue(result["isError"])
        self.assertEqual(self.removed(), [["kill", "w1"], ["rm", "w1"]])
        self.assert_failed_and_inactive()

    def test_cancel_during_address_read_removes_the_terminal(self):
        """agent-runtime/R72"""
        lead = self.start({"get:w1": {"out": raw("w1", "t0"), "sleep": 30}}, ENV)
        write = lambda m: (lead.proc.stdin.write(json.dumps(m) + "\n"), lead.proc.stdin.flush())
        write({"jsonrpc": "2.0", "id": 70, "method": "tools/call", "params": {"name": "spawn", "arguments": ARGS}})
        deadline = time.monotonic() + 5
        while not self.runs("get") or not [c for c in self.runs("get") if c[1] == "w1"]:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 70}})
        lead.proc.stdin.close()
        self.assertEqual(lead.proc.wait(timeout=15), 0)
        self.assertEqual(self.removed(), [["kill", "w1"], ["rm", "w1"]])
        self.assert_failed_and_inactive()

    def test_duplicate_name_spawn_keeps_the_live_worker_active_and_reporting(self):
        """agent-runtime/R72"""
        lead = self.start({"get:w1": {"out": raw("w1", "t0")}, "live": []}, ENV)
        result, text = self.call(lead, "spawn", ARGS)
        self.assertFalse(result["isError"], text)
        first = result["structuredContent"]
        result, text = self.call(lead, "spawn", ARGS)  # hc refuses: w1 is live
        self.assertTrue(result["isError"])
        self.assertIn("name_taken", text)
        self.assertEqual(self.removed(), [])  # the live terminal is not ours to discard
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [1])
        store.publish("/hc/sock", "t9", "t0", first["assignment_id"], "still here", "progress", caller_name="w1")

    def test_interrupted_hc_run_discards_only_an_orphan_we_started(self):
        """agent-runtime/R72"""
        self.start({"get:w1": {"out": raw("w1", "t0")}}, ENV)
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"), "HC_TERMINAL_ID": "t0"}
        env.update(ENV)
        real_run = hc_tools.run

        def timing_out(argv, env_, ctx, timeout=10):
            if argv[1] == "run":
                raise hc_tools.ToolError("hc run timed out")
            return real_run(argv, env_, ctx, timeout)

        with mock.patch.object(hc_tools, "run", timing_out):
            with self.assertRaises(hc_tools.ToolError):
                hc_tools.spawn(ARGS, env, hc_tools.Context())
            self.assertEqual(self.removed(), [["kill", "w1"], ["rm", "w1"]])  # unbound orphan under our parent
            self.log.write_text("")
            # An earlier live w1 already bound to that address is not an orphan.
            store = Store(env)
            team = store.team(*hc_tools.dispatch_identity(env)[:1], "t0", hc_tools.dispatch_identity(env)[1])
            earlier = store.register(team, "w1")
            store.bind(earlier["assignment_id"], "t9", hc_tools.dispatch_identity(env)[0])
            with self.assertRaises(hc_tools.ToolError):
                hc_tools.spawn(ARGS, env, hc_tools.Context())
            self.assertEqual(self.removed(), [])
            with store.transaction() as db:
                self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [1])

    def test_bind_error_removes_the_terminal(self):
        """agent-runtime/R72"""
        self.start({"get:w1": {"out": raw("w1", "t0")}}, ENV)
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"), "HC_TERMINAL_ID": "t0"}
        env.update(ENV)
        with mock.patch.object(Store, "bind", side_effect=StoreError("assignment is stale")):
            with self.assertRaises(StoreError):  # the original error is re-raised (the server reports it)
                hc_tools.spawn(ARGS, env, hc_tools.Context())
        self.assertEqual(self.removed(), [["kill", "w1"], ["rm", "w1"]])
        self.assert_failed_and_inactive()

    def test_failed_respawn_of_an_existing_worker_leaves_it_inactive(self):
        """agent-runtime/R72"""
        lead = self.start({"get:w1": {"out": raw("w1", "t0")}, "live": []}, ENV)
        result, text = self.call(lead, "spawn", ARGS)
        self.assertFalse(result["isError"], text)
        self.replies = json.loads((self.dir / "replies").read_text())
        self.replies["live"] = []  # the earlier terminal is gone, so the name is free again
        self.replies["run"] = {"out": json.dumps({"first_message": {"sent": False}})}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        result, _ = self.call(lead, "spawn", ARGS)
        self.assertTrue(result["isError"])
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [0])

    def test_a_failed_unsent_spawn_can_be_spawned_again_with_the_same_name(self):
        """agent-runtime/R72"""
        lead = self.start({"run": {"out": json.dumps({"first_message": {"sent": False}})},
                           "get:w1": {"out": raw("w1", "t0")}}, ENV)
        result, _ = self.call(lead, "spawn", ARGS)
        self.assertTrue(result["isError"])
        replies = json.loads((self.dir / "replies").read_text())
        replies["run"] = {"out": ""}
        (self.dir / "replies").write_text(json.dumps(replies))
        result, text = self.call(lead, "spawn", ARGS)
        self.assertFalse(result["isError"], text)

    def test_failed_spawn_releases_its_linked_task(self):
        """agent-runtime/R72; agent-runtime/R69"""
        self.start({"run": {"out": json.dumps({"first_message": {"sent": False}})}}, ENV)
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"), "HC_TERMINAL_ID": "t0"}
        env.update(ENV)
        store = Store(env)
        real_register = Store.register

        def register_with_task(self_, team, name, *rest, **more):
            identity = real_register(self_, team, name, *rest, **more)
            with self_.transaction() as db:
                db.execute("INSERT INTO tasks VALUES ('t1',?,'s','in_progress',?,?,'[]')",
                           (team, identity["worker_id"], identity["assignment_id"]))
            return identity

        with mock.patch.object(Store, "register", register_with_task):
            with self.assertRaises(hc_tools.ToolError):
                hc_tools.spawn(ARGS, env, hc_tools.Context())
        with store.transaction() as db:
            self.assertEqual(tuple(db.execute("SELECT status,owner,assignment FROM tasks").fetchone()), ("pending", None, None))

    def test_unresolvable_identity_starts_no_terminal_and_registers_nothing(self):
        """agent-runtime/R73"""
        self.start({}, ENV)
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"), "HC_TERMINAL_ID": "t0"}
        env.update(ENV)
        with mock.patch.object(hc_tools, "dispatch_identity", side_effect=hc_tools.ToolError("no session")):
            with self.assertRaises(hc_tools.ToolError):
                hc_tools.spawn(ARGS, env, hc_tools.Context())
        self.assertEqual(self.runs("run"), [])
        with Store(env).transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)

    def test_codex_without_instructions_starts_no_terminal_and_registers_nothing(self):
        """agent-runtime/R73"""
        (self.dir / "home" / ".codex" / "agents" / "implementer.toml").unlink()
        lead = self.start({}, ENV)
        result, _ = self.call(lead, "spawn", dict(ARGS, cli="codex"))
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("run"), [])
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 0)
