"""The baseline regressions: an end-to-end dispatch -> edit -> verification -> completion run, and the budget that
stops a run that stalls.

S0: WHEN a known orchestration failure is reproduced on an isolated fixture, THEN the test fails before the repair
and passes afterwards under the same fixed acceptance criteria; only a deterministic validator marks the source
revision verified. The failure reproduced here is the report timing one: the worker writes its report and its turn
ends (`runtime state idle`) before the runtime delivers the completion message. The driver must not park the ticket
as `ended without a report` and ask a human for a decision about a report it already has.

S3: WHEN a test run stalls or exceeds a declared budget, THEN the benchmark terminates it and records a failure
instead of running indefinitely.

Everything runs on the offline fixture: the fake runtime and the fake `gh` answer in process, and the driver runs
under `bench.run_budgeted`, so a stalled run is killed with its whole process group and leaves a record behind.
"""

import json
import subprocess
import unittest
from pathlib import Path

import bench
import pytest
from fixtures import DRIVER
from test_gates import GateFixture

ROOT = (
    Path(__file__).resolve().parents[3]
)  # the repository under test: the source revision the run is pinned to
PR = "https://github.com/o/r/pull/7"

# Every test here runs the driver in a subprocess: these are integration tests.
pytestmark = pytest.mark.integration


