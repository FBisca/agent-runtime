"""The step routes: each step of a run names its own backend, model and effort.

S8 — a step route names the step's backend, model and effort.
S9 — `--brain-cli` still names one backend for every gate step; a step route overrides it.
S10 — a step route that names something the driver cannot run fails the run before any worker.
"""

import json
import unittest
from unittest import mock

import pytest
from fixtures import RunFixture, load_script
from test_archive import ArchiveFixture

pytestmark = pytest.mark.integration

GAPS = "demo-gaps"
REVIEW = "demo-t1-review"
WORKER = "demo-t1"


class RoutedRunFixture(RunFixture):
    """A run stopped once its routes are settled: the driver has parsed its arguments and started, and no worker
    exists. What each gate would spawn on is the driver's own answer, read in this process."""

    def settle(self, *extra, expect=0):
        """The run's routes, settled: the driver the run built, stopped where its loop would begin."""
        spec = load_script()
        seen = []
        self.alias_state(self.plan)
        with mock.patch.object(spec.Driver, "loop", lambda driver: seen.append(driver)):
            result = self.in_process(spec, self.driver_args(*extra))
        self.assertEqual(result.returncode, expect, result.stderr)
        self.settled = seen[0] if seen else None
        return result

    def gate(self, step, klass, default_effort):
        """What the gate would spawn on: backend, model and effort, as the driver's spawn takes them."""
        backend, model = self.settled.gate_step(step, klass)
        return backend, model, self.settled.step_effort(step, default_effort)

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

    def brain_config(self):
        """The classes of a run whose medium class, which the gates run on, sits on the brain backend."""
        return self.config(
            self.route("small", "claude", "haiku"),
            self.route("medium", "codex", "gpt-m"),
            self.route("large", "claude", "opus"),
        )

    def reviewer(self):
        return self.gate("reviewer", "large", "high")

    def gaps(self):
        return self.gate("gaps", "medium", "medium")


class StepRouteTests(RoutedRunFixture, unittest.TestCase):
    """A run whose steps are routed one by one: the reviewer and the gaps reviewer, read where the driver settles them."""

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
                ],
            )
        )
        self.settle("--routes-file", path)
        self.assertEqual(self.reviewer(), ("codex", "gpt-l", "xhigh"))
        self.assertEqual(self.gaps(), ("pi", "minimax/MiniMax-M3.1-Flash", "low"))

    def test_S3_a_gate_without_a_step_route_keeps_todays_effort(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S3 — no step route, no new effort: the reviewer high, the gaps medium."""
        self.settle()
        self.assertEqual(self.reviewer()[2], "high")
        self.assertEqual(self.gaps()[2], "medium")

    def test_S3_a_step_route_names_the_gates_effort(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S3 — the gates' effort comes from their own step, not from a constant."""
        path = self.routes_file(
            self.config(
                *self.classes(),
                steps=[
                    self.step("reviewer", "claude", "opus", effort="xhigh"),
                    self.step("gaps", "claude", "sonnet", effort="low"),
                ],
            )
        )
        self.settle("--routes-file", path)
        self.assertEqual(self.reviewer()[2], "xhigh")
        self.assertEqual(self.gaps()[2], "low")

    def test_S9_the_brain_names_one_backend_for_every_gate_step(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S9 — `--brain-cli` still answers for the reviewer and the gaps reviewer alike."""
        self.settle("--brain-cli", "codex", "--routes-file", self.routes_file(self.brain_config()))
        self.assertEqual(self.reviewer()[:2], ("codex", "gpt-m"))
        self.assertEqual(self.gaps()[:2], ("codex", "gpt-m"))

    def test_S9_a_step_route_overrides_the_brain(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S9 — the reviewer may sit on another backend than the gaps reviewer."""
        self.settle(
            "--brain-cli",
            "codex",
            "--routes-file",
            self.routes_file(self.brain_config()),
            "--step",
            "reviewer=pi/minimax/MiniMax-M3.1-Flash@max",
        )
        self.assertEqual(self.reviewer(), ("pi", "minimax/MiniMax-M3.1-Flash", "max"))
        self.assertEqual(self.gaps()[:2], ("codex", "gpt-m"))

    def test_S10_a_step_route_naming_an_unknown_step_fails_the_run_before_any_worker_is_spawned(
        self,
    ):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — the run stops with an explanation, and nothing is spawned."""
        path = self.routes_file(
            self.config(*self.classes(), steps=[self.step("janitor", "codex", "gpt-l")])
        )
        result = self.settle("--routes-file", path, expect=2)
        self.assertIn("janitor", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S10_a_step_route_naming_an_unknown_backend_fails_the_run_before_any_worker_is_spawned(
        self,
    ):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — a backend the driver cannot run is an error, not a fall back."""
        path = self.routes_file(
            self.config(*self.classes(), steps=[self.step("reviewer", "gemini", "gpt-l")])
        )
        result = self.settle("--routes-file", path, expect=2)
        self.assertIn("gemini", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S10_a_step_override_naming_an_unknown_step_fails_the_run_before_any_worker_is_spawned(
        self,
    ):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — the same for the command line."""
        result = self.settle("--step", "janitor=codex/gpt-l", expect=2)
        self.assertIn("janitor", result.stderr)
        self.assertEqual(self.calls("spawn"), [])


class StepRouteBoundaryTests(RoutedRunFixture, ArchiveFixture, unittest.TestCase):
    """The same routes crossing the boundary: a run that spawns names each tier's backend, model and effort."""

    def setUp(self):
        super().setUp()
        self.waits(self.ticket_merged())

    def spawns(self):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}

    def test_S8_a_step_route_for_the_implementer_overrides_the_class_route(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S8 — the implementer's own step route answers, whatever the class says."""
        path = self.routes_file(
            self.config(
                *self.classes(),
                steps=[
                    self.step("implementer", "pi", "minimax/MiniMax-M3.1-Flash", effort="max"),
                    self.step("reviewer", "codex", "gpt-l", effort="xhigh"),
                    self.step("gaps", "pi", "minimax/MiniMax-M3.1-Flash", effort="low"),
                ],
            )
        )
        self.run_driver("--routes-file", path)
        spawns = self.spawns()
        for name, want in (
            (WORKER, ("pi", "minimax/MiniMax-M3.1-Flash", "max")),
            (REVIEW, ("codex", "gpt-l", "xhigh")),
            (GAPS, ("pi", "minimax/MiniMax-M3.1-Flash", "low")),
        ):
            self.assertEqual(
                (spawns[name]["cli"], spawns[name]["model"], spawns[name]["effort"]), want
            )


if __name__ == "__main__":
    unittest.main()
