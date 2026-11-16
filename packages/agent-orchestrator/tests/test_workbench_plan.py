"""The `workbench plan` view of one plan: its tickets' blocked-by layers, the loader's refusals, the approval
snapshot's drift, and each ticket's attempts with their addresses and their verified or claimed state.

The plan listing, the cross-cutting read-only checks and the fixture every workbench test module imports stay in
test_workbench.py, beside this module, because those modules reach `WorkbenchFixture`, `graph`, `tree_digest`,
`at`, `dead_pid` and `state_event` at that path. Nothing moved here is over a ceiling: the longest test is 48
lines, against the 50-line function ceiling, and the widest class declares 16 methods, against 25."""

import hashlib
import json

import pytest
import yaml
from test_workbench import BODY, WorkbenchFixture, graph, tree_digest

pytestmark = pytest.mark.integration


class PlanTests(WorkbenchFixture):
    def test_S4_every_ticket_has_its_blocked_by_edges_and_a_layer_ordered_by_layer_then_plan(self):
        # T1 free; T2 and T3 behind T1; T4 behind both; T5 free; T6 behind T4 and T5.
        path = self.write_plan(graph([], ["T1"], ["T1"], ["T2", "T3"], [], ["T5", "T4"]))
        shown = self.view(path)
        self.assertEqual(shown["schema_version"], 1)
        self.assertEqual(
            [(t["id"], t["layer"], t["blocked_by"]) for t in shown["tickets"]],
            [
                ("T1", 0, []),
                ("T5", 0, []),
                ("T2", 1, ["T1"]),
                ("T3", 1, ["T1"]),
                ("T4", 2, ["T2", "T3"]),
                ("T6", 3, ["T5", "T4"]),
            ],
        )
        first = self.workbench("plan", str(path))[1]
        self.assertEqual(first, self.workbench("plan", str(path))[1])
        self.assertEqual(shown["slug"], "bench")
        self.assertEqual(shown["state"], "APPROVED")
        self.assertFalse(shown["revision"]["drift"])

    def test_S4_a_ticket_carries_its_state_and_reason_from_the_ledger(self):
        path = self.write_plan(graph([], ["T1"]))
        self.write_ledger("bench", {"T1": "COMPLETE 2026-11-11", "T2": "needs you: pick a name"})
        by_id = {t["id"]: t for t in self.view(path)["tickets"]}
        self.assertEqual((by_id["T1"]["state"], by_id["T1"]["reason"]), ("COMPLETE", None))
        self.assertEqual(
            (by_id["T2"]["state"], by_id["T2"]["reason"]), ("needs you", "pick a name")
        )

    def refused(self, path):
        before = tree_digest(self.repo, self.state_dir)
        code, out, err = self.workbench("plan", str(path))
        self.assertNotEqual(code, 0)
        self.assertEqual(out, "", "a refusal prints no partial view model on stdout")
        shown = json.loads(err)
        self.assertNotIn("tickets", shown)
        self.assertNotIn("slug", shown)
        self.assertEqual(shown["error"]["kind"], "invalid_plan")
        self.assertEqual(shown["error"]["path"], str(path.resolve()))
        self.assertEqual(before, tree_digest(self.repo, self.state_dir))
        return shown["error"]["message"]

    def test_S5_a_cycle_is_the_loaders_error_naming_the_tickets_and_no_view(self):
        message = self.refused(self.write_plan(graph(["T2"], ["T1"]), approve=False))
        self.assertIn("dependency cycle", message)
        self.assertIn("T1", message)
        self.assertIn("T2", message)
        self.assertIn("tickets[", message)

    def test_S5_a_duplicate_ticket_id_is_named(self):
        data = graph([], [])
        data["tickets"][1]["id"] = "T1"
        message = self.refused(self.write_plan(data, approve=False))
        self.assertIn("duplicate id T1", message)

    def test_S5_a_reference_to_a_missing_ticket_is_named(self):
        message = self.refused(self.write_plan(graph([], ["T9"]), approve=False))
        self.assertIn("T9", message)
        self.assertIn("blocked_by", message)

    def test_S5_malformed_yaml_names_the_file(self):
        path = self.plans / "2026-11-11-bad.plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("slug: [unclosed\n", encoding="utf-8")
        message = self.refused(path)
        self.assertIn("invalid YAML", message)

    def test_S6_an_edited_plan_shows_the_approved_snapshot_with_drift_and_both_digests(self):
        path = self.write_plan(graph([], ["T1"]))
        approved = self.view(path)
        digest = yaml.safe_load(path.read_text(encoding="utf-8"))["approval"]["digest"]
        self.assertEqual(approved["revision"]["approved_digest"], digest)
        self.assertEqual(approved["revision"]["current_digest"], digest)

        def retitle(data):
            data["tickets"][0]["delivers"] = "EDITED after approval"
            data["tickets"].append(dict(data["tickets"][0], id="T3", delivers="a new ticket"))

        self.edit(path, retitle)
        shown = self.view(path)
        revision = shown["revision"]
        self.assertTrue(revision["drift"])
        self.assertEqual(revision["approved_digest"], digest)
        self.assertNotEqual(revision["current_digest"], digest)
        self.assertEqual(
            [(t["id"], t["delivers"]) for t in shown["tickets"]],
            [(t["id"], t["delivers"]) for t in approved["tickets"]],
        )
        self.assertNotIn("EDITED", json.dumps(shown))

    def test_S6_a_changed_oracle_is_drift_too(self):
        path = self.write_plan(graph([]))
        oracle = path.with_name(path.name.removesuffix(".plan.yaml") + ".oracle")
        oracle.mkdir()
        (oracle / "check.py.oracle").write_text("assert True\n", encoding="utf-8")
        shown = self.view(path)
        self.assertTrue(shown["revision"]["drift"])
        self.assertEqual([t["id"] for t in shown["tickets"]], ["T1"])

    def write_folder_plan(self):
        """An approved plan in its own folder: one acceptance file, placed at acceptance/flows/probe.txt."""
        data = graph([])
        data["domain"] = "flows"
        data["scenarios"][0]["evidence"] = {
            "check": "test -f acceptance/flows/probe.txt",
            "baseline": "pass",
            "acceptance": ["probe.txt"],
            "package": ".",
        }
        folder = self.plans / "2026-11-12-bench"
        (folder / "acceptance").mkdir(parents=True)
        (folder / "acceptance" / "probe.txt").write_text("probe\n", encoding="utf-8")
        path = self.write_plan(data, name="2026-11-12-bench/plan.yaml", approve=False)
        code, out, err = self.run_cli(
            "plan",
            "approve",
            str(path),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        )
        self.assertEqual(code, 0, out + err)
        return path

    def test_S7_an_approved_folder_plan_is_not_drift_until_an_acceptance_file_is_edited(self):
        path = self.write_folder_plan()
        approved = self.view(path)
        self.assertFalse(approved["revision"]["drift"])
        self.assertEqual(
            approved["revision"]["approved_digest"], approved["revision"]["current_digest"]
        )
        (path.parent / "acceptance" / "probe.txt").write_text("edited\n", encoding="utf-8")
        shown = self.view(path)
        self.assertTrue(shown["revision"]["drift"])
        self.assertNotEqual(
            shown["revision"]["approved_digest"], shown["revision"]["current_digest"]
        )
        self.assertEqual([t["id"] for t in shown["tickets"]], ["T1"])

    def test_S7_an_added_acceptance_file_is_drift_in_a_folder_plan(self):
        path = self.write_folder_plan()
        (path.parent / "acceptance" / "extra.txt").write_text("more\n", encoding="utf-8")
        self.assertTrue(self.view(path)["revision"]["drift"])

    def drifted_ledger(self):
        path = self.write_plan(graph([], []))
        self.write_ledger(
            "bench",
            {"T1": "COMPLETE 2026-11-11", "T2": "COMPLETE 2026-11-11"},
            plan_state="RUNNING",
        )
        return path

    def test_S6_a_slug_edited_after_approval_keeps_the_ledger_state_of_the_approved_plan(self):
        path = self.drifted_ledger()
        self.edit(path, lambda data: data.update(slug="renamed"))
        shown = self.view(path)
        self.assertTrue(shown["revision"]["drift"])
        self.assertEqual((shown["slug"], shown["state"]), ("bench", "RUNNING"))
        self.assertEqual([t["state"] for t in shown["tickets"]], ["COMPLETE", "COMPLETE"])
        listed = json.loads(self.workbench("plans")[1])["plans"][0]
        self.assertEqual((listed["slug"], listed["state"]), ("bench", "RUNNING"))
        self.assertEqual(listed["tickets"], {"total": 2, "by_state": {"COMPLETE": 2}})

    def test_S6_a_ticket_removed_after_approval_keeps_its_ledger_state(self):
        path = self.drifted_ledger()
        self.edit(path, lambda data: data["tickets"].pop())
        shown = self.view(path)
        self.assertTrue(shown["revision"]["drift"])
        self.assertEqual(
            {t["id"]: t["state"] for t in shown["tickets"]}, {"T1": "COMPLETE", "T2": "COMPLETE"}
        )
        listed = json.loads(self.workbench("plans")[1])["plans"][0]
        self.assertEqual(listed["tickets"], {"total": 2, "by_state": {"COMPLETE": 2}})

    def test_S6_a_draft_plan_has_no_approval_to_drift_from(self):
        path = self.write_plan(graph([]), approve=False)
        shown = self.view(path)
        self.assertEqual(shown["state"], "DRAFT")
        self.assertEqual(
            shown["revision"],
            {
                "approved": False,
                "drift": False,
                "approved_digest": None,
                "current_digest": None,
                "approved_by": None,
                "approved_at": None,
            },
        )


def attempt_task(task_id, status="COMPLETE", unit="t1", **over):
    """A ledger task as the driver writes it, without the bodies a reader must never print."""
    task = {
        "packet": {"unit": unit, "task_id": task_id, "worker": "bench-" + unit},
        "status": status,
        "runtime_id": "term-bench-" + unit,
        "assignment_id": "a-" + task_id,
        "started_at": "2026-11-11T10:00:00+00:00",
    }
    task.update(over)
    return task


def complete_report(**over):
    report = {"status": "COMPLETE", "acceptance_met": True, "checks": [{"outcome": "passed"}]}
    report.update(over)
    return report


class AttemptTests(WorkbenchFixture):
    def attempts(self, tasks, ticket="T1"):
        path = self.write_plan(graph([], ["T1"]))
        self.write_ledger("bench", {"T1": "IN PROGRESS", "T2": "TO-DO"}, tasks=tasks)
        (found,) = [t for t in self.view(path)["tickets"] if t["id"] == ticket]
        return found["attempts"]

    def test_S7_every_ledger_task_of_a_ticket_appears_once_and_the_newest_is_latest(self):
        tasks = {
            "t1": attempt_task("t1", "RELEASED", started_at="2026-11-11T10:00:00+00:00"),
            "t1r1": attempt_task("t1r1", "RELEASED", started_at="2026-11-11T10:10:00+00:00"),
            "t1d1": attempt_task("t1d1", "RELEASED", started_at="2026-11-11T10:20:00+00:00"),
            "t1o1": attempt_task("t1o1", "RELEASED", started_at="2026-11-11T10:30:00+00:00"),
            "t1p1": attempt_task(
                "t1p1",
                "ACTIVE",
                started_at="2026-11-11T10:40:00+00:00",
                review={"name": "bench-t1-review", "assignment_id": "a-t1p1-review"},
            ),
            "t2": attempt_task("t2", unit="t2"),
        }
        tasks["t1"]["finished_at"] = "2026-11-11T10:05:00+00:00"
        found = self.attempts(tasks)
        self.assertEqual(
            [(a["task_id"], a["kind"], a["latest"]) for a in found],
            [
                ("t1", "worker", False),
                ("t1r1", "fix", False),
                ("t1d1", "retry", False),
                ("t1o1", "orphan", False),
                ("t1p1", "reopen", True),
            ],
        )
        first = found[0]
        self.assertEqual(
            (first["status"], first["started_at"], first["finished_at"], first["assignment_id"]),
            ("RELEASED", "2026-11-11T10:00:00+00:00", "2026-11-11T10:05:00+00:00", "a-t1"),
        )
        self.assertEqual(
            sorted(first),
            sorted(
                [
                    "task_id",
                    "kind",
                    "status",
                    "started_at",
                    "finished_at",
                    "assignment_id",
                    "address",
                    "claimed",
                    "latest",
                ]
            ),
        )
        self.assertIsNone(found[4]["finished_at"], "a running attempt has no end: null, not 0")

    def test_S7_the_other_tickets_attempts_are_not_mixed_in_and_a_ticket_without_one_has_none(self):
        found = self.attempts({"t2": attempt_task("t2", unit="t2")}, ticket="T1")
        self.assertEqual(found, [])

    def test_S7_attempts_keep_the_ledger_order_when_two_start_at_the_same_time(self):
        same = "2026-11-11T10:00:00+00:00"
        found = self.attempts(
            {
                "t1": attempt_task("t1", "RELEASED", started_at=same),
                "t1r1": attempt_task("t1r1", "ACTIVE", started_at=same),
            }
        )
        self.assertEqual(
            [(a["task_id"], a["latest"]) for a in found], [("t1", False), ("t1r1", True)]
        )

    def test_S8_the_address_is_the_one_the_runtime_returned(self):
        found = self.attempts({"t1": attempt_task("t1", address="t1sf", runtime_id="t1sf")})
        self.assertEqual(found[0]["address"], "t1sf")

    def test_S8_a_ledger_without_an_address_field_reads_the_runtime_id_unless_it_is_the_fallback(
        self,
    ):
        tasks = {
            "t1": attempt_task("t1", "RELEASED", runtime_id="t1sf"),
            "t1r1": attempt_task("t1r1", "RELEASED", runtime_id="a-t1r1"),
            "t1d1": attempt_task("t1d1", "RELEASED", runtime_id="bench-t1"),
            "t1o1": attempt_task("t1o1", "RELEASED", runtime_id=None),
            "t1p1": attempt_task("t1p1", "ACTIVE", runtime_id="  "),
        }
        found = {a["task_id"]: a["address"] for a in self.attempts(tasks)}
        self.assertEqual(
            found, {"t1": "t1sf", "t1r1": None, "t1d1": None, "t1o1": None, "t1p1": None}
        )

    def test_S8_the_agent_name_or_assignment_of_another_attempt_is_the_fallback_too(self):
        tasks = {
            "t1": attempt_task("t1", "RELEASED", runtime_id="a-t1"),
            "t1r1": attempt_task("t1r1", "ACTIVE", runtime_id="a-t1"),
        }
        found = {a["task_id"]: a["address"] for a in self.attempts(tasks)}
        self.assertEqual(found, {"t1": None, "t1r1": None})

    def test_S8_a_recorded_null_address_is_null_whatever_the_runtime_id_holds(self):
        found = self.attempts({"t1": attempt_task("t1", address=None, runtime_id="term-bench-t1")})
        self.assertIsNone(found[0]["address"])

    def test_S8_an_address_is_never_taken_from_another_attempt(self):
        tasks = {
            "t1": attempt_task("t1", "RELEASED", address="t1sf"),
            "t1r1": attempt_task("t1r1", "ACTIVE", address=None, runtime_id="a-t1r1"),
        }
        found = {a["task_id"]: a["address"] for a in self.attempts(tasks)}
        self.assertEqual(found, {"t1": "t1sf", "t1r1": None})

    def test_S9_a_worker_that_reported_complete_over_failed_evidence_is_claimed_not_verified(self):
        path = self.write_plan(graph([], ["T1"]))
        ledger = self.write_ledger(
            "bench",
            {"T1": "COMPLETE", "T2": "TO-DO"},
            tasks={"t1": attempt_task("t1", report=complete_report(summary=BODY))},
        )
        data = json.loads(ledger.read_text(encoding="utf-8"))
        data["ticket_evidence"] = {
            "T1": {"S1": {"result": "FAIL", "receipt": "S1.ticket.1", "commit": "abc"}}
        }
        data["unverified"] = {
            "T1": {"scenarios": ["S1"], "reason": "merged with its evidence failed", "at": "t"}
        }
        receipts = ledger.with_suffix("") / "receipts"
        receipts.mkdir(parents=True)
        text = json.dumps({"result": "FAIL", "reason": "exit", "label": "FAIL (exit 1)"})
        (receipts / "S1.ticket.1.json").write_text(text, encoding="utf-8")
        data["receipts"] = {"S1.ticket.1": hashlib.sha256(text.encode()).hexdigest()}
        ledger.write_text(json.dumps(data), encoding="utf-8")
        shown = self.view(path)
        t1, t2 = shown["tickets"]
        self.assertEqual(t1["state"], "COMPLETE")
        self.assertIs(t1["verified"], False)
        self.assertIs(t1["unverified"], True)
        self.assertEqual(
            t1["gates"],
            [{"scenario": "S1", "phase": "ticket", "result": "FAIL", "reason": "FAIL (exit 1)"}],
        )
        self.assertIs(t1["attempts"][0]["claimed"], True)
        self.assertNotIn(BODY, json.dumps(shown))
        self.assertIs(t2["verified"], False)
        self.assertEqual(t2["gates"], [])

    def test_S9_a_ticket_whose_gates_passed_and_nothing_was_merged_past_is_verified(self):
        path = self.write_plan(graph([]))
        ledger = self.write_ledger(
            "bench", {"T1": "COMPLETE"}, tasks={"t1": attempt_task("t1", report=complete_report())}
        )
        data = json.loads(ledger.read_text(encoding="utf-8"))
        data["ticket_evidence"] = {"T1": {"S1": {"result": "PASS", "receipt": "S1.ticket.1"}}}
        ledger.write_text(json.dumps(data), encoding="utf-8")
        (t1,) = self.view(path)["tickets"]
        self.assertIs(t1["verified"], True)
        self.assertIs(t1["unverified"], False)
        self.assertEqual(
            t1["gates"],
            [{"scenario": "S1", "phase": "ticket", "result": "PASS", "reason": None}],
            "a gate whose receipt cannot be read has an unknown reason: null",
        )

    def test_S9_an_attempt_still_running_claims_nothing_yet(self):
        found = self.attempts({"t1": attempt_task("t1", "ACTIVE")})
        self.assertIsNone(found[0]["claimed"])

    def test_S9_an_attempt_that_ended_without_a_complete_report_claims_nothing(self):
        found = self.attempts({"t1": attempt_task("t1", "RELEASED")})
        self.assertIs(found[0]["claimed"], False)
