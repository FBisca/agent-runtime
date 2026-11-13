"""Outcome oracle for the orchestrator-audit-fixes plan.

A fixture plan is driven through the real driver, run as the Lead runs it (a process of its own), with the fake
runtime and gh on PATH: a Lead command holds the ledger lock across a booking and the drive survives it; a drive that
crashes or is killed is reported by `await` instead of polled forever; a mutation reviewer that goes silent after
writing its output is accepted, and one that goes silent with nothing written is asked about; a restart over a resume
the runtime may or may not have delivered asks the Lead and sends nothing again; the evidence runner keeps its scratch
HOME and never writes through a symlink; and `plan approve` refuses a worker at the CLI. The `control` tests hold today
and must keep holding: they prove each fixture is real.
"""

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from typing import IO

import pytest
import yaml
from agent_orchestrator import contracts, runner
from fixtures import DRIVER, TESTS, RunFixture, assignment, wait_result
from test_acceptance import AcceptanceFixture
from test_gates import GateFixture

pytestmark = pytest.mark.integration

SLUG = "orchestrator-audit-fixes"
MUTATOR = "demo-mut-s1-1"

# A Lead command, as far as the ledger can tell: a process that takes `<ledger>.lock`, says so, holds it for the given
# seconds and lets go. It forks so the wait step that starts it returns while the lock is still held.
HOLDER = r"""
import fcntl, os, sys, time
lock, flag, seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
if os.fork():
    deadline = time.time() + 15
    while not os.path.exists(flag) and time.time() < deadline:
        time.sleep(0.02)
    sys.exit(0 if os.path.exists(flag) else 1)
os.setsid()
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
started = time.time()
with open(flag + ".tmp", "w") as stream:
    stream.write(str(os.getpid()))
os.replace(flag + ".tmp", flag)
time.sleep(seconds)
with open(flag + ".released", "w") as stream:
    stream.write("%.3f" % (time.time() - started))
os._exit(0)
"""

# The fake runtime as a program, with two additions a restart test needs: a send (spawn or resume) of the named agent
# kills the driver that made it once the runtime has recorded it, and `call operation` answers `pending` for the
# operation that send carried. Every other call is the fixture's fake.
RUNTIME = r"""#!%(python)s
import json, os, signal, sys
sys.path.insert(0, %(tests)r)
import fake_runtime
argv = sys.argv[1:]
home = os.environ["FAKE_RUNTIME_HOME"]
tool = argv[1] if len(argv) > 1 and argv[0] == "call" else None
args = json.loads(argv[argv.index("--json") + 1]) if "--json" in argv else {}
killed_path = os.path.join(home, "killed-operations.json")
killed = fake_runtime.read_json(killed_path, {}) or {}
if tool == "operation" and args.get("operation_id") in killed:
    with open(os.path.join(home, "calls.jsonl"), "a", encoding="utf-8") as log:
        log.write(json.dumps({"tool": tool, "lead": None, "args": args, "events": []}) + "\n")
    print(json.dumps({"operation_id": args["operation_id"], "state": "pending",
                      "assignment_id": killed[args["operation_id"]]}))
    sys.exit(0)
result = fake_runtime.process(argv)
flag = os.path.join(home, "die-on-send.json")
target = fake_runtime.read_json(flag, {}) or {}
if tool in ("spawn", "resume") and target.get("tool") == tool and target.get("name") == args.get("name"):
    os.remove(flag)
    answer = json.loads(result.stdout or "{}")
    if args.get("operation_id"):
        killed[args["operation_id"]] = answer.get("assignment_id")
        fake_runtime.write_json(killed_path, killed)
    os.kill(os.getppid(), signal.SIGKILL)
    sys.exit(1)
if result.stdout:
    print(result.stdout, end="")
if result.stderr:
    print(result.stderr, end="", file=sys.stderr)
sys.exit(result.returncode)
"""


class Driven(subprocess.Popen):
    """A driver process that keeps the files its stdout and stderr go to."""

    out: IO[str]
    err: IO[str]


