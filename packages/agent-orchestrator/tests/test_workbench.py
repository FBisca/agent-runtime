"""The workbench's read side: `workbench plans` and `workbench plan` print what the plan loader and the ledger say,
as one JSON object, and write nothing."""

import copy
import hashlib
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script
from test_plan import contract

pytestmark = pytest.mark.integration

SECRET = "hunter2-workbench-secret"
BODY = "REPORT-BODY-MARKER the worker wrote this"


def graph(*edges):
    """A plan/1 contract whose tickets T1..Tn carry the given blocked_by lists, in plan order."""
    data = contract()
    data["slug"] = "bench"
    base = data["tickets"][0]
    data["tickets"] = []
    for index, blockers in enumerate(edges, 1):
        ticket = copy.deepcopy(base)
        ticket.update(id="T%d" % index, delivers="delivers %d" % index)
        if blockers:
            ticket["blocked_by"] = list(blockers)
        data["tickets"].append(ticket)
    return data


def tree_digest(*roots):
    """Every path under the roots with its size and content hash: two equal digests mean nothing was written."""
    seen = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames + dirnames):
                path = os.path.join(dirpath, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    with open(path, "rb") as stream:
                        seen[path] = hashlib.sha256(stream.read()).hexdigest()
                else:
                    seen[path] = "dir" if os.path.isdir(path) else "link"
    return seen


class WorkbenchFixture(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "XDG_STATE_HOME": str(self.root / "xdg"),
                "HC_TERMINAL_ID": "lead-test",
                "WORKBENCH_SECRET": SECRET,
            },
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.resolve_patch = mock.patch.object(
            load_script(), "resolve_runtime", return_value={"role": "lead"}
        )
        self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)
        # The fixture's Markdown plan is removed: each test lays out its own plans.
        self.plan.unlink()
        self.plans = self.repo / "docs" / "plans"

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_plan(self, data, name=None, approve=True):
        path = self.plans / (name or "2026-11-11-%s.plan.yaml" % data["slug"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# kept\n" + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        # Discovery lists the repository's tracked plan files.
        self.git(self.repo, "add", str(path))
        if approve:
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

    def edit(self, path, change):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text("# kept\n" + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def write_ledger(self, slug, statuses, plan_state=None, tasks=None):
        key = load_script().repo_key(self.repo)
        path = self.state_dir / key / (slug + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "tasks": tasks or {}, "ticket_status": statuses}
        if plan_state:
            data["plan"] = {"state": plan_state, "history": [{"state": plan_state, "at": "t"}]}
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def workbench(self, verb, *extra, repo=True):
        argv = ["workbench", verb, *extra, "--json", "--state-dir", str(self.state_dir)]
        if repo:
            argv += ["--repo", str(self.repo)]
        return self.run_cli(*argv)

    def view(self, path):
        code, out, err = self.workbench("plan", str(path))
        self.assertEqual(code, 0, out + err)
        return json.loads(out)


class PlansTests(WorkbenchFixture):
    def test_S2_one_json_object_lists_every_plan_with_its_state_and_ticket_counts(self):
        approved = self.write_plan(graph([], ["T1"], ["T1"]))
        draft_data = graph([], [])
        draft_data["slug"] = "sketch"
        draft = self.write_plan(draft_data, approve=False)
        self.write_ledger(
            "bench",
            {"T1": "COMPLETE 2026-11-11", "T2": "IN PROGRESS · round 2", "T3": "TO-DO"},
            plan_state="RUNNING",
        )
        legacy = self.plans / "2026-11-01-old.plan.md"
        legacy.write_text(
            "# Plan: old\n\nSlug: `old`. Status: APPROVED 2026-11-01.\n\n## Tickets\n\n"
            "| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| T1 | first | S1 | none | src/a/ | small | simple | agent | COMPLETE |\n"
            "| T2 | second | S1 | T1 | src/b/ | small | simple | agent | TO-DO |\n",
            encoding="utf-8",
        )
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        shown = json.loads(out)
        self.assertEqual(shown["schema_version"], 1)
        by_slug = {entry["slug"]: entry for entry in shown["plans"]}
        self.assertEqual(set(by_slug), {"bench", "sketch", "old"})
        bench = by_slug["bench"]
        self.assertEqual(
            (bench["format"], bench["path"], bench["state"], bench["legacy"]),
            ("yaml", str(approved.resolve()), "RUNNING", False),
        )
        self.assertEqual(
            bench["tickets"],
            {"total": 3, "by_state": {"COMPLETE": 1, "IN PROGRESS": 1, "TO-DO": 1}},
        )
        sketch = by_slug["sketch"]
        self.assertEqual(
            (sketch["state"], sketch["path"], sketch["tickets"]),
            ("DRAFT", str(draft.resolve()), {"total": 2, "by_state": {"TO-DO": 2}}),
        )
        old = by_slug["old"]
        self.assertEqual((old["format"], old["legacy"]), ("markdown", True))
        self.assertEqual(old["tickets"], {"total": 2, "by_state": {"COMPLETE": 1, "TO-DO": 1}})
        self.assertNotIn("graph", old)
        self.assertNotIn("layers", old)

    def test_S2_an_approved_plan_without_a_ledger_is_approved(self):
        self.write_plan(graph([]))
        shown = json.loads(self.workbench("plans")[1])
        self.assertEqual(shown["plans"][0]["state"], "APPROVED")

    def test_S2_a_path_names_the_plan_directory_without_a_repo_flag(self):
        path = self.write_plan(graph([]))
        code, out, err = self.workbench("plans", str(path), repo=False)
        self.assertEqual(code, 0, err)
        self.assertEqual([p["slug"] for p in json.loads(out)["plans"]], ["bench"])

    def test_S11_a_plan_that_does_not_load_is_an_error_entry_with_null_counts_not_zero(self):
        self.write_plan(graph([]))
        broken = graph([], ["T1"])
        broken["slug"] = "broken"
        broken["tickets"][1]["blocked_by"] = ["T9"]
        self.write_plan(broken, approve=False)
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        by_slug = {entry["slug"]: entry for entry in json.loads(out)["plans"]}
        self.assertEqual(by_slug["bench"]["tickets"]["total"], 1)
        bad = by_slug["broken"]
        self.assertIsNone(bad["tickets"])
        self.assertIsNone(bad["state"])
        self.assertEqual(bad["error"]["kind"], "invalid_plan")
        self.assertIn("T9", bad["error"]["message"])

    def error(self, *argv, **kwargs):
        code, out, err = self.workbench(*argv, **kwargs)
        self.assertEqual(code, 2, out + err)
        self.assertEqual(out, "", "a refusal prints no view on stdout")
        shown = json.loads(err)
        self.assertEqual(shown["schema_version"], 1)
        self.assertNotIn("plans", shown)
        self.assertNotIn("tickets", shown)
        return shown["error"]

    def test_S3_outside_a_repository_is_not_a_repo_with_the_way_out(self):
        elsewhere = self.root / "not-a-repo"
        elsewhere.mkdir()
        error = self.run_cli(
            "workbench", "plans", "--json", "--repo", str(elsewhere), "--state-dir", "x"
        )
        self.assertEqual(error[1], "")
        shown = json.loads(error[2])["error"]
        self.assertEqual(error[0], 2)
        self.assertEqual(shown["kind"], "not_a_repo")
        self.assertIn("--repo", shown["way_out"])
        cwd = os.getcwd()
        os.chdir(elsewhere)
        self.addCleanup(os.chdir, cwd)
        code, out, err = self.run_cli("workbench", "plans", "--json", "--state-dir", "x")
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(json.loads(err)["error"]["kind"], "not_a_repo")

    def test_S3_a_repository_without_plans_is_no_plans_never_an_empty_list(self):
        error = self.error("plans")
        self.assertEqual(error["kind"], "no_plans")
        self.assertIn("/start-planning", error["way_out"])
        self.assertIn("path", error["way_out"])

    def test_S3_several_plan_directories_are_ambiguous_and_all_named(self):
        self.write_plan(graph([]))
        other = graph([])
        other["slug"] = "other"
        (self.repo / ".plans").mkdir()
        path = self.repo / ".plans" / "2026-11-11-other.plan.yaml"
        path.write_text(yaml.safe_dump(other), encoding="utf-8")
        self.git(self.repo, "add", "docs/plans", ".plans")
        error = self.error("plans")
        self.assertEqual(error["kind"], "ambiguous")
        self.assertEqual(sorted(error["directories"]), [".plans", "docs/plans"])
        self.assertIn("docs/plans", error["message"])
        self.assertIn(".plans", error["message"])
        self.assertIn("path", error["way_out"])
        self.assertIn("--repo", error["way_out"])

    def test_S3_a_plan_that_does_not_exist_is_a_named_error(self):
        error = self.error("plan", str(self.plans / "nope.plan.yaml"))
        self.assertEqual(error["kind"], "no_such_plan")


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


class ReadOnlyTests(WorkbenchFixture):
    def test_S11_no_workbench_command_writes_under_the_repository_or_the_state_directory(self):
        path = self.write_plan(graph([], ["T1"]))
        # A ledger under the old `<state>/<slug>.json` key is what a run would import and a lock would guard.
        old = self.state_dir / "bench.json"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(
            json.dumps(
                {
                    "version": 1,
                    "tasks": {
                        "t1": {"packet": {"plan": str(path), "unit": "t1"}, "status": "COMPLETE"}
                    },
                    "ticket_status": {"T1": "COMPLETE"},
                }
            ),
            encoding="utf-8",
        )
        before = tree_digest(self.repo, self.state_dir, self.root / "xdg", self.root / "home")
        for argv in (("plans",), ("plan", str(path))):
            code, out, err = self.workbench(*argv)
            self.assertEqual(code, 0, err)
        self.assertEqual(
            before, tree_digest(self.repo, self.state_dir, self.root / "xdg", self.root / "home")
        )
        self.assertTrue(old.is_file())
        self.assertFalse(
            any(
                name.endswith(".lock") or name.startswith(".import")
                for _, _, files in os.walk(self.state_dir)
                for name in files
            )
        )

    def test_S11_output_holds_no_report_or_message_bodies_and_no_environment_values(self):
        path = self.write_plan(graph([]))
        self.write_ledger(
            "bench",
            {"T1": "COMPLETE"},
            tasks={
                "t1": {
                    "packet": {"unit": "t1", "plan": str(path)},
                    "status": "COMPLETE",
                    "report": {"summary": BODY},
                    "messages": [{"text": BODY}],
                    "pr": "https://github.com/o/r/pull/7",
                }
            },
        )
        shown = self.workbench("plans")[1] + self.workbench("plan", str(path))[1]
        self.assertNotIn(BODY, shown)
        self.assertNotIn("REPORT-BODY", shown)
        self.assertNotIn(SECRET, shown)
        self.assertNotIn("WORKBENCH_SECRET", shown)
