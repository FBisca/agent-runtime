"""The state dir holds only plans that are not yet archived: a driver start and `plan approve` sweep the rest."""

import fcntl
import json
import os
import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import events
from agent_orchestrator import ledger as ledgers
from agent_orchestrator.driver import prune
from fixtures import RunFixture, assignment, wait_result

pytestmark = [pytest.mark.integration, pytest.mark.spec("Archived plans leave no state")]


def plant(state, key, slug, plan_state="ARCHIVED", digests=()):
    """One plan's state as a driver leaves it: ledger, lock, run dir and the snapshots its digests name."""
    ledger = state / key / (slug + ".json")
    run_dir = state / key / slug
    run_dir.mkdir(parents=True)
    data = ledgers.new()
    data["plan"] = {"state": plan_state, "history": []}
    if digests:
        data["approval"] = {"base": "b", "digest": digests[-1]}
    ledger.write_text(json.dumps(data), encoding="utf-8")
    ledger.with_name(ledger.name + ".lock").write_text("")
    events.append(run_dir / "events.jsonl", "start", pid=2**22 + 7)
    for earlier, later in zip(digests, digests[1:]):
        events.append(run_dir / "events.jsonl", "amended", prev_digest=earlier, digest=later)
    for digest in digests:
        (state / "approvals" / digest).mkdir(parents=True)
        (state / "approvals" / digest / "plan.yaml").write_text("x")
    return ledger, run_dir


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())

    def test_an_archived_plan_loses_ledger_lock_run_dir_and_its_snapshots(self):
        ledger, run_dir = plant(self.state, "r-1", "old", digests=("d1", "d2"))
        prune.sweep(self.state)
        left = [p for p in self.state.rglob("*") if p.is_file()]
        self.assertEqual(left, [])  # nothing of it is left, snapshots included
        self.assertFalse(ledger.exists() or run_dir.exists())

    def test_a_plan_not_archived_keeps_everything(self):
        ledger, run_dir = plant(self.state, "r-1", "live", plan_state="RUNNING", digests=("d1",))
        prune.sweep(self.state)
        self.assertTrue(ledger.is_file() and run_dir.is_dir())
        self.assertTrue((self.state / "approvals" / "d1" / "plan.yaml").is_file())

    def test_a_snapshot_no_ledger_names_stays(self):
        plant(self.state, "r-1", "old", digests=("d1",))
        (self.state / "approvals" / "approved-not-started").mkdir()
        prune.sweep(self.state)
        self.assertTrue((self.state / "approvals" / "approved-not-started").is_dir())

    def test_a_ledger_whose_lock_is_held_is_skipped(self):
        ledger, run_dir = plant(self.state, "r-1", "busy", digests=("d1",))
        with open(ledger.with_name(ledger.name + ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            prune.sweep(self.state)
        self.assertTrue(ledger.is_file() and run_dir.is_dir())

    def test_a_ledger_whose_driver_pid_is_alive_is_skipped(self):
        ledger, run_dir = plant(self.state, "r-1", "busy", digests=("d1",))
        events.append(run_dir / "events.jsonl", "start", pid=os.getpid())
        prune.sweep(self.state)
        self.assertTrue(ledger.is_file() and run_dir.is_dir())


class SweepTriggerTests(RunFixture, unittest.TestCase):
    def test_a_driver_start_sweeps(self):
        ledger, _ = plant(self.state_dir, "other-1", "old", digests=("d9",))
        self.waits([wait_result([assignment("demo-t1", "working")], reason="timeout")])
        self.run_driver(expect=0)
        self.assertFalse(ledger.exists())

    def test_plan_approve_sweeps(self):
        ledger, _ = plant(self.state_dir, "other-1", "old", digests=("d9",))
        self.approved_yaml_plan(("T1",), name="second.plan.yaml")
        self.assertFalse(ledger.exists())
