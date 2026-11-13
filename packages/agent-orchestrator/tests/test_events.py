"""events.jsonl: one line per state change, finding, decision and idle tick; metrics.json rebuilt from it."""

import json
import subprocess
import unittest

import pytest
from fixtures import DRIVER, load_script, report_wait, resume_id
from test_archive import ArchiveFixture
from test_decider import DeciderFixture
from test_gates import DEFERRED, MAJOR


def subsequence(wanted, actual):
    rest = iter(actual)
    return all(item in rest for item in wanted)


class EventTests(ArchiveFixture, unittest.TestCase):
    @pytest.mark.integration
    def test_S27_every_state_change_is_one_json_line_logged_before_the_next_step(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        self.waits(self.ticket_merged())
        self.run_driver()
        states = [e for e in self.events("state") if e["task"] == "t1"]
        gates = [e["gate"][1] for e in states if e["gate"][0] != e["gate"][1]]
        self.assertTrue(
            subsequence(
                [
                    "reported",
                    "converged",
                    "reviewing",
                    "reviewed",
                    "ready-for-ci",
                    "awaiting-merge",
                    "cleanup",
                    "done",
                ],
                gates,
            ),
            gates,
        )
        statuses = [e["status"][1] for e in states if e["status"][0] != e["status"][1]]
        self.assertTrue(
            subsequence(["RESERVED", "ACTIVE", "PARTIAL", "COMPLETE"], statuses), statuses
        )
        self.assertEqual({e["unit"] for e in states}, {"t1"})
        self.assertTrue(all(e["at"] for e in self.events()))
        # the reviewer was spawned only after the converge pass was on the log
        reviewer_spawn = next(
            c for c in self.calls("spawn") if c["args"]["name"] == "demo-t1-review"
        )
        logged = [json.loads(line) for line in reviewer_spawn["events"]]
        last = [e for e in logged if e["kind"] == "state" and e["task"] == "t1"][-1]
        self.assertEqual(last["gate"][1], "converged")
        self.assert_intents_precede_spawns()

    @pytest.mark.integration
    def test_S27_findings_are_logged_with_their_kind_and_source(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        review = self.review(verdict="block", major=[MAJOR], deferred=[DEFERRED])
        self.waits([self.worker_done(tests=True), self.reviewer_done(review)])
        self.run_driver()
        findings = [(e["source"], e["finding_kind"], e["id"]) for e in self.events("finding")]
        self.assertEqual(findings, [("review", "deferred", "D1"), ("review", "blocking", "M1")])
        self.assertEqual(
            [(e["unit"], e["source"]) for e in self.events("round")], [("t1", "review")]
        )

    @pytest.mark.integration
    def test_S27_verify_failures_and_ci_lines_are_findings_too(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        self.waits([self.worker_done(), self.worker_done("t1r1", tests=True)])
        self.run_driver()
        found = self.events("finding")
        self.assertTrue(
            found
            and all(e["source"] == "verify" and e["finding_kind"] == "blocking" for e in found)
        )

    @pytest.mark.integration
    def test_S27_idle_ticks_are_logged(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        self.prs(["OPEN"] * 4 + ["MERGED"])
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertGreaterEqual(len(self.events("idle")), 2)

    @pytest.mark.integration
    def test_S27_metrics_are_rebuilt_from_events_after_a_crash(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        worker = self.worker_done(tests=True)
        worker["assignments"][0]["usage"] = {"input_tokens": 100, "output_tokens": 20}
        reviewer = self.reviewer_done(self.review(verdict="block", major=[MAJOR]))
        fix = self.worker_done("t1r1")
        again = report_wait("demo-t1-review", "complete", id=resume_id("demo-t1-review"))
        again["write"] = [[str(self.run_dir / "t1" / "review.2.json"), self.review()]]
        # Every assignment reports, so the ticket total is a sum and not null.
        for step in (reviewer, fix, again):
            step["assignments"][0]["usage"] = {"input_tokens": 0, "output_tokens": 0}
        self.waits([worker, reviewer, fix, again])
        self.run_driver()
        metrics_path = self.run_dir / "metrics.json"
        written = json.loads(metrics_path.read_text(encoding="utf-8"))
        self.assertEqual(written["tickets"]["t1"]["review_rounds"], 1)
        self.assertEqual(
            written["tickets"]["t1"]["usage"], {"input_tokens": 100, "output_tokens": 20}
        )
        # the driver died: no metrics.json and a half-written last line
        metrics_path.unlink()
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write('{"at": "2026-11-06T00:00:00+00:00", "kind": "sta')
        result = subprocess.run(
            [*DRIVER, "metrics", str(self.plan), "--state-dir", str(self.state_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(metrics_path.read_text(encoding="utf-8")), written)
        self.assertEqual(json.loads(result.stdout), written)

    @pytest.mark.unit
    def test_S27_the_rollup_counts_events_alone(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        spec = load_script()
        at = "2026-11-06T00:00:00+00:00"
        rows = [
            {
                "at": at,
                "kind": "state",
                "unit": "t1",
                "task": "t1",
                "gate": [None, None],
                "status": [None, "RESERVED"],
                "route": {
                    "size": "small",
                    "difficulty": "simple",
                    "class": "small",
                    "model": "haiku",
                },
            },
            {"at": at, "kind": "round", "unit": "t1", "source": "review"},
            {"at": at, "kind": "escalation", "unit": "t1"},
            {"at": at, "kind": "attention", "unit": "t1"},
            {"at": at, "kind": "agent", "unit": "t1", "role": "decider", "how": "spawn"},
            {
                "at": at,
                "kind": "usage",
                "unit": "t1",
                "assignment_id": "a1",
                "usage": {"input_tokens": 1},
            },
            {
                "at": at,
                "kind": "usage",
                "unit": "t1",
                "assignment_id": "a1",
                "usage": {"input_tokens": 5},
            },
            {
                "at": at,
                "kind": "usage",
                "unit": "t1",
                "assignment_id": "a2",
                "usage": {"input_tokens": 2},
            },
        ]
        ticket = spec.events.rollup(rows, "demo")["tickets"]["t1"]
        self.assertEqual(
            (ticket["review_rounds"], ticket["escalations"], ticket["decider_calls"]), (1, 2, 1)
        )
        self.assertEqual(ticket["usage"], {"input_tokens": 7})
        self.assertEqual(ticket["route"]["model"], "haiku")

    @pytest.mark.unit
    def test_S10_a_key_is_summed_when_every_assignment_reports_it_else_null(self):
        """worker-usage/S10"""
        spec = load_script()
        at = "2026-11-07T00:00:00+00:00"

        def usage(assignment, figures):
            return {
                "at": at,
                "kind": "usage",
                "unit": "t1",
                "assignment_id": assignment,
                "usage": figures,
            }

        rows = [
            {"at": at, "kind": "state", "unit": "t1", "route": {"model": "haiku"}},
            usage("a1", {"input_tokens": 1, "cost_usd": 0.5, "tool_calls": 2}),
            usage("a2", {"input_tokens": 2, "tool_calls": 3}),
        ]
        ticket = spec.events.rollup(rows, "demo")["tickets"]["t1"]
        self.assertEqual(ticket["usage"], {"input_tokens": 3, "cost_usd": None, "tool_calls": 5})
        rows = [{"at": at, "kind": "state", "unit": "t1", "route": {"model": "haiku"}}]
        self.assertEqual(spec.events.rollup(rows, "demo")["tickets"]["t1"]["usage"], "unknown")

    @pytest.mark.unit
    def test_S10_an_assignment_that_reported_no_usage_nulls_every_figure(self):
        """worker-usage/S10"""
        spec = load_script()
        at = "2026-11-07T00:00:00+00:00"

        def agent(assignment, role):
            return {
                "at": at,
                "kind": "agent",
                "unit": "t1",
                "role": role,
                "how": "spawn",
                "assignment_id": assignment,
            }

        route = {"at": at, "kind": "state", "unit": "t1", "route": {"model": "haiku"}}
        worker_usage = {
            "at": at,
            "kind": "usage",
            "unit": "t1",
            "assignment_id": "a1",
            "usage": {"input_tokens": 1, "tool_calls": 2},
        }
        rows = [route, agent("a1", "worker"), worker_usage, agent("a2", "reviewer")]
        ticket = spec.events.rollup(rows, "demo")["tickets"]["t1"]
        self.assertEqual(ticket["usage"], {"input_tokens": None, "tool_calls": None})

        rows = [route, agent("a1", "worker"), agent("a2", "reviewer")]
        self.assertEqual(spec.events.rollup(rows, "demo")["tickets"]["t1"]["usage"], "unknown")

    @pytest.mark.unit
    def test_S10_the_metrics_table_shows_each_usage_figure_in_its_column_and_null_as_null(self):
        """worker-usage/S10"""
        spec = load_script()
        ticket = {
            "route": {"size": "small", "difficulty": "simple"},
            "review_rounds": 0,
            "escalations": 0,
            "decider_calls": 0,
            "usage": {
                "input_tokens": 3,
                "output_tokens": 4,
                "cached_input_tokens": 5,
                "cache_write_tokens": None,
                "reasoning_tokens": 6,
                "model_calls": 7,
                "tool_calls": 8,
                "cost_usd": None,
            },
        }
        table = spec.metrics_table(
            {"slug": "demo", "tickets": {"t1": ticket}, "lead": {"model_turns": 1}}
        )
        header, line = table.splitlines()[1:3]
        for column in ("input", "output", "cached", "cache_write", "reasoning", "model_calls"):
            self.assertIn(column, header.split())
        self.assertIn("cost_usd", header.split())
        self.assertEqual(line.split()[-8:], ["3", "4", "5", "null", "6", "7", "8", "null"])


class DecisionEventTests(DeciderFixture, unittest.TestCase):
    @pytest.mark.integration
    def test_S27_an_applied_decision_is_logged(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S27"""
        self.waits([self.blocked(), self.decided("skip", reason="not needed")])
        self.run_driver()
        decisions = self.events("decision")
        self.assertEqual(
            [(d["unit"], d["action"], d["reason"]) for d in decisions],
            [("t1", "skip", "not needed")],
        )
        self.assertEqual(
            [(e["unit"], e["cause"]) for e in self.events("escalation")], [("t1", "blocked")]
        )


if __name__ == "__main__":
    unittest.main()
