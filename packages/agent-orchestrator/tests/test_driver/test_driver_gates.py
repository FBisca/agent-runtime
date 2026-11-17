"""The state machine has one home: `driver/gates.py`, and a `Gates` the Driver holds.

A unit moves through its gates — the report it sent, the review it passed, the checks its pull request
ran, the merge, the cleanup — and each transition is one step of the table `advance` dispatches. The
transitions live beside that table rather than in `__init__.py`, so a step and the gate it moves are
read in one place; a ledger the engine wrote before the split opens in the engine after it with no
migration, because the record is still the Book's.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit

# <repo>/packages/agent-orchestrator/tests/test_driver/<this file>
REPO = Path(__file__).resolve().parents[4]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
GATES = MEMBER / "driver" / "gates.py"

# The gates a task is held at, and the one step that moves it on: the state machine as the run names it.
STEPS = {
    "reported": "step_reported",
    "converged": "step_converged",
    "reviewed": "step_reviewed",
    "fix-pending": "step_fix",
    "requested": "step_requested",
    "decided": "step_decided",
    "redo-pending": "step_redo",
    "ready-for-ci": "step_ci",
    "awaiting-merge": "step_merge",
    "cleanup": "step_cleanup",
}

# The ledger one engine wrote, at the gate where it waits for the Lead: the version, the kind marker, one
# attempt, the request it opened and the keys a run keeps beside its tasks. Literal on purpose — this is
# the file left on disk, and the engine after the split reads what an older one wrote, not what today's
# writer would. The two paths are the only things a temporary directory fills in.
BEFORE_THE_SPLIT = """{
  "version": 1,
  "kind": "agent-orchestrator/ledger",
  "tasks": {
    "t1": {
      "packet": {"task_id": "t1", "worker": "demo-t1", "unit": "t1", "backend": "claude"},
      "status": "ACTIVE",
      "runtime_id": "rt-1",
      "gate": "requested",
      "started_at": "2026-11-15T09:00:00+00:00",
      "request": {"path": "%s", "kind": "blocked", "from_gate": "reported"}
    }
  },
  "ticket_status": {"T1": "IN PROGRESS · decision pending: no push credentials"},
  "rounds": {"t1": 2}
}"""

REQUEST = """{
  "contract": 2,
  "unit": "t1",
  "task": "t1",
  "event": "blocked",
  "reason": "the checkout has no push credentials",
  "created_at": "2026-11-15T09:30:00+00:00",
  "evidence": [],
  "menu": ["skip"],
  "status": "resolved",
  "decision": "%s"
}"""

DECISION = """{"contract": 2, "action": "skip", "reason": "not this release"}"""


def driver_over(root, ledger_path):
    """The Driver a run holds over one ledger file: a plan, a run directory and the ledger, nothing else."""
    plan = root / "plan.yaml"
    plan.write_text("schema: plan/2\n", encoding="utf-8")
    run = root / "run"
    run.mkdir(exist_ok=True)
    driver = load_script().Driver(SimpleNamespace(plan=str(plan), repo=str(root)))
    driver.slug, driver.run_dir, driver.ledger_path = "demo", run, ledger_path
    return driver


class PreSplitLedgerTests(unittest.TestCase):
    """S13: a ledger written before the split is read after it, with no migration."""

    @pytest.mark.integration
    @pytest.mark.spec("Pre-split ledgers open")
    def test_S13_a_ledger_written_before_the_split_moves_on_under_the_gates(self):
        from agent_orchestrator.driver import gates

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            run.mkdir()
            request = run / "t1" / "request.json"
            request.parent.mkdir()
            decision = request.with_name("decision.json")
            request.write_text(REQUEST % decision, encoding="utf-8")
            decision.write_text(DECISION, encoding="utf-8")
            path = root / "demo.json"
            path.write_text(BEFORE_THE_SPLIT % request, encoding="utf-8")
            driver = driver_over(root, path)
            self.assertIsInstance(driver.gates, gates.Gates, "the Driver holds the state machine")

            # One round of the dispatch table: the request the older engine left is applied, and the
            # decision it names releases the unit in the same pass.
            acted = driver.advance()
            after = json.loads(path.read_text(encoding="utf-8"))

        self.assertGreaterEqual(
            acted, 2, "the request and the decision were two steps of the round"
        )
        self.assertEqual(after["version"], 1, "the version the older engine wrote is kept")
        self.assertEqual(
            after["kind"],
            "agent-orchestrator/ledger",
            "the kind marker the older engine wrote is kept",
        )
        self.assertEqual(list(after["tasks"]), ["t1"], "the attempt the older engine wrote is kept")
        self.assertEqual(after["tasks"]["t1"]["status"], "RELEASED")
        self.assertEqual(after["tasks"]["t1"]["gate"], "skipped")
        self.assertIn("not this release", after["tasks"]["t1"]["release_reason"])
        self.assertEqual(after["tasks"]["t1"]["packet"]["worker"], "demo-t1")
        # Nothing a run keeps beside its tasks is dropped on the way through: no migration ran.
        self.assertEqual(after["rounds"], {"t1": 2})
        self.assertEqual(after["ticket_status"]["T1"], "SKIPPED · not this release")


class GateHomeTests(unittest.TestCase):
    """S13: the transitions live in `driver/gates.py`, with the table that drives them."""

    def test_S13_the_steps_live_in_one_module_with_the_table_that_drives_them(self):
        from agent_orchestrator import driver
        from agent_orchestrator.driver import gates

        self.assertEqual(Path(gates.__file__).resolve(), GATES.resolve())
        self.assertEqual(
            gates.STEPS, STEPS, "every gate the run holds is dispatched to one step of this module"
        )
        # The step is the gates module's own, and the engine keeps no second copy of the transition: a
        # gate that was moved but also answered on the Driver would move on from two places.
        for name in STEPS.values():
            with self.subTest(step=name):
                self.assertIn(name, vars(gates.Gates))
                self.assertNotIn(name, vars(driver.Driver))

    def test_S13_the_driver_advances_through_the_collaborator(self):
        from unittest import mock

        from agent_orchestrator import driver as engine

        driver = engine.Driver.__new__(engine.Driver)
        driver.gates = engine.Gates(driver)
        with mock.patch.object(engine.Gates, "advance", return_value=7) as advance:
            self.assertEqual(driver.advance(), 7, "the round answers what the collaborator did")
        advance.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
