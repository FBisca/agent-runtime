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


def worker_address(name):
    """The terminal address the runtime tests' fake hc gives worker `wNN` (see test_operations.worker_rows)."""
    return "t%d" % (100 + int(name[1:]))


def team_of(store):
    """The team a lead's first call created in `store`, and its scope, for a test that seeds around that call."""
    with store.transaction() as db:
        row = db.execute("SELECT id,scope FROM teams ORDER BY rowid DESC LIMIT 1").fetchone()
    return row["id"], row["scope"]


def seed_workers(store, team, scope, active=(), stopped=()):
    """Put workers in a team's store the way a spawn and the worker's first report leave them: registered, bound
    to their address, and active, or stopped after that. Nothing launches and no worker session starts, so a test
    that needs a full team drives only the step it is about through the public path. A stopped name that is
    named in `active` comes back active, as a resume leaves it."""
    # Stopped names go first: a name registered while 32 others are active would be refused as the 33rd.
    for name in stopped:
        store.bind(store.register(team, name)["assignment_id"], worker_address(name), scope)
        store.stopped(team, name)
    for name in active:
        store.bind(store.register(team, name)["assignment_id"], worker_address(name), scope)


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


class SeededTeamTests(unittest.TestCase):
    """S10: a runtime test that needs a full team seeds it through the store; it never spawns 32 workers."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store({"HOME": self.temp.name})
        self.team = self.store.team("test", "t0", "lead")

    def count(self, active):
        with self.store.transaction() as db:
            return db.execute(
                "SELECT COUNT(*) FROM workers WHERE team=? AND active=?", (self.team, active)
            ).fetchone()[0]

    def test_S10_a_seeded_full_team_refuses_the_33rd_active_worker(self):
        """agent-runtime/R241"""
        seed_workers(self.store, self.team, "test", active=["w%02d" % i for i in range(32)])
        self.assertEqual(self.count(1), 32)
        with self.assertRaises(StoreError) as caught:
            self.store.register(self.team, "w32")
        self.assertEqual(str(caught.exception), REFUSAL)

    def test_S10_a_seeded_stopped_worker_holds_no_slot_and_keeps_its_name(self):
        """agent-runtime/R240"""
        seed_workers(
            self.store,
            self.team,
            "test",
            active=["w%02d" % i for i in range(31)],
            stopped=["w31"],
        )
        self.assertEqual((self.count(1), self.count(0)), (31, 1))
        self.store.register(self.team, "w32")  # the 32nd active worker fits beside the stopped name
        with self.assertRaises(StoreError) as caught:
            self.store.register(
                self.team, "w31", transfer=True
            )  # reactivating it would be the 33rd
        self.assertEqual(str(caught.exception), REFUSAL)

    def test_S10_a_stopped_name_is_reactivated_by_seeding_it_active(self):
        """agent-runtime/R240"""
        seed_workers(self.store, self.team, "test", stopped=["w00", "w01"])
        seed_workers(self.store, self.team, "test", active=["w01"])
        self.assertEqual((self.count(1), self.count(0)), (1, 1))

    def test_S10_a_seeded_worker_answers_at_its_own_address(self):
        """agent-runtime/R240"""
        seed_workers(self.store, self.team, "test", active=["w05"])
        self.assertEqual(self.store.worker_address(self.team, "w05"), "t105")


if __name__ == "__main__":
    unittest.main()
