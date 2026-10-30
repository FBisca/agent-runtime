"""Durable dispatch over real MCP subprocesses, fake hc only."""
import json
import stat
import sys
from test_tools import ToolCase, raw
from test_mcp_server import SERVER

sys.path.insert(0, str(SERVER.parent))
from dispatch_store import MAX_BATCH_BYTES, Store, StoreError


class DispatchTests(ToolCase):
    def lead(self, generation="lead-session"):
        return self.start({"get:w1": {"out": raw("w1", "t0")}},
                          {"CODEX_THREAD_ID": generation, "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                           "AGENT_RUNTIME_DISPATCH": "1"})

    def spawn(self, lead, cli="claude"):
        result, text = self.call(lead, "spawn", {"kind": "worker", "cli": cli,
                                "name": "w1", "task": "work", "cwd": "/tmp"})
        self.assertFalse(result["isError"], text)
        return json.loads(text)

    def worker(self, generation="worker-session"):
        return self.start(env={"HC_TERMINAL_ID": "t9", "HC_SCOPE": "worker-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                              "CODEX_THREAD_ID": generation, "AGENT_RUNTIME_DISPATCH": "1"},
                          replies={"get:t9": {"out": raw("w1", "t0")}})

    def publish(self, worker, assignment, **extra):
        args = {"text": "done", "kind": "complete", "assignment_id": assignment["assignment_id"]}
        args.update(extra)
        return self.call(worker, "report", args)

    def read(self, lead, **extra):
        result, text = self.call(lead, "wait", dict(target="all", timeout=0, **extra))
        self.assertFalse(result["isError"], text)
        self.assertEqual(result["structuredContent"], json.loads(text))
        return result["structuredContent"]

    def test_structured_reports_from_both_clis_without_hc_send(self):
        """agent-runtime/R34"""
        for cli in ("claude", "codex"):
            lead = self.lead(cli)
            assignment = self.spawn(lead, cli)
            result, text = self.publish(self.worker(generation=cli), assignment)
            self.assertFalse(result["isError"], text)
            batch = self.read(lead)
            message, = batch["messages"]
            for key in ("team_id", "worker_id", "assignment_id"):
                self.assertEqual(message[key], assignment[key])
            self.assertTrue(message["message_id"])
            self.assertEqual(message["sender"], assignment["worker_id"])
            self.assertEqual(batch["reason"], "completed")
        self.assertEqual(self.runs("send"), [])

    def test_restart_keeps_reports_and_receipts_private(self):
        """agent-runtime/R35"""
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        self.publish(worker, assignment, message_id="restart")
        first = self.read(lead)
        worker.close()
        lead.close()
        result, text = self.publish(self.worker(), assignment, message_id="restart")
        self.assertFalse(result["isError"], text)
        restarted = self.lead()
        self.assertEqual(self.read(restarted)["messages"], first["messages"])
        self.assertEqual(self.read(restarted, receipt=first["receipt"])["messages"], [])
        path = self.dir / "home" / ".local" / "state" / "agent-runtime" / "dispatch.sqlite3"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_fast_report_binds_pending_assignment_without_resetting_completion(self):
        """agent-runtime/R34"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        identity = store.register(team, "w1")
        with self.assertRaises(StoreError):
            store.publish("test", "t9", "t0", identity["assignment_id"], "done", "complete", caller_name="other")
        store.publish("test", "t9", "t0", identity["assignment_id"], "done", "complete", caller_name="w1")
        store.bind(identity["assignment_id"], "t9", "test")
        self.assertEqual(store.read(team, "t0", "all")["reason"], "completed")

    def test_failure_after_fast_complete_preserves_completion(self):
        """agent-runtime/R34"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        identity = store.register(team, "w1")
        store.publish("test", "t9", "t0", identity["assignment_id"], "done", "complete", caller_name="w1")
        store.fail(identity["assignment_id"])
        batch = store.read(team, "t0", "all")
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["unfinished"], [])

    def test_explicit_opt_in_uses_structured_path_and_validates_report(self):
        """agent-runtime/R58"""
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        for missing in ("kind", "assignment_id"):
            args = {"text": "done", "kind": "complete", "assignment_id": assignment["assignment_id"]}
            del args[missing]
            result, _ = self.call(worker, "report", args)
            self.assertTrue(result["isError"])
        self.publish(worker, assignment)
        self.assertEqual(self.read(lead)["reason"], "completed")
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(self.runs("wait"), [])

    def test_failed_launch_preserves_previous_assignment(self):
        """agent-runtime/R46"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        old = store.register(team, "w1")
        store.bind(old["assignment_id"], "t9", "test")
        failed = store.register(team, "w1")
        store.fail(failed["assignment_id"])
        self.assertEqual(store.read(team, "t0", "all")["unfinished"], [old["assignment_id"]])

    def test_stale_pending_cannot_replace_newer_registration_or_binding(self):
        """agent-runtime/R46"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        for bound in (False, True):
            with self.subTest(newer_bound=bound):
                name = "worker-%s" % bound
                old = store.register(team, name)
                current = store.register(team, name)
                if bound:
                    store.bind(current["assignment_id"], "t9", "test")
                with self.assertRaises(StoreError):
                    store.publish("test", "t9", "t0", old["assignment_id"], "late", "complete", caller_name=name)
                with self.assertRaises(StoreError):
                    store.bind(old["assignment_id"], "t9", "test")
                store.publish("test", "t9", "t0", current["assignment_id"], "current", "complete", caller_name=name)
                batch = store.read(team, "t0", name)
                self.assertEqual([a["assignment_id"] for a in batch["assignments"]], [current["assignment_id"]])
                self.assertEqual([m["text"] for m in batch["messages"]], ["current"])

    def test_stale_pending_cannot_steal_address_from_new_team_generation(self):
        """agent-runtime/R46"""
        store = Store({"HOME": str(self.dir / "home")})
        old_team = store.team("test", "t0", "old-lead")
        old = store.register(old_team, "old-name")
        new_team = store.team("test", "t0", "new-lead")
        current = store.register(new_team, "new-name")
        store.bind(current["assignment_id"], "t9", "test")
        # Even a registration made later by the superseded generation has no authority.
        later_old = store.register(old_team, "another-old-name")
        for candidate, name in ((old, "old-name"), (later_old, "another-old-name")):
            with self.subTest(name=name):
                with self.assertRaises(StoreError):
                    store.publish("test", "t9", "t0", candidate["assignment_id"], "late", "complete", caller_name=name)
                with self.assertRaises(StoreError):
                    store.bind(candidate["assignment_id"], "t9", "test")
        store.publish("test", "t9", "t0", current["assignment_id"], "current", "complete", caller_name="new-name")
        self.assertEqual(store.read(old_team, "t0", "all")["messages"], [])
        self.assertEqual(store.read(new_team, "t0", "all")["reason"], "completed")

    def test_replay_and_repeated_ack_are_idempotent(self):
        """agent-runtime/R36"""
        lead = self.lead()
        assignment = self.spawn(lead)
        self.publish(self.worker(), assignment)
        first = self.read(lead)
        self.assertEqual(self.read(lead)["messages"], first["messages"])
        for _ in range(2):
            self.assertEqual(self.read(lead, receipt=first["receipt"])["messages"], [])
        result, _ = self.call(self.lead("other"), "wait", {"target": "all", "timeout": 0,
                                                            "receipt": first["receipt"]})
        self.assertTrue(result["isError"])

    def test_publication_deduplicates_and_conflicts(self):
        """agent-runtime/R37"""
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        for _ in range(2):
            result, text = self.publish(worker, assignment, message_id="retry")
            self.assertFalse(result["isError"], text)
        result, text = self.publish(worker, assignment, message_id="retry", text="changed")
        self.assertTrue(result["isError"])
        self.assertIn("conflict", text)
        self.assertEqual(len(self.read(lead)["messages"]), 1)

    def test_old_assignment_and_reused_address_are_isolated(self):
        """agent-runtime/R46"""
        lead = self.lead()
        old = self.spawn(lead)
        current = self.spawn(lead)
        result, _ = self.publish(self.worker(), old)
        self.assertTrue(result["isError"])
        self.assertEqual(self.read(lead)["unfinished"], [current["assignment_id"]])
        self.publish(self.worker(), current)
        self.assertEqual(self.read(self.lead("new-generation"))["messages"], [])
        foreign = self.lead("foreign")
        self.spawn(foreign)
        result, _ = self.publish(self.worker(), current, message_id="late")
        self.assertTrue(result["isError"])
        self.assertEqual(self.read(foreign)["messages"], [])

    def test_bounded_batches_and_large_payload_are_lossless(self):
        """agent-runtime/R53"""
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        for i in range(35):
            result, text = self.publish(worker, assignment, kind="progress", text=str(i), message_id=str(i))
            self.assertFalse(result["isError"], text)
        first = self.read(lead)
        self.assertTrue(first["more"])
        self.assertTrue(first["receipt"])
        second = self.read(lead, receipt=first["receipt"])
        self.assertEqual(len(first["messages"]) + len(second["messages"]), 35)
        self.assertFalse(second["more"])
        result, text = self.publish(worker, assignment, text="x" * 65537)
        self.assertTrue(result["isError"])
        self.assertIn("65536", text)
        self.assertEqual(self.read(lead)["messages"], second["messages"])
        self.read(lead, receipt=second["receipt"])
        for i in range(3):
            result, text = self.publish(worker, assignment, kind="progress", text="x" * 60000,
                                        message_id="large-%s" % i)
            self.assertFalse(result["isError"], text)
        page = self.read(lead)
        self.assertEqual(len(page["messages"]), 2)
        self.assertLess(len(json.dumps(page["messages"]).encode()), 131072)
        self.assertTrue(page["more"])
        last = self.read(lead, receipt=page["receipt"])
        self.assertEqual(len(last["messages"]), 1)
        self.assertFalse(last["more"])

    def test_exact_compact_array_boundary_and_one_byte_overflow_are_lossless(self):
        """agent-runtime/R59
        agent-runtime-dispatch/S26"""
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        encode = lambda value: json.dumps(value, separators=(",", ":")).encode("utf-8")
        for overflow in (0, 1):
            with self.subTest(overflow_bytes=overflow):
                first_id, second_id = "boundary-%s-a" % overflow, "boundary-%s-b" % overflow
                result, text = self.publish(worker, assignment, kind="progress", text="x" * 65530,
                                            message_id=first_id)
                self.assertFalse(result["isError"], text)
                first = result["structuredContent"]
                second = dict(first, message_id=second_id, text="y")
                padding = MAX_BATCH_BYTES + overflow - len(encode([first, second]))
                result, text = self.publish(worker, assignment, kind="progress", text="y" * (1 + padding),
                                            message_id=second_id)
                self.assertFalse(result["isError"], text)
                second = result["structuredContent"]
                self.assertEqual(len(encode([first, second])), MAX_BATCH_BYTES + overflow)
                result, text = self.call(lead, "wait", {"target": "all", "timeout": 0})
                self.assertFalse(result["isError"], text)
                page = result["structuredContent"]
                self.assertIn('"messages":' + encode(page["messages"]).decode(), text)
                self.assertLessEqual(len(encode(page["messages"])), MAX_BATCH_BYTES)
                self.assertEqual(page["messages"], [first] if overflow else [first, second])
                self.assertEqual(page["more"], bool(overflow))
                self.assertEqual(self.read(lead)["messages"], page["messages"])
                continuation = self.read(lead, receipt=page["receipt"])
                self.assertEqual(continuation["messages"], [second] if overflow else [])
                self.assertFalse(continuation["more"])
                self.assertEqual(self.read(lead, receipt=continuation["receipt"])["messages"], [])

    def test_worker_binds_and_reports_with_its_own_hc_scope_token(self):
        """agent-runtime/R62"""
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        result, text = self.call(worker, "wait", {"target": "inbox", "timeout": 0, "assignment_id": assignment["assignment_id"]})
        self.assertFalse(result["isError"], text)
        result, text = self.publish(worker, assignment)
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.read(lead)["reason"], "completed")
        # A different hc server (socket) never reaches this team.
        other = self.start(env={"HC_TERMINAL_ID": "t9", "HC_SCOPE": "worker-token", "HC_TERMINAL_SOCKET": "/other/sock",
                                "CODEX_THREAD_ID": "x", "AGENT_RUNTIME_DISPATCH": "1"},
                           replies={"get:t9": {"out": raw("w1", "t0")}})
        result, _ = self.publish(other, assignment, message_id="foreign")
        self.assertTrue(result["isError"])

    def test_worker_turn_ended_without_final_report_returns_attention(self):
        """agent-runtime/R63"""
        lead = self.start({"get:w1": {"out": raw("w1", "t0", "working")},
                           "ls": {"out": json.dumps({"terminals": [{"address": "t9", "name": "w1", "parent": "t0",
                                                                    "turn": 1, "status": {"state": "done"}}]})}},
                          {"CODEX_THREAD_ID": "l", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                           "AGENT_RUNTIME_DISPATCH": "1"})
        assignment = self.spawn(lead)
        result, text = self.call(lead, "wait", {"target": "all", "timeout": 2})
        batch = result["structuredContent"]
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(batch["unfinished"], [assignment["assignment_id"]])
        # A complete report committed first wins.
        self.publish(self.worker(), assignment)
        self.assertEqual(self.read(lead)["reason"], "completed")

    def test_fresh_spawn_idle_before_first_turn_is_not_attention(self):
        """agent-runtime/R63"""
        started = json.loads(raw("w1", "t0", "idle"))
        started["turn"] = 0
        row = {"address": "t9", "name": "w1", "parent": "t0", "turn": 0, "status": {"state": "idle"}}
        replies = {"get:w1": {"out": json.dumps(started)}, "ls": {"out": json.dumps({"terminals": [row]})},
                   "get:t0": {"out": raw("lead", None)}}
        lead = self.start(replies, {"CODEX_THREAD_ID": "l", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                                    "AGENT_RUNTIME_DISPATCH": "1"})
        self.spawn(lead)
        result, text = self.call(lead, "wait", {"target": "all", "timeout": 1})
        self.assertEqual(result["structuredContent"]["reason"], "timeout", text)  # CLI still starting up
        replies["ls"] = {"out": json.dumps({"terminals": [dict(row, turn=1, status={"state": "done"})]})}
        (self.dir / "replies").write_text(json.dumps(replies))
        result, text = self.call(lead, "wait", {"target": "all", "timeout": 2})
        self.assertEqual(result["structuredContent"]["reason"], "attention", text)  # first turn ended, no report

    def test_resume_then_immediate_wait_ignores_the_old_done_turn(self):
        """agent-runtime/R63"""
        def ls(turn):
            return {"out": json.dumps({"terminals": [{"address": "t9", "name": "w1", "parent": "t0", "turn": turn,
                                                       "status": {"state": "done"}}]})}
        worker = json.loads(raw("w1", "t0"))
        worker["turn"] = 1
        replies = {"get:w1": {"out": json.dumps(worker)}, "ls": ls(1), "get:t0": {"out": raw("lead", None)}}
        lead = self.start(replies, {"CODEX_THREAD_ID": "l", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                                    "AGENT_RUNTIME_DISPATCH": "1"})
        self.spawn(lead)
        result, text = self.call(lead, "resume", {"name": "w1", "task": "again"})
        self.assertFalse(result["isError"], text)
        result, text = self.call(lead, "wait", {"target": "all", "timeout": 1})
        self.assertEqual(result["structuredContent"]["reason"], "timeout")  # still the previous turn's done
        replies["ls"] = ls(2)
        (self.dir / "replies").write_text(json.dumps(replies))
        result, text = self.call(lead, "wait", {"target": "all", "timeout": 2})
        self.assertFalse(result["isError"], text)
        self.assertEqual(result["structuredContent"]["reason"], "attention")
        self.assertEqual(len(result["structuredContent"]["unfinished"]), 1)

    def test_resume_of_a_mid_turn_worker_ignores_the_old_turn_ending(self):
        """agent-runtime/R63"""
        def ls(turn):
            return {"out": json.dumps({"terminals": [{"address": "t9", "name": "w1", "parent": "t0", "turn": turn,
                                                       "status": {"state": "done"}}]})}
        worker = json.loads(raw("w1", "t0", "working"))
        worker["turn"] = 3
        replies = {"get:w1": {"out": json.dumps(worker)}, "ls": ls(3), "get:t0": {"out": raw("lead", None)}}
        lead = self.start(replies, {"CODEX_THREAD_ID": "l", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                                    "AGENT_RUNTIME_DISPATCH": "1"})
        self.spawn(lead)
        result, text = self.call(lead, "resume", {"name": "w1", "task": "again"})  # recorded: working, turn 3
        self.assertFalse(result["isError"], text)
        replies["ls"] = ls(4)  # the turn that was already running ends
        (self.dir / "replies").write_text(json.dumps(replies))
        result, _ = self.call(lead, "wait", {"target": "all", "timeout": 1})
        self.assertEqual(result["structuredContent"]["reason"], "timeout")
        replies["ls"] = ls(5)  # the resumed turn ends
        (self.dir / "replies").write_text(json.dumps(replies))
        result, _ = self.call(lead, "wait", {"target": "all", "timeout": 2})
        self.assertEqual(result["structuredContent"]["reason"], "attention")

    def test_S7_unsent_spawn_is_removed_and_can_be_spawned_again(self):
        """agent-runtime/R64"""
        replies = {"run": {"out": json.dumps({"first_message": {"sent": False}})}, "get:w1": {"out": raw("w1", "t0")}}
        lead = self.start(replies, {"CODEX_THREAD_ID": "l", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                                    "AGENT_RUNTIME_DISPATCH": "1"})
        args = {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"}
        result, _ = self.call(lead, "spawn", args)
        self.assertTrue(result["isError"])
        self.assertEqual([c[:2] for c in self.runs() if c[0] in ("kill", "rm")], [["kill", "w1"], ["rm", "w1"]])
        replies = json.loads((self.dir / "replies").read_text())
        replies["run"] = {"out": ""}
        (self.dir / "replies").write_text(json.dumps(replies))
        result, text = self.call(lead, "spawn", args)
        self.assertFalse(result["isError"], text)
        self.assertEqual([a["name"] for a in self.read(lead)["assignments"]], ["w1"])

    def test_old_shape_assignment_insert_still_works_after_init(self):
        """agent-runtime/R46"""
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            db.execute("INSERT INTO assignments VALUES (?,?,?,'pending',0)", ("a", "t", "w"))

    def test_wait_all_excludes_stopped_and_acknowledged_assignments(self):
        """agent-runtime/R65"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        stopped = store.register(team, "stopped")
        done = store.register(team, "done")
        live = store.register(team, "live")
        for n, identity in enumerate((stopped, done, live)):
            store.bind(identity["assignment_id"], "t%d" % (n + 1), "test")
        store.publish("test", "t2", "t0", done["assignment_id"], "ok", "complete", caller_name="done")
        before = store.snapshot(team, "t0", "all")  # a wait already in progress keeps its members (S21)
        store.stopped(team, "stopped")
        self.assertEqual(len(before), 3)
        # The unacknowledged complete result is still a member; the stopped worker is not.
        names = {m["name"] for m in store.snapshot(team, "t0", "all")}
        self.assertEqual(names, {"done", "live"})
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in before])
        self.assertEqual(batch["reason"], "attention")  # S21: the in-progress wait still sees the stop
        receipt = batch["receipt"]
        # After acknowledgement, only current unfinished work remains.
        self.assertEqual([m["name"] for m in store.snapshot(team, "t0", "all", receipt)], ["live"])
        store.stopped(team, "live")
        self.assertEqual(store.snapshot(team, "t0", "any"), [])
        self.assertEqual(store.read(team, "t0", "all", assignment_ids=[])["reason"], "empty")

    def test_address_reuse_surfaces_the_replaced_assignment_once(self):
        """agent-runtime/R65"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        a, b = store.register(team, "a"), store.register(team, "b")
        store.bind(a["assignment_id"], "t1", "test")
        store.bind(b["assignment_id"], "t1", "test")  # a's terminal vanished; the address was reused
        members = store.snapshot(team, "t0", "all")
        self.assertEqual({m["assignment_id"] for m in members}, {a["assignment_id"], b["assignment_id"]})
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in members])
        self.assertEqual(batch["reason"], "attention")
        failure, = batch["messages"]
        self.assertEqual((failure["assignment_id"], failure["kind"]), (a["assignment_id"], "failed"))
        self.assertIn("replaced", failure["text"])
        # Once acknowledged the replaced assignment is gone; the live one remains.
        self.assertEqual([m["assignment_id"] for m in store.snapshot(team, "t0", "all", batch["receipt"])],
                         [b["assignment_id"]])

    def test_unacknowledged_report_of_a_superseded_assignment_stays_a_member(self):
        """agent-runtime/R65"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        old = store.register(team, "c")
        store.bind(old["assignment_id"], "t1", "test")
        store.publish("test", "t1", "t0", old["assignment_id"], "stuck", "blocked", caller_name="c")
        new = store.register(team, "c")
        store.bind(new["assignment_id"], "t1", "test")  # resumed before the lead waited
        members = store.snapshot(team, "t0", "all")
        self.assertEqual({m["assignment_id"] for m in members}, {old["assignment_id"], new["assignment_id"]})
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in members])
        self.assertEqual([m["text"] for m in batch["messages"]], ["stuck"])
        self.assertEqual([m["assignment_id"] for m in store.snapshot(team, "t0", "all", batch["receipt"])],
                         [new["assignment_id"]])

    def test_runtime_failure_and_acknowledged_attention_stay_members(self):
        """agent-runtime/R65"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        working, blocked = store.register(team, "w"), store.register(team, "b")
        store.bind(working["assignment_id"], "t1", "test")
        store.bind(blocked["assignment_id"], "t2", "test")
        store.publish("test", "t2", "t0", blocked["assignment_id"], "help", "decision", caller_name="b")
        batch = store.read(team, "t0", "all")
        # w's terminal exited but the store still says working; b's decision is acknowledged but unanswered.
        names = {m["name"] for m in store.snapshot(team, "t0", "all", batch["receipt"])}
        self.assertEqual(names, {"w", "b"})

    def test_messages_without_members_are_not_empty(self):
        """agent-runtime/R65"""
        import dispatch_wait
        batch = {"assignments": [], "messages": [{"message_id": "m"}], "unfinished": []}
        self.assertEqual(dispatch_wait.reason(batch, "all", {}), "messages")
        self.assertEqual(dispatch_wait.reason(dict(batch, messages=[]), "all", {}), "empty")

    def test_acknowledged_completion_leaves_the_next_wait_empty(self):
        """agent-runtime/R65"""
        lead = self.lead()
        assignment = self.spawn(lead)
        self.publish(self.worker(), assignment)
        first = self.read(lead)
        self.assertEqual(first["reason"], "completed")
        later = self.read(lead, receipt=first["receipt"])
        self.assertEqual((later["reason"], later["assignments"], later["unfinished"]), ("empty", [], []))

    def test_failed_spawn_deactivates_worker_and_rejects_message_and_resume(self):
        """agent-runtime/R64"""
        lead = self.start({"run": {"rc": 1, "err": "bad name shaped like an id"},
                           "get:w1": {"rc": 1, "err": "no such terminal"}},
                          {"CODEX_THREAD_ID": "l", "HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock",
                           "AGENT_RUNTIME_DISPATCH": "1"})
        result, _ = self.call(lead, "spawn", {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"})
        self.assertTrue(result["isError"])
        for tool, args in (("message", {"to": "w1", "text": "hi"}), ("resume", {"name": "w1", "task": "go"})):
            result, text = self.call(lead, tool, args)
            self.assertTrue(result["isError"], tool)
            self.assertIn("no such terminal" if tool == "resume" else "not an active teammate", text)
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(self.read(lead)["messages"], [])


class ActivationTests(ToolCase):
    def test_status_without_hc_falls_back_to_legacy_with_dispatch_on(self):
        """agent-runtime/R56"""
        out = []
        for flag in ("0", "1"):
            session = self.start(env={"HC_TERMINAL_ID": "", "HERDR_ENV": "", "AGENT_RUNTIME_DISPATCH": flag})
            result, text = self.call(session, "status", {"name": "w1"})
            out.append((result["isError"], text))
        self.assertEqual(out[0], out[1])
        self.assertNotIn("gap: add native support", out[1][1])

    def test_default_preserves_legacy_spawn_report_wait_resume_and_schema(self):
        """agent-runtime/R58"""
        for flag in (None, "0"):
            with self.subTest(flag=flag):
                env = {} if flag is None else {"AGENT_RUNTIME_DISPATCH": flag}
                lead = self.start({"get:w1": {"out": raw("w1", "t0")},
                                   "wait": {"out": '{"reason":"idle"}'},
                                   "ls": {"out": '{"terminals":[]}'}}, env)
                result, text = self.call(lead, "spawn", {"kind": "worker", "name": "w1",
                                                       "task": "work", "cwd": "/tmp"})
                self.assertFalse(result["isError"], text)
                self.assertEqual(json.loads(text), {"name": "w1", "address": "t9", "parent": "t0"})
                self.assertNotIn("structuredContent", result)
                self.assertNotIn("assignment_id", self.runs("run")[-1][-1])
                tools = lead.send("tools/list")["result"]["tools"]
                report_schema = next(t["inputSchema"] for t in tools if t["name"] == "report")
                self.assertEqual(report_schema["required"], ["text"])
                result, text = self.call(lead, "wait", {"target": "any", "timeout": 20})
                self.assertFalse(result["isError"], text)
                self.assertEqual(json.loads(text), {"wait": {"exit": 0, "news": {"reason": "idle"}}, "workers": []})
                self.assertEqual(self.runs("wait")[-1], ["wait", "--children", "any", "--timeout", "20", "--json"])
                self.call(lead, "resume", {"name": "w1", "task": "continue"})
                self.assertEqual(self.runs("send")[-1], ["send", "--wait", "--timeout", "170", "w1", "continue"])
                worker = self.start(env=dict(env, HC_TERMINAL_ID="t1"))
                result, text = self.call(worker, "report", {"text": "done"})
                self.assertFalse(result["isError"], text)
                self.assertEqual(text, "reported to t0")
                self.assertEqual(self.runs("send")[-1], ["send", "t0", "done"])
                self.assertFalse((self.dir / "home/.local/state/agent-runtime").exists())
