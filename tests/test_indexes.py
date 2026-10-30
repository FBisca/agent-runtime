"""Dispatch store indexes: present, additive, and used by wait's hot queries."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from test_mcp_server import SERVER

sys.path.insert(0, str(SERVER.parent))
import dispatch_store
from dispatch_store import Store

EXPECTED = {
    "assignments": [["team", "current"], ["worker"]],
    "workers": [["address"]],
    "messages": [["assignment", "acked"]],
    "tasks": [["team", "status"], ["assignment"]],
}


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store({"HOME": self.tmp.name})

    def plan(self, db, sql, params):
        return [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + sql, params)]

    def test_S11_indexes_exist_user_version_unchanged_and_old_writes_work(self):
        """agent-runtime-hardening/S11"""
        with self.store.transaction() as db:
            for table, wanted in EXPECTED.items():
                have = []
                for idx in db.execute("PRAGMA index_list(%s)" % table).fetchall():
                    have.append([c[2] for c in db.execute("PRAGMA index_info(%s)" % idx[1])])
                for columns in wanted:
                    self.assertIn(columns, have, table)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            db.execute("INSERT INTO assignments VALUES (?,?,?,'pending',0)", ("a", "t", "w"))
            db.execute("INSERT INTO messages (id,team,recipient,assignment,body,acked) VALUES ('m','t','r','a','{}',0)")
        Store({"HOME": self.tmp.name})  # re-init is idempotent

    def test_S12_wait_snapshot_and_read_plans_use_indexes_without_scanning_messages(self):
        """agent-runtime-hardening/S12"""
        ds = dispatch_store
        members = ds.READ_MEMBERS_SQL
        queries = [
            (ds.SNAPSHOT_SQL, ("t",) + ("w",) * 6),
            (members.format(selection="a.current=1"), ("t",)),
            (members.format(selection="a.id IN (?)"), ("t", "a")),
            (ds.READ_MESSAGES_SQL + ds.READ_NAMED_CLAUSE + " ORDER BY seq", ("t", "r", "t", "w", "a")),
            (ds.READ_MESSAGES_SQL + ds.READ_PINNED_CLAUSE.format(ids="?") + " ORDER BY seq", ("t", "r", "a")),
            (ds.ACTOR_SQL, ("s", "lead", "addr", "a", "w")),
            (ds.BIND_NEWER_ADDRESS_SQL, ("addr", "s", 1)),
            (ds.BIND_GONE_SQL, ("addr", "w", "s")),
            (ds.BIND_NEWER_ASSIGNMENT_SQL, ("w", 1)),
            (ds.TASK_REPORT_SQL, ("complete", "a")),
            (ds.TASKS_DONE_SQL, ("t",)),
            (ds.TASKS_LIST_SQL, ("t", 0)),
            (ds.TASKS_DEPS_SQL, ("t",)),
        ]
        plans = []
        with self.store.transaction() as db:
            for sql, params in queries:
                plan = self.plan(db, sql, params)
                plans.append(" ".join(plan))
                for line in plan:
                    if line.startswith("USE TEMP B-TREE"):
                        continue
                    self.assertFalse(line.startswith("SCAN "), (sql, plan))
        seen = " ".join(plans)
        for name in ("idx_assignments_team_current", "idx_assignments_worker", "idx_workers_address",
                     "idx_messages_assignment_acked", "idx_tasks_team_status", "idx_tasks_assignment"):
            self.assertIn(name, seen)


if __name__ == "__main__":
    unittest.main()
