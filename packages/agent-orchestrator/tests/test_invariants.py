"""The ledger's state invariants: a write that breaks one is refused by name, `ledger check` reports the same on a
captured ledger, and what the driver itself writes passes both."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
from agent_orchestrator import driver, invariants

pytestmark = pytest.mark.integration

SHARED = "review-file-shared"
PARK = "park-without-key"
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

    def test_S2_a_write_that_breaks_an_invariant_is_refused_by_name_and_nothing_lands(self):
        """R: two open attempts naming one review file are refused at the write, naming the invariant, the task and
        the way out."""
        self.write(ledger(t1=task(), t1r1=task()))
        with self.assertRaises(invariants.LedgerInvariant) as refused:
            with driver.ledger(self.path) as data:
                for task_id in ("t1", "t1r1"):
                    data["tasks"][task_id].update(
                        gate="reviewing", review={"path": "/run/t1/review.json"}
                    )
        error = refused.exception
        self.assertEqual((error.invariant, error.task), (SHARED, "t1r1"))
        for needle in (SHARED, "t1r1", error.way_out):
            self.assertIn(needle, str(error))
        landed = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(landed["tasks"]["t1"]["gate"], None, "the refused write landed")

    def test_S2_a_read_of_a_broken_ledger_is_not_refused(self):
        """D1: only a write is checked, so a legacy ledger can still be diagnosed rather than refused on every read."""
        self.write(ledger(t1=task(gate="reviewer")))
        with driver.ledger(self.path) as data:
            self.assertEqual(data["tasks"]["t1"]["gate"], "reviewer")

    def test_S2_ledger_check_names_the_state_that_stalled_the_run(self):
        """R: the same three illegal states, on a captured ledger, each by name with the task that holds it and the
        way out."""
        states = {
            SHARED: (
                ledger(
                    t1=task(gate="reviewing", review={"path": "/run/t1/review.json"}),
                    t1r1=task(gate="reviewing", review={"path": "/run/t1/review.json"}),
                ),
                "t1r1",  # the attempt that took the file second
            ),
            PARK: (ledger(t1=task(gate="awaiting-amendment")), "t1"),
            GATE: (ledger(t1=task(gate="reviewer")), "t1"),
        }
        for invariant, (data, named) in states.items():
            with self.subTest(invariant=invariant):
                code, out, err = self.check(data, "%s.json" % invariant)
                self.assertEqual(code, 2, err or out)
                refused = json.loads(err)
                self.assertEqual(refused["invariant"], invariant)
                self.assertEqual(refused["task"], named)
                self.assertTrue(refused["way_out"])

    def test_S2_control_a_coherent_ledger_passes(self):
        """A ledger the driver itself writes passes: one review file per open attempt (a superseded attempt keeps its
        own), a park with its release key, every gate one the driver knows."""
        data = ledger(
            t1=task(status="PARTIAL", gate="reviewing", review={"path": "/run/t1/review.json"}),
            t1r1=task(status="RELEASED", gate="superseded", review={"path": "/run/t1/review.json"}),
            t2=task(status="COMPLETE", gate="done"),
            t3=task(gate="awaiting-amendment", amend_from="a-digest"),
        )
        invariants.check(data)
        code, out, err = self.check(data)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"ok": True, "tasks": 4})
