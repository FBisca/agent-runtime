"""A driver that dies leaves a trace the Lead can read: the crash guard logs it and wakes the Lead without the ledger
lock, and `await` tells a crashed driver, a driver that was killed and a driver that has not started yet apart."""

import fcntl
import json
import os
import signal
import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import pytest
from agent_orchestrator import ledger as ledgers
from fixtures import DRIVER, load_script, wait_result
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class AwaitFixture(GateFixture):
    """The driver run as a process of its own, as `launch` runs it, so a run that dies is seen from outside."""

    def setUp(self):
        super().setUp()
        self.stderr = {}  # driver pid -> the file its stderr went to

    def start_driver(self, *extra):
        self.alias_state(self.plan)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        out = open(self.root / "driver.out", "w+", encoding="utf-8")
        err = open(self.root / "driver.err", "w+", encoding="utf-8")
        self.addCleanup(out.close)
        self.addCleanup(err.close)
        process = subprocess.Popen(
            [
                *DRIVER,
                "run",
                str(self.plan),
                "--state-dir",
                str(self.state_dir),
                "--models-file",
                str(self.models),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
                *extra,
            ],
            stdout=out,
            stderr=err,
            env=self.env(),
            start_new_session=True,
        )
        self.addCleanup(self.stop, process)
        self.stderr[process.pid] = err
        return process

    def stop(self, process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.wait()

    def finish(self, process, timeout=60):
        """(exit code, stderr) of a driver that ends by itself within the timeout."""
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            self.stop(process)
            self.fail("the driver did not end within %ss" % timeout)
        err = self.stderr[process.pid]
        err.seek(0)
        return process.returncode, err.read()

    def crashing_step(self):
        """A wait answer the loop cannot read: one message that is not an object, so an exception escapes the loop."""
        return {
            "reason": "messages",
            "messages": [42],
            "assignments": [],
            "unfinished": [],
            "receipt": "r-bad",
            "more": False,
        }

    def run_lock_path(self):
        common = self.git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return os.path.join(common, "agent-orchestrator", "demo.lock")

    def hold_run_lock(self):
        """The lock a live driver holds for the whole run."""
        path = self.run_lock_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stream = open(path, "w", encoding="utf-8")
        self.addCleanup(stream.close)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def log(self, *rows):
        """The run's events.jsonl as a driver left it."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.run_dir / "events.jsonl", "a", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(dict({"at": "2026-11-12T10:00:00+00:00"}, **row)) + "\n")

    def await_once(self, timeout=10):
        return self.cli(
            "await",
            str(self.plan),
            "--state-dir",
            str(self.state_dir),
            "--poll-seconds",
            "0.1",
            timeout=timeout,
        )

    def assert_await_polls(self):
        spec = load_script()

        class PollObserved(Exception):
            pass

        clock = mock.Mock(wraps=spec.time)
        clock.sleep.side_effect = PollObserved
        with (
            mock.patch.object(spec, "time", clock),
            self.assertRaises(PollObserved),
        ):
            self.await_once()

    def awaited(self, want, attempts=4):
        """`await` called until it prints a line starting with `want`; each call must return. The lines it printed."""
        lines = []
        for _ in range(attempts):
            try:
                result = self.await_once()
            except subprocess.TimeoutExpired:
                self.fail(
                    "await polled with no driver holding the run lock (printed so far: %r)" % lines
                )
            lines.append(result.stdout.strip())
            if lines[-1].startswith(want):
                return lines
        self.fail("await never printed %r; it printed %r" % (want, lines))


class CrashGuardTests(AwaitFixture, unittest.TestCase):
    def crash_events(self):
        return self.run_events("crash")

    def crash_wakes(self):
        return [w for w in self.wakes() if "crashed" in w.get("text", "")]

    def test_S3_an_exception_escaping_the_loop_logs_a_crash_wakes_the_lead_and_exits_non_zero(self):
        """orchestrator-engine/R72"""
        self.waits([self.crashing_step()])
        code, err = self.finish(self.start_driver())
        self.assertNotEqual(code, 0)
        (crash,) = self.crash_events()
        self.assertTrue(crash["text"].startswith("crashed "), crash)
        self.assertIn(crash["text"], err)
        self.assertEqual(len(self.crash_wakes()), 1)

    def test_S3_an_exception_during_startup_logs_a_crash_wakes_the_lead_and_exits_non_zero(self):
        """orchestrator-engine/R72"""
        code, err = self.finish(self.start_driver("--models-file", str(self.root / "missing.md")))
        self.assertNotEqual(code, 0)
        self.assertEqual(len(self.crash_events()), 1, err)
        self.assertEqual(len(self.crash_wakes()), 1)

    def test_S3_an_exception_during_finalization_logs_a_crash(self):
        """orchestrator-engine/R72"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        spec = load_script()
        with mock.patch.object(spec.Driver, "report_metrics", side_effect=RuntimeError("no disk")):
            code = self.in_process_code()
        self.assertNotEqual(code, 0)
        (crash,) = self.crash_events()
        self.assertIn("no disk", crash["text"])
        self.assertEqual(len(self.crash_wakes()), 1)

    def in_process_code(self):
        spec = load_script()
        self.alias_state(self.plan)
        argv = [
            "run",
            str(self.plan),
            "--state-dir",
            str(self.state_dir),
            "--models-file",
            str(self.models),
            "--wait-seconds",
            "1",
            "--idle-interval",
            "0",
        ]
        try:
            return self.in_process(spec, argv).returncode
        except RuntimeError:
            return 1

    def test_S3_the_crash_is_written_while_another_command_holds_the_ledger_lock(self):
        """orchestrator-engine/R72: a crash must not depend on the resource that may have caused it."""
        spec = load_script()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        driver = spec.Driver(
            SimpleNamespace(plan=str(self.plan), repo=str(self.repo), state_dir=str(self.state_dir))
        )
        driver.slug, driver.run_dir = "demo", self.run_dir
        driver.ledger_path = self.run_dir.parent / "demo.json"
        driver.start = mock.Mock(side_effect=RuntimeError("boom"))
        lock = open(driver.ledger_path.with_name("demo.json.lock"), "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        with mock.patch.object(ledgers, "DEADLINE", 30):
            with self.assertRaises(RuntimeError):
                spec.drive_locked(driver)
        self.assertLess(time.monotonic() - started, 5, "the guard waited for the ledger lock")
        self.assertEqual([c["text"] for c in self.crash_events()], ["crashed RuntimeError: boom"])
        self.assertEqual(len(self.crash_wakes()), 1)

    def test_S2_a_ledger_busy_past_the_deadline_logs_the_crash_wakes_the_lead_and_exits_1(self):
        """orchestrator-engine/R72"""
        self.alias_state(self.plan)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        lock = open(self.run_dir.parent / "demo.json.lock", "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mock.patch.object(ledgers, "DEADLINE", 0.5):
            result = self.run_driver(expect=1)
        self.assertIn("ledger busy", result.stderr)
        (crash,) = self.crash_events()
        self.assertEqual(crash["text"], "crashed ledger busy >0.5s")
        self.assertEqual(len(self.crash_wakes()), 1)

    def test_S2_a_driver_that_cannot_take_the_run_lock_logs_no_crash_into_the_run_that_holds_it(
        self,
    ):
        """orchestrator-engine/R72: a second `run` is refused; it is not a crash of the first."""
        self.hold_run_lock()
        self.run_driver(expect=2)
        self.assertEqual(self.crash_events(), [])
        self.assertEqual(self.crash_wakes(), [])

    def test_S3_a_run_that_ends_well_logs_no_crash(self):
        """orchestrator-engine/R72"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.crash_events(), [])


class AwaitTests(AwaitFixture, unittest.TestCase):
    START = {"kind": "start", "pid": 4242}

    def test_S4_await_returns_the_crash_of_a_driver_that_crashed(self):
        """orchestrator-engine/R76"""
        self.log(self.START, {"kind": "crash", "reason": "boom", "text": "crashed boom"})
        result = self.await_once()
        self.assertEqual(result.returncode, 0, result.stderr)
        line = result.stdout.strip()
        self.assertTrue(line.startswith("crashed -> "), line)
        self.assertIn("crashed boom", line)
        self.assertIn(str(self.run_dir / "events.jsonl"), line)

    def test_S4_await_says_the_driver_is_gone_when_it_started_and_left_no_crash_log(self):
        """orchestrator-engine/R76"""
        self.log(self.START)
        result = self.await_once()
        self.assertEqual(
            (result.returncode, result.stdout.strip()), (0, "driver gone without a crash log")
        )

    def test_S4_await_keeps_waiting_for_a_run_whose_driver_has_not_started_yet(self):
        """orchestrator-engine/R76"""
        self.assert_await_polls()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.assert_await_polls()

    def test_S4_await_keeps_waiting_while_a_driver_holds_the_run_lock(self):
        """orchestrator-engine/R76: a driver that is alive is neither crashed nor gone, whatever the log says."""
        self.log(self.START)
        self.hold_run_lock()
        self.assert_await_polls()
        self.log({"kind": "crash", "reason": "old", "text": "crashed old"}, self.START)
        self.assert_await_polls()

    def test_S4_the_newest_start_or_crash_decides_not_the_oldest(self):
        """orchestrator-engine/R76: a restarted run that finished is finished, whatever an earlier drive did."""
        self.log(
            self.START,
            {"kind": "crash", "reason": "old", "text": "crashed old"},
            self.START,
            {"kind": "finish"},
        )
        self.assertEqual(
            self.await_once().stdout.strip(), "finished -> %s" % (self.run_dir / "retro.md")
        )

    def test_S4_a_wake_the_lead_has_not_seen_is_printed_before_the_crash(self):
        """orchestrator-engine/R76"""
        self.log(self.START, {"kind": "crash", "reason": "boom", "text": "crashed boom"})
        (self.run_dir / "wakes.jsonl").write_text(
            json.dumps({"kind": "decision", "unit": "t1", "text": "decision t1: x -> /p"}) + "\n"
        )
        self.assertEqual(self.await_once().stdout.strip(), "decision t1: x -> /p")
        self.assertTrue(self.await_once().stdout.startswith("crashed -> "))

    def test_S4_a_start_without_a_pid_is_a_run_the_check_cannot_judge_and_await_waits(self):
        """orchestrator-engine/R76: a run directory the previous version wrote has no pid in its start line."""
        self.log({"kind": "start"})
        self.assert_await_polls()

    def test_S4_await_returns_the_crash_of_a_driver_that_really_crashed(self):
        """orchestrator-engine/R76"""
        self.waits([self.crashing_step()])
        self.finish(self.start_driver())
        self.awaited("crashed -> ")

    def test_S4_await_says_the_driver_is_gone_when_it_was_killed_without_a_crash_log(self):
        """orchestrator-engine/R76: a SIGKILL leaves no crash log."""
        marker = self.root / "in-wait"
        self.waits([wait_result(run=[["sh", "-c", 'touch "$0"; sleep 60', str(marker)]])])
        process = self.start_driver()
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "the driver never reached its wait")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        self.awaited("driver gone without a crash log")
