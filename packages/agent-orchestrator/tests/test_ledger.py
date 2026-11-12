"""The ledger module the driver and the evidence runner share: one bounded lock, unlocked snapshots whose writes
revalidate, and the kind marker."""

import contextlib
import fcntl
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest
from agent_orchestrator import driver as app
from agent_orchestrator import ledger as ledgers
from agent_orchestrator import runner
from fixtures import load_script
from test_acceptance import AcceptanceFixture, check_item
from test_acceptance import scenario as acceptance_scenario
from test_gates import GateFixture

pytestmark = pytest.mark.integration

MARKER = "agent-orchestrator/ledger"


class Clock:
    """A monotonic clock and a sleep that move together, so a minute of backoff costs no time."""

    def __init__(self, on_sleep=None):
        self.now = 1000.0
        self.slept = []
        self.on_sleep = on_sleep

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(len(self.slept))


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "demo.json"

    def hold(self):
        """The ledger lock, held the way a Lead command holds it: through a file of its own."""
        stream = open(self.path.with_name(self.path.name + ".lock"), "w")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(stream.close)
        return stream

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))


class BoundedLockTests(LedgerCase):
    def test_S1_a_held_ledger_is_waited_out_with_jittered_backoff_and_then_booked(self):
        """orchestrator-engine/R71"""
        lock = self.hold()
        clock = Clock(on_sleep=lambda n: lock.close() if n == 4 else None)
        with ledgers.locked(self.path, clock=clock, sleep=clock.sleep) as data:
            data["tasks"]["t1"] = {"status": "ACTIVE"}
        self.assertEqual(len(clock.slept), 4)
        self.assertGreaterEqual(clock.slept[0], 0.1)
        self.assertLessEqual(clock.slept[0], 0.2)
        for index, slept in enumerate(clock.slept):
            self.assertLessEqual(
                slept, min(5.0, 0.2 * 2**index), "the backoff doubles up to its cap"
            )
        self.assertEqual(self.read()["tasks"], {"t1": {"status": "ACTIVE"}})

    def test_S1_the_backoff_grows_to_5_seconds_and_no_further(self):
        """orchestrator-engine/R71"""
        self.hold()
        clock = Clock()
        with self.assertRaises(ledgers.LedgerBusy):
            with ledgers.locked(self.path, clock=clock, sleep=clock.sleep):
                pass
        self.assertLessEqual(max(clock.slept), 5.0)
        self.assertGreaterEqual(max(clock.slept), 2.5, "the backoff never reached its cap")

    def test_S1_the_driver_waits_for_a_ledger_a_lead_command_holds_and_books_the_change(self):
        """orchestrator-engine/R71"""
        lock = self.hold()
        threading.Timer(0.5, lock.close).start()
        started = time.monotonic()
        with app.ledger(self.path) as data:
            data["tasks"]["t1"] = {"status": "ACTIVE"}
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        self.assertEqual(self.read()["tasks"], {"t1": {"status": "ACTIVE"}})

    def test_S2_a_ledger_busy_past_the_deadline_raises_ledger_busy_after_exactly_60_seconds(self):
        """orchestrator-engine/R72"""
        self.hold()
        clock = Clock()
        with self.assertRaises(ledgers.LedgerBusy) as caught:
            with ledgers.locked(self.path, clock=clock, sleep=clock.sleep):
                pass
        self.assertEqual(str(caught.exception), "ledger busy >60s")
        self.assertAlmostEqual(clock.now - 1000.0, 60.0, places=6)
        self.assertAlmostEqual(
            sum(clock.slept), 60.0, places=6, msg="the sleeps are in the deadline"
        )

    def test_S2_a_busy_ledger_is_not_a_value_error_the_loop_could_swallow(self):
        """orchestrator-engine/R72: the loop's step handlers catch `ValueError` and go on; a busy ledger must reach the
        crash guard instead."""
        self.assertFalse(issubclass(ledgers.LedgerBusy, ValueError))
        self.assertFalse(issubclass(ledgers.LedgerBusy, OSError))

    def test_S2_a_lead_command_that_meets_a_busy_ledger_exits_1_with_the_reason(self):
        """orchestrator-engine/R72"""
        self.hold()
        self.write({"version": 1, "tasks": {}})
        err = io.StringIO()
        with (
            mock.patch.object(ledgers, "DEADLINE", 0.3),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = app.run_cli(
                ["release", "--state", str(self.path), "--task", "t1", "--stopped", "--reason", "x"]
            )
        self.assertEqual(code, 1)
        self.assertIn("ledger busy", err.getvalue())

    def test_S6_the_runner_and_the_driver_take_the_lock_through_one_helper_and_one_deadline(self):
        """orchestrator-engine/R73"""
        self.assertEqual(runner.LEDGER_WAIT, ledgers.DEADLINE)
        with mock.patch.object(ledgers, "locked", wraps=ledgers.locked) as spy:
            with app.ledger(self.path) as data:
                data["tasks"]["t1"] = {}
            runner.record_receipts(self.path, [])
        self.assertEqual(spy.call_count, 2)
        self.assertEqual(
            {call.kwargs.get("wait") for call in spy.call_args_list} - {None}, {ledgers.DEADLINE}
        )

    def test_S6_the_runner_waits_for_a_busy_ledger_as_the_driver_does(self):
        """orchestrator-engine/R73"""
        lock = self.hold()
        threading.Timer(0.5, lock.close).start()
        started = time.monotonic()
        runner.record_receipts(self.path, [])
        self.assertGreaterEqual(time.monotonic() - started, 0.4)
        self.assertEqual(self.read()["version"], 1)

    def test_S6_the_runner_refuses_a_ledger_that_stays_busy_naming_it(self):
        """orchestrator-engine/R73"""
        self.hold()
        with mock.patch.object(runner, "LEDGER_WAIT", 0.3):
            with self.assertRaises(runner.RunnerError) as caught:
                runner.record_receipts(self.path, [])
        self.assertIn("busy", str(caught.exception))
        self.assertIn(str(self.path), str(caught.exception))


class KindMarkerTests(LedgerCase):
    def test_S7_a_ledger_the_orchestrator_creates_carries_the_marker_beside_version_1(self):
        """orchestrator-engine/R74"""
        with ledgers.locked(self.path) as data:
            data["tasks"]["t1"] = {}
        self.assertEqual(list(self.read())[:2], ["version", "kind"])
        self.assertEqual((self.read()["version"], self.read()["kind"]), (1, MARKER))

    def test_S7_the_runner_creating_a_ledger_writes_the_marker(self):
        """orchestrator-engine/R74"""
        runner.record_receipts(self.path, [])
        self.assertEqual((self.read()["version"], self.read()["kind"]), (1, MARKER))

    def test_S7_the_next_locked_write_to_a_ledger_without_the_marker_adds_it(self):
        """orchestrator-engine/R74"""
        self.write(
            {"version": 1, "tasks": {"t1": {"status": "ACTIVE"}}, "lead": "orchestrate:demo"}
        )
        with app.ledger(self.path) as data:
            data["tasks"]["t2"] = {}
        written = self.read()
        self.assertEqual((written["version"], written["kind"]), (1, MARKER))
        self.assertEqual(sorted(written["tasks"]), ["t1", "t2"])
        self.assertEqual(written["lead"], "orchestrate:demo")

    def test_S7_the_marker_is_on_disk_before_the_locked_block_runs(self):
        """orchestrator-engine/R74: before any worker-visible output exists."""
        self.write({"version": 1, "tasks": {}})
        with ledgers.locked(self.path):
            self.assertEqual(self.read()["kind"], MARKER)

    def test_S7_a_ledger_that_has_the_marker_is_not_rewritten_by_a_block_that_changes_nothing(self):
        """orchestrator-engine/R74"""
        self.write({"version": 1, "kind": MARKER, "tasks": {}})
        before = self.path.stat().st_mtime_ns
        with ledgers.locked(self.path):
            pass
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_S7_a_ledger_of_another_kind_is_refused(self):
        """orchestrator-engine/R74"""
        self.write({"version": 1, "kind": "something/else", "tasks": {}})
        with self.assertRaises(ValueError) as caught:
            with ledgers.locked(self.path):
                pass
        self.assertIn("something/else", str(caught.exception))


class RunMarkerTests(GateFixture, unittest.TestCase):
    def test_S7_a_run_creates_its_ledger_with_the_marker_before_any_file_a_worker_reads(self):
        """orchestrator-engine/R74"""
        seen = []
        original = Path.write_text
        ledger_file = self.run_dir.parent / "demo.json"

        def spy(path, *args, **kwargs):
            if path.name in ("contract.md", "task.md"):
                try:
                    kind = json.loads(ledger_file.read_text(encoding="utf-8")).get("kind")
                except (OSError, ValueError):
                    kind = None
                seen.append((path.name, kind))
            return original(path, *args, **kwargs)

        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        with mock.patch.object(Path, "write_text", spy):
            self.run_driver()
        self.assertTrue(seen, "the run wrote no brief")
        self.assertEqual({kind for _, kind in seen}, {MARKER}, seen)
        self.assertEqual((self.ledger()["version"], self.ledger()["kind"]), (1, MARKER))


class SnapshotTests(LedgerCase):
    def test_S5_a_snapshot_read_takes_no_lock_and_waits_for_none(self):
        """orchestrator-engine/R75"""
        self.write({"version": 1, "tasks": {"t1": {}}})
        self.hold()
        with mock.patch.object(ledgers, "DEADLINE", 0.2):
            self.assertEqual(ledgers.snapshot(self.path)["tasks"], {"t1": {}})

    def test_S5_show_reads_a_snapshot_while_a_lead_command_holds_the_lock(self):
        """orchestrator-engine/R75"""
        self.write({"version": 1, "kind": MARKER, "tasks": {}})
        self.hold()
        out = io.StringIO()
        with mock.patch.object(ledgers, "DEADLINE", 0.2), contextlib.redirect_stdout(out):
            code = app.run_cli(["show", "--state", str(self.path)])
        self.assertEqual((code, json.loads(out.getvalue())), (0, []))

    def test_S5_show_of_a_ledger_that_does_not_exist_yet_is_empty_and_creates_nothing(self):
        """orchestrator-engine/R75"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = app.run_cli(["show", "--state", str(self.path)])
        self.assertEqual((code, json.loads(out.getvalue())), (0, []))
        self.assertEqual(os.listdir(self.temp.name), [])

    def test_S5_a_write_after_the_ledger_moved_since_the_snapshot_is_refused_with_a_reason(self):
        """orchestrator-engine/R75"""
        self.write({"version": 1, "kind": MARKER, "tasks": {}, "integration": {"sha": "a"}})
        snapshot = ledgers.snapshot(self.path)
        with ledgers.locked(self.path) as data:  # another writer
            data["integration"] = {"sha": "b"}
        before = self.path.read_bytes()
        with self.assertRaises(ledgers.LedgerStale) as caught:
            with ledgers.locked(self.path, snapshot=snapshot, keys=("integration",)) as data:
                data["acceptance"] = {"S1": {}}
        self.assertIn("integration", str(caught.exception))
        self.assertIn("read it again", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), before)

    def test_S5_a_write_on_a_snapshot_that_is_still_current_goes_through(self):
        """orchestrator-engine/R75"""
        self.write({"version": 1, "kind": MARKER, "tasks": {}, "integration": {"sha": "a"}})
        snapshot = ledgers.snapshot(self.path)
        with ledgers.locked(self.path, snapshot=snapshot, keys=("integration",)) as data:
            data["acceptance"] = {"S1": {}}
        self.assertEqual(self.read()["acceptance"], {"S1": {}})

    def test_S5_a_change_to_a_key_the_write_does_not_depend_on_is_not_stale(self):
        """orchestrator-engine/R75: the driver books tasks all the time; a snapshot only watches what it decided on."""
        self.write({"version": 1, "kind": MARKER, "tasks": {}, "integration": {"sha": "a"}})
        snapshot = ledgers.snapshot(self.path)
        with ledgers.locked(self.path) as data:
            data["tasks"]["t1"] = {}
        with ledgers.locked(self.path, snapshot=snapshot, keys=("integration",)) as data:
            data["acceptance"] = {"S1": {}}
        self.assertEqual(sorted(self.read()["tasks"]), ["t1"])
        self.assertEqual(self.read()["acceptance"], {"S1": {}})

    def test_S5_evidence_rebuilds_from_a_snapshot_while_the_lock_is_held(self):
        """orchestrator-engine/R75"""
        runs = Path(self.temp.name)
        plan = runs / "p.plan.yaml"
        self.write({"version": 1, "kind": MARKER, "tasks": {}})
        self.hold()
        with (
            mock.patch.object(ledgers, "DEADLINE", 0.2),
            mock.patch.object(app, "plan_home", return_value=("demo", self.path, runs / "demo")),
            mock.patch.object(app.evidence, "attempts", return_value=[]),
        ):
            self.assertEqual(app.rebuild_evidence(mock.Mock(plan=str(plan))), [])


class AcceptsRevalidateTests(AcceptanceFixture, unittest.TestCase):
    def test_S5_accept_refuses_when_the_ledger_moved_after_it_was_read(self):
        """orchestrator-engine/R75: the read half of `accept` is a snapshot; the write takes the lock and revalidates."""
        self.COVERS = {
            "S1": ["src/a/test_feature.py::test_feature"],
            "S2": ["src/a/test_feature.py::test_feature"],
        }
        scenarios = [
            acceptance_scenario("S1", check_item()),
            acceptance_scenario("S2", {"human": "a person confirms the panel"}),
        ]
        self.delivered(lands="done", scenarios=scenarios)
        spec = load_script()
        read = spec.acceptance_context

        def moved_after_the_read(args):
            found = read(args)
            with ledgers.locked(found[2]) as data:  # another Lead command accepts something else
                data["integration"] = dict(data["integration"], moved="by another command")
            return found

        err = io.StringIO()
        with (
            mock.patch.object(spec, "acceptance_context", moved_after_the_read),
            mock.patch.object(spec, "resolve_runtime", return_value={"role": "lead"}),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = spec.run_cli(
                ["accept", str(self.plan), "S2", "--note", "x", "--state-dir", str(self.state_dir)]
            )
        self.assertEqual(code, 2, err.getvalue())
        self.assertIn("changed since", err.getvalue())
        self.assertNotIn("acceptance", self.ledger())
