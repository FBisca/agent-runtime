"""A run dir written by the previous driver (no events.jsonl) keeps working."""

import json
import subprocess
import unittest

import pytest
from fixtures import DRIVER
from test_gates import MAJOR, GateFixture

pytestmark = pytest.mark.integration


class CompatTests(GateFixture, unittest.TestCase):
    def v1_run_dir(self):
        """Leave the unit at gate `reviewing`, then strip what only the new driver writes."""
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(self.ledger()["tasks"]["t1"]["gate"], "reviewing")
        (self.run_dir / "events.jsonl").unlink()
        data = self.ledger()
        data.pop("agents", None)
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")

    def test_S34_in_flight_units_continue_on_a_run_dir_without_events(self):
        """orchestrate-lean-loop/S34"""
        self.v1_run_dir()
        review = self.review(major=[MAJOR])
        self.waits([self.reviewer_done(review)])
        self.run_driver()
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual([i["id"] for i in findings["items"]], ["R1"])
        kinds = [(e["finding_kind"], e["id"]) for e in self.run_events("finding")]
        self.assertEqual(kinds, [("blocking", "R1")])
        self.assertEqual(self.run_events()[0]["kind"], "import")

    def test_S34_metrics_of_a_run_dir_without_events_come_from_the_ledger(self):
        """orchestrate-lean-loop/S34"""
        self.v1_run_dir()
        result = subprocess.run(
            [*DRIVER, "metrics", str(self.plan), "--state-dir", str(self.state_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        ticket = json.loads(result.stdout)["tickets"]["t1"]
        self.assertEqual((ticket["review_rounds"], ticket["route"]["model"]), (0, "haiku"))


if __name__ == "__main__":
    unittest.main()
