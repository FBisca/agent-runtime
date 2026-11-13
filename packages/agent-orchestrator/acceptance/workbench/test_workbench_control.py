"""The workbench as a control panel: each ticket names the agents the driver started for it, each active plan says
who works on it and which PRs wait for a merge, a dead driver's stale items leave Needs me, the merge wait is a
measured phase, and `workbench totals` sums the last seven days for the home cards and their dashboards."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from fixtures import load_script
from test_workbench import WorkbenchFixture, graph, tree_digest

pytestmark = pytest.mark.integration

PR = "https://example.invalid/pull/%d"


def dead_pid():
    for pid in range(99999, 1000, -1):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue
    raise AssertionError("no free pid found")


class PanelFixture(WorkbenchFixture):
    def setUp(self):
        super().setUp()
        # Totals read a window that ends now, so every event is written relative to the real clock.
        self.t0 = datetime.now(timezone.utc) - timedelta(days=2)

    def at(self, minutes, t0=None):
        return ((t0 or self.t0) + timedelta(minutes=minutes)).isoformat()

    def state(
        self, minutes, unit, task=None, gate=(None, None), status=("ACTIVE", "ACTIVE"), t0=None
    ):
        return {
            "at": self.at(minutes, t0),
            "kind": "state",
            "unit": unit,
            "task": task or unit,
            "gate": list(gate),
            "status": list(status),
        }

    def approved(self, slug, *edges):
        data = graph(*(edges or ([],)))
        data["slug"] = slug
        return self.write_plan(data)

    def ledger(self, slug, statuses, plan_state, tasks=None, agents=None):
        key = load_script().repo_key(self.repo)
        path = self.state_dir / key / (slug + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "tasks": tasks or {},
            "ticket_status": statuses,
            "plan": {"state": plan_state, "history": [{"state": plan_state, "at": "t"}]},
        }
        if agents is not None:
            data["agents"] = agents
        path.write_text(json.dumps(data), encoding="utf-8")

    def events(self, slug, rows):
        run = self.state_dir / load_script().repo_key(self.repo) / slug
        run.mkdir(parents=True, exist_ok=True)
        with open(run / "events.jsonl", "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def merged(self, unit, start, minutes, fix=False, t0=None):
        """A ticket dispatched at `start`, worked, reviewed, through CI and awaiting merge, merged at
        `start + minutes` (the driver leaves awaiting-merge for cleanup), then done a minute later."""
        s = start
        rows = [self.state(s, unit, status=(None, "RESERVED"), t0=t0)]
        if fix:
            rows.append(self.state(s + 1, unit, task=unit + "r1", t0=t0))
        rows += [
            self.state(s + 2, unit, gate=(None, "reported"), t0=t0),
            self.state(s + 2, unit, gate=("reported", "reviewing"), t0=t0),
            self.state(s + 4, unit, gate=("reviewing", "ready-for-ci"), t0=t0),
            self.state(s + 6, unit, gate=("ready-for-ci", "awaiting-merge"), t0=t0),
            self.state(s + minutes, unit, gate=("awaiting-merge", "cleanup"), t0=t0),
            self.state(
                s + minutes + 1,
                unit,
                gate=("cleanup", "done"),
                status=("ACTIVE", "COMPLETE"),
                t0=t0,
            ),
        ]
        return rows

    def listing(self):
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def totals(self):
        code, out, err = self.workbench("totals")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def by_slug(self, shown):
        return {entry["slug"]: entry for entry in shown["plans"]}


AGENTS = {
    "bench-t1": {"role": "worker", "unit": "t1", "address": "t9a"},
    "bench-t1-review": {"role": "reviewer", "unit": "t1", "address": "t9b"},
    "bench-t2": {"role": "worker", "unit": "t2"},
    "bench-archive": {"role": "worker", "unit": "archive", "address": "t9z"},
    "bench-gaps": {"role": "worker", "unit": "gaps", "address": "t9y"},
}


class BaselineTests(PanelFixture):
    def test_baseline_an_approved_plan_is_listed_and_viewed(self):
        path = self.approved("bench")
        self.assertEqual(list(self.by_slug(self.listing())), ["bench"])
        self.assertEqual([t["id"] for t in self.view(path)["tickets"]], ["T1"])


class AgentTests(PanelFixture):
    def test_S1_each_ticket_names_the_worker_and_reviewer_the_driver_started_for_it(self):
        path = self.approved("bench", [], [])
        self.ledger("bench", {"T1": "IN PROGRESS", "T2": "IN PROGRESS"}, "RUNNING", agents=AGENTS)
        tickets = {t["id"]: t for t in self.view(path)["tickets"]}
        self.assertEqual(
            sorted(tickets["T1"]["agents"], key=lambda a: a["name"]),
            [
                {"name": "bench-t1", "role": "worker", "address": "t9a"},
                {"name": "bench-t1-review", "role": "reviewer", "address": "t9b"},
            ],
        )
        self.assertEqual(
            tickets["T2"]["agents"], [{"name": "bench-t2", "role": "worker", "address": None}]
        )

    def test_S1_agents_join_tickets_by_their_ledger_unit_not_by_name(self):
        path = self.approved("bench", *([[]] * 10))
        agents = {
            "renamed-worker": {"role": "worker", "unit": "t10", "address": "t8a"},
            "bench-t1-review": {"role": "reviewer", "unit": "t10", "address": "t8b"},
        }
        self.ledger("bench", {}, "RUNNING", agents=agents)
        tickets = {t["id"]: t for t in self.view(path)["tickets"]}
        self.assertEqual(tickets["T1"]["agents"], [])
        self.assertEqual(
            sorted((a["name"], a["role"]) for a in tickets["T10"]["agents"]),
            [("bench-t1-review", "reviewer"), ("renamed-worker", "worker")],
        )

    def test_S1_a_ticket_no_agent_was_started_for_has_none(self):
        path = self.approved("bench")
        self.ledger("bench", {"T1": "TO-DO"}, "RUNNING")
        self.assertEqual(self.view(path)["tickets"][0]["agents"], [])

    def test_S2_an_active_plan_lists_its_ticket_agents_and_the_prs_awaiting_merge(self):
        self.approved("bench", [], [], [])
        tasks = {
            "t2": {
                "packet": {"task_id": "t2", "unit": "t2"},
                "status": "ACTIVE",
                "gate": "awaiting-merge",
                "pr": PR % 7,
            },
            "t3": {
                "packet": {"task_id": "t3", "unit": "t3"},
                "status": "ACTIVE",
                "gate": "ready-for-ci",
                "pr": PR % 8,
            },
        }
        self.ledger(
            "bench",
            {"T1": "IN PROGRESS", "T2": "IN PROGRESS", "T3": "IN PROGRESS"},
            "RUNNING",
            tasks=tasks,
            agents=AGENTS,
        )
        entry = self.by_slug(self.listing())["bench"]
        self.assertEqual(
            sorted(entry["agents"], key=lambda a: a["name"]),
            [
                {"name": "bench-t1", "role": "worker", "ticket": "T1", "address": "t9a"},
                {"name": "bench-t1-review", "role": "reviewer", "ticket": "T1", "address": "t9b"},
                {"name": "bench-t2", "role": "worker", "ticket": "T2", "address": None},
            ],
        )
        self.assertEqual(entry["awaiting_merge"], [{"ticket": "T2", "pr": PR % 7}])

    def test_S2_an_older_plan_lists_no_agents_and_nothing_awaiting_merge(self):
        self.approved("done")
        self.ledger("done", {"T1": "COMPLETE"}, "ARCHIVED", agents=AGENTS)
        entry = self.by_slug(self.listing())["done"]
        self.assertEqual((entry["agents"], entry["awaiting_merge"]), ([], []))


class NeedsMeTests(PanelFixture):
    def escalated(self, slug, pid):
        self.approved(slug, [], [])
        self.ledger(slug, {"T1": "needs you: sign off", "T2": "IN PROGRESS"}, "RUNNING")
        self.events(
            slug,
            [
                {"at": self.at(0), "kind": "start", "pid": pid},
                self.state(1, "t2"),
                {
                    "at": self.at(2),
                    "kind": "escalation",
                    "unit": "t2",
                    "task": "t2",
                    "cause": "gate",
                    "reason": "x",
                },
                {"at": self.at(3), "kind": "error", "unit": "t2", "task": "t2", "error": "boom"},
            ],
        )

    def mine(self, slug):
        return {
            (i.get("ticket"), i["kind"]) for i in self.listing()["needs_me"] if i["plan"] == slug
        }

    def test_S3_a_dead_driver_drops_its_lead_and_error_items_and_keeps_the_rest(self):
        self.escalated("crashed", dead_pid())
        self.assertEqual(self.mine("crashed"), {(None, "driver_gone"), ("T1", "needs_you")})

    def test_S3_a_live_driver_keeps_its_lead_and_error_items(self):
        self.escalated("live", os.getpid())
        kinds = self.mine("live")
        self.assertIn(("T2", "lead_pending"), kinds)
        self.assertIn((None, "error"), kinds)
        self.assertIn(("T1", "needs_you"), kinds)


class MergePhaseTests(PanelFixture):
    def test_S4_the_wait_for_a_merge_is_its_own_phase_and_ends_at_the_merge(self):
        path = self.approved("bench")
        self.ledger("bench", {"T1": "COMPLETE"}, "RUNNING")
        self.events(
            "bench",
            [
                {"at": self.at(0), "kind": "start", "pid": dead_pid()},
                *self.merged("t1", 0, 30),
                {"at": self.at(32), "kind": "finish"},
            ],
        )
        metrics = self.view(path)["metrics"]
        phases = metrics["tickets"]["T1"]["phases"]
        self.assertAlmostEqual(phases["merge"], 24 * 60, delta=1)
        self.assertAlmostEqual(phases["ci"], 2 * 60, delta=1)
        lane = metrics["timeline"]["T1"]
        self.assertEqual([seg["phase"] for seg in lane[-2:]], ["ci", "merge"])
        self.assertEqual(lane[-2]["end"], lane[-1]["start"])
        self.assertEqual(datetime.fromisoformat(lane[-1]["end"]), self.t0 + timedelta(minutes=30))


class TotalsTests(PanelFixture):
    def drive(self):
        """In the window: alpha merges T1 (20m, measured) and T2 (60m, one fix round), beta merges T1 (40m), and
        gamma, archived since, merges T1 (30m). Outside it: delta merged eight days ago. Escalations: two in the
        window, one ten days ago. A plan never driven, and an unmerged ticket with tokens, count for nothing."""
        eight = datetime.now(timezone.utc) - timedelta(days=8)
        self.approved("alpha", [], [], [])
        self.ledger(
            "alpha",
            {"T1": "COMPLETE", "T2": "COMPLETE", "T3": "IN PROGRESS"},
            "RUNNING",
            tasks={
                "t1": {
                    "packet": {"task_id": "t1", "unit": "t1"},
                    "status": "COMPLETE",
                    "pr": PR % 1,
                },
                "t2": {
                    "packet": {"task_id": "t2", "unit": "t2"},
                    "status": "COMPLETE",
                    "pr": PR % 2,
                },
            },
        )

        def usage(minutes, unit, assignment, out):
            return [
                {
                    "at": self.at(minutes),
                    "kind": "agent",
                    "unit": unit,
                    "assignment_id": assignment,
                },
                {
                    "at": self.at(minutes),
                    "kind": "usage",
                    "assignment_id": assignment,
                    "usage": {"input_tokens": 100, "output_tokens": out},
                },
            ]

        self.events(
            "alpha",
            [
                {
                    "at": self.at(-60 * 24 * 8),
                    "kind": "escalation",
                    "unit": "t1",
                    "task": "t1",
                    "cause": "idle",
                    "reason": "x",
                },
                {"at": self.at(-1), "kind": "start", "pid": dead_pid()},
                *usage(0, "t1", "a1", 40),
                *usage(0, "t3", "a3", 999),
                *self.merged("t1", 0, 20),
                *self.merged("t2", 0, 60, fix=True),
                self.state(0, "t3", status=(None, "RESERVED")),
                {
                    "at": self.at(5),
                    "kind": "escalation",
                    "unit": "t2",
                    "task": "t2",
                    "cause": "gate",
                    "reason": "SECRET-REASON",
                },
                {"at": self.at(62), "kind": "finish"},
            ],
        )
        self.approved("beta")
        self.ledger("beta", {"T1": "COMPLETE"}, "RUNNING")
        self.events(
            "beta",
            [
                {"at": self.at(0), "kind": "start", "pid": dead_pid()},
                *self.merged("t1", 0, 40),
                {
                    "at": self.at(7),
                    "kind": "escalation",
                    "unit": "t1",
                    "task": "t1",
                    "cause": "blocked",
                    "reason": "x",
                },
                {"at": self.at(42), "kind": "finish"},
            ],
        )
        self.approved("gamma")
        self.ledger("gamma", {"T1": "COMPLETE"}, "ARCHIVED")
        self.events(
            "gamma",
            [
                {"at": self.at(0), "kind": "start", "pid": dead_pid()},
                *self.merged("t1", 0, 30),
                {"at": self.at(32), "kind": "finish"},
            ],
        )
        self.approved("delta")
        self.ledger("delta", {"T1": "COMPLETE"}, "RUNNING")
        self.events(
            "delta",
            [
                {"at": self.at(0, eight), "kind": "start", "pid": dead_pid()},
                *self.merged("t1", 0, 30, t0=eight),
                {"at": self.at(32, eight), "kind": "finish"},
            ],
        )
        self.approved("idle")

    def test_S5_the_cards_sum_the_last_seven_days(self):
        self.drive()
        shown = self.totals()
        self.assertEqual(shown["window"]["days"], 7)
        cards = shown["cards"]
        self.assertEqual(cards["merged"], 4)
        self.assertAlmostEqual(cards["first_pass_rate"], 3 / 4, places=3)
        self.assertEqual(cards["escalations"], 2)
        self.assertEqual(cards["tokens"], {"input": 100, "output": 40, "measured": 1, "tickets": 4})
        # Dispatch to merge: 20, 30, 40 and 60 minutes; the median of an even count is the mean of the middle two.
        self.assertAlmostEqual(cards["median_ticket_seconds"], 35 * 60, delta=1)

    def test_S5_nothing_in_the_window_is_zero_and_unknown_never_a_guess(self):
        self.approved("idle")
        cards = self.totals()["cards"]
        self.assertEqual((cards["merged"], cards["escalations"]), (0, 0))
        self.assertIsNone(cards["first_pass_rate"])
        self.assertIsNone(cards["median_ticket_seconds"])
        self.assertEqual(
            cards["tokens"], {"input": None, "output": None, "measured": 0, "tickets": 0}
        )

    def test_S6_each_card_has_its_dashboard(self):
        self.drive()
        shown = self.totals()
        steps = shown["time"]["steps"]
        self.assertEqual(sorted(steps), ["ci", "merge", "review", "work"])
        # Merge waits: 14, 24, 34 and 54 minutes.
        self.assertAlmostEqual(steps["merge"]["max"], 54 * 60, delta=1)
        self.assertAlmostEqual(steps["merge"]["median"], 29 * 60, delta=1)
        self.assertAlmostEqual(steps["review"]["median"], 2 * 60, delta=1)
        slowest = shown["time"]["slowest"]
        self.assertEqual(
            [(s["plan"], s["ticket"]) for s in slowest],
            [("alpha", "T2"), ("beta", "T1"), ("gamma", "T1"), ("alpha", "T1")],
        )
        merged = shown["merged"]
        self.assertEqual(sum(day["count"] for day in merged["by_day"]), 4)
        tickets = {(t["plan"], t["ticket"]): t for t in merged["tickets"]}
        self.assertEqual(tickets[("alpha", "T1")]["pr"], PR % 1)
        self.assertIsNone(tickets[("beta", "T1")]["pr"])
        self.assertEqual(
            datetime.fromisoformat(tickets[("alpha", "T1")]["merged_at"]),
            self.t0 + timedelta(minutes=20),
        )
        first = shown["first_pass"]
        self.assertAlmostEqual(first["by_plan"]["alpha"], 0.5, places=3)
        self.assertEqual(
            [(t["plan"], t["ticket"], t["fix_rounds"]) for t in first["fixed"]],
            [("alpha", "T2", 1)],
        )
        escalations = shown["escalations"]
        self.assertEqual(escalations["by_cause"], {"gate": 1, "blocked": 1})
        self.assertEqual(escalations["by_plan"], {"alpha": 1, "beta": 1})
        self.assertEqual(
            [(e["plan"], e["cause"]) for e in escalations["latest"]],
            [("beta", "blocked"), ("alpha", "gate")],
        )
        tokens = shown["tokens"]
        self.assertEqual(tokens["by_plan"]["alpha"]["output"], 40)
        self.assertEqual([(t["plan"], t["ticket"]) for t in tokens["top"]], [("alpha", "T1")])

    def test_S6_totals_print_no_escalation_text_and_write_nothing(self):
        self.drive()
        before = tree_digest(self.state_dir, self.repo)
        code, out, err = self.workbench("totals")
        self.assertEqual(code, 0, err)
        self.assertNotIn("SECRET-REASON", out)
        self.assertEqual(tree_digest(self.state_dir, self.repo), before)
