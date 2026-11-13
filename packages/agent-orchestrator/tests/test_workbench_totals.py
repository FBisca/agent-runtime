"""`workbench totals --json` sums the last seven days of every plan of the repository for the home cards, with each
card's dashboard detail, from the plans' ledgers and events, and writes nothing."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from test_workbench import tree_digest
from test_workbench_metrics import MetricsFixture

pytestmark = pytest.mark.integration

PR = "https://example.invalid/pull/%d"


class TotalsFixture(MetricsFixture):
    def setUp(self):
        super().setUp()
        # Totals read a window that ends now, so every event is written relative to the real clock.
        self.t0 = datetime.now(timezone.utc) - timedelta(days=2)

    def stamp(self, minutes, t0=None):
        return ((t0 or self.t0) + timedelta(minutes=minutes)).isoformat()

    def step(
        self, minutes, unit, task=None, gate=(None, None), status=("ACTIVE", "ACTIVE"), t0=None
    ):
        return {
            "at": self.stamp(minutes, t0),
            "kind": "state",
            "unit": unit,
            "task": task or unit,
            "gate": list(gate),
            "status": list(status),
        }

    def merged(self, unit, minutes, fix=False, t0=None):
        """A ticket dispatched at minute 0, worked, reviewed, through CI and awaiting merge from minute 6, merged at
        `minutes` (the driver leaves awaiting-merge for cleanup) and done a minute later."""
        rows = [self.step(0, unit, status=(None, "RESERVED"), t0=t0)]
        if fix:
            rows.append(self.step(1, unit, task=unit + "r1", t0=t0))
        rows += [
            self.step(2, unit, gate=(None, "reported"), t0=t0),
            self.step(2, unit, gate=("reported", "reviewing"), t0=t0),
            self.step(4, unit, gate=("reviewing", "ready-for-ci"), t0=t0),
            self.step(6, unit, gate=("ready-for-ci", "awaiting-merge"), t0=t0),
            self.step(minutes, unit, gate=("awaiting-merge", "cleanup"), t0=t0),
            self.step(
                minutes + 1, unit, gate=("cleanup", "done"), status=("ACTIVE", "COMPLETE"), t0=t0
            ),
        ]
        return rows

    def escalation(self, minutes, unit, cause, t0=None):
        return {
            "at": self.stamp(minutes, t0),
            "kind": "escalation",
            "unit": unit,
            "task": unit,
            "cause": cause,
            "reason": "SECRET-REASON",
        }

    def totals(self):
        code, out, err = self.workbench("totals")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def drive(self):
        """In the window: alpha merges T1 (20m, measured) and T2 (60m, one fix round), beta merges T1 (40m) and
        gamma, archived since, merges T1 (30m). Outside it: delta merged eight days ago. Escalations: two in the
        window, one ten days ago. A plan never driven, and an unmerged ticket with tokens, count for nothing."""
        eight = datetime.now(timezone.utc) - timedelta(days=8)
        self.approved("alpha", [], [], [])
        tasks = {
            unit: {"packet": {"task_id": unit, "unit": unit}, "status": "COMPLETE", "pr": PR % n}
            for n, unit in ((1, "t1"), (2, "t2"))
        }
        self.write_ledger(
            "alpha",
            {"T1": "COMPLETE", "T2": "COMPLETE", "T3": "IN PROGRESS"},
            plan_state="RUNNING",
            tasks=tasks,
        )

        def usage(unit, assignment, out):
            return [
                {"at": self.stamp(0), "kind": "agent", "unit": unit, "assignment_id": assignment},
                {
                    "at": self.stamp(0),
                    "kind": "usage",
                    "assignment_id": assignment,
                    "usage": {"input_tokens": 100, "output_tokens": out},
                },
            ]

        self.write_events(
            "alpha",
            [
                self.escalation(-60 * 24 * 8, "t1", "idle"),
                {"at": self.stamp(-1), "kind": "start", "pid": 1},
                *usage("t1", "a1", 40),
                *usage("t3", "a3", 999),
                *self.merged("t1", 20),
                *self.merged("t2", 60, fix=True),
                self.step(0, "t3", status=(None, "RESERVED")),
                self.escalation(5, "t2", "gate"),
                {"at": self.stamp(62), "kind": "finish"},
            ],
        )
        self.approved("beta")
        self.write_ledger("beta", {"T1": "COMPLETE"}, plan_state="RUNNING")
        self.write_events(
            "beta",
            [
                {"at": self.stamp(0), "kind": "start", "pid": 1},
                *self.merged("t1", 40),
                self.escalation(7, "t1", "blocked"),
                {"at": self.stamp(42), "kind": "finish"},
            ],
        )
        self.approved("gamma")
        self.write_ledger("gamma", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        self.write_events(
            "gamma",
            [
                {"at": self.stamp(0), "kind": "start", "pid": 1},
                *self.merged("t1", 30),
                {"at": self.stamp(32), "kind": "finish"},
            ],
        )
        self.approved("delta")
        self.write_ledger("delta", {"T1": "COMPLETE"}, plan_state="RUNNING")
        self.write_events(
            "delta",
            [
                {"at": self.stamp(0, eight), "kind": "start", "pid": 1},
                *self.merged("t1", 30, t0=eight),
                {"at": self.stamp(32, eight), "kind": "finish"},
            ],
        )
        self.approved("idle")


class TotalsTests(TotalsFixture):
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

    def test_S5_the_median_of_an_odd_count_is_the_middle_one(self):
        self.approved("alpha", [], [], [])
        self.write_ledger("alpha", {"T1": "COMPLETE"}, plan_state="RUNNING")
        self.write_events(
            "alpha",
            [
                {"at": self.stamp(0), "kind": "start", "pid": 1},
                *self.merged("t1", 20),
                *self.merged("t2", 50),
                *self.merged("t3", 30),
            ],
        )
        self.assertAlmostEqual(self.totals()["cards"]["median_ticket_seconds"], 30 * 60, delta=1)

    def test_S5_nothing_in_the_window_is_zero_and_unknown_never_a_guess(self):
        self.approved("idle")
        cards = self.totals()["cards"]
        self.assertEqual((cards["merged"], cards["escalations"]), (0, 0))
        self.assertIsNone(cards["first_pass_rate"])
        self.assertIsNone(cards["median_ticket_seconds"])
        self.assertEqual(
            cards["tokens"], {"input": None, "output": None, "measured": 0, "tickets": 0}
        )

    def test_S5_a_plan_whose_file_no_longer_loads_still_counts_what_it_merged(self):
        path = self.approved("gone")
        self.write_ledger("gone", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        self.write_events(
            "gone",
            [
                {"at": self.stamp(0), "kind": "start", "pid": 1},
                *self.merged("t1", 30),
                {"at": self.stamp(32), "kind": "finish"},
            ],
        )
        # A ticket blocked by one that does not exist: the file keeps its slug but no longer loads.
        self.edit(path, lambda data: data["tickets"][0].update(blocked_by=["T9"]))
        self.assertEqual(self.totals()["cards"]["merged"], 1)

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

    def test_S6_the_lists_are_cut_to_five_slowest_and_ten_latest_escalations(self):
        self.approved("alpha", *([[]] * 7))
        self.write_ledger("alpha", {}, plan_state="RUNNING")
        rows = [{"at": self.stamp(0), "kind": "start", "pid": 1}]
        for number in range(1, 8):
            rows += self.merged("t%d" % number, 10 + number)
        rows += [self.escalation(minute, "t1", "gate") for minute in range(1, 13)]
        self.write_events("alpha", rows)
        shown = self.totals()
        self.assertEqual(len(shown["time"]["slowest"]), 5)
        self.assertEqual(shown["time"]["slowest"][0]["ticket"], "T7")
        self.assertEqual(len(shown["escalations"]["latest"]), 10)
        self.assertEqual(shown["escalations"]["by_cause"], {"gate": 12})

    def test_S6_totals_print_no_escalation_text_and_write_nothing(self):
        self.drive()
        before = tree_digest(self.state_dir, self.repo)
        code, out, err = self.workbench("totals")
        self.assertEqual(code, 0, err)
        self.assertNotIn("SECRET-REASON", out)
        self.assertEqual(tree_digest(self.state_dir, self.repo), before)
