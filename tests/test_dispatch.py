"""Durable dispatch over real MCP subprocesses, fake hc only."""

import json
import os
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest import mock

from test_mcp_server import RUNTIME_SRC, FakeCli
from test_operations import FakeHc
from test_team_limit import team_of
from test_tools import ToolCase, hc_in_process, raw

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import agent_runtime as lib
from agent_runtime import dispatch_wait, hc_tools, mcp_server
from agent_runtime.dispatch_store import MAX_BATCH_BYTES, Store, StoreError, TerminalRefused

pytestmark = pytest.mark.integration

# A generation whose process is gone: no process carries that pid, so the row is provably dead.
DEAD_GENERATION = "999999999:Wed Nov  4 10:00:00 2026"


class DispatchTests(ToolCase):
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

    def test_structured_reports_from_both_clis_without_hc_send(self):
        """agent-runtime/R34"""
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

    def test_failure_after_fast_complete_preserves_completion(self):
        """agent-runtime/R34"""
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

    def test_explicit_opt_in_uses_structured_path_and_validates_report(self):
        """agent-runtime/R58"""
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

    def test_replay_and_repeated_ack_are_idempotent(self):
        """agent-runtime/R36"""
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
        # Another team generation, on a terminal of its own, never sees this team's work: the late
        # report of a replaced assignment cannot land in its inbox.
        foreign = self.lead("foreign", socket="/hc/other")
        self.spawn(foreign)
        result, _ = self.publish(self.worker(), current, message_id="late")
        self.assertTrue(result["isError"])
        self.assertEqual(self.read(foreign)["messages"], [])

    def test_bounded_batches_and_large_payload_are_lossless(self):
        """agent-runtime/R53"""
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

    def test_exact_compact_array_boundary_and_one_byte_overflow_are_lossless(self):
        """S1/S2: new reports behind a full replay page return without losing replay.
        agent-runtime/R59
        agent-runtime-dispatch/S26"""
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

    def test_worker_binds_and_reports_with_its_own_hc_scope_token(self):
        """agent-runtime/R62"""
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

    def test_worker_turn_ended_without_final_report_returns_attention(self):
        """agent-runtime/R63"""
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

    def test_unsent_spawn_is_removed_and_can_be_spawned_again(self):
        """agent-runtime/R72
        agent-runtime/R64"""
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

    def test_address_reuse_surfaces_the_replaced_assignment_once(self):
        """agent-runtime/R65"""
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
        self.assertEqual(
            {m["assignment_id"] for m in members}, {old["assignment_id"], new["assignment_id"]}
        )
        batch = store.read(team, "t0", "all", assignment_ids=[m["assignment_id"] for m in members])
        self.assertEqual([m["text"] for m in batch["messages"]], ["stuck"])
        self.assertEqual(
            [m["assignment_id"] for m in store.snapshot(team, "t0", "all", batch["receipt"])],
            [new["assignment_id"]],
        )

    def test_runtime_failure_and_acknowledged_attention_stay_members(self):
        """agent-runtime/R65"""
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

    def test_messages_without_members_are_not_empty(self):
        """agent-runtime/R65"""
        from agent_runtime import dispatch_wait

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
        self.assertEqual(
            (later["reason"], later["assignments"], later["unfinished"]), ("empty", [], [])
        )

    def test_failed_spawn_deactivates_worker_and_rejects_message_and_resume(self):
        """agent-runtime/R64"""
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


