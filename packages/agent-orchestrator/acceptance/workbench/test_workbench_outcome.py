"""Outcome oracle for the agent-workbench plan.

The workbench read commands show a plan as the orchestrator knows it: the plan's own slug, the exact `blocked_by`
graph, every attempt with the assignment and the address the runtime returned (null for the driver's fallback), a
ticket that was claimed but not verified, and the outcome `verify` computes. They write nothing and print no report
body. The fixture plans run through the real driver with the fake runtime and gh, as the acceptance tests do.
"""

import json
import os
import unittest

import pytest
import yaml
from fixtures import spawn_id
from test_acceptance import AcceptanceFixture, check_item, plan_data, scenario, ticket

pytestmark = pytest.mark.integration

BODY = "PRIVATE-REPORT-BODY-7f3a"


def tree(*roots):
    """Every file under the roots, without following links or entering .git: path -> bytes."""
    found = {}
    for root in roots:
        for here, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                path = os.path.join(here, name)
                if not os.path.islink(path):
                    with open(path, "rb") as stream:
                        found[path] = stream.read()
    return found


class WorkbenchOutcomeTests(AcceptanceFixture, unittest.TestCase):
    def report(self, task_id, **over):
        over.setdefault("summary", BODY)
        return super().report(task_id, **over)

    def workbench(self, *args):
        return self.cli("workbench", *args, "--state-dir", str(self.state_dir), "--json")

    def view(self):
        result = self.workbench("plan", str(self.plan))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(BODY, result.stdout, "a report body reached the view model")
        body = json.loads(result.stdout)
        self.assertEqual(body["schema_version"], 1)
        return body

    def ticket_view(self, body, tid):
        (found,) = [t for t in body["tickets"] if t["id"] == tid]
        return found

    def attempt(self, body, tid, task_id):
        (found,) = [a for a in self.ticket_view(body, tid)["attempts"] if a["task_id"] == task_id]
        return found

    def test_the_plan_list_names_the_plan_by_its_slug_and_marks_markdown_legacy(self):
        self.write_plan()
        (self.plan.parent / "2026-11-01-old.plan.md").write_text(
            "# Plan: old\n\nStatus: APPROVED\n", encoding="utf-8"
        )
        listed = self.cli(
            "plans", "list", str(self.plan.parent), "--repo", str(self.repo), "--json"
        )
        self.assertEqual(listed.returncode, 0, listed.stderr)
        yaml_entry = [p for p in json.loads(listed.stdout) if p["format"] == "yaml"]
        self.assertEqual([p["slug"] for p in yaml_entry], ["demo"])

        result = self.workbench("plans", str(self.plan.parent), "--repo", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["schema_version"], 1)
        by_format = {p["format"]: p for p in body["plans"]}
        self.assertEqual(
            (by_format["yaml"]["slug"], by_format["yaml"]["state"]), ("demo", "APPROVED")
        )
        self.assertTrue(by_format["markdown"]["legacy"])
        self.assertNotIn("tickets", by_format["markdown"])

    def test_the_graph_is_exactly_blocked_by_layered_and_stable_and_reading_writes_nothing(self):
        self.write_plan(
            [scenario("S1", check_item()), scenario("S2"), scenario("S3")],
            [
                ticket("T1", ("S2",)),
                ticket("T2", ("S1",), blocked_by=["T1"]),
                ticket("T3", ("S3",), blocked_by=["T1", "T2"]),
            ],
        )
        before = tree(self.repo, self.state_dir)
        first = self.workbench("plan", str(self.plan))
        second = self.workbench("plan", str(self.plan))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout, "the same inputs gave a different view")
        body = json.loads(first.stdout)
        self.assertEqual(body["state"], "APPROVED")
        graph = [(t["id"], t["blocked_by"], t["layer"]) for t in body["tickets"]]
        self.assertEqual(graph, [("T1", [], 0), ("T2", ["T1"], 1), ("T3", ["T1", "T2"], 2)])
        self.assertEqual(tree(self.repo, self.state_dir), before, "a read command wrote a file")

    def test_a_cycle_is_a_scoped_error_and_no_view(self):
        data = plan_data(
            [scenario("S1"), scenario("S2")],
            [ticket("T1", ("S1",), blocked_by=["T2"]), ticket("T2", ("S2",), blocked_by=["T1"])],
        )
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        before = tree(self.repo, self.state_dir)
        result = self.workbench("plan", str(self.plan))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("T1", result.stderr)
        self.assertIn("T2", result.stderr)
        self.assertNotIn("tickets", result.stdout)
        self.assertEqual(tree(self.repo, self.state_dir), before)

    def test_an_accepted_plan_maps_its_attempt_to_the_runtime_address_and_says_what_verify_says(
        self,
    ):
        self.delivered()
        body = self.view()
        t1 = self.ticket_view(body, "T1")
        self.assertTrue(t1["verified"])
        attempt = self.attempt(body, "T1", "t1")
        self.assertEqual(attempt["assignment_id"], spawn_id("demo-t1"))
        self.assertEqual(attempt["address"], "term-demo-t1")
        self.assertTrue(attempt["claimed"])

        verify = self.verify()
        self.assertEqual(verify.returncode, 0, verify.stderr)
        verdict = json.loads(verify.stdout)
        self.assertEqual(body["outcome"]["state"], verdict["state"])
        self.assertEqual(
            [(i["scenario"], i["state"]) for i in body["outcome"]["items"]],
            [(i["scenario"], i["state"]) for i in verdict["items"]],
        )

    def test_the_driver_fallback_address_is_unknown_not_a_terminal(self):
        self.delivered()
        data = self.ledger()
        assignment = data["tasks"]["t1"]["assignment_id"]
        data["tasks"]["t1"]["runtime_id"] = assignment
        for agent in data["agents"].values():
            if agent.get("assignment_id") == assignment:
                agent["address"] = assignment
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
        attempt = self.attempt(self.view(), "T1", "t1")
        self.assertEqual(attempt["assignment_id"], assignment)
        self.assertIsNone(attempt["address"])

    def hand_merged_failure(self):
        """T1's evidence failed at its gate and a person merged its pull request by hand."""
        self.write_plan([scenario("S1", check_item())], [ticket("T1", ("S1",), risk=["state"])])
        sha = self.merged_on_main("almost")
        self.early([{"state": "MERGED", "mergeCommit": {"oid": sha}}])
        self.drive([self.worker("almost")])

    def test_control_the_delivered_fixture_is_accepted_by_verify(self):
        """Holds today: the accepted fixture the workbench reads is accepted by `verify`."""
        self.delivered()
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "ACCEPTED")

    def test_control_the_hand_merged_failure_is_rejected_by_verify(self):
        """Holds today: the failure fixture is a complete, unverified ticket and `verify` refuses the plan."""
        self.hand_merged_failure()
        (status,) = self.status()["tickets"]
        self.assertEqual(status["state"], "COMPLETE")
        self.assertTrue(status["unverified"])
        self.assertNotEqual(self.verify().returncode, 0)

    def test_a_ticket_claimed_done_whose_evidence_failed_is_not_verified_and_the_plan_not_accepted(
        self,
    ):
        self.hand_merged_failure()
        body = self.view()
        t1 = self.ticket_view(body, "T1")
        self.assertEqual(t1["state"], "COMPLETE")
        self.assertFalse(t1["verified"])
        self.assertIn("FAIL", [g["result"] for g in t1["gates"]])
        self.assertTrue(self.attempt(body, "T1", "t1")["claimed"])
        self.assertNotIn(body["outcome"]["state"], ("ACCEPTED", "ACCEPTED_WITH_HUMAN"))
        self.assertNotEqual(body["outcome"]["items"][0]["state"], "PASS")


if __name__ == "__main__":
    unittest.main()
