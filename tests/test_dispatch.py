"""Durable dispatch over real MCP subprocesses, fake hc only: what a spawn binds, what a report
publishes, and what one wait delivers. The class stays in this module because its 33 public
methods are the only ones over the class-methods ceiling, and a new path has no recorded base
to inherit; the ownership rule, the clocked wait and the activation fallback live beside it."""

import json
import os
import stat
import sys

from dispatch_harness import DEAD_GENERATION
from test_mcp_server import RUNTIME_SRC, FakeCli
from test_team_limit import team_of
from tools_harness import ToolCase, raw

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime.dispatch_store import MAX_BATCH_BYTES, Store, StoreError

pytestmark = pytest.mark.integration


class DispatchTests(ToolCase):  # noqa: PLR0904
    def lead(self, generation="lead-session", socket="/hc/sock"):
        """The lead's session.

        A session that stands for a generation of its own runs under its own fake CLI, so it
        presents a generation of its own: the store registers its team when it first calls, and
        refuses a session that would take the terminal from the session that owns it.
        """
        cli = None
        if generation != "lead-session":
            cli = FakeCli()
            self.addCleanup(cli.close)
        return self.start(
            {"get:w1": {"out": raw("w1", "t0")}},
            {
                "CODEX_THREAD_ID": generation,
                "HC_SCOPE": "lead-token",
                "HC_TERMINAL_SOCKET": socket,
                "AGENT_RUNTIME_DISPATCH": "1",
            },
            new_cli=cli if cli is not None else False,
        )

    def stray_team(self, generation, socket="/hc/sock"):
        """A team row for the terminal, written with SQL.

        The store refuses a generation it does not own, so a fixture that needs the state that
        refusal is about writes the row itself."""
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            db.execute(
                "INSERT INTO teams VALUES (?,?,?,?)",
                ("stray-%s" % generation[:8], os.path.realpath(socket), "t0", generation),
            )
        return "stray-%s" % generation[:8]

    def spawn(self, lead, cli="claude"):
        result, text = self.call(
            lead,
            "spawn",
            {"kind": "worker", "cli": cli, "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(result["isError"], text)
        return json.loads(text)

    def worker(self, generation="worker-session", socket="/hc/sock"):
        return self.start(
            env={
                "HC_TERMINAL_ID": "t9",
                "HC_SCOPE": "worker-token",
                "HC_TERMINAL_SOCKET": socket,
                "CODEX_THREAD_ID": generation,
                "AGENT_RUNTIME_DISPATCH": "1",
            },
            replies={"get:t9": {"out": raw("w1", "t0")}},
            new_cli=generation != "worker-session",
        )

    def publish(self, worker, assignment, **extra):
        args = {"text": "done", "kind": "complete", "assignment_id": assignment["assignment_id"]}
        args.update(extra)
        return self.call(worker, "report", args)

    def seed_report(self, assignment, text, kind="progress", message_id=None):
        """A report of worker w1 written through the store, as its first publish leaves it (the assignment binds to
        the worker's address). For a test about what the lead does with many reports, not about publishing one: it
        starts no worker session and makes no call. Returns the envelope the lead will read."""
        store = Store({"HOME": str(self.dir / "home")})
        _, scope = team_of(store)
        return store.publish(
            scope, "t9", "t0", assignment["assignment_id"], text, kind, message_id, caller_name="w1"
        )

    def read(self, lead, **extra):
        result, text = self.call(lead, "wait", dict(target="all", timeout=0, **extra))
        self.assertFalse(result["isError"], text)
        self.assertEqual(result["structuredContent"], json.loads(text))
        return result["structuredContent"]

    @pytest.mark.spec("Terminal ownership")
    def test_S8_a_spawn_binds_to_the_leads_own_team_when_the_newer_row_is_dead_and_empty(self):
        """The binding and the authorization agree on who owns the terminal: a session that left nothing
        behind never takes the terminal from the lead that still works."""
        lead = self.lead()
        self.call(
            lead, "wait", {"target": "all", "timeout": 0}
        )  # the lead's own team row exists now
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            _, _, team = db.execute(
                "SELECT scope,lead,id FROM teams ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        self.stray_team(DEAD_GENERATION)  # a newer row that left nothing behind
        assignment = self.spawn(lead)
        self.assertEqual(assignment["team_id"], team)
        self.assertIsNotNone(store.worker_address(team, "w1"))

    @pytest.mark.spec("Tests start only what they exercise")
    def test_S10_seeded_reports_reach_the_lead_as_a_worker_session_would_publish_them(self):
        """A test about how the lead reads many reports seeds them through the store: no worker session starts."""
        lead = self.lead()
        assignment = self.spawn(lead)
        sent = [
            self.seed_report(assignment, "report %d" % i, message_id="seed-%d" % i)
            for i in range(3)
        ]
        batch = self.read(lead)
        self.assertEqual(batch["messages"], sent)
        self.assertEqual(
            sorted(batch["messages"][0]),
            sorted(
                [
                    "message_id",
                    "team_id",
                    "worker_id",
                    "assignment_id",
                    "sender",
                    "recipient",
                    "kind",
                    "text",
                ]
            ),
        )
        self.assertEqual(self.runs("send"), [], "a report went through hc send")

    @pytest.mark.spec("Structured report delivery")
    def test_structured_reports_from_both_clis_without_hc_send(self):
        for cli in ("claude", "codex"):
            # Each CLI's lead runs in its own terminal: a terminal belongs to one owning session.
            socket = "/hc/sock-%s" % cli
            lead = self.lead(cli, socket=socket)
            assignment = self.spawn(lead, cli)
            result, text = self.publish(self.worker(generation=cli, socket=socket), assignment)
            self.assertFalse(result["isError"], text)
            batch = self.read(lead)
            (message,) = batch["messages"]
            for key in ("team_id", "worker_id", "assignment_id"):
                self.assertEqual(message[key], assignment[key])
            self.assertTrue(message["message_id"])
            self.assertEqual(message["sender"], assignment["worker_id"])
            self.assertEqual(batch["reason"], "completed")
        self.assertEqual(self.runs("send"), [])

    @pytest.mark.spec("Structured report delivery")
    def test_restart_keeps_reports_and_receipts_private(self):
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

    @pytest.mark.spec("Structured report delivery")
    def test_fast_report_binds_pending_assignment_without_resetting_completion(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        identity = store.register(team, "w1")
        with self.assertRaises(StoreError):
            store.publish(
                "test",
                "t9",
                "t0",
                identity["assignment_id"],
                "done",
                "complete",
                caller_name="other",
            )
        store.publish(
            "test", "t9", "t0", identity["assignment_id"], "done", "complete", caller_name="w1"
        )
        store.bind(identity["assignment_id"], "t9", "test")
        self.assertEqual(store.read(team, "t0", "all")["reason"], "completed")

    @pytest.mark.spec("Structured report delivery")
    def test_failure_after_fast_complete_preserves_completion(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        identity = store.register(team, "w1")
        store.publish(
            "test", "t9", "t0", identity["assignment_id"], "done", "complete", caller_name="w1"
        )
        store.fail(identity["assignment_id"])
        batch = store.read(team, "t0", "all")
        self.assertEqual(batch["reason"], "completed")
        self.assertEqual(batch["unfinished"], [])

    @pytest.mark.spec("Dispatch activation")
    def test_explicit_opt_in_uses_structured_path_and_validates_report(self):
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        for missing in ("kind", "assignment_id"):
            args = {
                "text": "done",
                "kind": "complete",
                "assignment_id": assignment["assignment_id"],
            }
            del args[missing]
            result, _ = self.call(worker, "report", args)
            self.assertTrue(result["isError"])
        self.publish(worker, assignment)
        self.assertEqual(self.read(lead)["reason"], "completed")
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(self.runs("wait"), [])

    @pytest.mark.spec("Stale reports")
    def test_failed_launch_preserves_previous_assignment(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        old = store.register(team, "w1")
        store.bind(old["assignment_id"], "t9", "test")
        failed = store.register(team, "w1")
        store.fail(failed["assignment_id"])
        self.assertEqual(store.read(team, "t0", "all")["unfinished"], [old["assignment_id"]])

    @pytest.mark.spec("Stale reports")
    def test_stale_pending_cannot_replace_newer_registration_or_binding(self):
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
                    store.publish(
                        "test",
                        "t9",
                        "t0",
                        old["assignment_id"],
                        "late",
                        "complete",
                        caller_name=name,
                    )
                with self.assertRaises(StoreError):
                    store.bind(old["assignment_id"], "t9", "test")
                store.publish(
                    "test",
                    "t9",
                    "t0",
                    current["assignment_id"],
                    "current",
                    "complete",
                    caller_name=name,
                )
                batch = store.read(team, "t0", name)
                self.assertEqual(
                    [a["assignment_id"] for a in batch["assignments"]], [current["assignment_id"]]
                )
                self.assertEqual([m["text"] for m in batch["messages"]], ["current"])

    @pytest.mark.spec("Stale reports")
    def test_stale_pending_cannot_steal_address_from_new_team_generation(self):
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
                    store.publish(
                        "test",
                        "t9",
                        "t0",
                        candidate["assignment_id"],
                        "late",
                        "complete",
                        caller_name=name,
                    )
                with self.assertRaises(StoreError):
                    store.bind(candidate["assignment_id"], "t9", "test")
        store.publish(
            "test",
            "t9",
            "t0",
            current["assignment_id"],
            "current",
            "complete",
            caller_name="new-name",
        )
        self.assertEqual(store.read(old_team, "t0", "all")["messages"], [])
        self.assertEqual(store.read(new_team, "t0", "all")["reason"], "completed")

    @pytest.mark.spec("Receipt acknowledgment")
    def test_replay_and_repeated_ack_are_idempotent(self):
        lead = self.lead()
        assignment = self.spawn(lead)
        self.publish(self.worker(), assignment)
        first = self.read(lead)
        self.assertEqual(self.read(lead)["messages"], first["messages"])
        for _ in range(2):
            self.assertEqual(self.read(lead, receipt=first["receipt"])["messages"], [])
        result, _ = self.call(
            self.lead("other", socket="/hc/other"),
            "wait",
            {"target": "all", "timeout": 0, "receipt": first["receipt"]},
        )
        self.assertTrue(result["isError"])

    @pytest.mark.spec("Structured report delivery")
    def test_publication_deduplicates_and_conflicts(self):
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

    @pytest.mark.spec("Stale reports")
    def test_old_assignment_and_reused_address_are_isolated(self):
        lead = self.lead()
        old = self.spawn(lead)
        current = self.spawn(lead)
        result, _ = self.publish(self.worker(), old)
        self.assertTrue(result["isError"])
        self.assertEqual(self.read(lead)["unfinished"], [current["assignment_id"]])
        self.publish(self.worker(), current)
        # Another team generation, on a terminal of its own, never sees this team's work: the late
        # report of a replaced assignment cannot land in its inbox.
        foreign = self.lead("foreign", socket="/hc/other")
        self.spawn(foreign)
        result, _ = self.publish(self.worker(), current, message_id="late")
        self.assertTrue(result["isError"])
        self.assertEqual(self.read(foreign)["messages"], [])

    @pytest.mark.spec("Bounded results")
    def test_bounded_batches_and_large_payload_are_lossless(self):
        lead = self.lead()
        assignment = self.spawn(lead)
        for i in range(35):
            self.seed_report(assignment, str(i), message_id=str(i))
        first = self.read(lead)
        self.assertTrue(first["more"])
        self.assertTrue(first["receipt"])
        second = self.read(lead, receipt=first["receipt"])
        self.assertEqual(len(first["messages"]) + len(second["messages"]), 35)
        self.assertFalse(second["more"])
        # the one report that goes through a worker session is the one refused
        worker = self.worker()
        result, text = self.publish(worker, assignment, text="x" * 65537)
        self.assertTrue(result["isError"])
        self.assertIn("65536", text)
        self.assertEqual(self.read(lead)["messages"], second["messages"])
        self.read(lead, receipt=second["receipt"])
        for i in range(3):
            self.seed_report(assignment, "x" * 60000, message_id="large-%s" % i)
        page = self.read(lead)
        self.assertEqual(len(page["messages"]), 2)
        self.assertLess(len(json.dumps(page["messages"]).encode()), 131072)
        self.assertTrue(page["more"])
        last = self.read(lead, receipt=page["receipt"])
        self.assertEqual(len(last["messages"]), 1)
        self.assertFalse(last["more"])

    @pytest.mark.spec("Bounded results")
    def test_exact_compact_array_boundary_and_one_byte_overflow_are_lossless(self):
        """S1/S2: new reports behind a full replay page return without losing replay."""
        lead = self.lead()
        assignment = self.spawn(lead)

        def encode(value):
            return json.dumps(value, separators=(",", ":")).encode("utf-8")

        for overflow in (0, 1):
            with self.subTest(overflow_bytes=overflow):
                first_id, second_id = "boundary-%s-a" % overflow, "boundary-%s-b" % overflow
                first = self.seed_report(assignment, "x" * 65530, message_id=first_id)
                second = dict(first, message_id=second_id, text="y")
                padding = MAX_BATCH_BYTES + overflow - len(encode([first, second]))
                second = self.seed_report(assignment, "y" * (1 + padding), message_id=second_id)
                self.assertEqual(len(encode([first, second])), MAX_BATCH_BYTES + overflow)
                result, text = self.call(lead, "wait", {"target": "all", "timeout": 0})
                self.assertFalse(result["isError"], text)
                page = result["structuredContent"]
                self.assertIn('"messages":' + encode(page["messages"]).decode(), text)
                self.assertLessEqual(len(encode(page["messages"])), MAX_BATCH_BYTES)
                self.assertEqual(page["messages"], [first] if overflow else [first, second])
                self.assertEqual(page["more"], bool(overflow))
                replay = self.read(lead)
                self.assertEqual(replay["messages"], [second] if overflow else page["messages"])
                continuation = self.read(lead, receipt=page["receipt"])
                self.assertEqual(continuation["messages"], [second] if overflow else [])
                self.assertFalse(continuation["more"])
                self.assertEqual(self.read(lead, receipt=continuation["receipt"])["messages"], [])

    @pytest.mark.spec("Live scope binding")
    def test_worker_binds_and_reports_with_its_own_hc_scope_token(self):
        lead = self.lead()
        assignment = self.spawn(lead)
        worker = self.worker()
        result, text = self.call(
            worker,
            "wait",
            {"target": "inbox", "timeout": 0, "assignment_id": assignment["assignment_id"]},
        )
        self.assertFalse(result["isError"], text)
        result, text = self.publish(worker, assignment)
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.read(lead)["reason"], "completed")
        # A different hc server (socket) never reaches this team.
        other = self.start(
            env={
                "HC_TERMINAL_ID": "t9",
                "HC_SCOPE": "worker-token",
                "HC_TERMINAL_SOCKET": "/other/sock",
                "CODEX_THREAD_ID": "x",
                "AGENT_RUNTIME_DISPATCH": "1",
            },
            replies={"get:t9": {"out": raw("w1", "t0")}},
        )
        result, _ = self.publish(other, assignment, message_id="foreign")
        self.assertTrue(result["isError"])

    @pytest.mark.spec("Wait attention and failure")
    def test_worker_turn_ended_without_final_report_returns_attention(self):
        lead = self.start(
            {
                "get:w1": {"out": raw("w1", "t0", "working")},
                "ls": {
                    "out": json.dumps(
                        {
                            "terminals": [
                                {
                                    "address": "t9",
                                    "name": "w1",
                                    "parent": "t0",
                                    "turn": 1,
                                    "status": {"state": "done"},
                                }
                            ]
                        }
                    )
                },
            },
            {
                "CODEX_THREAD_ID": "l",
                "HC_SCOPE": "lead-token",
                "HC_TERMINAL_SOCKET": "/hc/sock",
                "AGENT_RUNTIME_DISPATCH": "1",
            },
        )
        assignment = self.spawn(lead)
        result, _ = self.call(lead, "wait", {"target": "all", "timeout": 2})
        batch = result["structuredContent"]
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(batch["unfinished"], [assignment["assignment_id"]])
        # A complete report committed first wins.
        self.publish(self.worker(), assignment)
        self.assertEqual(self.read(lead)["reason"], "completed")

    @pytest.mark.spec("Spawn failure cleanup")
    def test_unsent_spawn_is_removed_and_can_be_spawned_again(self):
        replies = {
            "run": {"out": json.dumps({"first_message": {"sent": False}})},
            "get:w1": {"out": raw("w1", "t0")},
        }
        lead = self.start(
            replies,
            {
                "CODEX_THREAD_ID": "l",
                "HC_SCOPE": "lead-token",
                "HC_TERMINAL_SOCKET": "/hc/sock",
                "AGENT_RUNTIME_DISPATCH": "1",
            },
        )
        args = {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"}
        result, _ = self.call(lead, "spawn", args)
        self.assertTrue(result["isError"])
        self.assertEqual(
            [c[:2] for c in self.runs() if c[0] in ("kill", "rm")], [["kill", "w1"], ["rm", "w1"]]
        )
        replies = json.loads((self.dir / "replies").read_text())
        replies["run"] = {"out": ""}
        (self.dir / "replies").write_text(json.dumps(replies))
        result, text = self.call(lead, "spawn", args)
        self.assertFalse(result["isError"], text)
        self.assertEqual([a["name"] for a in self.read(lead)["assignments"]], ["w1"])

    @pytest.mark.spec("Stale reports")
    def test_old_shape_assignment_insert_still_works_after_init(self):
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            db.execute("INSERT INTO assignments VALUES (?,?,?,'pending',0)", ("a", "t", "w"))

    @pytest.mark.spec("Stopped workers")
    def test_wait_all_excludes_stopped_and_acknowledged_assignments(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        stopped = store.register(team, "stopped")
        done = store.register(team, "done")
        live = store.register(team, "live")
        for n, identity in enumerate((stopped, done, live)):
            store.bind(identity["assignment_id"], "t%d" % (n + 1), "test")
        store.publish(
            "test", "t2", "t0", done["assignment_id"], "ok", "complete", caller_name="done"
        )
        before = store.snapshot(
            team, "t0", "all"
        )  # a wait already in progress keeps its members (S21)
        store.stopped(team, "stopped")
        self.assertEqual(len(before), 3)
        # The unacknowledged complete result is still a member; the stopped worker is not.
        names = {m["name"] for m in store.snapshot(team, "t0", "all")}
        self.assertEqual(names, {"done", "live"})
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in before])
        self.assertEqual(
            batch["reason"], "attention"
        )  # S21: the in-progress wait still sees the stop
        receipt = batch["receipt"]
        # After acknowledgement, only current unfinished work remains.
        self.assertEqual([m["name"] for m in store.snapshot(team, "t0", "all", receipt)], ["live"])
        store.stopped(team, "live")
        self.assertEqual(store.snapshot(team, "t0", "any"), [])
        self.assertEqual(store.read(team, "t0", "all", assignment_ids=[])["reason"], "empty")

    @pytest.mark.spec("Stopped workers")
    def test_address_reuse_surfaces_the_replaced_assignment_once(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        a, b = store.register(team, "a"), store.register(team, "b")
        store.bind(a["assignment_id"], "t1", "test")
        store.bind(
            b["assignment_id"], "t1", "test"
        )  # a's terminal vanished; the address was reused
        members = store.snapshot(team, "t0", "all")
        self.assertEqual(
            {m["assignment_id"] for m in members}, {a["assignment_id"], b["assignment_id"]}
        )
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in members])
        self.assertEqual(batch["reason"], "attention")
        (failure,) = batch["messages"]
        self.assertEqual(
            (failure["assignment_id"], failure["kind"]), (a["assignment_id"], "failed")
        )
        self.assertIn("replaced", failure["text"])
        # Once acknowledged the replaced assignment is gone; the live one remains.
        self.assertEqual(
            [m["assignment_id"] for m in store.snapshot(team, "t0", "all", batch["receipt"])],
            [b["assignment_id"]],
        )

    @pytest.mark.spec("Stopped workers")
    def test_unacknowledged_report_of_a_superseded_assignment_stays_a_member(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        old = store.register(team, "c")
        store.bind(old["assignment_id"], "t1", "test")
        store.publish("test", "t1", "t0", old["assignment_id"], "stuck", "blocked", caller_name="c")
        new = store.register(team, "c")
        store.bind(new["assignment_id"], "t1", "test")  # resumed before the lead waited
        members = store.snapshot(team, "t0", "all")
        self.assertEqual(
            {m["assignment_id"] for m in members}, {old["assignment_id"], new["assignment_id"]}
        )
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in members])
        self.assertEqual([m["text"] for m in batch["messages"]], ["stuck"])
        self.assertEqual(
            [m["assignment_id"] for m in store.snapshot(team, "t0", "all", batch["receipt"])],
            [new["assignment_id"]],
        )

    @pytest.mark.spec("Stopped workers")
    def test_runtime_failure_and_acknowledged_attention_stay_members(self):
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("test", "t0", "lead")
        working, blocked = store.register(team, "w"), store.register(team, "b")
        store.bind(working["assignment_id"], "t1", "test")
        store.bind(blocked["assignment_id"], "t2", "test")
        store.publish(
            "test", "t2", "t0", blocked["assignment_id"], "help", "decision", caller_name="b"
        )
        batch = store.read(team, "t0", "all")
        # w's terminal exited but the store still says working; b's decision is acknowledged but unanswered.
        names = {m["name"] for m in store.snapshot(team, "t0", "all", batch["receipt"])}
        self.assertEqual(names, {"w", "b"})

    @pytest.mark.spec("Stopped workers")
    def test_messages_without_members_are_not_empty(self):
        from agent_runtime import dispatch_wait

        batch = {"assignments": [], "messages": [{"message_id": "m"}], "unfinished": []}
        self.assertEqual(dispatch_wait.reason(batch, "all", {}), "messages")
        self.assertEqual(dispatch_wait.reason(dict(batch, messages=[]), "all", {}), "empty")

    @pytest.mark.spec("Stopped workers")
    def test_acknowledged_completion_leaves_the_next_wait_empty(self):
        lead = self.lead()
        assignment = self.spawn(lead)
        self.publish(self.worker(), assignment)
        first = self.read(lead)
        self.assertEqual(first["reason"], "completed")
        later = self.read(lead, receipt=first["receipt"])
        self.assertEqual(
            (later["reason"], later["assignments"], later["unfinished"]), ("empty", [], [])
        )

    @pytest.mark.spec("Spawn failure cleanup")
    def test_failed_spawn_deactivates_worker_and_rejects_message_and_resume(self):
        lead = self.start(
            {
                "run": {"rc": 1, "err": "bad name shaped like an id"},
                "get:w1": {"rc": 1, "err": "no such terminal"},
            },
            {
                "CODEX_THREAD_ID": "l",
                "HC_SCOPE": "lead-token",
                "HC_TERMINAL_SOCKET": "/hc/sock",
                "AGENT_RUNTIME_DISPATCH": "1",
            },
        )
        result, _ = self.call(
            lead, "spawn", {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"}
        )
        self.assertTrue(result["isError"])
        for tool, args in (
            ("message", {"to": "w1", "text": "hi"}),
            ("resume", {"name": "w1", "task": "go"}),
        ):
            result, text = self.call(lead, tool, args)
            self.assertTrue(result["isError"], tool)
            self.assertIn(
                "no such terminal" if tool == "resume" else "not an active teammate", text
            )
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(self.read(lead)["messages"], [])
