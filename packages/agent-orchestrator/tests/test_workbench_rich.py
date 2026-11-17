"""`workbench totals --json` carries what the rich dashboards draw: a daily series per card over every UTC date the
window touches, last week's cards and the change, findings by reason and the fix-round histogram, escalations by unit
and by plan and cause, and each merged ticket's phase times; every field it had before stays as it was, but
`merged.by_day`, which now lists every date with its plans."""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from test_workbench_totals import TotalsFixture

pytestmark = pytest.mark.integration


def window_days(window):
    """Every UTC date the window touches, oldest first: eight when it starts mid-day."""
    start = datetime.fromisoformat(window["start"]).date()
    end = datetime.fromisoformat(window["end"]).date()
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


class RichFixture(TotalsFixture):
    def setUp(self):
        super().setUp()
        # Noon two days ago: the drive's merges all fall on one UTC day, whatever the hour the suite runs.
        self.t0 = (datetime.now(timezone.utc) - timedelta(days=2)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )

    def drive(self):
        """The base drive, plus alpha's T2 fix round raising three findings (two review concerns, one gate) and an
        escalation of alpha's archive unit, which is no ticket."""
        super().drive()
        log = self.run_dir("alpha") / "events.jsonl"
        extra = [
            {
                "at": self.stamp(3),
                "kind": "finding",
                "unit": "t2",
                "task": "t2",
                "source": "review",
                "finding_kind": "concern",
                "id": "R1",
                "summary": "one",
            },
            {
                "at": self.stamp(3),
                "kind": "finding",
                "unit": "t2",
                "task": "t2",
                "source": "review",
                "finding_kind": "concern",
                "id": "R2",
                "summary": "two",
            },
            {
                "at": self.stamp(3),
                "kind": "finding",
                "unit": "t2",
                "task": "t2",
                "source": "verify",
                "finding_kind": "blocking",
                "id": "R1",
                "summary": "three",
            },
            self.escalation(8, "archive", "ci"),
        ]
        with open(log, "a", encoding="utf-8") as stream:
            for row in extra:
                stream.write(json.dumps(row) + "\n")


class BaselineTests(RichFixture):
    def test_baseline_totals_answer_the_cards_they_answer_today(self):
        self.drive()
        cards = self.totals()["cards"]
        self.assertEqual(cards["merged"], 4)


class SeriesTests(RichFixture):
    def test_S1_every_card_has_a_seven_day_series_ending_today_with_empty_days_present(self):
        self.drive()
        answer = self.totals()
        days = window_days(answer["window"])
        merged = answer["merged"]["by_day"]
        self.assertEqual([d["day"] for d in merged], days)
        self.assertEqual(sum(d["count"] for d in merged), answer["cards"]["merged"])
        for d in merged:
            self.assertEqual(sum(d["by_plan"].values()), d["count"], d)
        busy = next(d for d in merged if d["count"])
        self.assertEqual(busy["by_plan"], {"alpha": 2, "beta": 1, "gamma": 1})
        for section, field in (("first_pass", "rate"), ("tokens", "output"), ("time", "median")):
            series = answer[section]["by_day"]
            self.assertEqual([d["day"] for d in series], days, section)
            quiet = [d for d in series if d["day"] != busy["day"]]
            # A day with nothing merged has no rate, no tokens and no time: unknown, never 0.
            self.assertTrue(all(d[field] is None for d in quiet), (section, quiet))
        escalations = answer["escalations"]["by_day"]
        self.assertEqual([d["day"] for d in escalations], days)
        self.assertEqual(sum(d["count"] for d in escalations), answer["cards"]["escalations"])
        for d in escalations:
            self.assertEqual(sum(d["by_cause"].values()), d["count"], d)

    def test_S1_the_busy_day_carries_its_rate_tokens_and_median(self):
        self.drive()
        answer = self.totals()
        day = next(d["day"] for d in answer["merged"]["by_day"] if d["count"])
        rate = next(d for d in answer["first_pass"]["by_day"] if d["day"] == day)["rate"]
        self.assertEqual(rate, 0.75)
        output = next(d for d in answer["tokens"]["by_day"] if d["day"] == day)["output"]
        self.assertEqual(output, 40)
        median = next(d for d in answer["time"]["by_day"] if d["day"] == day)["median"]
        # Dispatch to merge: 20, 30, 40 and 60 minutes.
        self.assertAlmostEqual(median, 35 * 60, delta=1)


class PreviousWeekTests(RichFixture):
    def test_S2_last_weeks_cards_come_from_the_seven_days_before_the_window_with_the_change(self):
        self.drive()
        # Delta's log was last written eight days ago, as a plan last driven then: last week must still read it.
        eight = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
        os.utime(self.run_dir("delta") / "events.jsonl", (eight, eight))
        answer = self.totals()
        window = answer["window"]
        self.assertEqual(window["previous"]["end"], window["start"])
        start = datetime.fromisoformat(window["previous"]["start"])
        self.assertEqual(datetime.fromisoformat(window["start"]) - start, timedelta(days=7))
        previous = answer["previous"]
        self.assertEqual(previous["merged"], 1)
        self.assertEqual(previous["escalations"], 1)
        self.assertEqual(previous["first_pass_rate"], 1.0)
        self.assertAlmostEqual(previous["median_ticket_seconds"], 30 * 60, delta=1)
        change = answer["change"]
        self.assertEqual(change["merged"], 3.0)
        self.assertEqual(change["escalations"], 2.0)
        self.assertAlmostEqual(change["first_pass_rate"], -0.25, places=3)
        self.assertAlmostEqual(change["median_ticket_seconds"], (35 - 30) / 30, places=3)
        # Delta's merge was not measured: last week has no output tokens, so there is no change to show, not -100%.
        self.assertIsNone(previous["tokens"]["output"])
        self.assertIsNone(change["tokens"])

    def test_S2_a_first_week_has_no_previous_value_and_no_change(self):
        self.approved("solo")
        self.write_ledger("solo", {"T1": "COMPLETE"}, plan_state="RUNNING")
        self.write_events(
            "solo", [{"at": self.stamp(0), "kind": "start", "pid": 1}, *self.merged("t1", 20)]
        )
        answer = self.totals()
        self.assertEqual(answer["previous"]["merged"], 0)
        self.assertIsNone(answer["previous"]["first_pass_rate"])
        self.assertIsNone(answer["change"]["merged"])
        self.assertIsNone(answer["change"]["first_pass_rate"])


