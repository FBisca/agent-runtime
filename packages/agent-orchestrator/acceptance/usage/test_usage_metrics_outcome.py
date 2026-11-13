"""worker-usage/S13: usage the runtime puts on `wait` reaches metrics.json and attempts.jsonl, null where incomplete."""

import json
import unittest

import pytest
from test_gates import GateFixture

pytestmark = pytest.mark.integration

WORKER = {
    "input_tokens": 117,
    "cached_input_tokens": 2100,
    "cache_write_tokens": 50,
    "output_tokens": 53,
    "model_calls": 3,
    "tool_calls": 4,
    "cost_usd": 0.25,
}
REVIEWER = {
    "input_tokens": 700,
    "cached_input_tokens": 1800,
    "cache_write_tokens": 0,
    "output_tokens": 120,
    "reasoning_tokens": 40,
    "model_calls": 2,
    "tool_calls": 2,
}


class UsageMetricsOutcome(GateFixture, unittest.TestCase):
    def run_with(self, worker_usage, reviewer_usage):
        worker = self.worker_done(tests=True)
        worker["assignments"][0]["usage"] = worker_usage
        reviewer = self.reviewer_done()
        reviewer["assignments"][0]["usage"] = reviewer_usage
        self.waits([worker, reviewer])
        self.run_driver()
        metrics = json.loads((self.run_dir / "metrics.json").read_text(encoding="utf-8"))
        rows = [
            json.loads(line)
            for line in (self.run_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        return metrics["tickets"]["t1"]["usage"], next(r for r in rows if r["task_id"] == "t1")

    def test_control_figures_both_assignments_report_reach_both_files(self):
        """Holds today: the wait steps' usage reaches metrics.json and attempts.jsonl for the figures both report."""
        common = ("input_tokens", "cached_input_tokens", "output_tokens")
        usage, attempt = self.run_with(
            {key: WORKER[key] for key in common}, {key: REVIEWER[key] for key in common}
        )
        self.assertEqual(
            usage, {"input_tokens": 817, "cached_input_tokens": 3900, "output_tokens": 173}
        )
        self.assertEqual(
            (attempt["tokens"]["input"], attempt["tokens"]["output"], attempt["tokens"]["cached"]),
            (817, 173, 3900),
        )

    def test_S13_a_figure_one_assignment_lacks_totals_to_null(self):
        """agent-runtime: worker-usage/S13"""
        usage, attempt = self.run_with(WORKER, REVIEWER)
        self.assertEqual(usage["input_tokens"], 817)
        self.assertEqual(usage["cached_input_tokens"], 3900)
        self.assertEqual(usage["cache_write_tokens"], 50)
        self.assertEqual(usage["output_tokens"], 173)
        self.assertEqual(usage["model_calls"], 5)
        self.assertEqual(usage["tool_calls"], 6)
        self.assertIsNone(usage["cost_usd"])
        self.assertIsNone(usage["reasoning_tokens"])

        self.assertEqual(attempt["schema_version"], 3)
        self.assertEqual(
            attempt["tokens"],
            {"input": 817, "output": 173, "cached": 3900, "cache_write": 50, "reasoning": None},
        )
        self.assertEqual(attempt["counters"]["model_calls"], 5)
        self.assertEqual(attempt["counters"]["tool_calls"], 6)
        self.assertIn("cost_usd", attempt)
        self.assertIsNone(attempt["cost_usd"])

    def test_S13_a_figure_every_assignment_reports_is_summed(self):
        """agent-runtime: worker-usage/S13"""
        usage, attempt = self.run_with(
            dict(WORKER, reasoning_tokens=0), dict(REVIEWER, cost_usd=0.5)
        )
        self.assertAlmostEqual(usage["cost_usd"], 0.75, places=6)
        self.assertAlmostEqual(attempt["cost_usd"], 0.75, places=6)
        self.assertEqual(
            {key: usage[key] for key in usage if key != "cost_usd"},
            {
                "input_tokens": 817,
                "cached_input_tokens": 3900,
                "cache_write_tokens": 50,
                "output_tokens": 173,
                "reasoning_tokens": 40,
                "model_calls": 5,
                "tool_calls": 6,
            },
        )
        self.assertEqual(
            attempt["tokens"],
            {"input": 817, "output": 173, "cached": 3900, "cache_write": 50, "reasoning": 40},
        )
        self.assertEqual(attempt["counters"]["model_calls"], 5)
        self.assertEqual(attempt["counters"]["tool_calls"], 6)

    def test_S13_a_count_one_assignment_lacks_totals_to_null(self):
        """agent-runtime: worker-usage/S13"""
        reviewer = {
            key: value
            for key, value in REVIEWER.items()
            if key not in ("tool_calls", "cache_write_tokens")
        }
        usage, attempt = self.run_with(WORKER, reviewer)
        self.assertIsNone(usage["tool_calls"])
        self.assertIsNone(usage["cache_write_tokens"])
        self.assertEqual(usage["model_calls"], 5)
        self.assertIsNone(attempt["counters"]["tool_calls"])
        self.assertIsNone(attempt["tokens"]["cache_write"])
        self.assertEqual(attempt["counters"]["model_calls"], 5)


if __name__ == "__main__":
    unittest.main()
