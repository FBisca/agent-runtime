"""The workbench's attempts read from ledgers the real driver wrote (fake runtime and gh): each attempt maps to its
assignment and address, and a ticket is verified only when it is COMPLETE."""

import json
import unittest

import pytest
from fixtures import spawn_id
from test_acceptance import AcceptanceFixture, ticket

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
        person merged it by hand."""

        def build():
            # A person's sign-off left open keeps the accepted plan from going on to its archive.
            self.write_plan(
                tickets=[
                    ticket(),
                    ticket("T9", owner="Felippe", paths=["docs/"], blocked_by=["T1"]),
                ]
            )
            sha = self.merged_on_main("almost")
            self.early([{"state": "MERGED", "mergeCommit": {"oid": sha}}])
            self.drive([self.worker("almost", covers={"S1": ["src/a/test_gone.py::test_nope"]})])

        self.from_snapshot(self.snapshot_key("hand_merged_failure"), build)

    @pytest.mark.spec("Attempt history")
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

    @pytest.mark.spec("Attempt addresses")
    def test_S8_the_attempt_maps_to_the_address_the_runtime_returned(self):
        self.delivered()
        (attempt,) = self.t1(self.view())["attempts"]
        self.assertEqual(attempt["address"], "term-demo-t1")
        self.assertEqual(attempt["assignment_id"], spawn_id("demo-t1"))

    @pytest.mark.spec("Claimed versus verified")
    def test_S9_a_completed_ticket_is_verified(self):
        """A ticket completes only through verify's invariants and its pull request's CI: complete is verified."""
        self.delivered()
        t1 = self.t1(self.view())
        self.assertIs(t1["verified"], True)
        self.assertNotIn("gates", t1)


if __name__ == "__main__":
    unittest.main()
