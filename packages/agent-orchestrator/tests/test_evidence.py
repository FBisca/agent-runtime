"""Per-attempt evidence: one schema-versioned record per attempt, unknown measurements null, metrics.json intact.

P3.T3 / S2: WHEN a run succeeds or fails, THEN results include verified completion, elapsed time, intervention and
tool-loop/retry counts, with token figures labelled input/output/cached and unavailable fields left blank.
"""

import json
import unittest
from pathlib import Path

import pytest
from fixtures import report_wait
from test_gates import GateFixture

MANIFEST = Path(__file__).resolve().parent / "evidence-manifest.json"


def load_evidence():
    """The evidence module of the engine."""
    from agent_orchestrator import evidence

    return evidence


def task(**over):
    """A ledger task of a finished attempt."""
    record = {
        "packet": {
            "task_id": "t1",
            "unit": "t1",
            "backend": "claude",
            "model": "haiku",
            "worker": "demo-t1",
        },
        "status": "COMPLETE",
        "runtime_id": "term-demo-t1",
        "assignment_id": "a-demo-t1",
        "started_at": "2026-11-07T00:00:00+00:00",
        "finished_at": "2026-11-07T00:01:30+00:00",
        "updated_at": "2026-11-07T00:01:30+00:00",
        "report": {
            "contract": 1,
            "task_id": "t1",
            "status": "COMPLETE",
            "acceptance_met": True,
            "checks": [{"command": "pytest", "outcome": "passed"}],
            "head": "abc1234",
        },
    }
    record.update(over)
    return record


def row(kind, **fields):
    return dict({"at": "2026-11-07T00:00:00+00:00", "kind": kind}, **fields)