class BaselineTests(GateFixture, unittest.TestCase):
    """One ticket, scripted worker and reviewer, the driver run under a budget."""

    def revision(self):
        """The source revision the run is pinned to: the commit the driver under test came from."""
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True
        )
        return result.stdout.strip()

    def commit(self):
        """The wait-step commands that commit and push the worker's edit in its checkout."""
        git = [
            "git",
            "-C",
            str(self.worktree),
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
        ]
        return [
            git + ["add", "."],
            git + ["commit", "-q", "-m", "work"],
            ["git", "-C", str(self.worktree), "push", "-q", "origin", "HEAD:feat/demo-t1"],
        ]

    def worker_turn_ended(self, report=True, stall=None, complete=False, commit=True):
        """A wait step: the worker edited its checkout and wrote its report, and its turn ended (`runtime state idle`)
        without the runtime delivering the completion message yet.

        With `complete` the runtime delivers the message in the same step: the worker that reports and says so in
        one go is the flow that already works.
        """
        step = {"agents": ["demo-t1"], "runtime_state": "idle", "write": [], "run": []}
        if complete:
            step.pop("agents")
            step["complete"] = ["demo-t1"]
        if report:
            step["write"].append([str(self.run_dir / "t1" / "report.json"), self.report("t1")])
        step["write"].append(
            [str(self.worktree / "src" / "a" / "x.py"), "def x():\n    return 1\n"]
        )
        step["write"].append(
            [str(self.worktree / "src" / "a" / "test_a.py"), 'def test_a():\n    """S1"""\n']
        )
        step["run"] = self.commit() if commit else []
        if stall:
            step["run"].append(stall)
        return step

    def worker_complete(self):
        """A wait step: the runtime delivers the worker's completion message. The reviewer is already running, so
        the runtime still holds it open: the driver must not read the run as over."""
        return {"complete": ["demo-t1"], "agents": ["demo-t1-review"]}

    def reviewer_complete(self):
        """A wait step: the reviewer wrote its review and the runtime delivered the completion message."""
        return {
            "complete": ["demo-t1-review"],
            "runtime_state": "idle",
            "write": [[str(self.run_dir / "t1" / "review.json"), self.review()]],
        }

    def drive(self, budget=25.0, label="", records=None):
        """The driver run under a budget: a run that overruns it is terminated and recorded as a failure."""
        self.alias_state(self.plan)
        argv = [
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
            "0.05",
        ]
        return bench.run_budgeted(
            argv,
            env=self.env(),
            cwd=str(self.root),
            budget_seconds=budget,
            label=label,
            revision=self.revision(),
            records=records,
        )

    def request(self, unit="t1"):
        """The unit's open request, or None: the driver asked nobody anything."""
        path = self.run_dir / unit / "request.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def task(self, task_id="t1"):
        return self.ledger()["tasks"].get(task_id) or {}

    def test_S0_a_report_that_landed_before_the_turn_ended_reaches_a_verified_terminal_state(self):
        """orchestrator-engine/R2: P3.T1/S0 — the report is on disk and the runtime says the turn ended; the ticket still gets verified."""
        self.prs(["OPEN", "MERGED"])
        self.waits([self.worker_turn_ended(), self.worker_complete(), self.reviewer_complete()])
        record = self.drive(budget=15.0, label="S0-report-timing")
        self.assertIsNone(self.request(), "the driver asked a human about a report it already had")
        self.assertNotIn(
            "ended without a report",
            record["stderr"],
            "the driver parked the ticket instead of reading the report",
        )
        self.assertEqual(
            record["outcome"],
            "pass",
            "the run did not reach a verified terminal state: %s\n%s"
            % (record["reason"], record["stderr"]),
        )
        task = self.task()
        self.assertEqual(task.get("status"), "COMPLETE", task)
        self.assertEqual(task.get("gate"), "done", task)
        self.assertIn("COMPLETE ", self.ticket_status("T1"))

    def test_S0_a_restart_after_the_report_landed_still_finishes_the_ticket(self):
        """orchestrator-engine/R2: P3.T1/S0 — the driver died with the report on disk; the restart finishes the ticket, nobody is asked."""
        self.prs(["OPEN", "MERGED"])
        self.waits([self.worker_turn_ended(stall=["sleep", "60"])])
        first = self.drive(budget=10.0, label="S0-restart-killed")
        self.assertEqual(
            first["outcome"], "failure", "the stalled run should have overrun its budget"
        )
        self.assertIn(
            "budget", first["reason"], "the run was not terminated when its budget ran out"
        )
        self.waits(
            [self.worker_turn_ended(commit=False), self.worker_complete(), self.reviewer_complete()]
        )
        second = self.drive(budget=15.0, label="S0-restart")
        self.assertEqual(
            second["outcome"],
            "pass",
            "the restart did not reach a verified terminal state: %s\n%s"
            % (second["reason"], second["stderr"]),
        )
        self.assertIsNone(self.request(), "the driver asked a human about a report it already had")
        self.assertEqual(self.task().get("status"), "COMPLETE", self.task())

    def test_S3_a_run_that_stalls_is_terminated_and_recorded_as_a_failure(self):
        """orchestrator-engine/R2, orchestrator-engine/R7: P3.T1/S3 — the run exceeds its declared budget; the
        benchmark kills it, waits for the tree it killed, and records the failure."""
        records = self.root / "records.jsonl"
        self.waits([self.worker_turn_ended(stall=["sleep", "120"])])
        record = self.drive(budget=8.0, label="S3-stall", records=str(records))
        self.assertEqual(record["outcome"], "failure", record)
        self.assertIn("budget", record["reason"])
        self.assertTrue(record.get("tree_gone"), "the run's whole tree is still alive")
        self.assertLess(
            record["elapsed_seconds"], 30.0, "the run was not terminated when its budget ran out"
        )
        self.assertEqual([r["label"] for r in bench.read_records(str(records))], ["S3-stall"])
        self.assertEqual(bench.read_records(str(records))[0]["outcome"], "failure")

    def test_S3_a_run_that_finishes_is_recorded_as_a_pass_with_its_revision(self):
        """orchestrator-engine/R2: P3.T1/S3 — the record says which revision ran, so only that revision can be called verified."""
        records = self.root / "records.jsonl"
        self.prs(["OPEN", "MERGED"])
        self.waits([self.worker_turn_ended(complete=True), self.reviewer_complete()])
        record = self.drive(budget=25.0, label="S3-pass", records=str(records))
        self.assertEqual(record["outcome"], "pass", record["reason"])
        self.assertEqual(record["revision"], self.revision())
        self.assertEqual(bench.read_records(str(records))[0]["outcome"], "pass")
        self.assertIsNotNone(
            bench.verify(str(records), self.revision()), "a passing run verifies its revision"
        )

    def test_S0_only_the_validator_marks_a_source_revision_verified(self):
        """orchestrator-engine/R2: P3.T1/S0 — a revision is verified only when every run of it passed."""
        records = self.root / "records.jsonl"
        manifest_path = self.root / "verified.json"
        passing = {
            "label": "S3-pass",
            "revision": "abc1234",
            "outcome": "pass",
            "argv": ["true"],
            "elapsed_seconds": 0.1,
        }
        failing = {
            "label": "S3-stall",
            "revision": "abc1234",
            "outcome": "failure",
            "argv": ["false"],
            "elapsed_seconds": 8.0,
        }
        with open(records, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(passing) + "\n")
        self.assertIsNotNone(bench.verify(str(records), "abc1234"))
        with open(records, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(failing) + "\n")
        self.assertIsNone(
            bench.verify(str(records), "abc1234"), "a failed run leaves the revision unverified"
        )
        self.assertFalse(manifest_path.exists())
        with open(records, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(passing) + "\n")
        manifest = bench.verify(str(records), "abc1234", out_path=str(manifest_path)) or {}
        self.assertEqual((manifest.get("revision"), manifest.get("verified")), ("abc1234", True))
        self.assertEqual(
            json.loads(manifest_path.read_text(encoding="utf-8"))["revision"], "abc1234"
        )


if __name__ == "__main__":
    unittest.main()
