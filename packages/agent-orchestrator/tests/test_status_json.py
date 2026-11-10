"""The ledger owns a YAML plan's status: the plan file is never written, and `status --json` reads the ledger."""

import json
import sys
import unittest

import pytest
from fixtures import (
    DRIVER,
    assignment,
    load_script,
    report_wait,
    resume_id,
    spawn_id,
    wait_result,
)
from test_gates import GateFixture

pytestmark = pytest.mark.integration

PR = "https://github.com/o/r/pull/7"


class StatusFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.plan = self.approved_yaml_plan(("T1",))
        self.key = load_script().repo_key(self.repo)

    def status_argv(self):
        return [
            *DRIVER,
            "status",
            str(self.plan),
            "--json",
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        ]

    def status(self):
        """`status <plan> --json` as the Lead runs it."""
        result = self.cli(*self.status_argv()[len(DRIVER) :])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def snapshot(self, name, then=(), working=()):
        """A wait step during which the Lead runs `status --json`; its answer lands in <root>/<name>.json."""
        out = self.root / (name + ".json")
        program = (
            "import subprocess,sys; out=open(sys.argv[1],'w'); "
            "sys.exit(subprocess.run(sys.argv[2:], stdout=out).returncode)"
        )
        return wait_result(
            [assignment(w, "working", id=spawn_id(w)) for w in working],
            run=[[sys.executable, "-c", program, str(out), *self.status_argv()], *then],
        )

    def snapped(self, name):
        return json.loads((self.root / (name + ".json")).read_text(encoding="utf-8"))

    def answered_report(self):
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1", 1))
        step["write"] = [
            [str(self.run_dir / "t1" / "report.json"), self.report("t1")],
            [str(self.worktree / "src" / "a" / "test_a.py"), 'def test_a():\n    """S1"""\n'],
        ]
        return step


class StatusJsonTests(StatusFixture, unittest.TestCase):
    def test_S7_status_json_reads_the_ledger_and_plan_yaml_never_changes(self):
        """plan-contract/S7: dispatch, a pause, a decide answer and a merge leave plan.yaml byte-identical"""
        before = self.plan.read_bytes()
        idle = self.status()
        self.assertEqual(idle["run_dir"], str(self.state_dir / self.key / "demo"))
        self.assertEqual(idle["state"], "APPROVED")
        self.assertEqual([h["state"] for h in idle["history"]], ["APPROVED"])
        self.assertEqual(
            idle["tickets"], [{"id": "T1", "state": "TO-DO", "reason": None, "pr": None}]
        )
        self.assertFalse(
            (self.state_dir / self.key / "demo.json").exists()
        )  # reading creates nothing

        self.waits(
            [
                report_wait("demo-t1", "blocked", "cannot reach the database"),
                self.snapshot(
                    "paused",
                    then=[self.decide_argv("t1", "answer", "the port is known", "use port 5432")],
                ),
                self.answered_report(),
                self.snapshot("reviewing", working=["demo-t1-review"]),
                self.reviewer_done(),
            ]
        )
        self.run_driver("--repo", str(self.repo))

        paused = self.snapped("paused")
        self.assertEqual(paused["state"], "RUNNING")
        self.assertEqual([h["state"] for h in paused["history"]], ["APPROVED", "RUNNING"])
        (ticket,) = paused["tickets"]
        self.assertEqual(ticket["state"], "IN PROGRESS")
        self.assertTrue(ticket["reason"].startswith("decision pending: "), ticket)
        self.assertIn("cannot reach the database", ticket["reason"])
        reviewing = self.snapped("reviewing")["tickets"][0]
        self.assertEqual((reviewing["state"], reviewing["reason"]), ("IN PROGRESS", None))

        done = self.status()
        self.assertEqual(done["run_dir"], idle["run_dir"])
        # The merge delivered the plan; its only evidence is a human item, so it waits for a person (VERIFYING).
        self.assertEqual(done["state"], "VERIFYING")
        self.assertEqual(done["history"][:2], paused["history"])
        (ticket,) = done["tickets"]
        self.assertEqual((ticket["state"], ticket["pr"]), ("COMPLETE", PR))
        self.assertTrue(ticket["reason"].startswith("merged " + PR), ticket)
        self.assertEqual(self.plan.read_bytes(), before)

    def test_S7_a_ticket_the_driver_spawned_is_in_progress_in_the_ledger_not_the_plan(self):
        """plan-contract/S7: the dispatch Status goes to the ledger like every other Status"""
        before = self.plan.read_bytes()
        self.waits([wait_result([assignment("demo-t1", "working")], reason="timeout")])
        self.run_driver("--repo", str(self.repo))
        self.assertEqual(self.ledger()["ticket_status"]["T1"], "IN PROGRESS")
        self.assertEqual(self.plan.read_bytes(), before)