class EvidenceTests(unittest.TestCase):
    @pytest.mark.unit
    def test_S2_token_figures_are_labelled_input_output_and_cached(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        evidence = load_evidence()
        counted = evidence.tokens(
            {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 5}
        )
        self.assertEqual(counted, {"input": 100, "output": 20, "cached": 5})
        self.assertEqual(
            evidence.tokens({"input_tokens": 3}), {"input": 3, "output": None, "cached": None}
        )

    @pytest.mark.unit
    def test_S2_an_unmeasured_figure_is_null_and_never_zero(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        evidence = load_evidence()
        self.assertEqual(evidence.tokens(None), {"input": None, "output": None, "cached": None})
        self.assertEqual(
            evidence.tokens("unknown"), {"input": None, "output": None, "cached": None}
        )

    @pytest.mark.unit
    def test_S2_an_attempt_record_counts_retries_loops_and_interventions_and_leaves_unknowns_null(
        self,
    ):
        """orchestrator-engine/R2: P3.T3/S2"""
        evidence = load_evidence()
        data = {"version": 1, "tasks": {"t1": task()}}
        rows = [
            row("round", unit="t1", task="t1", source="converge", attempt=1),
            row("idle", unit="t1", task="t1", waiting="agent", ticks=1),
            row("escalation", unit="t1", task="t1", cause="blocked"),
            row("decision", unit="t1", task="t1", action="skip"),
            row(
                "usage",
                unit="t1",
                assignment_id="a-demo-t1",
                usage={"input_tokens": 100, "output_tokens": 20},
            ),
        ]
        record = evidence.attempts(data, rows, "demo")[0]
        self.assertEqual(record["schema_version"], evidence.SCHEMA_VERSION)
        self.assertEqual((record["task_id"], record["unit"], record["attempt"]), ("t1", "t1", 1))
        self.assertEqual((record["harness"], record["provider"]), ("claude", "haiku"))
        self.assertEqual(record["source_revision"], "abc1234")
        self.assertEqual(record["elapsed_seconds"], 90.0)
        self.assertEqual(record["outcome"], "COMPLETE")
        self.assertIs(record["verified"], True)
        self.assertEqual(record["counters"]["retries"], 1)
        self.assertEqual(record["counters"]["tool_loops"], 1)
        self.assertEqual(record["counters"]["interventions"], 1)
        self.assertEqual(record["counters"]["human_rescues"], 1)
        self.assertIsNone(record["counters"]["tool_calls"])
        self.assertIsNone(record["counters"]["model_calls"])
        self.assertEqual(record["tokens"], {"input": 100, "output": 20, "cached": None})
        self.assertEqual([g["source"] for g in record["gates"]], ["converge"])

    @pytest.mark.unit
    def test_S2_an_attempt_that_ended_without_a_verified_report_is_not_verified(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        evidence = load_evidence()
        data = {"version": 1, "tasks": {"t1": task(status="RELEASED", report=None)}}
        record = evidence.attempts(data, [], "demo")[0]
        self.assertIs(record["verified"], False)
        self.assertEqual(record["outcome"], "RELEASED")
        self.assertEqual(record["finished_at"], "2026-11-07T00:01:30+00:00")
        self.assertEqual(record["elapsed_seconds"], 90.0)

    @pytest.mark.unit
    def test_S2_the_evidence_manifest_names_the_fields_the_records_carry(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        evidence = load_evidence()
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], evidence.SCHEMA_VERSION)
        self.assertEqual(set(manifest["fields"]), set(evidence.FIELDS))
        for field in evidence.FIELDS:
            self.assertTrue(manifest["fields"][field])


class EvidenceRunTests(GateFixture, unittest.TestCase):
    def attempts(self):
        path = self.run_dir / "attempts.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return rows

    def attempt(self, task_id):
        return next(row for row in self.attempts() if row["task_id"] == task_id)

    @pytest.mark.integration
    def test_S2_a_finished_run_writes_one_evidence_record_per_attempt(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        worker = self.worker_done(tests=True)
        worker["assignments"][0]["usage"] = {"input_tokens": 100, "output_tokens": 20}
        self.waits([worker, self.reviewer_done()])
        self.run_driver()
        records = self.attempts()
        self.assertIn("t1", [r["task_id"] for r in records])
        record = self.attempt("t1")
        self.assertEqual(record["outcome"], "COMPLETE")
        self.assertIs(record["verified"], True)
        self.assertIsNotNone(record["elapsed_seconds"])
        self.assertGreaterEqual(record["elapsed_seconds"], 0)
        self.assertEqual(record["harness"], "claude")
        self.assertEqual(record["provider"], "haiku")
        self.assertEqual(record["tokens"], {"input": 100, "output": 20, "cached": None})
        self.assertEqual(record["counters"]["retries"], 0)
        self.assertEqual(record["counters"]["interventions"], 0)
        self.assertIsNone(record["counters"]["tool_calls"])

    @pytest.mark.integration
    def test_S2_a_run_that_needed_a_person_records_the_intervention_and_no_verified_completion(
        self,
    ):
        """orchestrator-engine/R2: P3.T3/S2"""
        blocked = report_wait("demo-t1", "blocked", "cannot reach the database")
        self.waits([blocked, self.decide_step("t1", "skip")])
        self.run_driver()
        records = self.attempts()
        self.assertIn("t1", [r["task_id"] for r in records])
        record = self.attempt("t1")
        self.assertIs(record["verified"], False)
        self.assertEqual(record["counters"]["interventions"], 1)
        self.assertEqual(record["counters"]["human_rescues"], 1)
        self.assertEqual(record["tokens"], {"input": None, "output": None, "cached": None})

    @pytest.mark.integration
    def test_S2_the_evidence_can_be_rebuilt_after_the_run_died(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        worker = self.worker_done(tests=True)
        self.waits([worker, self.reviewer_done()])
        self.run_driver()
        path = self.run_dir / "attempts.jsonl"
        before = path.read_text(encoding="utf-8")
        path.unlink()
        result = self.cli("evidence", str(self.plan), "--state-dir", str(self.state_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertTrue(json.loads(result.stdout))

    @pytest.mark.integration
    def test_S2_the_old_metrics_json_consumer_still_reads_a_run_written_with_evidence(self):
        """orchestrator-engine/R2: P3.T3/S2"""
        worker = self.worker_done(tests=True)
        worker["assignments"][0]["usage"] = {"input_tokens": 100, "output_tokens": 20}
        self.waits([worker, self.reviewer_done()])
        self.run_driver()
        metrics = json.loads((self.run_dir / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(set(metrics) >= {"slug", "lead", "tickets", "events"}, True)
        self.assertEqual(
            metrics["tickets"]["t1"]["usage"], {"input_tokens": 100, "output_tokens": 20}
        )
        self.assertTrue(self.attempts())


if __name__ == "__main__":
    unittest.main()
