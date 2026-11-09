"""The step routes: each step of a run names its own backend, model and effort.

S8 — a step route names the step's backend, model and effort.
S9 — `--brain-cli` still names one backend for every gate step; a step route overrides it.
S10 — a step route that names something the driver cannot run fails the run before any worker.
"""

import json
import unittest

import pytest
from test_archive import ARCHIVE, ArchiveFixture

pytestmark = pytest.mark.integration

GAPS = "demo-gaps"
REVIEW = "demo-t1-review"
WORKER = "demo-t1"


class StepRouteTests(ArchiveFixture, unittest.TestCase):
    """A run whose steps are routed one by one: the worker, the reviewer, the gaps and the archive."""

    def setUp(self):
        super().setUp()
        self.waits(self.ticket_merged() + [self.archive_done(), self.gaps_done()])

    def spawns(self):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}

    def routes_file(self, data):
        path = self.root / "routes.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def config(self, *entries, steps=()):
        return {
            "version": 1,
            "routes": [dict(entry) for entry in entries],
            "steps": [dict(step) for step in steps],
        }

    def route(self, klass="small", backend="claude", model="haiku", **extra):
        entry = {"class": klass, "backend": backend, "model": model}
        entry.update(extra)
        return entry

    def step(self, name, backend="claude", model="opus", **extra):
        entry = {"step": name, "backend": backend, "model": model}
        entry.update(extra)
        return entry

    def classes(self):
        return (
            self.route("small", "claude", "haiku"),
            self.route("medium", "claude", "sonnet"),
            self.route("large", "claude", "opus"),
        )

    def test_S8_each_step_names_its_own_backend_model_and_effort(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S8 — the reviewer and the gaps reviewer sit where their own step says."""
        path = self.routes_file(
            self.config(
                self.route("small", "pi", "minimax/MiniMax-M3.1-Flash", effort="max"),
                self.route("medium", "claude", "sonnet"),
                self.route("large", "claude", "opus"),
                steps=[
                    self.step("reviewer", "codex", "gpt-l", effort="xhigh"),
                    self.step("gaps", "pi", "minimax/MiniMax-M3.1-Flash", effort="low"),
                    self.step("archive", "codex", "gpt-s", effort="medium"),
                ],
            )
        )
        self.run_driver("--routes-file", path)
        spawns = self.spawns()
        self.assertEqual(
            (spawns[WORKER]["cli"], spawns[WORKER]["model"], spawns[WORKER]["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "max"),
        )
        self.assertEqual(
            (spawns[REVIEW]["cli"], spawns[REVIEW]["model"], spawns[REVIEW]["effort"]),
            ("codex", "gpt-l", "xhigh"),
        )
        self.assertEqual(
            (spawns[GAPS]["cli"], spawns[GAPS]["model"], spawns[GAPS]["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "low"),
        )
        self.assertEqual(
            (spawns[ARCHIVE]["cli"], spawns[ARCHIVE]["model"], spawns[ARCHIVE]["effort"]),
            ("codex", "gpt-s", "medium"),
        )

    def test_S8_a_step_route_for_the_implementer_overrides_the_class_route(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S8 — the implementer's own step route answers, whatever the class says."""
        path = self.routes_file(
            self.config(
                *self.classes(),
                steps=[self.step("implementer", "pi", "minimax/MiniMax-M3.1-Flash", effort="max")],
            )
        )
        self.run_driver("--routes-file", path)
        worker = self.spawns()[WORKER]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "max"),
        )

    def test_S3_a_gate_without_a_step_route_keeps_todays_effort(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S3 — no step route, no new effort: the reviewer high, the gaps medium, the archive low."""
        self.run_driver()
        spawns = self.spawns()
        self.assertEqual(spawns[REVIEW]["effort"], "high")
        self.assertEqual(spawns[GAPS]["effort"], "medium")
        self.assertEqual(spawns[ARCHIVE]["effort"], "low")

    def test_S3_a_step_route_names_the_gates_effort(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S3 — the gates' effort comes from their own step, not from a constant."""
        path = self.routes_file(
            self.config(
                *self.classes(),
                steps=[
                    self.step("reviewer", "claude", "opus", effort="xhigh"),
                    self.step("gaps", "claude", "sonnet", effort="low"),
                    self.step("archive", "claude", "haiku", effort="max"),
                ],
            )
        )
        self.run_driver("--routes-file", path)
        spawns = self.spawns()
        self.assertEqual(spawns[REVIEW]["effort"], "xhigh")
        self.assertEqual(spawns[GAPS]["effort"], "low")
        self.assertEqual(spawns[ARCHIVE]["effort"], "max")

    def test_S9_the_brain_names_one_backend_for_every_gate_step(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S9 — `--brain-cli` still answers for the reviewer and the gaps reviewer alike."""
        self.run_driver("--brain-cli", "codex")
        spawns = self.spawns()
        self.assertEqual((spawns[REVIEW]["cli"], spawns[REVIEW]["model"]), ("codex", "gpt-m"))
        self.assertEqual((spawns[GAPS]["cli"], spawns[GAPS]["model"]), ("codex", "gpt-m"))

    def test_S9_a_step_route_overrides_the_brain(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S9 — the reviewer may sit on another backend than the gaps reviewer."""
        self.run_driver(
            "--brain-cli", "codex", "--step", "reviewer=pi/minimax/MiniMax-M3.1-Flash@max"
        )
        spawns = self.spawns()
        self.assertEqual(
            (spawns[REVIEW]["cli"], spawns[REVIEW]["model"], spawns[REVIEW]["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "max"),
        )
        self.assertEqual((spawns[GAPS]["cli"], spawns[GAPS]["model"]), ("codex", "gpt-m"))

    def test_S10_a_step_route_naming_an_unknown_step_fails_the_run_before_any_worker_is_spawned(
        self,
    ):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — the run stops with an explanation, and nothing is spawned."""
        path = self.routes_file(
            self.config(*self.classes(), steps=[self.step("janitor", "codex", "gpt-l")])
        )
        result = self.run_driver("--routes-file", path, expect=2)
        self.assertIn("janitor", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S10_a_step_route_naming_an_unknown_backend_fails_the_run_before_any_worker_is_spawned(
        self,
    ):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — a backend the driver cannot run is an error, not a fall back."""
        path = self.routes_file(
            self.config(*self.classes(), steps=[self.step("reviewer", "gemini", "gpt-l")])
        )
        result = self.run_driver("--routes-file", path, expect=2)
        self.assertIn("gemini", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S10_a_step_override_naming_an_unknown_step_fails_the_run_before_any_worker_is_spawned(
        self,
    ):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — the same for the command line."""
        result = self.run_driver("--step", "janitor=codex/gpt-l", expect=2)
        self.assertIn("janitor", result.stderr)
        self.assertEqual(self.calls("spawn"), [])


if __name__ == "__main__":
    unittest.main()
