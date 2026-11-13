"""`workbench plans` says what waits on the person: a `needs_me` list of {plan, ticket, kind} for the active plans, built
from the ledger and the run's events, without printing a word a worker or the driver wrote."""

import json
import os

import pytest
from test_workbench import WorkbenchFixture, at, dead_pid, state_event

pytestmark = pytest.mark.integration


def start(minutes=0, pid=None):
    return {"at": at(minutes), "kind": "start", "pid": os.getpid() if pid is None else pid}


def escalation(minutes, unit="t1", reason="blocked"):
    return {
        "at": at(minutes),
        "kind": "escalation",
        "unit": unit,
        "task": unit,
        "cause": "blocked",
        "reason": reason,
    }


class NeedsMeTests(WorkbenchFixture):
    def kinds(self, shown):
        return {(item["plan"], item["ticket"], item["kind"]) for item in shown["needs_me"]}

    def running(self, slug, rows, statuses=None, *edges):
        self.approved(slug, *edges)
        self.write_ledger(slug, statuses or {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        self.log_events(slug, rows)

    def test_S28_needs_me_lists_what_waits_on_the_person_in_active_plans_only(self):
        self.running(
            "live",
            [start()],
            {"T1": "needs you: sign off", "T2": "IN PROGRESS"},
            [],
            [],
        )
        self.running("crashed", [start(0, dead_pid()), state_event(3, "t1")])
        self.approved("ready")
        self.draft("sketch")
        self.broken("authoring")
        self.approved("done")
        self.write_ledger("done", {"T1": "needs you: old"}, plan_state="ARCHIVED")
        shown = self.listing()
        kinds = self.kinds(shown)
        self.assertEqual(
            kinds,
            {
                ("live", "T1", "needs_you"),
                ("crashed", None, "driver_gone"),
                ("ready", None, "ready_to_drive"),
                ("sketch", None, "ready_to_approve"),
                ("authoring", None, "fix_plan"),
            },
        )
        for item in shown["needs_me"]:
            self.assertEqual(set(item), {"plan", "ticket", "kind"})

    def test_S28_a_plan_that_is_not_running_or_has_a_live_driver_is_not_driver_gone(self):
        self.running("alive", [start()])
        self.running("finished", [start(0, dead_pid()), {"at": at(5), "kind": "finish"}])
        self.approved("delivered")
        self.write_ledger("delivered", {"T1": "COMPLETE"}, plan_state="DELIVERED")
        self.log_events("delivered", [start(0, dead_pid())])
        self.assertEqual(self.kinds(self.listing()), set())

    def test_S28_the_last_start_decides_the_driver(self):
        self.running("restarted", [start(0, dead_pid()), start(10), state_event(11, "t1")])
        self.assertEqual(self.kinds(self.listing()), set())

    def test_S28_an_error_since_the_last_start_is_an_error(self):
        error = {"at": at(5), "kind": "error", "unit": "t1", "gate": "poll", "error": "boom"}
        self.running("old", [start(0), error, start(10)])
        self.running("new", [start(0), error])
        self.assertEqual(self.kinds(self.listing()), {("new", None, "error")})

    def test_S28_an_unverified_merge_needs_the_person(self):
        self.approved("merged")
        path = self.write_ledger("merged", {"T1": "COMPLETE"}, plan_state="RUNNING")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["unverified"] = {"T1": {"reason": "a gate never ran"}}
        path.write_text(json.dumps(data), encoding="utf-8")
        self.log_events("merged", [start()])
        self.assertEqual(self.kinds(self.listing()), {("merged", "T1", "unverified")})

    def test_S28_the_last_escalation_of_a_unit_with_no_later_decision_or_merge_is_lead_pending(
        self,
    ):
        self.running("asked", [start(), escalation(1)])
        self.running(
            "answered",
            [start(), escalation(1), {"at": at(2), "kind": "decision", "unit": "t1"}],
        )
        self.running(
            "merged",
            [
                start(),
                escalation(1),
                {"at": at(2), "kind": "stop", "unit": "t1", "name": "w", "why": "merged"},
            ],
        )
        self.running(
            "other",
            [start(), escalation(1), {"at": at(2), "kind": "stop", "unit": "t1", "why": "swap"}],
        )
        self.running(
            "again",
            [
                start(),
                escalation(1),
                {"at": at(2), "kind": "decision", "unit": "t1"},
                escalation(3),
            ],
        )
        self.assertEqual(
            self.kinds(self.listing()),
            {
                ("asked", "T1", "lead_pending"),
                ("other", "T1", "lead_pending"),
                ("again", "T1", "lead_pending"),
            },
        )

    def test_S28_needs_me_prints_no_escalation_or_report_text(self):
        self.running("live", [start(), escalation(1, reason="REPORT-BODY-MARKER the worker wrote")])
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        self.assertNotIn("REPORT-BODY-MARKER", out)
        self.assertIn("lead_pending", out)

    def test_S28_needs_me_is_an_empty_list_when_nothing_waits(self):
        self.approved("done")
        self.write_ledger("done", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        self.assertEqual(self.listing()["needs_me"], [])

    def test_S3_a_gone_driver_keeps_driver_gone_and_needs_you_and_drops_what_only_a_driver_clears(
        self,
    ):
        error = {"at": at(5), "kind": "error", "unit": "t1", "gate": "poll", "error": "boom"}
        rows = [start(0, dead_pid()), escalation(1), error]
        self.running("gone", rows, {"T1": "needs you: sign off"})
        self.running("alive", [start(), escalation(1), error], {"T1": "needs you: sign off"})
        self.assertEqual(
            self.kinds(self.listing()),
            {
                ("gone", None, "driver_gone"),
                ("gone", "T1", "needs_you"),
                ("alive", "T1", "needs_you"),
                ("alive", None, "error"),
                ("alive", "T1", "lead_pending"),
            },
        )
