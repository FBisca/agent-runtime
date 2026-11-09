"""An implementer or reviewer short of context is swapped for a fresh agent of the same class before a resume."""

import unittest
from datetime import datetime, timezone

import pytest
from fixtures import spawn_id
from test_reuse import ReuseFixture

pytestmark = pytest.mark.integration


def sample(used, window=200000, model="opus"):
    return {
        "kind": "current_context",
        "source": "test",
        "model": model,
        "context_tokens": used,
        "effective_window": window,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def with_usage(step, usage):
    step["assignments"][0]["usage"] = usage
    return step


def with_id(step, assignment_id):
    step["assignments"][0]["assignment_id"] = assignment_id
    step["messages"][0]["assignment_id"] = assignment_id
    return step


class SwapTests(ReuseFixture, unittest.TestCase):
    def spawns_of(self, name):
        return [c["args"] for c in self.calls("spawn") if c["args"]["name"] == name]

    def test_S19_an_implementer_below_the_reserve_is_swapped_for_a_fresh_one_with_the_handoff(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        self.waits(
            [
                with_usage(self.worker_done(), sample(190000)),
                with_id(self.worker_done("t1r1"), spawn_id("demo-t1", 2)),
            ]
        )
        self.run_driver()
        self.assertEqual(
            [r["args"]["task"].split("/")[-2] for r in self.calls("resume")], ["t1r2"]
        )  # the fresh agent, unmeasured
        stops = [c["args"]["name"] for c in self.calls("stop")]
        self.assertEqual(stops, ["demo-t1"])
        first, fresh = self.spawns_of("demo-t1")
        for key in ("kind", "cwd", "cli", "model", "effort"):
            self.assertEqual(fresh[key], first[key], key)
        for needle in (
            str(self.run_dir / "t1r1" / "task.md"),
            str(self.run_dir / "t1" / "task.md"),
            str(self.run_dir / "t1" / "report.json"),
            str(self.run_dir / "t1r1" / "findings.json"),
            "origin/main...HEAD",
        ):
            self.assertIn(needle, fresh["task"])
        rows = self.events("swap")
        self.assertEqual(
            [(r["name"], r["unit"], r["role"]) for r in rows], [("demo-t1", "t1", "worker")]
        )
        self.assertEqual(self.wakes(), [])

    def test_S19_the_units_counts_survive_a_swap(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        self.waits(
            [
                with_usage(self.worker_done(), sample(190000)),
                with_id(self.worker_done("t1r1"), spawn_id("demo-t1", 2)),
            ]
        )
        self.run_driver()
        self.assertEqual(self.ledger()["rounds"], {"t1": 2})
        self.assertEqual(len(self.events("round")), 2)
        self.assertEqual(self.task("t1r1")["gate"], "superseded")

    def test_S19_a_reviewer_below_the_reserve_is_swapped_before_the_re_review(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        first = with_usage(
            self.reviewer_done(self.review2(verdict="block", items=[self.blocking()])),
            sample(195000),
        )
        again = with_id(self.re_review(), spawn_id("demo-t1-review", 2))
        self.waits([self.worker_done(tests=True), first, self.fix_report(), again])
        self.run_driver()
        base = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        _, fresh = self.spawns_of("demo-t1-review")
        for needle in (
            str(self.run_dir / "t1-review2" / "task.md"),
            str(self.run_dir / "t1" / "task.md"),
            str(self.run_dir / "t1r1" / "report.json"),
            str(self.run_dir / "t1r1" / "findings.json"),
            "origin/main...HEAD",
            "%s..HEAD" % base,
        ):
            self.assertIn(needle, fresh["task"])
        self.assertEqual(
            [(r["name"], r["role"]) for r in self.events("swap")], [("demo-t1-review", "reviewer")]
        )
        self.assertEqual({w["kind"] for w in self.wakes()}, {"pr-ready"})
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S19_unknown_usage_resumes_and_logs_context_unknown(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        self.waits([self.worker_done()])
        self.run_driver()
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        self.assertEqual(self.events("swap"), [])
        rows = self.events("context")
        self.assertEqual(
            [(r["name"], r["unit"], r["context"]) for r in rows], [("demo-t1", "t1", "unknown")]
        )

    def test_S19_a_stale_or_unusable_measurement_counts_as_unknown(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        stale = dict(sample(190000), observed_at="2020-01-01T00:00:00+00:00")
        self.waits([with_usage(self.worker_done(), stale)])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual([r["context"] for r in self.events("context")], ["unknown"])

    def test_S19_an_agent_with_room_is_resumed_without_a_swap(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        self.waits([with_usage(self.worker_done(), sample(50000))])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual(self.events("swap", "context"), [])

    def test_S19_the_reserve_is_set_by_a_flag(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S19"""
        self.waits([with_usage(self.worker_done(), sample(190000))])
        self.run_driver("--context-reserve", "1000")
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual(self.events("swap"), [])


if __name__ == "__main__":
    unittest.main()