class TeamOwnershipTests(ToolCase):
    """One rule owns a terminal: the newest team row that is neither dead nor empty.

    A row is dead when its generation is a `pid:lstart` stamp whose process no longer carries that
    stamp, and empty when it holds no worker, assignment, message, receipt, caller, task, last_wait
    row or operation.
    """

    def setUp(self):
        super().setUp()
        self.store = Store({"HOME": str(self.dir / "home")})
        self.scope, self.terminal = "scope", "t0"

    def live_generation(self):
        """A generation whose process runs right now: this test process."""
        return "%d:%s" % (os.getpid(), lib.lstart(os.getpid()))

    def other_live_generation(self):
        """A generation of another process that runs right now."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(proc.kill)
        self.addCleanup(proc.wait)
        return "%d:%s" % (proc.pid, lib.lstart(proc.pid))

    def stray_team(self, generation):
        """A newer team row for the terminal, written with SQL.

        The store's registration refuses a session that does not own the terminal, so a fixture that
        needs the state that refusal is about writes the row itself."""
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO teams VALUES (?,?,?,?)",
                ("stray", self.scope, self.terminal, generation),
            )
        return "stray"

    def test_S3_a_dead_and_empty_newer_row_does_not_fence_the_live_lead(self):
        """A session that left nothing behind never fences the terminal."""
        live = self.live_generation()
        team = self.store.team(self.scope, self.terminal, live)
        self.store.register(team, "w1")  # the live lead's team holds work
        self.stray_team(DEAD_GENERATION)  # newer, and empty
        actor = self.store.actor(self.scope, self.terminal, None, generation=live)
        self.assertEqual(actor["team_id"], team)

    def test_S4_a_live_newer_row_owns_the_terminal_and_fences_the_live_lead(self):
        """The live lead is fenced with 'stale team generation' and the message names the owner."""
        live = self.live_generation()
        team = self.store.team(self.scope, self.terminal, live)
        self.store.register(team, "w1")
        owner = self.other_live_generation()
        self.stray_team(owner)
        with self.assertRaises(StoreError) as error:
            self.store.actor(self.scope, self.terminal, None, generation=live)
        message = str(error.exception)
        self.assertIn("stale team generation", message)
        self.assertIn(self.terminal, message)
        self.assertIn(owner, message)

    def test_S5_a_dead_newer_row_that_holds_work_keeps_the_terminal(self):
        """A generation that is gone but still holds work keeps the terminal."""
        live = self.live_generation()
        team = self.store.team(self.scope, self.terminal, live)
        self.store.register(team, "w1")
        gone = self.stray_team(DEAD_GENERATION)
        self.store.register(gone, "w2")  # the gone session still holds a worker
        with self.assertRaises(StoreError) as error:
            self.store.actor(self.scope, self.terminal, None, generation=live)
        message = str(error.exception)
        self.assertIn("stale team generation", message)
        self.assertIn(DEAD_GENERATION, message)


class TerminalRefusalTests(ToolCase):
    """One terminal, one owning session: the refusal names the terminal, the owner and the way out.

    The store's team registration is the one seam both registration sites share, so a session that
    does not own the terminal registers nothing and is refused. The process evidence is injected,
    so a test can stand for the process table.
    """

    def setUp(self):
        super().setUp()
        self.env = {"HOME": str(self.dir / "home")}
        self.store = Store(self.env)
        self.scope, self.terminal = "scope", "t0"

    def live_generation(self):
        """A generation whose process runs right now: this test process."""
        return "%d:%s" % (os.getpid(), lib.lstart(os.getpid()))

    def other_live_generation(self):
        """A generation of another process that runs right now."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(proc.kill)
        self.addCleanup(proc.wait)
        return "%d:%s" % (proc.pid, lib.lstart(proc.pid))

    def dead_generation(self):
        """A generation whose process is gone: no process carries that start stamp."""
        return "%d:%s" % (os.getpid(), "a stamp no process carries")

    def hold_work(self, team):
        """The owner's team holds live work: a worker whose assignment is running.

        The state is written with SQL, the way the store holds it: the rule reads a team that is
        serving, and a fixture writes that state down."""
        with self.store.transaction() as db:
            db.execute("INSERT INTO workers VALUES (?,?,?,?,?)", ("w9", team, "w1", "t9", 1))
            db.execute("INSERT INTO assignments VALUES (?,?,?,'working',1)", ("a9", team, "w9"))

    def stray_team(self, generation):
        """A team row for another generation, written with SQL.

        The store refuses a generation it does not own, so a fixture that needs the state that
        refusal is about writes the row itself."""
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO teams VALUES (?,?,?,?)",
                ("stray", self.scope, self.terminal, generation),
            )

    def teams(self):
        with self.store.transaction() as db:
            return [
                dict(row) for row in db.execute("SELECT id,generation FROM teams ORDER BY rowid")
            ]

    def test_S1_a_session_that_descends_from_the_owner_is_refused(self):
        """stale-team-generation/S1: the session that descends from the owner never takes the terminal."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.store.register(team, "w1")  # the owner's team holds work
        owner_pid = int(owner.split(":")[0])
        descendant = Store(self.env, ancestors=lambda: [owner_pid])  # the owner is in this chain
        with self.assertRaises(TerminalRefused) as error:
            descendant.team(self.scope, self.terminal, "4242:descendant")
        message = str(error.exception)
        self.assertIn(self.terminal, message, "the refusal does not name the terminal")
        self.assertIn(owner, message, "the refusal does not name the owning generation")
        actor = self.store.actor(self.scope, self.terminal, None, generation=owner)
        self.assertEqual(actor["team_id"], team, "the owner lost its terminal")
        self.assertEqual(len(self.teams()), 1, "the descendant registered a team")

    def test_S1_a_descendant_is_refused_even_when_the_owners_team_is_idle(self):
        """stale-team-generation/S1: the owner's own session tree never takes the terminal, idle or not."""
        owner = self.live_generation()
        self.store.team(self.scope, self.terminal, owner)  # the owner's team holds nothing
        owner_pid = int(owner.split(":")[0])
        descendant = Store(self.env, ancestors=lambda: [owner_pid])
        with self.assertRaises(TerminalRefused):
            descendant.team(self.scope, self.terminal, "4242:descendant")
        self.assertEqual(len(self.teams()), 1, "the descendant registered a team")

    def test_S1_an_unrelated_session_is_refused_while_the_owner_is_alive_and_holds_work(self):
        """stale-team-generation/S1: a session that inherits a live lead's terminal is refused."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.hold_work(team)  # the owner's team holds work
        with self.assertRaises(TerminalRefused):
            self.store.team(self.scope, self.terminal, "4242:unrelated")
        self.assertEqual(len(self.teams()), 1, "the refused session registered a team")

    def test_S1_an_unrelated_session_takes_a_terminal_whose_owner_is_idle(self):
        """stale-team-generation/D7: an idle terminal is free for the next session."""
        owner = self.live_generation()
        self.store.team(self.scope, self.terminal, owner)  # the owner's team holds nothing
        team = self.store.team(self.scope, self.terminal, "4242:unrelated")
        self.assertNotEqual(team, None)
        self.assertEqual(len(self.teams()), 2, "the next session did not register a team")

    def test_S1_an_unrelated_session_takes_a_terminal_whose_owner_is_gone(self):
        """stale-team-generation/S3: a session that left nothing behind never keeps the terminal."""
        self.stray_team(self.dead_generation())  # a newer row whose process is gone
        team = self.store.team(self.scope, self.terminal, "4242:unrelated")
        self.assertIsNotNone(team)
        self.assertEqual(len(self.teams()), 2, "the next session did not register a team")

    def test_the_process_evidence_is_injected(self):
        """The store reads the process table only through the readers it is given."""
        stamp = "a stamp the fixture wrote"
        alive = Store(self.env, alive=lambda _pid: (True, stamp))
        team = alive.team(self.scope, self.terminal, "4242:%s" % stamp)
        self.hold_work(team)
        with self.assertRaises(TerminalRefused):
            alive.team(self.scope, self.terminal, "4243:unrelated")
        self.assertEqual(len(self.teams()), 1, "the refused session registered a team")

    def test_S6_the_refusal_and_the_fence_name_the_owner_and_the_way_out(self):
        """stale-team-generation/S6: a refusal names the reason and the way out."""
        owner = self.live_generation()
        team = self.store.team(self.scope, self.terminal, owner)
        self.hold_work(team)  # the owner's team holds work
        with self.assertRaises(TerminalRefused) as error:
            self.store.team(self.scope, self.terminal, "12345:other-session")
        message = str(error.exception)
        self.assertIn(self.terminal, message, "the refusal does not name the terminal")
        self.assertIn(owner, message, "the refusal does not name the owning generation")
        self.assertIn("hc terminal", message, "the refusal does not name the way out")
        self.assertIn("new session", message, "the refusal does not name the way out")
        self.assertEqual(len(self.teams()), 1, "the refused session registered a team")
        newer = self.other_live_generation()
        self.stray_team(newer)
        with self.assertRaises(StoreError) as fenced:
            self.store.actor(self.scope, self.terminal, None, generation=owner)
        text = str(fenced.exception)
        self.assertIn("stale team generation", text)
        self.assertIn(self.terminal, text, "the fence does not name the terminal")
        self.assertIn(newer, text, "the fence does not name the owning generation")


class ClockedWaitTests(FakeHc):
    """agent-runtime/R63: how a lead's `wait` reads a worker's turns, with the wait's clock injected.

    The server's call path runs in this process with the fake hc answered in process, and the wait's monotonic clock
    and listener are replaced: the seconds a wait would sit in its listener pass on the injected clock at once, so a
    `timeout` the test asks for costs no real time. What the wait answers is the signal the code produced; the real
    server over stdio is held by the wait tests of test_event_wait and by DispatchTests."""

    def setUp(self):
        super().setUp()
        self.now = 1000.0
        test = self

        class InjectedListener(dispatch_wait.Listener):
            def wait(
                self, seconds
            ):  # nothing publishes in process: the time a real wait takes just passes
                test.now += seconds

        self.enterContext(
            mock.patch.object(dispatch_wait, "time", SimpleNamespace(monotonic=lambda: test.now))
        )
        self.enterContext(mock.patch.object(dispatch_wait, "Listener", InjectedListener))
        self.session = {}
        self.env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "CODEX_THREAD_ID": "l",
            "HC_SCOPE": "lead-token",
            "HC_TERMINAL_SOCKET": str(self.socket_path),
            "AGENT_RUNTIME_DISPATCH": "1",
        }

    def lead(self, replies):
        """A lead whose fake hc answers `replies`, with the one worker w1 spawned; returns its assignment."""
        self.replies = dict(replies, **{"get:t0": {"out": raw("lead", None)}})
        self.install(self.replies)
        self.enterContext(hc_in_process(self.dir / "bin" / "hc"))
        result, text = self.call_tool(
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(result["isError"], text)
        return json.loads(text)

    def change(self, **replies):
        self.replies.update(replies)
        (self.dir / "replies").write_text(json.dumps(self.replies))

    def call_tool(self, tool, arguments):
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            result = mcp_server.call_tool(tool, arguments, self.env, hc_tools.Context(self.session))
        return result, result["content"][0]["text"]

    def wait(self, timeout):
        started = self.now
        result, text = self.call_tool("wait", {"target": "all", "timeout": timeout})
        self.assertFalse(result["isError"], text)
        return result["structuredContent"], self.now - started

    @staticmethod
    def terminals(turn, state="done"):
        row = {
            "address": "t9",
            "name": "w1",
            "parent": "t0",
            "turn": turn,
            "status": {"state": state},
        }
        return {"out": json.dumps({"terminals": [row]})}

    @staticmethod
    def worker(turn, state="idle"):
        row = json.loads(raw("w1", "t0", state))
        row["turn"] = turn
        return {"out": json.dumps(row)}

    def test_S7_a_wait_with_nothing_to_report_answers_timeout_when_the_injected_clock_reaches_it(
        self,
    ):
        """agent-runtime/R63"""
        real = time.monotonic()
        self.lead({"get:w1": self.worker(0), "ls": self.terminals(0, "idle")})
        batch, elapsed = self.wait(30)
        self.assertEqual(batch["reason"], "timeout")
        self.assertGreaterEqual(
            elapsed, 30, "the wait did not run to its timeout on the injected clock"
        )
        self.assertTrue(
            self.runs("ls"), "the wait never looked at the terminals it was waiting for"
        )
        self.assertLess(time.monotonic() - real, 10, "the 30 seconds were waited in real time")

    def test_fresh_spawn_idle_before_first_turn_is_not_attention(self):
        """agent-runtime/R63"""
        self.lead({"get:w1": self.worker(0), "ls": self.terminals(0, "idle")})
        batch, _ = self.wait(1)
        self.assertEqual(batch["reason"], "timeout")  # CLI still starting up
        self.assertTrue(self.runs("ls"), "the wait never looked at the terminal")
        self.change(ls=self.terminals(1))
        batch, _ = self.wait(2)
        self.assertEqual(batch["reason"], "attention")  # first turn ended, no report

    def test_resume_then_immediate_wait_ignores_the_old_done_turn(self):
        """agent-runtime/R63"""
        self.lead({"get:w1": self.worker(1, "idle"), "ls": self.terminals(1)})
        result, text = self.call_tool("resume", {"name": "w1", "task": "again"})
        self.assertFalse(result["isError"], text)
        batch, _ = self.wait(1)
        self.assertEqual(batch["reason"], "timeout")  # still the previous turn's done
        self.assertTrue(self.runs("ls"), "the wait never looked at the terminal")
        self.change(ls=self.terminals(2))
        batch, _ = self.wait(2)
        self.assertEqual(batch["reason"], "attention")
        self.assertEqual(len(batch["unfinished"]), 1)

    def test_resume_of_a_mid_turn_worker_ignores_the_old_turn_ending(self):
        """agent-runtime/R63"""
        self.lead({"get:w1": self.worker(3, "working"), "ls": self.terminals(3)})
        result, text = self.call_tool(
            "resume", {"name": "w1", "task": "again"}
        )  # recorded: working, turn 3
        self.assertFalse(result["isError"], text)
        self.change(ls=self.terminals(4))  # the turn that was already running ends
        batch, _ = self.wait(1)
        self.assertEqual(batch["reason"], "timeout")
        self.change(ls=self.terminals(5))  # the resumed turn ends
        batch, _ = self.wait(2)
        self.assertEqual(batch["reason"], "attention")


class ActivationTests(ToolCase):
    def test_status_without_hc_falls_back_to_legacy_with_dispatch_on(self):
        """agent-runtime/R56"""
        out = []
        for flag in ("0", "1"):
            session = self.start(
                env={"HC_TERMINAL_ID": "", "HERDR_ENV": "", "AGENT_RUNTIME_DISPATCH": flag}
            )
            result, text = self.call(session, "status", {"name": "w1"})
            out.append((result["isError"], text))
        self.assertEqual(out[0], out[1])
        self.assertNotIn("gap: add native support", out[1][1])

    def test_default_preserves_legacy_spawn_report_wait_resume_and_schema(self):
        """agent-runtime/R58"""
        for flag in (None, "0"):
            with self.subTest(flag=flag):
                env = {} if flag is None else {"AGENT_RUNTIME_DISPATCH": flag}
                lead = self.start(
                    {
                        "get:w1": {"out": raw("w1", "t0")},
                        "wait": {"out": '{"reason":"idle"}'},
                        "ls": {"out": '{"terminals":[]}'},
                    },
                    env,
                )
                result, text = self.call(
                    lead, "spawn", {"kind": "worker", "name": "w1", "task": "work", "cwd": "/tmp"}
                )
                self.assertFalse(result["isError"], text)
                self.assertEqual(json.loads(text), {"name": "w1", "address": "t9", "parent": "t0"})
                self.assertNotIn("structuredContent", result)
                self.assertNotIn("assignment_id", self.runs("run")[-1][-1])
                self.assertIn("report(text)", self.runs("run")[-1][-1])
                tools = (lead.send("tools/list") or {})["result"]["tools"]
                report_schema = next((t["inputSchema"] for t in tools if t["name"] == "report"), {})
                self.assertEqual(report_schema["required"], ["text"])
                result, text = self.call(lead, "wait", {"target": "any", "timeout": 20})
                self.assertFalse(result["isError"], text)
                self.assertEqual(
                    json.loads(text),
                    {"wait": {"exit": 0, "news": {"reason": "idle"}}, "workers": []},
                )
                self.assertEqual(
                    self.runs("wait")[-1],
                    ["wait", "--children", "any", "--timeout", "20", "--json"],
                )
                self.call(lead, "resume", {"name": "w1", "task": "continue"})
                self.assertEqual(
                    self.runs("send")[-1], ["send", "--wait", "--timeout", "170", "w1", "continue"]
                )
                worker = self.start(env=dict(env, HC_TERMINAL_ID="t1"))
                result, text = self.call(worker, "report", {"text": "done"})
                self.assertFalse(result["isError"], text)
                self.assertEqual(text, "reported to t0")
                self.assertEqual(self.runs("send")[-1], ["send", "t0", "done"])
                self.assertFalse((self.dir / "home/.local/state/agent-runtime").exists())
