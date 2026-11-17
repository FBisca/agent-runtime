"""The ledger's state invariants: a write that breaks one is refused by name, `ledger check` reports the same on a
captured ledger, and what the driver itself writes passes both."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
from agent_orchestrator import driver, invariants
from test_gates import GateFixture

pytestmark = pytest.mark.integration

GATE = "unknown-gate"


def task(**over):
    """One ledger task with the fields the invariants read."""
    return dict({"status": "ACTIVE", "gate": None}, **over)


def ledger(**tasks):
    return {"version": 1, "kind": "agent-orchestrator/ledger", "tasks": tasks}


class LedgerInvariantTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.path = self.root / "ledger.json"

    def write(self, data, name="ledger.json"):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def check(self, data, name="ledger.json"):
        """`agent-orchestrator ledger check <captured ledger>`, in process: the code and both streams."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = driver.run_cli(["ledger", "check", str(self.write(data, name))])
        return code, out.getvalue(), err.getvalue()

    @pytest.mark.spec("Ledger invariants")
    def test_S2_a_write_that_breaks_an_invariant_is_refused_by_name_and_nothing_lands(self):
        """R: an unknown gate is refused at the write, naming the invariant, the task and the way out."""
        self.write(ledger(t1=task()))
        with self.assertRaises(invariants.LedgerInvariant) as refused:
            with driver.ledger(self.path) as data:
                data["tasks"]["t1"].update(gate="reviewer")
        error = refused.exception
        self.assertEqual((error.invariant, error.task), (GATE, "t1"))
        for needle in (GATE, "t1", error.way_out):
            self.assertIn(needle, str(error))
        landed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(landed["tasks"]["t1"]["gate"], None, "the refused write landed")

    @pytest.mark.spec("Ledger invariants")
    def test_S2_a_read_of_a_broken_ledger_is_not_refused(self):
        """D1: only a write is checked, so a legacy ledger can still be diagnosed rather than refused on every read."""
        self.write(ledger(t1=task(gate="reviewer")))
        with driver.ledger(self.path) as data:
            self.assertEqual(data["tasks"]["t1"]["gate"], "reviewer")

    @pytest.mark.spec("Ledger invariants")
    def test_S2_ledger_check_names_the_state_that_stalled_the_run(self):
        """R: the same illegal states, on a captured ledger, each by name with the task that holds it and the
        way out."""
        states = {GATE: (ledger(t1=task(gate="reviewer")), "t1")}
        for invariant, (data, named) in states.items():
            with self.subTest(invariant=invariant):
                code, out, err = self.check(data, "%s.json" % invariant)
                self.assertEqual(code, 2, err or out)
                refused = json.loads(err)
                self.assertEqual(refused["invariant"], invariant)
                self.assertEqual(refused["task"], named)
                self.assertTrue(refused["way_out"])

    @pytest.mark.spec("Ledger invariants")
    def test_S2_control_a_coherent_ledger_passes(self):
        """A ledger the driver itself writes passes: a parked unit, every gate one the driver knows."""
        data = ledger(
            t1=task(status="PARTIAL", gate="reviewing"),
            t1r1=task(status="RELEASED", gate="superseded"),
            t2=task(status="COMPLETE", gate="done"),
            t3=task(status="BLOCKED", gate="blocked"),
        )
        invariants.check(data)
        code, out, err = self.check(data)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"ok": True, "tasks": 4})


class LedgerRefusedAtAStepTests(GateFixture, unittest.TestCase):
    """S2 where it is written: a refusal that lands on a gate step's own write asks the Lead, by name."""

    def refusing(self, *gates):
        """A checker that refuses every write once a task holds one of these gates, as the real one refuses the
        illegal states it reads: injected here so a step's own write is the refused one."""

        def check(data):
            held = {task.get("gate") for task in data["tasks"].values()}
            if held & set(gates):
                raise invariants.LedgerInvariant(
                    invariants.UNKNOWN_GATE, "t1", "names the gate 'reviewer'"
                )

        return mock.patch.object(invariants, "check", check)

    @pytest.mark.spec("Ledger invariants")
    def test_S2_a_refused_step_write_asks_the_lead_instead_of_ending_the_run(self):
        self.waits([self.worker_done(tests=True), self.skipped()])
        with self.refusing("converged"):
            self.run_driver()
        asked = self.request()["reason"]
        self.assertIn(invariants.UNKNOWN_GATE, asked)
        self.assertIn("t1", asked)
        self.assertIn(invariants.WAY_OUT[invariants.UNKNOWN_GATE], asked)
        self.assertEqual(
            self.run_events("error"), [], "the drive booked a ledger refusal as its own error"
        )
        self.assertEqual(
            self.task("t1")["request"]["kind"], "reported", "the request does not name the step"
        )

    @pytest.mark.spec("Ledger invariants")
    def test_S2_a_ledger_that_cannot_hold_the_request_stops_the_run_by_name(self):
        """A ledger already illegal before the write holds no request either: the run stops naming the invariant."""
        self.waits([self.worker_done(tests=True)])
        with self.refusing("converged", "requested"):
            self.run_driver(expect=1)
        self.assertIn(
            invariants.UNKNOWN_GATE, " ".join(e["error"] for e in self.run_events("error"))
        )
        self.assertFalse((self.run_dir / "t1" / "request.json").exists())