class Drives(RunFixture):
    """The driver run as its own process, as `launch` runs it, so a run that hangs or dies is observed from outside."""

    def driver_argv(self, *extra):
        return [
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
        ]

    def driver_env(self, first=None):
        env = self.env()
        if first:
            env["PATH"] = str(first) + os.pathsep + env["PATH"]
        return env

    def start_driver(self, *extra, first=None):
        self.alias_state(self.plan)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.runs = getattr(self, "runs", 0) + 1
        out = open(self.root / ("driver-%d.out" % self.runs), "w+", encoding="utf-8")
        err = open(self.root / ("driver-%d.err" % self.runs), "w+", encoding="utf-8")
        self.addCleanup(out.close)
        self.addCleanup(err.close)
        process = Driven(
            self.driver_argv(*extra),
            stdout=out,
            stderr=err,
            env=self.driver_env(first),
            start_new_session=True,
        )
        self.addCleanup(self.kill, process)
        process.out, process.err = out, err
        return process

    def kill(self, process):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            process.wait()

    def settle(self, process, timeout, until=None):
        """Wait until the driver exits, or `until()` holds, or the timeout passes; a driver still running then is
        killed. Returns (exit code or None when it had to be stopped, stdout, stderr)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            if until is not None and until():
                break
            time.sleep(0.1)
        code = process.poll()
        self.kill(process)
        process.out.seek(0)
        process.err.seek(0)
        return code, process.out.read(), process.err.read()

    def drive_process(self, steps, timeout, *extra, until=None, first=None):
        self.waits(steps)
        return self.settle(self.start_driver(*extra, first=first), timeout, until)

    def run_files_text(self):
        """Every text file the run wrote under its run directory, joined."""
        found = []
        run_dir = self.run_dir_of()
        for path in sorted(run_dir.rglob("*")) if run_dir.exists() else []:
            if path.is_file() and path.suffix in (".jsonl", ".log", ".md", ".json", ".txt"):
                try:
                    found.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    pass
        return "\n".join(found)

    def awaited(self, want, attempts=5, seconds=6):
        """`await` called until it prints a line starting with `want`; each call must return. The lines it printed."""
        lines = []
        for _ in range(attempts):
            try:
                result = self.cli(
                    "await",
                    str(self.plan),
                    "--state-dir",
                    str(self.state_dir),
                    "--poll-seconds",
                    "0.1",
                    timeout=seconds,
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is None:
                self.fail(
                    "await polled for %ss with no driver holding the run lock (printed so far: %r)"
                    % (seconds, lines)
                )
            line = result.stdout.strip()
            lines.append(line)
            if line.startswith(want):
                return lines
            if not line or line.startswith("finished"):
                break
        self.fail("await never printed %r; it printed %r" % (want, lines))


class LedgerContentionOutcome(Drives, GateFixture, unittest.TestCase):
    def lock_path(self):
        return self.state_dir / "demo.json.lock"

    def hold(self, seconds, name="held"):
        """A wait step during which a Lead command takes the ledger lock and keeps it `seconds` past the step."""
        flag = self.root / name
        self.addCleanup(self.release, flag)
        return wait_result(
            run=[[sys.executable, "-c", HOLDER, str(self.lock_path()), str(flag), str(seconds)]]
        ), flag

    def release(self, flag):
        try:
            os.kill(int(flag.read_text(encoding="utf-8")), signal.SIGKILL)
        except (OSError, ValueError):
            pass

    def crashed_step(self):
        """A wait answer the loop cannot read: one message that is not an object, so an exception escapes it."""
        return {
            "reason": "messages",
            "messages": [42],
            "assignments": [],
            "unfinished": [],
            "receipt": "r-bad",
            "more": False,
        }

    def test_a_lead_command_holding_the_ledger_across_a_booking_is_waited_out(self):
        """orchestrator-audit-fixes/S1: the drive waits for the lock and books the change; it does not exit."""
        held, flag = self.hold(3)
        worker = self.worker_done(tests=True)
        worker["run"] = held["run"]
        code, out, err = self.drive_process([worker, self.reviewer_done()], 60)
        self.assertTrue(flag.exists(), "the Lead command never held the ledger lock")
        self.assertEqual(
            code, 0, "the drive exited while a Lead command held the ledger: %s" % err[-800:]
        )
        busy = [e for e in self.events("error") if "busy" in json.dumps(e)]
        self.assertEqual(busy, [], "a busy ledger was an error, not a wait")
        self.assertEqual((self.task()["status"], self.task()["gate"]), ("COMPLETE", "done"))

    def test_a_ledger_busy_past_the_deadline_logs_a_crash_and_writes_a_wake(self):
        """orchestrator-audit-fixes/S2: past 60 s the driver logs `crashed ledger busy >60s`, wakes the Lead without the
        lock and exits non-zero."""
        held, flag = self.hold(150)
        worker = self.worker_done(tests=True)
        worker["run"] = held["run"]
        started = time.monotonic()
        code, out, err = self.drive_process([worker, self.reviewer_done()], 110)
        self.assertTrue(flag.exists(), "the Lead command never held the ledger lock")
        self.assertIsNotNone(code, "the driver neither booked nor gave up within 110 s")
        self.assertNotEqual(code, 0)
        logged = out + err + self.run_files_text()
        self.assertTrue(
            "crashed ledger busy >60s" in logged, "no crash was logged: %s" % err[-600:]
        )
        self.assertGreaterEqual(
            time.monotonic() - started, 55, "the driver gave up before the deadline"
        )
        self.assertTrue(
            [w for w in self.wakes() if "crashed" in w.get("text", "")],
            "no wake told the Lead about the crash",
        )

    def test_an_exception_escaping_the_loop_logs_a_crash_and_writes_a_wake(self):
        """orchestrator-audit-fixes/S3"""
        code, out, err = self.drive_process([self.crashed_step()], 30)
        self.assertIsNotNone(code, "the driver did not end")
        self.assertNotEqual(code, 0)
        logged = out + err + self.run_files_text()
        self.assertTrue("crashed " in logged, "no crash was logged: %s" % err[-600:])
        self.assertTrue(
            [w for w in self.wakes() if "crashed" in w.get("text", "")],
            "no wake told the Lead about the crash",
        )

    def test_await_returns_the_crash_of_a_driver_that_crashed(self):
        """orchestrator-audit-fixes/S4: `crashed -> <log line>`, never a poll that lasts forever."""
        code, _, _ = self.drive_process([self.crashed_step()], 30)
        self.assertIsNotNone(code, "the driver did not end")
        lines = self.awaited("crashed -> ")
        self.assertGreater(len(lines[-1]), len("crashed -> "), "the crash names no log line")

    def test_await_says_the_driver_is_gone_when_it_was_killed_without_a_crash_log(self):
        """orchestrator-audit-fixes/S4: a SIGKILL leaves no crash log: `driver gone without a crash log`."""
        marker = self.root / "in-wait"
        hang = wait_result(run=[["sh", "-c", 'touch "$0"; sleep 60', str(marker)]])
        self.waits([hang])
        process = self.start_driver()
        deadline = time.monotonic() + 30
        while not marker.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.05)
        self.assertTrue(marker.exists(), "the driver never reached its wait")
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        self.awaited("driver gone without a crash log")

    def test_control_the_fixture_plan_drives_to_completion_without_contention(self):
        """Holds today: the process-run driver completes the unit and `await` then says finished."""
        code, out, err = self.drive_process(
            [self.worker_done(tests=True), self.reviewer_done()], 60
        )
        self.assertEqual(code, 0, err[-800:])
        self.assertEqual((self.task()["status"], self.task()["gate"]), ("COMPLETE", "done"))
        self.assertEqual(
            self.awaited("finished")[-1], "finished -> %s" % (self.run_dir / "retro.md")
        )


class SilentReviewerOutcome(Drives, AcceptanceFixture, unittest.TestCase):
    PATCH = "--- a/src/a/feature.txt\n+++ b/src/a/feature.txt\n@@ -1 +1 @@\n-done\n+nope\n"

    def silent_mutator(self, write=True):
        """A wait step: the mutation reviewer's turn ended (idle) with no `complete`; with `write`, its patch is on disk."""
        step = wait_result([assignment(MUTATOR, "working", "idle")])
        if write:
            step["write"] = [[str(self.run_dir / "mutation" / "S1.1.patch"), self.PATCH]]
        return step

    def mutation_receipts(self):
        return self.receipts("mutation")

    def test_a_mutation_reviewer_silent_after_writing_its_patch_is_accepted(self):
        """orchestrator-audit-fixes/S8: its output is validated exactly as for a reported completion."""
        self.write_plan()
        self.merged_on_main("done")
        steps = [self.worker("done"), self.reviewer(), self.silent_mutator()] + [
            self.silent_mutator(write=False)
        ] * 3
        code, out, err = self.drive_process(steps, 30, "--repo", str(self.repo))
        self.assertIsNotNone(code, "the plan hung in VERIFYING on a silent mutation reviewer")
        self.assertEqual(code, 0, err[-800:])
        receipts = self.mutation_receipts()
        self.assertEqual(len(receipts), 1, "the silent reviewer's patch was never run")
        self.assertTrue(receipts[0]["mutation"]["killed"])
        self.assertIn("ACCEPTED", self.history())

    def test_a_mutation_reviewer_silent_with_no_patch_gets_an_idle_request(self):
        """orchestrator-audit-fixes/S9: the same idle request as a silent task agent; no hang, not ended at once."""
        self.write_plan()
        self.merged_on_main("done")
        steps = [self.worker("done"), self.reviewer()] + [self.silent_mutator(write=False)] * 8

        def asked():
            return [w for w in self.wakes() if MUTATOR in w.get("text", "")]

        code, out, err = self.drive_process(
            steps, 25, "--repo", str(self.repo), "--idle-minutes", "0", until=asked
        )
        self.assertTrue(
            asked(), "no idle request named the silent mutation reviewer: %s" % err[-600:]
        )
        (wake,) = asked()[:1]
        self.assertIn("idle", wake["text"])
        self.assertNotIn(
            "demo-mut-s1-2", self.spawned(), "the silent reviewer was treated as ended at once"
        )
        self.assertEqual(self.mutation_receipts(), [], "a patch that was never written was applied")

    def decide(self, *args):
        return self.cli("decide", str(self.plan), *args, "--state-dir", str(self.state_dir))

    def open_request(self, task):
        """An open request on disk for unit t1, as the driver leaves it, for a test of `decide` alone."""
        self.write_plan()
        path = self.run_dir / "t1" / "request.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(contracts.make_request("t1", task, "blocked", "cannot go on", [str(path)])),
            encoding="utf-8",
        )
        return path

    def test_an_answer_naming_t1a1_for_t1a2_is_refused_as_foreign(self):
        """orchestrator-audit-fixes/S13: the `a` round suffix is an attempt id like the others."""
        path = self.open_request("t1a2")
        before = path.read_text(encoding="utf-8")
        result = self.decide("t1", "answer", "--reason", "x", "--answer", "use t1a1")
        self.assertEqual(result.returncode, 2, "t1a1 was not recognized as another attempt")
        self.assertIn("t1a1", result.stderr)
        self.assertIn("t1a2", result.stderr)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_control_an_answer_naming_t1r1_for_t1r2_is_refused(self):
        """Holds today: the round suffixes the pattern knows are refused."""
        self.open_request("t1r2")
        result = self.decide("t1", "answer", "--reason", "x", "--answer", "use t1r1")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())

    def test_control_a_mutation_reviewer_that_reports_is_accepted(self):
        """Holds today: the process-run drive accepts the plan on the reviewer's reported patch."""
        self.write_plan()
        self.merged_on_main("done")
        code, out, err = self.drive_process(
            [self.worker("done"), self.reviewer(), self.default_mutator()],
            60,
            "--repo",
            str(self.repo),
        )
        self.assertEqual(code, 0, err[-800:])
        (receipt,) = self.mutation_receipts()
        self.assertTrue(receipt["mutation"]["killed"])
        self.assertIn("ACCEPTED", self.history())


