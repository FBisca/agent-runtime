"""Operation keys: `operation_id` on spawn and resume, replay and conflict, and the owner-scoped lookup.

The store is real (a temporary HOME), hc is the fake of test_tools, and the calls are the server's own call path in
process, so every behaviour here is observable the way an MCP client or `agent-runtime call` sees it.
"""

import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

import pytest
from test_mcp_server import RUNTIME_SRC
from test_runtime_audit_outcome import ENV, FakeHc, name, worker_rows

sys.path.insert(0, str(RUNTIME_SRC))
from agent_runtime import hc_tools, mcp_server  # noqa: E402
from agent_runtime.dispatch_store import Store, StoreError  # noqa: E402

pytestmark = pytest.mark.integration

SPAWN = {"kind": "worker", "name": name(0), "task": "work", "cwd": "/tmp"}


class Operations(FakeHc):
    """A lead terminal t0 with four fake children, and the server's call path in this process."""

    def setUp(self):
        super().setUp()
        self.install(worker_rows(4))
        self.session = {}
        self.env = dict(
            {
                "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
                "HOME": str(self.dir / "home"),
                "HC_TERMINAL_ID": "t0",
            },
            **ENV,
        )

    def call_tool(self, tool, arguments):
        # the resolver's `hc get` runs on the process PATH: point it at the fake hc, never a real one
        with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
            out = mcp_server.call_tool(tool, arguments, self.env, hc_tools.Context(self.session))
        return out, out["content"][0]["text"]

    def store(self):
        return Store(self.env)

    def rows(self, table="operations"):
        with self.store().transaction() as db:
            return [tuple(r) for r in db.execute("SELECT * FROM %s ORDER BY rowid" % table)]

    def owner(self):
        return hc_tools.operation_owner(self.env, "t0")

    def lookup(self, operation_id="op-1"):
        out, text = self.call_tool("operation", {"operation_id": operation_id})
        self.assertFalse(out["isError"], text)
        return json.loads(text)

    def spawned(self, operation_id="op-1", **arguments):
        out, text = self.call_tool("spawn", dict(SPAWN, operation_id=operation_id, **arguments))
        self.assertFalse(out["isError"], text)
        return out["structuredContent"]


class OperationRowTests(Operations):
    def test_S12_a_new_operation_id_writes_one_row_with_the_assignment_before_the_launch(self):
        """agent-runtime/R251"""
        seen = []
        real_run = hc_tools.run

        def run(argv, env, ctx, timeout=hc_tools.SHORT_SECONDS):
            if argv[:2] == ["hc", "run"]:
                seen.append(self.rows())
            return real_run(argv, env, ctx, timeout)

        with mock.patch.object(hc_tools, "run", run):
            first = self.spawned()
        (before,) = seen
        self.assertEqual(len(before), 1, "the row was not written before hc run")
        team, owner, operation_id, fingerprint, assignment = before[0]
        self.assertEqual(
            (owner, operation_id, assignment), (self.owner(), "op-1", first["assignment_id"])
        )
        self.assertEqual(team, first["team_id"])
        self.assertTrue(fingerprint)
        self.assertEqual(self.rows(), before)

    def test_S12_the_row_is_written_in_the_transaction_that_registers_the_assignment(self):
        """agent-runtime/R251: a registration the store refuses leaves neither an assignment nor a row."""
        store = self.store()
        team = store.team("scope", "t0", "gen")
        store.register(team, "w", operation=("owner", "k", "fp"))
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 1)
        with self.assertRaises(StoreError):
            store.register(team, "x", task_id="no-such-task", operation=("owner", "j", "fp"))
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 1)

    def test_S12_a_resume_with_an_operation_id_writes_a_row_for_its_assignment(self):
        """agent-runtime/R251"""
        self.spawned("spawn-op")
        out, text = self.call_tool(
            "resume", {"name": name(0), "task": "again", "operation_id": "resume-op"}
        )
        self.assertFalse(out["isError"], text)
        rows = {row[2]: row for row in self.rows()}
        self.assertEqual(rows["resume-op"][4], out["structuredContent"]["assignment_id"])
        self.assertNotEqual(rows["resume-op"][4], rows["spawn-op"][4])

    def test_S12_no_operation_id_writes_no_row(self):
        """agent-runtime/R251"""
        out, text = self.call_tool("spawn", SPAWN)
        self.assertFalse(out["isError"], text)
        self.assertEqual(self.rows(), [])

    def test_S12_an_operation_id_that_is_not_a_short_plain_string_is_refused_before_anything_starts(
        self,
    ):
        """agent-runtime/R251"""
        for bad in ("", "  ", "x" * 129, "has space", 7, ["a"], {"k": 1}, True):
            out, text = self.call_tool("spawn", dict(SPAWN, operation_id=bad))
            self.assertTrue(out["isError"], repr(bad))
            self.assertIn("operation_id", text)
        self.assertEqual(self.runs("run"), [])
        self.assertEqual(self.rows(), [])

    def test_S12_an_operation_id_needs_the_durable_store(self):
        """agent-runtime/R251"""
        self.env.pop("AGENT_RUNTIME_DISPATCH")
        out, text = self.call_tool("spawn", dict(SPAWN, operation_id="op-1"))
        self.assertTrue(out["isError"])
        self.assertIn("AGENT_RUNTIME_DISPATCH=1", text)
        self.assertEqual(self.runs("run"), [])


