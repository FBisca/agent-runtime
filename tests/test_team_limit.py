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
        """Workers w0..: the first `active` are active, the next `stopped` were registered, then stopped."""
        names = ["w%d" % i for i in range(active + stopped)]
        for name in names[active:]:
            self.store.register(self.team, name)
            with self.store.transaction() as db:
                db.execute("UPDATE workers SET active=0 WHERE team=? AND name=?", (self.team, name))
                db.execute(
                    "UPDATE assignments SET state='failed' WHERE worker=(SELECT id FROM workers WHERE team=? AND name=?)",
                    (self.team, name),
                )
        for name in names[:active]:
            self.store.register(self.team, name)

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

    def worker_active(self, name):
        with self.store.transaction() as db:
            return db.execute(
                "SELECT active FROM workers WHERE team=? AND name=?", (self.team, name)
            ).fetchone()[0]

    def test_S2_a_full_team_refuses_a_new_name(self):
        """agent-runtime/R241"""
        self.fill(32, 0)
        with self.assertRaises(StoreError) as caught:
            self.store.register(self.team, "brand-new")
        self.assertEqual(str(caught.exception), REFUSAL)
        self.assertEqual(self.active(), 32)

    def test_S2_a_full_team_refuses_to_resume_a_stopped_name(self):
        """agent-runtime/R241"""
        self.fill(32, 1)  # 33 registered, w32 stopped
        with self.assertRaises(StoreError) as caught:
            self.store.register(self.team, "w32", transfer=True)
        self.assertEqual(str(caught.exception), REFUSAL)
        self.assertEqual(self.active(), 32)
        self.assertEqual(self.worker_active("w32"), 0)

    def test_S2_a_pending_resume_of_a_stopped_name_holds_its_slot_until_it_fails(self):
        """agent-runtime/R241"""
        self.fill(31, 1)
        identity = self.store.register(self.team, "w31", transfer=True)
        with self.assertRaises(StoreError) as caught:
            self.store.register(self.team, "brand-new")
        self.assertEqual(str(caught.exception), REFUSAL)
        self.store.fail(identity["assignment_id"])
        self.assertEqual(self.worker_active("w31"), 0)
        self.assertEqual(self.active(), 31)
        self.store.register(self.team, "brand-new")

    def test_S2_a_failed_resume_of_an_active_worker_leaves_it_active(self):
        """agent-runtime/R241"""
        self.fill(2, 0)
        with self.store.transaction() as db:
            db.execute("UPDATE workers SET address='pane-1' WHERE name='w1'")  # a bound worker
        identity = self.store.register(self.team, "w1", transfer=True)
        self.store.fail(identity["assignment_id"])
        self.assertEqual(self.worker_active("w1"), 1)

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
