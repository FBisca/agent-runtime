"""Acceptance for the driver-invariants plan: a worker's payload never ends the drive, the ledger refuses the
states that stalled a run, a rework after a review writes a new review, and a check whose run still runs is pending.

It drives `run` and `ledger check` through the gate fixture (fake runtime, fake gh), the seams the
engine's own tests use.

The four cases are the plan's outcome:

  - S1: a worker report whose check outcome is prose is refused back as a request naming it; the drive books no
    error and reaches `finish` (it exited 1 on this report in the 00c run)
  - S2: `ledger check` refuses two open attempts naming one review file, and a gate
    the driver does not know, each by name; a coherent ledger passes
  - S3: a unit answered after its review writes the next review file, and the earlier review keeps its blocking id
  - S4: a check whose run is still in progress stays pending and its log placeholder never becomes evidence; the
    request that finally raises quotes the failing step's own log

Each case has a control that holds today and must keep holding: a well-formed report is gated as before, a
finished run leaves a coherent ledger, a first review is written once, and a readable failed log escalates at once.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

# A fresh worktree's venv holds no workspace member: the package under test is importable from its source, so the
# fixtures can import it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixtures import DRIVER, assignment, report_wait, resume_id, wait_result  # noqa: E402
from test_gates import GateFixture  # noqa: E402

pytestmark = pytest.mark.integration

OPEN = {
    "state": "OPEN",
    "isDraft": False,
    "mergeStateStatus": "CLEAN",
    "mergeable": "MERGEABLE",
}
REAL_LOG = (
    "test (ubuntu-latest, 3.13)\tsuite budget\tsuite budget: 6 module(s) grew past their share"
)
PLACEHOLDER = "run 43710622205 is still in progress; logs will be available when it is complete"


def task(**over):
    """One ledger task with the fields the invariants read."""
    return dict(
        {
            "packet": {"unit": "t1", "task_id": "t1"},
            "status": "ACTIVE",
            "gate": None,
            "report_path": "/run/t1/report.json",
        },
        **over,
    )


class InvariantFixture(GateFixture):
    """One ticket, its run directory, and the ledger a run leaves."""

    def setUp(self):
        super().setUp()
        self.set_plan(("T1",))

    def malformed(self, task_id="t1"):
        """A complete report whose check outcome is prose: what killed the 00c drive."""
        data = json.loads(self.report(task_id))
        # The report the 00c drive died on: it fails a COMPLETE rule (remaining work), so the refusal path is
        # `fail_gate`, and its check outcome is prose.
        data["checks"] = [{"command": "uv run pytest", "outcome": "green, 394 passed"}]
        data["remaining"] = ["the other ticket's work"]
        return json.dumps(data)

    def second_report(self, task_id="t1", text=None):
        """A wait step: the answered worker writes report.2.json and says complete again."""
        name = "demo-t1"
        step = report_wait(name, "complete", id=resume_id(name, 1))
        step["write"] = [
            [str(self.run_dir / task_id / "report.2.json"), text or self.report(task_id)]
        ]
        return step

    def write_ledger(self, data, name="ledger.json"):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def ledger_check(self, path):
        """`agent-orchestrator ledger check <ledger.json>`, as the Lead reads a captured ledger."""
        return subprocess.run(
            [*DRIVER, "ledger", "check", str(path)], capture_output=True, text=True
        )

    def snapshot(self, name):
        """A wait step that copies the ledger aside, so the path the drive allocated is read mid-run."""
        out = self.root / "snap"
        out.mkdir(exist_ok=True)
        return wait_result(
            assignments=[assignment("demo-t1")],
            run=[
                [
                    "sh",
                    "-c",
                    "cp '%s' '%s' 2>/dev/null; true"
                    % (self.state_dir / "demo.json", out / ("%s.json" % name)),
                ]
            ],
        )

    def review_path(self, name):
        data = json.loads((self.root / "snap" / ("%s.json" % name)).read_text(encoding="utf-8"))
        return (data["tasks"].get("t1") or {}).get("review", {}).get("path")


class S1ControlTests(InvariantFixture, unittest.TestCase):
    @pytest.mark.spec("A report is handed in through the runtime")
    def test_S1_control_a_well_formed_report_is_gated_as_before(self):
        """A report that meets the COMPLETE rules still converges and reaches its review."""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        result = self.run_driver()
        self.assertEqual(json.loads(result.stdout)["errors"], [])
        self.assertEqual(self.run_events("error"), [])
        self.assertTrue(self.run_events("finish"))


class S2LedgerTests(InvariantFixture, unittest.TestCase):
    @pytest.mark.spec("Ledger invariants")
    def test_S2_the_ledger_refuses_the_states_that_stalled_the_run(self):
        """An unknown gate is refused by name."""
        for name, tasks, invariant in (("unknown", {"t1": task(gate="reviewer")}, "unknown-gate"),):
            with self.subTest(invariant=invariant):
                data = (
                    tasks
                    if "version" in tasks
                    else {"version": 1, "kind": "agent-orchestrator/ledger", "tasks": tasks}
                )
                result = self.ledger_check(self.write_ledger(data, "%s.json" % name))
                self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
                self.assertIn(invariant, result.stderr)
                self.assertIn("way_out", result.stderr)

    @pytest.mark.spec("Ledger invariants")
    def test_S2_control_a_finished_run_leaves_a_coherent_ledger(self):
        """What the driver itself writes is coherent: `ledger check` passes it."""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        result = self.ledger_check(self.state_dir / "demo.json")
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class S4CiPendingTests(InvariantFixture, unittest.TestCase):
    @pytest.mark.spec("A pending CI log is not a finding")
    def test_S4_a_failing_check_whose_run_still_runs_stays_pending_and_quotes_the_real_log(self):
        """The placeholder is not evidence: the drive polls until the failing step's log is readable."""
        (self.home / "run-view.json").write_text(
            json.dumps([[0, PLACEHOLDER, ""], [0, PLACEHOLDER, ""], [0, REAL_LOG, ""]]),
            encoding="utf-8",
        )
        self.checks(["fail"], ready=True)
        self.prs([OPEN] * 40)
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), wait_result(), self.skipped()]
        )
        self.run_driver("--max-fix-rounds", "0")
        asked = self.request()
        self.assertIn(
            "suite budget", asked["reason"], "the request does not quote the failing step's log"
        )
        self.assertNotIn(
            "still in progress", asked["reason"], "the log placeholder became evidence"
        )


class S4ControlTests(InvariantFixture, unittest.TestCase):
    @pytest.mark.spec("A pending CI log is not a finding")
    def test_S4_control_a_readable_failed_log_escalates_at_once(self):
        """A failure whose log is readable is asked immediately, as it is today."""
        self.checks(["fail"], ready=True)
        self.prs([OPEN] * 40)
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(), wait_result(), self.skipped()]
        )
        self.run_driver("--max-fix-rounds", "0")
        asked = self.request()
        self.assertEqual(asked["event"], "gate")
        self.assertIn("FAILED STEP LOG TAIL", asked["reason"])


if __name__ == "__main__":
    unittest.main()
