"""The workbench's attempts, gates and outcome read from ledgers the real driver wrote (fake runtime and gh): each
attempt maps to its assignment and address, a ticket's gates and the plan's outcome are what the receipts say, and
`verify` and the view never disagree."""

import hashlib
import json
import unittest

import pytest
from fixtures import spawn_id
from test_acceptance import AcceptanceFixture, check_item, scenario, ticket

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
        self.write_plan([scenario("S1", check_item())], [ticket("T1", ("S1",), risk=["state"])])
        sha = self.merged_on_main("almost")
        self.early([{"state": "MERGED", "mergeCommit": {"oid": sha}}])
        self.drive([self.worker("almost")])

    def rewrite_receipt(self, stem, change):
        """Change a receipt on disk and keep the ledger's sha256 of it in step, as a receipt that was produced for
        something else would be."""
        path = self.run_dir / "receipts" / (stem + ".json")
        receipt = json.loads(path.read_text(encoding="utf-8"))
        change(receipt)
        text = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8")
        data = self.ledger()
        data["receipts"][stem] = hashlib.sha256(text.encode()).hexdigest()
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")

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

    def test_S9_a_ticket_merged_by_hand_after_failed_evidence_is_claimed_but_not_verified(self):
        self.hand_merged_failure()
        body = self.view()
        t1 = self.t1(body)
        self.assertEqual(t1["state"], "COMPLETE")
        self.assertIs(t1["verified"], False)
        self.assertIs(t1["unverified"], True)
        self.assertIn("S1", t1["unverified_reason"])
        (gate,) = t1["gates"]
        self.assertEqual(
            (gate["scenario"], gate["phase"], gate["result"]), ("S1", "ticket", "FAIL")
        )
        self.assertTrue(gate["reason"], "the receipt's reason is named")
        self.assertIs(t1["attempts"][0]["claimed"], True)

    def test_S9_a_ticket_whose_evidence_passed_is_verified(self):
        self.delivered()
        t1 = self.t1(self.view())
        self.assertIs(t1["verified"], True)
        self.assertEqual([g["result"] for g in t1["gates"]], ["PASS"])
        self.assertIsNone(t1["gates"][0]["reason"])

    def test_S10_an_accepted_plans_outcome_and_item_states_are_what_verify_computes(self):
        self.delivered()
        outcome = self.view()["outcome"]
        verdict = self.verdict()
        self.assertEqual(verdict["state"], "ACCEPTED")
        self.assertEqual(outcome["state"], verdict["state"])
        self.assertEqual(
            [(i["scenario"], i["state"]) for i in outcome["items"]],
            [(i["scenario"], i["state"]) for i in verdict["items"]],
        )
        self.assertEqual([i["freshness"] for i in outcome["items"]], ["fresh"])

    def test_S10_a_receipt_that_no_longer_matches_its_sha256_is_stale_and_says_why(self):
        self.delivered()
        path = self.run_dir / "receipts" / "S1.integration.1.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        (item,) = self.view()["outcome"]["items"]
        self.assertEqual((item["freshness"], item["state"]), ("stale", "UNKNOWN"))
        self.assertIn("sha256", item["reason"])
        self.assertEqual(self.verdict()["items"][0]["state"], "UNKNOWN")

    def test_S10_a_receipt_for_another_approval_digest_is_stale(self):
        self.delivered()
        self.rewrite_receipt("S1.integration.1", lambda r: r.update(digest="0" * 64))
        (item,) = self.view()["outcome"]["items"]
        self.assertEqual((item["freshness"], item["state"]), ("stale", "UNKNOWN"))
        self.assertIn("another approval digest", item["reason"])

    def test_S10_a_receipt_for_another_tree_is_stale(self):
        self.delivered()
        self.rewrite_receipt("S1.integration.1", lambda r: r.update(tree="1" * 40))
        (item,) = self.view()["outcome"]["items"]
        self.assertEqual((item["freshness"], item["state"]), ("stale", "UNKNOWN"))
        self.assertIn("another tree", item["reason"])

    def test_S10_a_receipt_file_that_is_gone_is_missing(self):
        self.delivered()
        (self.run_dir / "receipts" / "S1.integration.1.json").unlink()
        (item,) = self.view()["outcome"]["items"]
        self.assertEqual((item["freshness"], item["state"]), ("missing", "UNKNOWN"))

    def test_S10_a_plan_that_was_not_integrated_has_missing_items_and_is_not_accepted(self):
        self.write_plan([scenario("S1", check_item())], [ticket("T1", ("S1",))])
        outcome = self.view()["outcome"]
        self.assertNotIn(outcome["state"], ("ACCEPTED", "ACCEPTED_WITH_HUMAN"))
        self.assertEqual([i["freshness"] for i in outcome["items"]], ["missing"])

    def test_S10_every_ticket_complete_is_not_acceptance(self):
        self.hand_merged_failure()
        body = self.view()
        self.assertEqual({t["state"] for t in body["tickets"]}, {"COMPLETE"})
        self.assertNotIn(body["outcome"]["state"], ("ACCEPTED", "ACCEPTED_WITH_HUMAN"))
        self.assertEqual(body["outcome"]["state"], self.verdict()["state"])


if __name__ == "__main__":
    unittest.main()