class ReasonTests(RichFixture):
    def test_S3_fixed_tickets_carry_their_findings_by_reason_and_the_week_sums_them(self):
        self.drive()
        answer = self.totals()["first_pass"]
        fixed = next(f for f in answer["fixed"] if (f["plan"], f["ticket"]) == ("alpha", "T2"))
        self.assertEqual(fixed["reasons"], {"review/concern": 2, "verify/blocking": 1})
        self.assertEqual(fixed["findings"], 3)
        self.assertEqual(answer["by_reason"], {"review/concern": 2, "verify/blocking": 1})

    def test_S3_the_fix_round_histogram_counts_every_merged_ticket(self):
        self.drive()
        rounds = self.totals()["first_pass"]["rounds"]
        self.assertEqual(rounds, {"0": 3, "1": 1, "2": 0, "3+": 0})


class EscalationTests(RichFixture):
    def test_S4_each_escalation_names_its_unit_and_a_plan_level_one_has_no_ticket(self):
        self.drive()
        latest = self.totals()["escalations"]["latest"]
        archive = next(e for e in latest if e["cause"] == "ci")
        self.assertEqual(
            (archive["plan"], archive["unit"], archive["ticket"]), ("alpha", "archive", None)
        )
        gate = next(e for e in latest if e["cause"] == "gate")
        self.assertEqual((gate["unit"], gate["ticket"]), ("T2", "T2"))

    def test_S4_escalations_are_counted_by_plan_and_cause(self):
        self.drive()
        matrix = self.totals()["escalations"]["by_plan_cause"]
        self.assertEqual(matrix, {"alpha": {"gate": 1, "ci": 1}, "beta": {"blocked": 1}})


class PhaseTests(RichFixture):
    def test_S5_merged_tickets_carry_their_phase_times_slowest_first(self):
        self.drive()
        time = self.totals()["time"]
        tickets = time["tickets"]
        self.assertEqual(
            [(t["plan"], t["ticket"]) for t in tickets],
            [("alpha", "T2"), ("beta", "T1"), ("gamma", "T1"), ("alpha", "T1")],
        )
        slowest = tickets[0]
        self.assertEqual(slowest["seconds"], time["slowest"][0]["seconds"])
        self.assertEqual(slowest["pr"], "https://example.invalid/pull/2")
        self.assertEqual(set(slowest["phases"]), {"work", "review", "ci", "merge"})
        # Awaiting merge from minute 6 to the merge at minute 60.
        self.assertAlmostEqual(slowest["phases"]["merge"], 54 * 60, delta=90)

    def test_S5_each_step_carries_its_ninetieth_percentile(self):
        self.drive()
        steps = self.totals()["time"]["steps"]
        for name in ("work", "review", "ci", "merge"):
            self.assertIn("p90", steps[name], name)
            self.assertGreaterEqual(steps[name]["p90"], steps[name]["median"], name)


class KeptTests(RichFixture):
    def test_S6_the_answer_keeps_its_version_and_every_field_it_had(self):
        self.drive()
        answer = self.totals()
        self.assertEqual(answer["schema_version"], 1)
        self.assertEqual(answer["window"]["days"], 7)
        self.assertEqual(
            set(answer["cards"]),
            {"merged", "first_pass_rate", "escalations", "tokens", "median_ticket_seconds"},
        )
        self.assertEqual(
            answer["cards"]["tokens"], {"input": 100, "output": 40, "measured": 1, "tickets": 4}
        )
        for step in ("work", "review", "ci", "merge"):
            self.assertLessEqual({"median", "max"}, set(answer["time"]["steps"][step]), step)
        self.assertLessEqual({"plan", "ticket", "pr", "seconds"}, set(answer["time"]["slowest"][0]))
        self.assertLessEqual(
            {"plan", "ticket", "pr", "merged_at"}, set(answer["merged"]["tickets"][0])
        )
        self.assertEqual(answer["first_pass"]["by_plan"]["alpha"], 0.5)
        self.assertLessEqual(
            {"plan", "ticket", "fix_rounds", "findings"}, set(answer["first_pass"]["fixed"][0])
        )
        self.assertEqual(answer["escalations"]["by_cause"], {"gate": 1, "blocked": 1, "ci": 1})
        self.assertEqual(answer["escalations"]["by_plan"], {"alpha": 2, "beta": 1})
        self.assertLessEqual(
            {"plan", "ticket", "cause", "at"}, set(answer["escalations"]["latest"][0])
        )
        self.assertEqual(answer["tokens"]["by_plan"]["alpha"]["measured"], 1)
        self.assertIn("by_model", answer["tokens"])
        self.assertLessEqual(
            {"plan", "ticket", "input", "output", "model"}, set(answer["tokens"]["top"][0])
        )
