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
from test_gates import BLOCKING, GateFixture  # noqa: E402

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


class S1PayloadTests(InvariantFixture, unittest.TestCase):
    def test_S1_a_malformed_report_is_refused_and_the_drive_reaches_finish(self):
        """A prose check outcome is refused to the sender: the drive keeps its errors empty and finishes."""
        # The refusal goes back to the sender, which writes a report the driver accepts; the unit then converges,
        # is reviewed and merges. At base the drive exits 1 before any of that.
        self.waits(
            [
                self.worker_done(tests=True, report=self.malformed()),
                self.second_report(text=self.report("t1")),
                self.reviewer_done(),
            ]
        )
        result = self.run_driver()
        answer = json.loads(result.stdout)
        self.assertEqual(answer["errors"], [], "the drive booked a worker payload as its own error")
        self.assertEqual(
            self.run_events("error"), [], "the drive logged an error for a worker payload"
        )
        self.assertTrue(self.run_events("finish"), "the drive did not reach finish")
        resumed = self.calls("resume")
        self.assertTrue(resumed, "the sender was never told its report was refused")
        self.assertIn("invalid check outcome", resumed[-1]["args"]["task"])
        self.assertIn("report", resumed[-1]["args"]["task"].lower())


class S1ControlTests(InvariantFixture, unittest.TestCase):
    def test_S1_control_a_well_formed_report_is_gated_as_before(self):
        """A report that meets the COMPLETE rules still converges and reaches its review."""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        result = self.run_driver()
        self.assertEqual(json.loads(result.stdout)["errors"], [])
        self.assertEqual(self.run_events("error"), [])
        self.assertTrue(self.run_events("finish"))


class S2LedgerTests(InvariantFixture, unittest.TestCase):
    def test_S2_the_ledger_refuses_the_states_that_stalled_the_run(self):
        """Two attempts naming one review file, and an unknown gate, are each refused by name."""
        shared = {
            "version": 1,
            "kind": "agent-orchestrator/ledger",
            "tasks": {
                "t1": task(gate="reviewing", review={"path": "/run/t1/review.json"}),
                "t1r1": task(gate="reviewing", review={"path": "/run/t1/review.json"}),
            },
        }
        for name, tasks, invariant in (
            ("shared", shared, "review-file-shared"),
            ("unknown", {"t1": task(gate="reviewer")}, "unknown-gate"),
        ):
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

    def test_S2_control_a_finished_run_leaves_a_coherent_ledger(self):
        """What the driver itself writes is coherent: one file per open review."""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        tasks = self.ledger()["tasks"]
        self.assertTrue(tasks, "the run wrote no task")
        paths = [t["review"]["path"] for t in tasks.values() if t.get("review", {}).get("path")]
        self.assertEqual(len(paths), len(set(paths)), "two attempts name one review file")


class S3ReviewWriteOnceTests(InvariantFixture, unittest.TestCase):
    """The rework that overwrote `t1/review.json` in the 00c run, driven through the same `answer`."""

    def setUp(self):
        super().setUp()
        self.waits(
            [
                self.worker_done(tests=True),
                self.snapshot("first"),
                self.reviewer_done(
                    write=[[str(self.run_dir / "t1" / "review.json"), self.blocking()]]
                ),
                self.decide_step(
                    "t1", "answer", answer="the guard is per target", reason="rule on it"
                ),
                self.second_report(),
                self.snapshot("second"),
                self.reviewer_done(
                    write=[[str(self.run_dir / "t1" / "review.2.json"), self.review2()]]
                ),
                self.skipped(),
            ]
        )
        self.prs(["MERGED"])

    def blocking(self):
        return json.dumps(
            {
                "contract": 2,
                "verdict": "block",
                "commit": "@HEAD",
                "items": [dict(BLOCKING, id="U1")],
                "checks_run": [],
            }
        )

    def test_S3_a_rework_writes_the_next_review_and_the_earlier_review_keeps_its_id(self):
        """After an answer the drive allocates the next review file; the first review and U1 stay readable."""
        self.run_driver("--max-fix-rounds", "0")
        first, second = self.review_path("first"), self.review_path("second")
        self.assertIsNotNone(first, "the first review was never allocated")
        self.assertNotEqual(first, second, "the rework reused the first review file")
        self.assertTrue(Path(first).exists(), "the first review is gone from disk")
        review = json.loads((self.run_dir / "t1" / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["items"][0]["id"], "U1", "the first review lost its blocker")


class S4CiPendingTests(InvariantFixture, unittest.TestCase):
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
