"""The team limit counts active workers; the slot is reserved in the registering transaction."""

import sys
import tempfile
import threading
import unittest

from test_mcp_server import RUNTIME_SRC

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime.dispatch_store import Store, StoreError

pytestmark = pytest.mark.integration

REFUSAL = "team limit is 32 active workers: stop an active worker, then retry"


class TeamLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store({"HOME": self.temp.name})
        self.team = self.store.team("test", "t0", "lead")

    def fill(self, active, stopped):
        for i in range(active + stopped):
            self.store.register(self.team, "w%d" % i)
        with self.store.transaction() as db:
            for i in range(active, active + stopped):
                db.execute(
                    "UPDATE workers SET active=0 WHERE team=? AND name=?", (self.team, "w%d" % i)
                )

    def active(self):
        with self.store.transaction() as db:
            return db.execute(
                "SELECT COUNT(*) FROM workers WHERE team=? AND active=1", (self.team,)
            ).fetchone()[0]

    def test_S1_stopped_workers_do_not_count_toward_the_limit(self):
        """agent-runtime/R240"""
        self.fill(1, 31)
        self.store.register(self.team, "fresh")
        self.assertEqual(self.active(), 2)

    def test_S2_a_full_team_refuses_a_new_name_and_a_stopped_name(self):
        """agent-runtime/R241"""
        self.fill(32, 0)
        with self.store.transaction() as db:
            db.execute("UPDATE workers SET active=0 WHERE team=? AND name='w0'", (self.team,))
        self.store.register(self.team, "w0")  # 31 active: the stopped name takes the last slot
        for name in ("brand-new", "w1"):
            with self.subTest(name=name):
                if name == "w1":
                    with self.store.transaction() as db:
                        db.execute("UPDATE workers SET active=0 WHERE name='w1'")
                    self.assertEqual(self.active(), 31)
                    self.store.register(self.team, "w1")
                with self.assertRaises(StoreError) as caught:
                    self.store.register(self.team, "brand-new")
                self.assertEqual(str(caught.exception), REFUSAL)
        with self.store.transaction() as db:
            db.execute("UPDATE workers SET active=0 WHERE name='w2'")
        with self.assertRaises(StoreError) as caught:
            self.store.register(self.team, "w2")
            self.store.register(self.team, "w3x")
        self.assertEqual(str(caught.exception), REFUSAL)

    def test_S2_concurrent_registrations_never_exceed_the_limit(self):
        """agent-runtime/R241"""
        self.fill(30, 0)
        outcomes = []

        def spawn(name):
            try:
                self.store.register(self.team, name)
                outcomes.append("ok")
            except StoreError as error:
                outcomes.append(str(error))

        threads = [threading.Thread(target=spawn, args=("c%d" % i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("ok"), 2)
        self.assertEqual(outcomes.count(REFUSAL), 6)
        self.assertEqual(self.active(), 32)

    def test_S3_resuming_an_active_worker_takes_no_slot(self):
        """agent-runtime/R242"""
        self.fill(32, 0)
        identity = self.store.register(self.team, "w5")
        self.assertEqual(identity["team_id"], self.team)
        self.assertEqual(self.active(), 32)


if __name__ == "__main__":
    unittest.main()