class OperationReplayTests(Operations):
    def test_S13_a_repeated_spawn_returns_the_first_assignment_and_sends_nothing(self):
        """agent-runtime/R252"""
        first = self.spawned()
        again = self.spawned()
        self.assertEqual(again["assignment_id"], first["assignment_id"])
        self.assertEqual(again["worker_id"], first["worker_id"])
        self.assertEqual(again["name"], name(0))
        self.assertIn(again["state"], ("bound", "working"))
        self.assertIs(again["replayed"], True)
        self.assertNotIn("replayed", first)
        self.assertEqual(len(self.runs("run")), 1)
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(len(self.rows("assignments")), 1)

    def test_S13_a_repeated_resume_returns_the_first_assignment_and_sends_once(self):
        """agent-runtime/R252"""
        self.spawned("spawn-op")
        arguments = {"name": name(0), "task": "again", "operation_id": "resume-op"}
        first, text = self.call_tool("resume", arguments)
        self.assertFalse(first["isError"], text)
        again, text = self.call_tool("resume", arguments)
        self.assertFalse(again["isError"], text)
        self.assertEqual(
            again["structuredContent"]["assignment_id"], first["structuredContent"]["assignment_id"]
        )
        self.assertIs(again["structuredContent"]["replayed"], True)
        self.assertEqual(len(self.sent_to(name(0))), 1)
        self.assertEqual(len(self.rows("assignments")), 2)

    def test_S13_the_replay_reports_the_assignments_current_state(self):
        """agent-runtime/R252"""
        first = self.spawned()
        store = self.store()
        store.publish(
            hc_tools.hc_server(self.env), "t100", "t0", first["assignment_id"], "done", "complete"
        )
        self.assertEqual(self.spawned()["state"], "complete")

    def test_S13_a_replay_takes_no_team_slot_and_is_not_refused_by_the_limit(self):
        """agent-runtime/R252"""
        first = self.spawned()
        store = self.store()
        team = first["team_id"]
        for index in range(1, 32):
            store.register(team, "filler%d" % index)
        again = self.spawned()
        self.assertEqual(again["assignment_id"], first["assignment_id"])

    def test_S13_an_operation_id_belongs_to_its_owner(self):
        """agent-runtime/R252: another lead's identical operation_id is a new operation."""
        first = self.spawned()
        other = dict(self.env, HC_TERMINAL_ID="t9")
        self.install(dict(worker_rows(4), **{"get:t9": {"out": hc_tools_raw("lead2", None)}}))
        with mock.patch.dict(os.environ, {"PATH": other["PATH"]}):
            out = mcp_server.call_tool(
                "spawn",
                dict(SPAWN, name=name(1), operation_id="op-1"),
                other,
                hc_tools.Context({}),
            )
        self.assertFalse(out["isError"], out["content"][0]["text"])
        self.assertNotEqual(out["structuredContent"]["assignment_id"], first["assignment_id"])
        self.assertEqual(len(self.rows()), 2)