class IntentRestartOutcome(Drives, GateFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.bin = self.root / "runtime-bin"
        self.bin.mkdir()
        tool = self.bin / "agent-runtime"
        tool.write_text(RUNTIME % {"python": sys.executable, "tests": str(TESTS)}, encoding="utf-8")
        tool.chmod(0o755)

    def sends(self, name="demo-t1"):
        return [
            c
            for c in self.calls()
            if c["tool"] in ("spawn", "resume") and c["args"].get("name") == name
        ]

    def requests(self, unit="t1"):
        found = []
        for path in sorted((self.run_dir / unit).glob("request*.json")):
            try:
                found.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        return found

    def test_a_restart_over_a_pending_resume_asks_the_lead_and_sends_nothing_again(self):
        """orchestrator-audit-fixes/S15: pending -> a Lead request naming the worker; the runtime sees no second send."""
        (self.home / "die-on-send.json").write_text(
            json.dumps({"tool": "resume", "name": "demo-t1"}), encoding="utf-8"
        )
        code, _, err = self.drive_process([self.worker_done()], 60, first=self.bin)
        self.assertEqual(
            code, -signal.SIGKILL, "the driver was not killed at its resume: %s" % err[-800:]
        )
        before = self.sends()
        self.assertEqual(
            [c["tool"] for c in before], ["spawn", "resume"], "the fixture did not reach the resume"
        )

        def settled():
            return self.requests() or len(self.sends()) > len(before)

        self.drive_process([], 30, until=settled, first=self.bin)
        again = [c["tool"] for c in self.sends()[len(before) :]]
        self.assertEqual(again, [], "the restart sent demo-t1 again")
        opened = [r for r in self.requests() if r["status"] == "open"]
        self.assertTrue(opened, "the restart raised no Lead request")
        self.assertIn("demo-t1", opened[-1]["reason"])
        self.assertTrue(self.wake_lines("decision"), "the Lead was not woken")

    def test_control_a_fix_round_without_a_restart_resumes_the_worker_once(self):
        """Holds today: the wrapped runtime answers as the fake does, and the fix round completes the unit."""
        code, _, err = self.drive_process(
            [self.worker_done(), self.worker_done("t1r1", tests=True), self.reviewer_done()],
            60,
            first=self.bin,
        )
        self.assertEqual(code, 0, err[-800:])
        self.assertEqual([c["tool"] for c in self.sends()], ["spawn", "resume"])
        self.assertEqual(self.task("t1r1")["gate"], "done")


class RunnerContainmentOutcome(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.plan = self.repo / "docs" / "plans" / "demo.plan.yaml"

    def evidence(self, **over):
        values = dict(
            scenario="S1",
            phase="ticket",
            digest="d" * 64,
            check="true",
            oracles={"probe.txt.oracle": b"probe\n"},
            place="checks/",
        )
        values.update(over)
        return runner.run_evidence(self.repo, "HEAD", **values)

    def commit(self, message):
        self.git(self.repo, "add", "-A")
        self.git(self.repo, "commit", "-q", "-m", message)

    def outside_files(self):
        return sorted(p.name for p in self.outside.rglob("*"))

    def write_plan(self, env):
        data = {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "deliver",
            "spec": "docs/specs/demo.md",
            "outcome": ["S1"],
            "scenarios": [
                {
                    "id": "S1",
                    "when": "a",
                    "then": "b",
                    "evidence": {"check": "true", "baseline": "pass", "env": list(env)},
                }
            ],
            "tickets": [
                {
                    "id": "T1",
                    "delivers": "first",
                    "satisfies": ["S1"],
                    "paths": ["src/a/"],
                    "size": "small",
                    "difficulty": "simple",
                }
            ],
        }
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def plan_check(self):
        return self.cli(
            "plan",
            "check",
            str(self.plan),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        )

    def test_plan_check_refuses_evidence_env_naming_home_path_or_xdg(self):
        """orchestrator-audit-fixes/S17: the refusal names the variable and the way out."""
        wrong = []
        for name in ("HOME", "PATH", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
            self.write_plan(["MY_TOKEN", name])
            result = self.plan_check()
            if result.returncode == 0:
                wrong.append("%s: accepted" % name)
            elif name not in result.stderr or "scratch" not in result.stderr:
                wrong.append(
                    "%s: the refusal names neither it nor the way out: %s"
                    % (name, result.stderr[-300:])
                )
        self.assertEqual(wrong, [], "plan check let a reserved env name through")

    def test_plan_approve_refuses_evidence_env_naming_home(self):
        """orchestrator-audit-fixes/S17: approve refuses too, and writes no approval."""
        self.write_plan(["HOME"])
        before = self.plan.read_text(encoding="utf-8")
        result = subprocess.run(
            [
                *DRIVER,
                "plan",
                "approve",
                str(self.plan),
                "--repo",
                str(self.repo),
                "--state-dir",
                str(self.state_dir),
            ],
            capture_output=True,
            text=True,
            env=plain_env(self.root),
            timeout=120,
        )
        self.assertNotEqual(result.returncode, 0, "plan approve accepted env HOME")
        self.assertIn("HOME", result.stderr)
        self.assertEqual(self.plan.read_text(encoding="utf-8"), before, "an approval was written")

    def test_the_scratch_home_wins_over_a_declared_home(self):
        """orchestrator-audit-fixes/S18: HOME and XDG_STATE_HOME are the scratch values whatever the plan declares."""
        seen = self.root / "seen-env"
        declared = {
            "HOME": str(self.root / "declared-home"),
            "XDG_STATE_HOME": str(self.root / "declared-state"),
        }
        run = self.evidence(
            check='printf "%%s\\n%%s\\n" "$HOME" "$XDG_STATE_HOME" > %s' % seen,
            oracles={},
            place=None,
            env_names=["HOME", "XDG_STATE_HOME"],
            base_env=dict(os.environ, **declared),
        )
        self.assertTrue(seen.exists(), "the check never ran: %s" % run.receipt["label"])
        home, state = seen.read_text(encoding="utf-8").splitlines()
        self.assertNotEqual(home, declared["HOME"], "the declared HOME overrode the scratch HOME")
        self.assertNotEqual(state, declared["XDG_STATE_HOME"], "the declared XDG_STATE_HOME won")

    def test_an_oracle_placed_through_a_committed_symlink_is_an_evidence_error(self):
        """orchestrator-audit-fixes/S19: nothing is written outside the scratch tree, and the receipt names the path."""
        os.symlink(str(self.outside), str(self.repo / "checks"))
        self.commit("a directory that is a link out of the tree")
        run = self.evidence()
        self.assertEqual(self.outside_files(), [], "the oracle was written through the symlink")
        self.assertNotEqual(run.receipt["result"], "PASS")
        self.assertIn(
            "checks", (run.receipt.get("detail") or "") + (run.receipt.get("reason") or "")
        )

    def test_an_oracle_placed_through_a_symlink_the_patch_adds_is_an_evidence_error(self):
        """orchestrator-audit-fixes/S19: the patch under test adds the link; the oracle still stays in scratch."""
        os.symlink(str(self.outside), str(self.repo / "checks"))
        self.git(self.repo, "add", "checks")
        patch = subprocess.run(
            ["git", "-C", str(self.repo), "diff", "--cached", "--binary"],
            capture_output=True,
            check=True,
        ).stdout
        self.git(self.repo, "rm", "-q", "--cached", "checks")
        os.unlink(self.repo / "checks")
        run = runner.run_mutation(
            self.repo,
            "HEAD",
            scenario="S1",
            digest="d" * 64,
            check="true",
            patch=patch,
            oracles={"probe.txt.oracle": b"probe\n"},
            place="checks/",
        )
        self.assertEqual(
            self.outside_files(), [], "the oracle was written through the patch's symlink"
        )
        self.assertNotEqual(run.receipt["result"], "PASS")
        self.assertIn(
            "checks", (run.receipt.get("detail") or "") + (run.receipt.get("reason") or "")
        )

    def test_control_an_ordinary_env_name_and_place_pass(self):
        """Holds today: plan check accepts an ordinary env name, and an oracle placed in a real directory runs."""
        self.write_plan(["MY_TOKEN"])
        result = self.plan_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.repo / "checks").mkdir()
        (self.repo / "checks" / "keep.txt").write_text("x\n", encoding="utf-8")
        self.commit("a real directory")
        run = self.evidence(check="test -f checks/probe.txt")
        self.assertEqual(run.receipt["result"], "PASS", run.log)
        self.assertEqual(self.outside_files(), [])


def plain_env(root, markers=None):
    """A terminal with no host markers: no hc, Herdr or agent-runtime variables, its own HOME and state."""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(root / "home"),
        "XDG_STATE_HOME": str(root / "xdg"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    for name in ("UV_CACHE_DIR", "TMPDIR", "SYSTEMROOT"):
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(markers or {})
    return env


class ApproveGateOutcome(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.plan = self.repo / "docs" / "plans" / "demo.plan.yaml"
        data = {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "deliver",
            "spec": "docs/specs/demo.md",
            "outcome": ["S1"],
            "scenarios": [
                {"id": "S1", "when": "a", "then": "b", "evidence": {"human": "confirm b"}}
            ],
            "tickets": [
                {
                    "id": "T1",
                    "delivers": "first",
                    "satisfies": ["S1"],
                    "paths": ["src/a/"],
                    "size": "small",
                    "difficulty": "simple",
                }
            ],
        }
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.bin = self.root / "resolve-bin"
        self.bin.mkdir()

    def runtime_answers(self, answer):
        """The `agent-runtime` on PATH: `resolve --json` prints `answer`, or fails when `answer` is None."""
        tool = self.bin / "agent-runtime"
        body = (
            "sys.stderr.write('agent-runtime: cannot reach the server\\n'); sys.exit(1)"
            if answer is None
            else "print(%r)" % json.dumps(answer)
        )
        tool.write_text("#!%s\nimport sys\n%s\n" % (sys.executable, body), encoding="utf-8")
        tool.chmod(0o755)

    def approve(self, markers=None):
        before = self.plan.read_text(encoding="utf-8")
        env = plain_env(self.root, markers)
        env["PATH"] = str(self.bin) + os.pathsep + env["PATH"]
        result = subprocess.run(
            [
                *DRIVER,
                "plan",
                "approve",
                str(self.plan),
                "--repo",
                str(self.repo),
                "--state-dir",
                str(self.state_dir),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        approved = "approval" in (yaml.safe_load(self.plan.read_text(encoding="utf-8")) or {})
        return result, approved, before

    def assert_refused(self, result, approved, before):
        self.assertNotEqual(result.returncode, 0, "a worker approved the plan")
        self.assertFalse(approved, "an approval was written")
        self.assertEqual(self.plan.read_text(encoding="utf-8"), before)
        self.assertIn("Lead", result.stderr, "the refusal does not name the way out")

    def test_a_caller_the_runtime_verifies_as_a_worker_by_ancestry_is_refused(self):
        """orchestrator-audit-fixes/S20: no host markers, but the runtime saw worker ancestry."""
        self.runtime_answers(
            {
                "runtime": "native",
                "role": "lead",
                "cli": "claude",
                "caller": {"kind": "worker", "verified": True},
            }
        )
        self.assert_refused(*self.approve())

    def test_an_unknown_caller_inside_an_hc_host_is_refused(self):
        """orchestrator-audit-fixes/S20"""
        self.runtime_answers(
            {
                "runtime": "hc",
                "role": "unknown",
                "cli": "claude",
                "caller": {"kind": "unknown", "verified": False},
            }
        )
        self.assert_refused(*self.approve({"HC_TERMINAL_ID": "term-worker-7"}))

    def test_a_failed_runtime_lookup_with_agent_markers_is_refused(self):
        """orchestrator-audit-fixes/S20: the lookup failing never overrides the markers."""
        self.runtime_answers(None)
        self.assert_refused(*self.approve({"HC_TERMINAL_ID": "term-worker-7"}))

    def test_control_a_plain_terminal_approves(self):
        """orchestrator-audit-fixes/S21: holds today."""
        self.runtime_answers(
            {
                "runtime": "native",
                "role": "lead",
                "cli": "claude",
                "caller": {"kind": "lead", "verified": True},
            }
        )
        result, approved, _ = self.approve()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(approved)

    def test_control_the_lead_verified_inside_hc_approves(self):
        """orchestrator-audit-fixes/S21: holds today."""
        self.runtime_answers(
            {
                "runtime": "hc",
                "role": "lead",
                "cli": "claude",
                "caller": {"kind": "lead", "verified": True},
            }
        )
        result, approved, _ = self.approve({"HC_TERMINAL_ID": "term-lead"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(approved)

    def test_control_a_worker_the_runtime_names_is_refused(self):
        """Holds today: the runtime's `role: worker` is refused at the CLI."""
        self.runtime_answers(
            {
                "runtime": "hc",
                "role": "worker",
                "parent": "term-lead",
                "cli": "claude",
                "caller": {"kind": "worker", "verified": True},
            }
        )
        self.assert_refused(*self.approve({"HC_TERMINAL_ID": "term-worker-7"}))


if __name__ == "__main__":
    unittest.main()
