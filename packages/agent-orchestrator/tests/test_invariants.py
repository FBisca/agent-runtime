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
from fixtures import report_wait, resume_id
from test_gates import BLOCKING, QUESTION, GateFixture

pytestmark = pytest.mark.integration

SHARED = "review-file-shared"
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
        states = {
            SHARED: (
                ledger(
                    t1=task(gate="reviewing", review={"path": "/run/t1/review.json"}),
                    t1r1=task(gate="reviewing", review={"path": "/run/t1/review.json"}),
                ),
                "t1r1",  # the attempt that took the file second
            ),
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

    @pytest.mark.spec("Ledger invariants")
    def test_S2_control_a_coherent_ledger_passes(self):
        """A ledger the driver itself writes passes: one review file per open attempt (a superseded attempt keeps its
        own), a parked unit, every gate one the driver knows."""
        data = ledger(
            t1=task(status="PARTIAL", gate="reviewing", review={"path": "/run/t1/review.json"}),
            t1r1=task(status="RELEASED", gate="superseded", review={"path": "/run/t1/review.json"}),
            t2=task(status="COMPLETE", gate="done"),
            t3=task(status="BLOCKED", gate="blocked"),
        )
        invariants.check(data)
        code, out, err = self.check(data)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out), {"ok": True, "tasks": 4})


class ReviewWriteOnceTests(GateFixture, unittest.TestCase):
    """S3: a unit answered after its review writes the next review file, so the blockers it named stay nameable."""

    def answered_report(self, task_id="t1"):
        """A wait step: the answered worker writes the next write-once report and says complete again."""
        name = "demo-t1"
        step = report_wait(name, "complete", id=resume_id(name, 1))
        step["write"] = [[str(self.run_dir / task_id / "report.2.json"), self.report(task_id)]]
        return step

    def rereview(self, text, name="review.2.json", nth=1):
        """The re-review's report, under the assignment the resumed reviewer pane holds, its review written to the
        file the driver allocated for it."""
        return report_wait(
            "demo-t1-review",
            "complete",
            id=resume_id("demo-t1-review", nth),
            write=[[str(self.run_dir / "t1" / name), text]],
        )

    def answers_report(self, responses, nth=2):
        """A wait step: the answerer of the review's questions writes its responses and says complete."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", nth))
        step["write"] = [
            [str(self.run_dir / "t1a1" / "report.json"), self.report("t1a1", responses=responses)]
        ]
        return step

    def read_review(self, name):
        return json.loads((self.run_dir / "t1" / name).read_text(encoding="utf-8"))

    def open_ids(self, unit="t1"):
        """The ids still open for the unit, as `record_blockers` left them."""
        path = self.run_dir / unit / "open-findings.json"
        return (
            [item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["items"]]
            if path.exists()
            else []
        )

    @pytest.mark.spec("A reworked unit writes a new review")
    def test_S3_a_rework_after_an_answer_writes_the_next_review_file(self):
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(
                    write=[[str(self.run_dir / "t1" / "review.json"), self.blocking()]]
                ),
                self.decide_step(
                    "t1", "answer", answer="the guard is per target", reason="rule on it"
                ),
                self.answered_report(),
                self.rereview(self.review2()),
            ]
        )
        self.run_driver("--max-fix-rounds", "0")
        self.assertEqual(
            Path(self.task("t1")["review"]["path"]).name,
            "review.2.json",
            "the rework reused the first review file",
        )
        self.assertEqual(
            [item["id"] for item in self.read_review("review.json")["items"]],
            ["B1"],
            "the earlier review lost the blocker it named",
        )
        # The re-review was read (it carries no resolution, so the blocker it left open holds the unit): the driver
        # asks the reviewer to close it rather than losing it with the file.
        asked = [
            c["args"]["task"] for c in self.calls("resume") if c["args"]["name"] == "demo-t1-review"
        ]
        self.assertTrue(asked, "the re-review was never read")
        self.assertIn(
            "resolutions", asked[-1], "the driver forgot the blocker the earlier review raised"
        )

    @pytest.mark.spec("A reworked unit writes a new review")
    def test_S3_a_rework_resolution_closes_the_blocker_the_first_review_raised(self):
        """S3's promise: the earlier blockers stay nameable, so the next review can close one with a resolution."""
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(
                    write=[[str(self.run_dir / "t1" / "review.json"), self.blocking()]]
                ),
                self.decide_step(
                    "t1", "answer", answer="the guard is per target", reason="rule on it"
                ),
                self.answered_report(),
                self.rereview(self.review2(**self.closure(BLOCKING))),
            ]
        )
        self.run_driver("--max-fix-rounds", "0")
        self.assertEqual(
            [event["reason"] for event in self.run_events("payload-warning")],
            [],
            "the driver refused the resolution that closes the earlier blocker",
        )
        self.assertEqual(self.open_ids(), [], "B1 is still open after a supported resolution")
        self.assertEqual(self.task("t1")["gate"], "done")

    def blocking(self):
        return self.review2(verdict="block", items=[BLOCKING])

    @pytest.mark.spec("A reworked unit writes a new review")
    def test_S3_the_delta_rereview_of_a_disputed_answer_still_names_the_earlier_blocker(self):
        """S3 one pass later: the delta re-review a disputed answer starts is the same rework, so the reviews the
        unit already had stay nameable and its resolution still closes the blocker they raised."""
        disputed = {
            "id": "Q1",
            "status": "disputed",
            "commit": "abc1234",
            "note": "the contract says no",
            "changes_code": True,
        }
        self.waits(
            [
                self.worker_done(tests=True),
                self.reviewer_done(
                    write=[[str(self.run_dir / "t1" / "review.json"), self.blocking()]]
                ),
                self.decide_step(
                    "t1", "answer", answer="the guard is per target", reason="rule on it"
                ),
                self.answered_report(),
                self.rereview(
                    self.review2(items=[QUESTION], **self.closure(BLOCKING)),
                    "review.2.json",
                    1,
                ),
                self.answers_report([disputed]),
                self.rereview(self.review2(**self.closure(BLOCKING)), "review.3.json", 2),
            ]
        )
        self.run_driver("--max-fix-rounds", "0")
        self.assertEqual(
            [event["reason"] for event in self.run_events("payload-warning")],
            [],
            "the delta re-review refused the resolution that closes the earlier blocker",
        )
        self.assertEqual(self.open_ids(), [], "B1 is still open after a supported resolution")
        self.assertEqual(
            [Path(path).name for path in self.task("t1")["review"]["earlier"]],
            ["review.2.json", "review.json"],
            "the delta re-review lost the reviews the unit already had",
        )
        self.assertEqual(
            [item["id"] for item in self.read_review("review.json")["items"]],
            ["B1"],
            "the earlier review lost the blocker it named",
        )


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
