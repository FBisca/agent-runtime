"""The workbench is worth opening: `workbench plans` lists only what matters, in the order it last moved, with what
waits on the person; `workbench plan` carries the run's metrics from the driver's own events; the shipped layout lets
the panel take the whole work area."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fixtures import load_script
from test_workbench import WorkbenchFixture, graph

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]
T0 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=timezone.utc)


def at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def dead_pid():
    for pid in range(99999, 1000, -1):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except PermissionError:
            continue
    raise AssertionError("no free pid found")


def state(minutes, unit, task=None, gate=(None, None), status=("ACTIVE", "ACTIVE")):
    return {
        "at": at(minutes),
        "kind": "state",
        "unit": unit,
        "task": task or unit,
        "gate": list(gate),
        "status": list(status),
    }


class ValueFixture(WorkbenchFixture):
    def approved(self, slug, *edges):
        data = graph(*(edges or ([],)))
        data["slug"] = slug
        return self.write_plan(data)

    def draft(self, slug):
        data = graph([])
        data["slug"] = slug
        return self.write_plan(data, approve=False)

    def broken(self, slug):
        data = graph([], ["T1"])
        data["slug"] = slug
        data["tickets"][1]["blocked_by"] = ["T9"]
        return self.write_plan(data, approve=False)

    def markdown(self, name, slug):
        path = self.plans / name
        path.write_text(
            "# Plan: %s\n\nSlug: `%s`. Status: APPROVED 2026-11-01.\n\n## Tickets\n\n"
            "| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| T1 | first | S1 | none | src/a/ | small | simple | agent | TO-DO |\n" % (slug, slug),
            encoding="utf-8",
        )
        self.git(self.repo, "add", str(path))
        return path

    def run_dir(self, slug):
        return self.state_dir / load_script().repo_key(self.repo) / slug

    def events(self, slug, rows):
        run = self.run_dir(slug)
        run.mkdir(parents=True, exist_ok=True)
        with open(run / "events.jsonl", "w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")
        return run

    def listing(self):
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, out + err)
        return json.loads(out)

    def by_slug(self, shown):
        return {entry["slug"]: entry for entry in shown["plans"]}


class ControlTests(ValueFixture):
    def test_control_an_approved_plan_is_listed_and_viewed(self):
        path = self.approved("bench")
        self.assertEqual(list(self.by_slug(self.listing())), ["bench"])
        self.assertEqual([t["id"] for t in self.view(path)["tickets"]], ["T1"])


class PlanListTests(ValueFixture):
    def test_S25_a_markdown_twin_of_a_yaml_plan_is_not_listed(self):
        yaml_path = self.approved("bench")
        self.markdown(yaml_path.name.replace(".plan.yaml", ".plan.md"), "bench")
        self.markdown("2026-11-01-old.plan.md", "old")
        shown = self.listing()
        paths = [entry["path"] for entry in shown["plans"]]
        self.assertEqual(sorted(self.by_slug(shown)), ["bench", "old"])
        self.assertNotIn(str(yaml_path.resolve()).replace(".plan.yaml", ".plan.md"), paths)
        self.assertEqual(self.by_slug(shown)["bench"]["format"], "yaml")

    def test_S26_each_plan_is_grouped_active_or_older_and_an_archived_plan_that_fails_to_load_is_older(
        self,
    ):
        self.approved("live")
        self.write_ledger("live", {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        self.approved("done")
        self.write_ledger("done", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        self.approved("ready")
        self.draft("sketch")
        self.broken("gone")
        self.write_ledger("gone", {}, plan_state="ARCHIVED")
        self.broken("authoring")
        self.markdown("2026-11-01-old.plan.md", "old")
        groups = {slug: entry["group"] for slug, entry in self.by_slug(self.listing()).items()}
        self.assertEqual(
            groups,
            {
                "live": "active",
                "ready": "active",
                "sketch": "active",
                "authoring": "active",
                "done": "older",
                "gone": "older",
                "old": "older",
            },
        )

    def test_S27_plans_carry_their_last_activity_and_are_ordered_active_first_newest_first(self):
        for slug, minutes in (("early", 5), ("late", 50)):
            self.approved(slug)
            self.write_ledger(slug, {"T1": "IN PROGRESS"}, plan_state="RUNNING")
            self.events(
                slug, [{"at": at(0), "kind": "start", "pid": os.getpid()}, state(minutes, "t1")]
            )
        self.approved("done")
        self.write_ledger("done", {"T1": "COMPLETE"}, plan_state="ARCHIVED")
        shown = self.listing()
        entries = self.by_slug(shown)
        self.assertEqual(
            datetime.fromisoformat(entries["late"]["last_activity"]), T0 + timedelta(minutes=50)
        )
        self.assertEqual(
            datetime.fromisoformat(entries["early"]["last_activity"]), T0 + timedelta(minutes=5)
        )
        order = [entry["slug"] for entry in shown["plans"]]
        self.assertLess(order.index("late"), order.index("early"))
        groups = [entry["group"] for entry in shown["plans"]]
        self.assertEqual(groups, sorted(groups, key=lambda g: g != "active"), order)


class NeedsMeTests(ValueFixture):
    def kinds(self, shown):
        return {(item["plan"], item.get("ticket"), item["kind"]) for item in shown["needs_me"]}

    def test_S28_needs_me_lists_what_waits_on_the_person_in_active_plans_only(self):
        self.approved("live", [], [])
        self.write_ledger(
            "live", {"T1": "needs you: sign off", "T2": "IN PROGRESS"}, plan_state="RUNNING"
        )
        self.events("live", [{"at": at(0), "kind": "start", "pid": os.getpid()}])
        self.approved("crashed")
        self.write_ledger("crashed", {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        self.events("crashed", [{"at": at(0), "kind": "start", "pid": dead_pid()}, state(3, "t1")])
        self.approved("ready")
        self.draft("sketch")
        self.broken("authoring")
        self.approved("done")
        self.write_ledger("done", {"T1": "needs you: old"}, plan_state="ARCHIVED")
        kinds = self.kinds(self.listing())
        self.assertIn(("live", "T1", "needs_you"), kinds)
        self.assertIn(("crashed", None, "driver_gone"), kinds)
        self.assertIn(("ready", None, "ready_to_drive"), kinds)
        self.assertIn(("sketch", None, "ready_to_approve"), kinds)
        self.assertIn(("authoring", None, "fix_plan"), kinds)
        self.assertNotIn(("live", None, "driver_gone"), kinds)
        self.assertFalse([k for k in kinds if k[0] == "done"], kinds)
        self.assertFalse([k for k in kinds if k[0] == "live" and k[1] == "T2"], kinds)

    def test_S28_needs_me_prints_no_escalation_or_report_text(self):
        self.approved("live")
        self.write_ledger("live", {"T1": "IN PROGRESS"}, plan_state="RUNNING")
        self.events(
            "live",
            [
                {"at": at(0), "kind": "start", "pid": os.getpid()},
                {
                    "at": at(1),
                    "kind": "escalation",
                    "unit": "t1",
                    "task": "t1",
                    "cause": "blocked",
                    "reason": "REPORT-BODY-MARKER the worker wrote this",
                },
            ],
        )
        code, out, err = self.workbench("plans")
        self.assertEqual(code, 0, err)
        self.assertNotIn("REPORT-BODY-MARKER", out)


class MetricsTests(ValueFixture):
    def drive(self):
        path = self.approved("bench", [], [])
        self.write_ledger("bench", {"T1": "COMPLETE", "T2": "IN PROGRESS"}, plan_state="RUNNING")
        pr = "https://example.invalid/pull/7"
        ci = lambda minutes, gate: {
            "at": at(minutes),
            "kind": "ci",
            "unit": "t1",
            "task": "t1",
            "gate": gate,
            "state": "failed",
            "pr": pr,
        }
        run = self.events(
            "bench",
            [
                {"at": at(0), "kind": "start", "pid": dead_pid()},
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
                {"at": at(60), "kind": "start", "pid": dead_pid()},
                state(65, "t2", task="t2r1"),
            ],
        )
        return path, run

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
        self.assertIsNone(t1["tokens"])
        self.assertEqual(metrics["tickets"]["T2"]["fix_rounds"], 1)
        lane = metrics["timeline"]["T1"]
        self.assertEqual([segment["phase"] for segment in lane], ["work", "review", "ci"])
        self.assertEqual(datetime.fromisoformat(lane[0]["start"]), T0)
        self.assertEqual(datetime.fromisoformat(lane[2]["end"]), T0 + timedelta(minutes=31))

    def test_S29_a_plan_never_driven_has_no_run_dir_and_no_metrics(self):
        shown = self.view(self.approved("idle"))
        self.assertIsNone(shown["run_dir"])
        self.assertIsNone(shown["metrics"])

    def test_S29_reading_metrics_writes_nothing(self):
        path, run = self.drive()
        before = sorted(p.name for p in run.iterdir())
        self.view(path)
        self.assertEqual(sorted(p.name for p in run.iterdir()), before)


class LayoutTests(ValueFixture):
    def test_S34_the_shipped_layout_lets_the_panel_take_the_whole_work_area(self):
        layout = json.loads(
            (REPO_ROOT / "herdr" / "layouts" / "workbench.json").read_text(encoding="utf-8")
        )

        def find(node, wanted):
            if isinstance(node, dict):
                if node.get("id") == wanted:
                    return node
                for child in node.get("children") or []:
                    found = find(child, wanted)
                    if found:
                        return found
            return None

        root = layout.get("root", layout)
        panel = find(root, "panel")
        self.assertIsNotNone(find(root, "main"))
        self.assertIsNotNone(panel)
        self.assertNotIn("max", panel)
        self.assertEqual(panel["size"], {"px": 520})
