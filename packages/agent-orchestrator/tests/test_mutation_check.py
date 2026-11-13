"""The integration mutation check: an outcome oracle that was uncalibrated at baseline and passes at integration
must fail on an assertion when a fresh reviewer's behavioural mutation of the delivered code is applied (S21)."""

import json
import unittest

import pytest
from fixtures import report_wait
from test_acceptance import STATES, AcceptanceFixture, scenario

pytestmark = pytest.mark.integration

KILLS = "--- a/src/a/feature.txt\n+++ b/src/a/feature.txt\n@@ -1 +1 @@\n-done\n+nope\n"
SURVIVES = "--- /dev/null\n+++ b/src/a/unrelated.txt\n@@ -0,0 +1 @@\n+noise\n"
EDITS_THE_ORACLE = "--- /dev/null\n+++ b/checks/probe.txt\n@@ -0,0 +1 @@\n+probe\n"
NOT_A_PATCH = "I could not find a behaviour worth changing.\n"


class MutationFixture(AcceptanceFixture):
    def mutator(self, attempt, patch):
        """A wait step: the fresh mutation reviewer of attempt `attempt` wrote its patch and reports complete."""
        step = report_wait("demo-mut-s1-%d" % attempt, "complete")
        step["write"] = [[str(self.run_dir / "mutation" / ("S1.%d.patch" % attempt)), patch]]
        return step

    def mutated(self, *patches):
        self.write_plan()
        self.merged_on_main("done")
        steps = [self.worker("done"), self.reviewer()]
        steps += [self.mutator(n, patch) for n, patch in enumerate(patches, 1)]
        self.drive(steps)

    def mutation_receipts(self):
        return self.receipts("mutation")


class MutationCheckTests(MutationFixture, unittest.TestCase):
    def test_S21_an_uncalibrated_oracle_that_passes_at_integration_is_killed_by_a_reviewers_mutation(
        self,
    ):
        self.mutated(KILLS)
        self.assertEqual(self.history(), STATES + ["ACCEPTED", "ARCHIVED"])
        spawned = self.spawned()
        self.assertIn("demo-mut-s1-1", spawned)
        self.assertNotIn("demo-mut-s1-2", spawned)
        implementers = {"demo-t1", "demo-t1-review"}
        self.assertFalse(implementers & {"demo-mut-s1-1"}, "the mutation reviewer is a fresh agent")
        (receipt,) = self.mutation_receipts()
        self.assertEqual(
            (receipt["scenario"], receipt["result"], receipt["mutation"]["killed"]),
            ("S1", "FAIL", True),
        )
        self.assertEqual(receipt["commit"], self.ledger()["integration"]["sha"])
        self.assertIn(receipt["label"], ("FAIL",))
        self.assertEqual(self.verify().returncode, 0)

    def test_S21_an_ineffective_mutation_gets_one_more_attempt_with_a_fresh_reviewer(self):
        self.mutated(SURVIVES, KILLS)
        self.assertEqual(self.history(), STATES + ["ACCEPTED", "ARCHIVED"])
        spawned = self.spawned()
        self.assertIn("demo-mut-s1-1", spawned)
        self.assertIn("demo-mut-s1-2", spawned, "the second attempt is another fresh reviewer")
        first, second = self.mutation_receipts()
        self.assertEqual(
            (first["result"], first["mutation"]["killed"]),
            ("PASS", False),
            "the oracle still passes with the first mutation applied",
        )
        self.assertTrue(second["mutation"]["killed"])
        task = (self.run_dir / "mutation" / "S1.2-task.md").read_text(encoding="utf-8")
        self.assertIn(
            "attempt 1: the oracle still passed",
            task,
            "the retry names what happened to the first mutation",
        )

    def test_S21_a_mutation_that_survives_twice_makes_the_item_a_human_item(self):
        self.mutated(SURVIVES, SURVIVES)
        self.assertEqual(self.history(), STATES, "the plan waits for a person")
        self.assertNotIn("demo-archive", self.spawned())
        self.assertNotIn("demo-mut-s1-3", self.spawned(), "one retry, no more")
        check = self.verify()
        self.assertNotEqual(check.returncode, 0)
        self.assertIn("S1", check.stderr + check.stdout)
        self.assertIn("mutation", (check.stderr + check.stdout).lower())
        (wake,) = [w for w in self.wakes() if w["kind"] == "plan-needs-accept"]
        self.assertIn("S1", wake["text"])
        accepted = self.accept("S1")
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(self.status()["history"][-1]["state"], "ACCEPTED_WITH_HUMAN")

    def test_S21_a_patch_that_edits_the_oracle_or_is_no_patch_is_an_ineffective_attempt(self):
        self.mutated(EDITS_THE_ORACLE, NOT_A_PATCH)
        self.assertEqual(self.history(), STATES)
        outcomes = [h["outcome"] for h in self.ledger()["mutation"]["S1"]["history"]]
        self.assertEqual(outcomes, ["oracle", "invalid"])
        self.assertEqual(self.mutation_receipts(), [], "neither patch reached the oracle")

    def test_S21_an_item_that_is_not_an_uncalibrated_new_oracle_runs_no_mutation(self):
        self.write_plan([scenario("S1", {"check": "true", "baseline": "pass"})])
        self.merged_on_main("done")
        self.drive([self.worker("done"), self.reviewer()])
        self.assertEqual(self.history(), STATES + ["ACCEPTED", "ARCHIVED"])
        self.assertFalse([n for n in self.spawned() if "-mut-" in n])
        self.assertEqual(self.mutation_receipts(), [])

    def test_S21_the_mutation_reviewer_gets_the_oracle_the_delivered_commit_and_a_patch_path(self):
        self.mutated(KILLS)
        task = (self.run_dir / "mutation" / "S1.1-task.md").read_text(encoding="utf-8")
        sha = self.ledger()["integration"]["sha"]
        for needed in (sha, "probe.txt.oracle", "S1.1.patch", str(self.plan)):
            self.assertIn(needed, task)
        (call,) = [c for c in self.calls("spawn") if c["args"].get("name") == "demo-mut-s1-1"]
        self.assertIn("S1.1-task.md", call["args"]["task"])
        json.dumps(self.ledger()["mutation"])


if __name__ == "__main__":
    unittest.main()
