"""`workbench plan --json` carries the run's directory and its metrics, read from the driver's own events and the
per-attempt evidence, and writes nothing."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fixtures import load_script
from test_workbench import WorkbenchFixture, graph, tree_digest

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)
PR = "https://example.invalid/pull/7"


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def state(minutes, unit, task=None, gate=(None, None), status=("ACTIVE", "ACTIVE"), **extra):
    return dict(
        {
            "at": at(minutes),
            "kind": "state",
            "unit": unit,
            "task": task or unit,
            "gate": list(gate),
            "status": list(status),
        },
        **extra,
    )


def ci(minutes, gate, unit="t1", task="t1", result="failed", pr=PR):
    return {
        "at": at(minutes),
        "kind": "ci",
        "unit": unit,
        "task": task,
        "gate": gate,
        "state": result,
        "pr": pr,
    }


class MetricsFixture(WorkbenchFixture):
    def approved(self, slug, *edges):
        data = graph(*(edges or ([],)))
        data["slug"] = slug
        return self.write_plan(data)

    def run_dir(self, slug):
        return self.state_dir / load_script().repo_key(self.repo) / slug

    def write_events(self, slug, rows):
        run = self.run_dir(slug)
        run.mkdir(parents=True, exist_ok=True)
        with open(run / "events.jsonl", "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        return run

    def attempts(self, slug, rows):
        run = self.run_dir(slug)
        with open(run / "attempts.jsonl", "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def drive(self):
        """T1 runs 32 minutes in 10-minute phases and merges; T2 needs a fix round; the driver is down for 28
        minutes between two drives."""
        path = self.approved("bench", [], [])
        self.write_ledger("bench", {"T1": "COMPLETE", "T2": "IN PROGRESS"}, plan_state="RUNNING")
        run = self.write_events(
            "bench",
            [
                {"at": at(0), "kind": "start", "pid": 1},
                state(0, "t1", status=(None, "RESERVED")),
                state(0, "t2", status=(None, "RESERVED")),
                state(5, "t2", task="t2r1"),
                state(10, "t1", gate=(None, "reported")),
                state(11, "t1", gate=("reported", "converged")),
                state(11, "t1", gate=("converged", "reviewing")),
                ci(12, "reviewing"),
                ci(13, "reviewing"),
                ci(14, "reviewing"),
                {
                    "at": at(15),
                    "kind": "error",
                    "unit": "t1",
                    "task": "t1",
                    "gate": "reviewing",
                    "error": "git failed: " + "x" * 400,
                },
                state(21, "t1", gate=("reviewing", "reviewed")),
                state(21, "t1", gate=("reviewed", "ready-for-ci")),
                ci(25, "ready-for-ci"),
                state(31, "t1", gate=("ready-for-ci", "awaiting-merge")),
                state(32, "t1", gate=("awaiting-merge", "done"), status=("ACTIVE", "COMPLETE")),
                {"at": at(32), "kind": "finish"},
                {"at": at(60), "kind": "start", "pid": 2},
                state(65, "t2", task="t2r1"),
            ],
        )
        return path, run


class MetricsTests(MetricsFixture):
    def test_control_an_approved_plan_is_viewed(self):
        path = self.approved("bench")
        self.assertEqual([t["id"] for t in self.view(path)["tickets"]], ["T1"])

    def test_S29_a_plan_carries_its_run_dir_and_metrics_from_the_events(self):
        path, run = self.drive()
        shown = self.view(path)
        self.assertEqual(shown["run_dir"], str(run))
        metrics = shown["metrics"]
        self.assertAlmostEqual(metrics["wall_seconds"], (32 + 5) * 60, delta=1)
        self.assertEqual(metrics["first_pass_rate"], 0.5)
        self.assertEqual(metrics["errors"]["by_gate"], {"reviewing": 1})
        self.assertLessEqual(len(metrics["errors"]["last"]), 120)
        t1 = metrics["tickets"]["T1"]
        self.assertAlmostEqual(t1["elapsed_seconds"], 32 * 60, delta=1)
        for phase, seconds in (("work", 600), ("review", 600), ("ci", 600)):
            self.assertAlmostEqual(t1["phases"][phase], seconds, delta=1, msg=phase)
        self.assertEqual(t1["ci_failures"], 2)
        self.assertEqual(t1["fix_rounds"], 0)
        self.assertEqual(t1["review_rounds"], 1)
        self.assertIsNone(t1["tokens"])
        self.assertIsNone(t1["model"])
        self.assertEqual(metrics["tickets"]["T2"]["fix_rounds"], 1)
        lane = metrics["timeline"]["T1"]
        self.assertEqual([segment["phase"] for segment in lane], ["work", "review", "ci"])
        self.assertEqual(datetime.fromisoformat(lane[0]["start"]), T0)
        self.assertEqual(datetime.fromisoformat(lane[2]["end"]), T0 + timedelta(minutes=31))

    def test_S29_an_open_phase_has_no_end_and_no_duration(self):
        path, _ = self.drive()
        t2 = self.view(path)["metrics"]["tickets"]["T2"]
        self.assertIsNone(t2["phases"]["work"])
        self.assertIsNone(t2["phases"]["review"])
        lane = self.view(path)["metrics"]["timeline"]["T2"]
        self.assertEqual([(s["phase"], s["end"]) for s in lane], [("work", None)])

    def test_S29_a_drive_without_a_finish_ends_at_its_last_event(self):
        path = self.approved("bench")
        self.write_ledger("bench", {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        self.write_events(
            "bench",
            [
                {"at": at(0), "kind": "start", "pid": 1},
                state(1, "t1", status=(None, "RESERVED")),
                {"at": at(9), "kind": "idle"},
            ],
        )
        self.assertAlmostEqual(self.view(path)["metrics"]["wall_seconds"], 9 * 60, delta=1)

    def test_S29_errors_and_escalations_are_counted_and_the_last_error_is_cut(self):
        path = self.approved("bench")
        self.write_ledger("bench", {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        self.write_events(
            "bench",
            [
                {"at": at(0), "kind": "start", "pid": 1},
                {"at": at(1), "kind": "error", "gate": "poll", "error": "first"},
                {"at": at(2), "kind": "error", "gate": "poll", "error": "second " + "y" * 300},
                {"at": at(3), "kind": "error", "error": "no gate"},
                {"at": at(4), "kind": "escalation", "unit": "t1", "task": "t1", "cause": "ci"},
                {"at": at(5), "kind": "escalation", "unit": "t1", "task": "t1", "cause": "ci"},
                {"at": at(6), "kind": "escalation", "unit": "t1", "task": "t1", "cause": "stuck"},
                {"at": at(7), "kind": "finding", "unit": "t1", "task": "t1", "finding_kind": "x"},
                {"at": at(8), "kind": "finding", "unit": "t1", "task": "t1", "finding_kind": "y"},
            ],
        )
        metrics = self.view(path)["metrics"]
        self.assertEqual(metrics["errors"]["by_gate"], {"poll": 2, "unknown": 1})
        self.assertEqual(metrics["errors"]["last"], "no gate")
        t1 = metrics["tickets"]["T1"]
        self.assertEqual(t1["escalations"], {"ci": 2, "stuck": 1})
        self.assertEqual(t1["findings"], 2)

    def test_S29_tokens_and_model_come_from_the_attempts_and_the_route_and_are_null_when_unmeasured(
        self,
    ):
        path = self.approved("bench", [], [])
        self.write_ledger("bench", {}, plan_state="RUNNING")
        self.write_events(
            "bench",
            [
                {"at": at(0), "kind": "start", "pid": 1},
                state(1, "t1", status=(None, "RESERVED"), route={"model": "opus"}),
                state(1, "t2", status=(None, "RESERVED")),
            ],
        )
        self.attempts(
            "bench",
            [
                {"unit": "t1", "tokens": {"input": 100, "output": 10, "cached": None}},
                {"unit": "t1", "tokens": {"input": 50, "output": None, "cached": None}},
                {"unit": "t2", "tokens": {"input": None, "output": None, "cached": None}},
            ],
        )
        tickets = self.view(path)["metrics"]["tickets"]
        self.assertEqual(
            tickets["T1"]["tokens"],
            {"input": 150, "output": 10, "cached": None, "cache_write": None, "reasoning": None},
        )
        self.assertEqual(tickets["T1"]["model"], "opus")
        self.assertIsNone(tickets["T2"]["tokens"])
        self.assertIsNone(tickets["T2"]["model"])

    def test_S29_a_plan_never_driven_has_no_run_dir_and_no_metrics(self):
        shown = self.view(self.approved("idle"))
        self.assertIsNone(shown["run_dir"])
        self.assertIsNone(shown["metrics"])

    def test_S29_reading_metrics_writes_nothing(self):
        path, run = self.drive()
        self.attempts("bench", [{"unit": "t1", "tokens": {"input": 1}}])
        before = tree_digest(self.state_dir)
        self.assertIsNotNone(self.view(path)["metrics"])
        self.assertEqual(tree_digest(self.state_dir), before)