def hc_tools_raw(name_, parent):
    from test_tools import raw

    return raw(name_, parent)


class OperationConflictTests(Operations):
    CHANGES = (
        ("task", {"task": "other work"}),
        ("name", {"name": name(1)}),
        ("cwd", {"cwd": "/var"}),
        ("model", {"model": "other-model"}),
        ("effort", {"effort": "high"}),
        ("cli", {"cli": "codex"}),
        ("agent", {"agent": "implementer"}),
    )

    def test_S14_every_argument_that_changes_behaviour_is_in_the_fingerprint(self):
        """agent-runtime/R253"""
        self.spawned()
        refused = {}
        for label, change in self.CHANGES:
            out, text = self.call_tool("spawn", dict(SPAWN, operation_id="op-1", **change))
            refused[label] = (
                out["isError"],
                "conflict" in text.lower(),
                "new operation_id" in text,
            )
        self.assertEqual(refused, {label: (True, True, True) for label, _ in self.CHANGES}, refused)
        self.assertEqual(len(self.runs("run")), 1)
        self.assertEqual(len(self.rows("assignments")), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_S14_the_task_link_and_the_instructions_are_in_the_fingerprint(self):
        """agent-runtime/R253"""
        created, text = self.call_tool("task", {"action": "create", "subject": "do it"})
        task_id = created["structuredContent"]["task_id"]
        instructions = self.dir / "role.md"
        instructions.write_text("be careful")
        arguments = dict(SPAWN, cli="pi", instructions_file=str(instructions), operation_id="op-1")
        with mock.patch.object(hc_tools.pi_mcp, "ensure"):
            first, text = self.call_tool("spawn", arguments)
            self.assertFalse(first["isError"], text)
            instructions.write_text("be reckless")
            changed, text = self.call_tool("spawn", arguments)
            self.assertTrue(changed["isError"] and "conflict" in text.lower(), text)
            instructions.write_text("be careful")
            linked, text = self.call_tool("spawn", dict(arguments, task_id=task_id))
            self.assertTrue(linked["isError"] and "conflict" in text.lower(), text)
            same, text = self.call_tool("spawn", arguments)
            self.assertFalse(same["isError"], text)
        self.assertEqual(len(self.runs("run")), 1)

    def test_S14_the_verb_is_in_the_fingerprint(self):
        """agent-runtime/R253: a resume cannot reuse a spawn's operation_id."""
        self.spawned()
        out, text = self.call_tool(
            "resume", {"name": name(0), "task": "work", "operation_id": "op-1"}
        )
        self.assertTrue(out["isError"] and "conflict" in text.lower(), text)
        self.assertEqual(self.sent_to(name(0)), [])
        self.assertEqual(len(self.rows("assignments")), 1)

    def test_S14_a_resume_with_a_changed_task_conflicts_and_sends_nothing(self):
        """agent-runtime/R253"""
        self.spawned("spawn-op")
        arguments = {"name": name(0), "task": "again", "operation_id": "resume-op"}
        self.assertFalse(self.call_tool("resume", arguments)[0]["isError"])
        out, text = self.call_tool("resume", dict(arguments, task="something else"))
        self.assertTrue(out["isError"], text)
        self.assertIn("conflict", text.lower())
        self.assertIn("new operation_id", text)
        self.assertEqual(len(self.sent_to(name(0))), 1)
        self.assertEqual(len(self.rows("assignments")), 2)

    def test_S14_the_conflict_names_the_operation_id(self):
        """agent-runtime/R253"""
        self.spawned("named-op")
        out, text = self.call_tool("spawn", dict(SPAWN, task="x", operation_id="named-op"))
        self.assertIn("named-op", text)


class OperationLookupTests(Operations):
    def force(self, assignment, state, **changes):
        with self.store().transaction() as db:
            db.execute("UPDATE assignments SET state=? WHERE id=?", (state, assignment))
            for column, value in changes.items():
                db.execute("UPDATE assignments SET %s=? WHERE id=?" % column, (value, assignment))

    def test_S15_an_unseen_operation_id_is_absent(self):
        """agent-runtime/R254"""
        self.assertEqual(self.lookup("never")["state"], "absent")
        self.assertIsNone(self.lookup("never")["assignment_id"])

    def test_S15_the_state_follows_the_assignment_with_its_id(self):
        """agent-runtime/R254"""
        first = self.spawned()
        found = self.lookup()
        self.assertEqual(found["state"], "bound")
        self.assertEqual(found["assignment_id"], first["assignment_id"])
        self.assertEqual(found["operation_id"], "op-1")
        store = self.store()
        scope = hc_tools.hc_server(self.env)
        store.publish(scope, "t100", "t0", first["assignment_id"], "step", "progress")
        self.assertEqual(self.lookup()["state"], "working")
        store.publish(scope, "t100", "t0", first["assignment_id"], "stuck", "blocked")
        self.assertEqual(self.lookup()["state"], "blocked")
        store.publish(scope, "t100", "t0", first["assignment_id"], "which?", "decision")
        self.assertEqual(self.lookup()["state"], "decision")
        store.publish(scope, "t100", "t0", first["assignment_id"], "done", "complete")
        self.assertEqual(self.lookup()["state"], "complete")

    def test_S15_a_registered_assignment_that_never_bound_is_pending(self):
        """agent-runtime/R254"""
        store = self.store()
        team = store.team("scope", "t0", "gen")
        identity = store.register(team, "w", operation=(self.owner(), "op-1", "fp"))
        found = self.lookup()
        self.assertEqual(
            (found["state"], found["assignment_id"]), ("pending", identity["assignment_id"])
        )

    def test_S15_a_failed_assignment_is_failed(self):
        """agent-runtime/R254"""
        first = self.spawned()
        self.force(first["assignment_id"], "failed")
        self.assertEqual(self.lookup()["state"], "failed")

    def test_S15_a_row_whose_assignment_is_gone_is_unknown_with_a_reason_to_act_on(self):
        """agent-runtime/R254"""
        first = self.spawned()
        with self.store().transaction() as db:
            db.execute("DELETE FROM assignments WHERE id=?", (first["assignment_id"],))
        found = self.lookup()
        self.assertEqual(found["state"], "unknown")
        self.assertEqual(found["assignment_id"], first["assignment_id"])
        self.assertIn("new operation_id", found["reason"])

    def test_S15_a_store_that_cannot_be_read_is_unknown_not_an_error(self):
        """agent-runtime/R254"""
        self.spawned()
        with mock.patch.object(
            Store, "operation", side_effect=sqlite3.OperationalError("disk I/O error")
        ):
            found = self.lookup()
        self.assertEqual(found["state"], "unknown")
        self.assertIn("disk I/O error", found["reason"])
        self.assertIn("retry", found["reason"])

    def test_S15_the_lookup_is_scoped_to_its_owner(self):
        """agent-runtime/R254"""
        self.spawned()
        self.install(dict(worker_rows(4), **{"get:t9": {"out": hc_tools_raw("lead2", None)}}))
        other = dict(self.env, HC_TERMINAL_ID="t9")
        with mock.patch.dict(os.environ, {"PATH": other["PATH"]}):
            out = mcp_server.call_tool(
                "operation", {"operation_id": "op-1"}, other, hc_tools.Context({})
            )
        self.assertEqual(json.loads(out["content"][0]["text"])["state"], "absent")

    def test_S15_the_lookup_never_acknowledges_a_report(self):
        """agent-runtime/R254: even when it carries a receipt, a lookup leaves the lead's inbox as it was."""
        first = self.spawned()
        store = self.store()
        store.publish(
            hc_tools.hc_server(self.env), "t100", "t0", first["assignment_id"], "done", "complete"
        )

        def unacked():
            with store.transaction() as db:
                return db.execute("SELECT COUNT(*) FROM messages WHERE acked=0").fetchone()[0]

        self.assertEqual(unacked(), 1)
        out, text = self.call_tool("wait", {"target": "all", "timeout": 5})
        receipt = json.loads(text)["receipt"]
        self.assertEqual(unacked(), 1, "harness: a wait must not ack by itself")
        out, text = self.call_tool("operation", {"operation_id": "op-1", "receipt": receipt})
        self.assertEqual(json.loads(text)["state"], "complete")
        self.assertEqual(unacked(), 1)
        self.assertNotIn("inbox", json.loads(text))

    def test_S15_the_lookup_needs_an_operation_id(self):
        """agent-runtime/R254"""
        for arguments in ({}, {"operation_id": ""}, {"operation_id": 3}):
            out, text = self.call_tool("operation", arguments)
            self.assertTrue(out["isError"], arguments)
            self.assertIn("operation_id", text)


class OperationStoreTests(unittest.TestCase):
    def test_S15_the_table_is_additive_and_the_schema_version_stays_2(self):
        """agent-runtime/R254: a version 2 store written before the table existed opens, keeps its data and
        its version, and a server that never uses operation_id keeps working on it."""
        import tempfile

        with tempfile.TemporaryDirectory() as home:
            directory = os.path.join(home, ".local", "state", "agent-runtime")
            os.makedirs(directory, mode=0o700)
            path = os.path.join(directory, "dispatch.sqlite3")
            with sqlite3.connect(path) as db:
                db.execute(
                    "CREATE TABLE teams (id TEXT PRIMARY KEY, scope TEXT, lead TEXT, generation TEXT, UNIQUE(scope,lead,generation))"
                )
                db.execute("INSERT INTO teams VALUES ('t','s','l','g')")
                db.execute("PRAGMA user_version=2")
            store = Store({"HOME": home})
            self.assertEqual(store.team("s", "l", "g"), "t")
            store.register("t", "w")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
                tables = {
                    r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertIn("operations", tables)
                columns = [r[1] for r in db.execute("PRAGMA table_info(operations)")]
                self.assertEqual(
                    columns, ["team", "owner", "operation_id", "fingerprint", "assignment_id"]
                )
                notnull = {r[1]: r[3] for r in db.execute("PRAGMA table_info(operations)")}
                self.assertTrue(all(notnull.values()), notnull)


class DescriptionTests(unittest.TestCase):
    def test_S12_spawn_and_resume_take_an_operation_id_within_the_word_cap(self):
        """agent-runtime/R251"""
        tools = {n: s["properties"] for n, _, s in mcp_server.TOOLS}
        self.assertIn("operation_id", tools["spawn"])
        self.assertIn("operation_id", tools["resume"])
        self.assertNotIn("operation_id", tools["message"])
        words = sum(
            len(d.split())
            + sum(len(p.get("description", "").split()) for p in s["properties"].values())
            for _, d, s in mcp_server.TOOLS
        )
        self.assertLessEqual(words, 400)

    def test_S15_the_lookup_is_a_call_verb_not_an_mcp_tool(self):
        """agent-runtime/R254: the public tool surface stays the ten tools."""
        self.assertNotIn("operation", mcp_server.TOOL_NAMES)
        self.assertIn("operation", hc_tools.HANDLERS)


if __name__ == "__main__":
    unittest.main()
