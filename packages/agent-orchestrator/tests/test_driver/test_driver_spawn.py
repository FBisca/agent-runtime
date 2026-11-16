"""The workers' door to the runtime has one home: `driver/spawn.py`, and a `Spawning` the Driver holds.

Every way a worker is put to work — started, re-attached, resumed with a new round, handed over to its
successor, swapped when its context runs out, stopped — is one method of the collaborator, so the send
surface reads in one place instead of seven thousand lines apart. The names the package, the gate steps
and the Lead's commands ask for still resolve on the Driver, and `agent_orchestrator.driver` is still a
package inside the member's source directory.
"""

import inspect
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.unit

# <repo>/packages/agent-orchestrator/tests/test_driver/<this file>
REPO = Path(__file__).resolve().parents[4]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
SPAWN = MEMBER / "driver" / "spawn.py"

# The five verbs of the door, and the bookkeeping a send needs beside them: every spawn and resume goes
# out under an intent, and the intent and the send are one rule.
MOVED = (
    "agent_live",
    "open_intent",
    "spawn_args",
    "book_intent",
    "settle_intent",
    "release_intents",
    "send",
    "reconcile_intent",
    "unsettled_text",
    "adopt",
    "hold_intent",
    "ensure_agent",
    "keep_address",
    "swap_due",
    "handoff",
    "stop_agent",
    "stop_unit_agents",
    "pane_alive",
    "resume_worker",
    "hand_round",
    "hand_over",
)

# The two members a caller reaches with no instance of the collaborator: the answer an unsettled intent
# is named by, and the address a hand-over keeps on the task.
STATIC = ("unsettled_text", "keep_address")


class SpawnHomeTests(unittest.TestCase):
    """S10: the send surface lives in `driver/spawn.py`, and the Driver holds it as one collaborator."""

    def test_S10_the_spawn_members_live_in_one_module(self):
        """structure-budget/S10"""
        from agent_orchestrator import driver
        from agent_orchestrator.driver import spawn

        self.assertEqual(Path(spawn.__file__).resolve(), SPAWN.resolve())
        self.assertEqual(
            Path(driver.__file__).resolve().parent.parent,
            MEMBER.resolve(),
            "the engine still has one home: the member's source directory",
        )
        for name in MOVED:
            with self.subTest(name=name):
                self.assertIn(name, vars(spawn.Spawning), "%s is not the collaborator's own" % name)
                self.assertTrue(
                    callable(getattr(driver.Driver, name)),
                    "the package, the gate steps and the suite ask `Driver.%s`" % name,
                )
        # The two static members are the collaborator's own object, not a second copy of the rule: a
        # package-level caller gets the one home's answer.
        for name in STATIC:
            with self.subTest(name=name):
                self.assertIs(getattr(driver.Driver, name), getattr(spawn.Spawning, name))

    def test_S10_the_driver_reaches_every_moved_member_through_the_collaborator(self):
        """structure-budget/S10"""
        from agent_orchestrator import driver as engine

        driver = engine.Driver.__new__(engine.Driver)
        driver.spawning = engine.Spawning(driver)
        for name in MOVED:
            if name in STATIC:
                continue  # the collaborator's own object, asserted above
            with self.subTest(name=name):
                body = [
                    line.strip()
                    for line in inspect.getsource(getattr(engine.Driver, name)).splitlines()
                    if line.strip()
                ]
                self.assertTrue(
                    body[-1].startswith("return self.spawning.%s(" % name),
                    "%s answers for itself instead of the collaborator" % name,
                )
                self.assertLessEqual(len(body), 5, "%s carries more than the delegation" % name)
        # The delegation is what runs: one hand-over, sent once, with the arguments it was given.
        task, successor = {"runtime_id": "rt-1"}, {"task_id": "t1r1", "worker": "demo-t1"}
        with mock.patch.object(
            engine.Spawning, "hand_over", return_value=("address", "assignment")
        ) as hand_over:
            self.assertEqual(
                driver.hand_over(task, successor, "task.md", "text"), ("address", "assignment")
            )
        hand_over.assert_called_once_with(task, successor, "task.md", "text", False, None)


if __name__ == "__main__":
    unittest.main()
