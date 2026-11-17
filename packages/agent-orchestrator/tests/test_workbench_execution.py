"""The workbench's attempts and outcome read from ledgers the real driver wrote (fake runtime and gh): each attempt
maps to its assignment and address, the plan's outcome is what the integration run says, and `verify` and the view
never disagree."""

import json
import unittest

import pytest
from fixtures import spawn_id
from test_acceptance import FEATURE_DONE, AcceptanceFixture

pytestmark = pytest.mark.integration

BODY = "PRIVATE-REPORT-BODY-9c1e"


class WorkbenchExecutionTests(AcceptanceFixture, unittest.TestCase):
    def report(self, task_id, **over):
        over.setdefault("summary", BODY)
        return super().report(task_id, **over)

    def view(self):
        result = self.cli(
            "workbench", "plan", str(self.plan), "--state-dir", str(self.state_dir), "--json"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(BODY, result.stdout)
        return json.loads(result.stdout)

    def t1(self, body):
        (found,) = [t for t in body["tickets"] if t["id"] == "T1"]
        return found

    def hand_merged_failure(self):
        """The ticket's gate failed (its covers names a test that does not exist), so a fix round was open when a
        person merged it by hand; what the merge landed fails the repo's whole suite."""

        def build():
            self.declare_full(FEATURE_DONE)
            self.write_plan()
            sha = self.merged_on_main("almost")
            self.early([{"state": "MERGED", "mergeCommit": {"oid": sha}}])
            self.drive([self.worker("almost", covers={"S1": ["src/a/test_feature.py::test_nope"]})])

        self.from_snapshot(self.snapshot_key("hand_merged_failure"), build)

    def verdict(self):
        result = self.verify()
        return json.loads(result.stdout)

    def test_S7_a_fix_round_keeps_the_earlier_attempt_and_the_newest_is_latest(self):
        self.hand_merged_failure()
        attempts = self.t1(self.view())["attempts"]
        self.assertEqual([a["task_id"] for a in attempts][:2], ["t1", "t1r1"])
        self.assertEqual([a["latest"] for a in attempts], [False] * (len(attempts) - 1) + [True])
        first = attempts[0]
        self.assertEqual(
            (first["kind"], first["status"], first["assignment_id"]),
            ("worker", "RELEASED", spawn_id("demo-t1")),
        )
        self.assertEqual(attempts[1]["kind"], "fix")
        ledger_tasks = [k for k, v in self.ledger()["tasks"].items() if v["packet"]["unit"] == "t1"]
        self.assertEqual([a["task_id"] for a in attempts], ledger_tasks, "no attempt dropped")

    def test_S8_the_attempt_maps_to_the_address_the_runtime_returned(self):
        self.delivered()
        (attempt,) = self.t1(self.view())["attempts"]
        self.assertEqual(attempt["address"], "term-demo-t1")
        self.assertEqual(attempt["assignment_id"], spawn_id("demo-t1"))

    def test_S9_a_completed_ticket_is_verified(self):
        """A ticket completes only through verify's invariants and its pull request's CI: complete is verified."""
        self.delivered()
        t1 = self.t1(self.view())
        self.assertIs(t1["verified"], True)
        self.assertNotIn("gates", t1)

    def test_S10_an_accepted_plans_outcome_and_item_states_are_what_verify_computes(self):
        self.delivered()
        outcome = self.view()["outcome"]
        verdict = self.verdict()
        self.assertEqual(verdict["state"], "ACCEPTED")
        self.assertEqual(outcome["state"], verdict["state"])
        self.assertEqual(outcome["items"], verdict["items"])
        self.assertEqual(outcome["integration"], verdict["integration"])

    def test_S10_an_integration_for_another_approval_digest_is_rejected_and_says_why(self):
        self.delivered()
        data = self.ledger()
        data["integration"]["digest"] = "0" * 64
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
        outcome = self.view()["outcome"]
        (item,) = outcome["items"]
        self.assertEqual((outcome["state"], item["state"]), ("REJECTED", "UNKNOWN"))
        self.assertIn("another approval digest", item["reason"])
        self.assertEqual(self.verdict()["items"], outcome["items"])

    def test_S10_a_plan_that_was_not_integrated_is_not_accepted(self):
        self.write_plan()
        outcome = self.view()["outcome"]
        self.assertNotEqual(outcome["state"], "ACCEPTED")
        self.assertEqual([i["scenario"] for i in outcome["items"]], ["integration"])

    def test_S10_every_ticket_complete_is_not_acceptance(self):
        self.hand_merged_failure()
        body = self.view()
        self.assertEqual({t["state"] for t in body["tickets"]}, {"COMPLETE"})
        self.assertNotEqual(body["outcome"]["state"], "ACCEPTED")
        self.assertEqual(body["outcome"]["state"], self.verdict()["state"])


if __name__ == "__main__":
    unittest.main()
