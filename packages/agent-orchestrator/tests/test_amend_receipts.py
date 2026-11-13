"""`plan amend` re-runs only the baselines that changed and carries the others' receipts to the new digest."""

import json
import unittest
from unittest import mock

import pytest
from agent_orchestrator import plan as plan_module
from agent_orchestrator import runner, verify
from test_plan_amend import AmendFixture, contract

pytestmark = pytest.mark.integration


def two_checks():
    """S1 and S2 both carry a check evidence item; S2's check is the one the edits below change."""
    data = contract()
    data["scenarios"][1]["evidence"] = {"check": "grep -q ali status.txt", "baseline": "pass"}
    return data


class CarriedReceiptTests(AmendFixture):
    def ran(self):
        """The scenarios whose baseline the runner executes while `amend` runs."""
        seen = []
        real = runner.run_evidence

        def spy(*args, **kwargs):
            seen.append(kwargs["scenario"])
            return real(*args, **kwargs)

        with mock.patch.object(runner, "run_evidence", side_effect=spy):
            code, message = self.amend()
        self.assertEqual(code, 0, message)
        return seen

    def receipts(self):
        ledger, directory = plan_module.state_paths(self.repo, "demo", self.state_dir)
        return json.loads(ledger.read_text()), directory

    def latest(self, sid):
        data, directory = self.receipts()
        stem = verify.receipt_stems(data, sid, "baseline")[-1]
        return stem, json.loads((directory / (stem + ".json")).read_text())

    def test_S9_only_a_changed_item_runs_and_an_unchanged_one_is_carried_under_the_new_digest(self):
        self.write(two_checks())
        first = self.approve()
        edit = self.read()
        edit["scenarios"][1]["evidence"]["check"] = "grep -q aliv status.txt"
        self.write(edit)
        ran = self.ran()
        new = self.read()["approval"]["digest"]
        self.assertEqual(ran, ["S2"])
        data, directory = self.receipts()
        tree = self.git(self.repo, "rev-parse", first["base"] + "^{tree}")
        stem, carried = self.latest("S1")
        self.assertEqual(stem, "S1.baseline.2")
        self.assertEqual(carried["carried_from"], first["digest"])
        self.assertEqual(carried["digest"], new)
        # the receipt ledger's tamper check accepts it, and so would an acceptance read of the new digest
        self.assertEqual(verify.read_receipt(data, stem, directory, new, tree)[1], None)
        _, rerun = self.latest("S2")
        self.assertNotIn("carried_from", rerun)
        self.assertEqual(rerun["digest"], new)
        # the first approval's receipts stay as they were
        self.assertEqual(
            json.loads((directory / "S1.baseline.1.json").read_text())["digest"], first["digest"]
        )

    def test_S9_a_carried_receipt_keeps_the_result_of_the_run_it_was_issued_for(self):
        self.write(two_checks())
        self.approve()
        _, before = self.latest("S1")
        edit = self.read()
        edit["scenarios"][1]["evidence"]["timeout"] = 90
        self.write(edit)
        self.ran()
        _, after = self.latest("S1")
        for key in ("result", "started_at", "commit", "tree", "check", "oracles", "log_sha256"):
            self.assertEqual(after[key], before[key], key)

    def changed_item_runs_again(self, field, value):
        env = mock.patch.dict("os.environ", {"DEMO_FLAG": "1"})  # a check's env name must be set
        env.start()
        self.addCleanup(env.stop)
        self.write(two_checks())
        self.approve()
        edit = self.read()
        edit["scenarios"][0]["evidence"][field] = value
        self.write(edit)
        self.assertEqual(self.ran(), ["S1"])
        self.assertNotIn("carried_from", self.latest("S1")[1])
        self.assertEqual(
            self.latest("S2")[1]["carried_from"], self.read()["amendments"][-1]["prev_digest"]
        )

    def test_S9_a_changed_timeout_runs_again_and_the_other_item_is_carried(self):
        self.changed_item_runs_again("timeout", 90)

    def test_S9_a_changed_env_runs_again_and_the_other_item_is_carried(self):
        self.changed_item_runs_again("env", ["DEMO_FLAG"])

    def test_S9_a_changed_check_runs_again_and_the_other_item_is_carried(self):
        self.changed_item_runs_again("check", "grep -q aliv status.txt")

    def test_S9_a_tampered_receipt_is_not_carried_and_the_item_runs_again(self):
        self.write(two_checks())
        self.approve()
        _, directory = self.receipts()
        (directory / "S1.baseline.1.json").write_text("{}\n")
        edit = self.read()
        edit["scenarios"][1]["evidence"]["check"] = "grep -q aliv status.txt"
        self.write(edit)
        self.assertEqual(sorted(self.ran()), ["S1", "S2"])

    def test_S9_a_carried_receipt_is_judged_against_the_new_baseline(self):
        self.write(two_checks())
        self.approve()
        edit = self.read()
        edit["scenarios"][0]["evidence"]["baseline"] = "fail"  # the carried PASS is not a failure
        self.write(edit)
        before = self.plan.read_bytes()
        code, message = self.amend()
        self.assertEqual(code, 2, message)
        self.assertIn("S1", message)
        self.assertEqual(self.plan.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
