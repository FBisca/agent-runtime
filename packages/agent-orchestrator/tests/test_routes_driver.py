"""The driver and the typed route config: an invalid config stops the run before any worker is spawned.

S3 — an invalid route config fails the run before a worker exists.
"""

import json
import unittest

import pytest
from fixtures import RunFixture  # noqa: E402

pytestmark = pytest.mark.integration


def config(*entries, version=1):
    return {"version": version, "routes": [dict(entry) for entry in entries]}


def route(klass="small", backend="claude", model="haiku", **extra):
    entry = {"class": klass, "backend": backend, "model": model}
    entry.update(extra)
    return entry


class RouteDriverTests(RunFixture, unittest.TestCase):
    """The driver: an invalid route config stops the run before a worker exists."""

    def routes_file(self, data):
        path = self.root / "routes.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_S2_an_invalid_route_config_fails_the_run_before_any_worker_is_spawned(self):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S2 — the run stops with an explanation, and nothing is spawned."""
        path = self.routes_file(config(route(backend="gemini")))
        result = self.run_driver("--routes-file", path, expect=2)
        self.assertIn("route config", result.stderr)
        self.assertIn("gemini", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S3_a_route_for_an_unknown_class_fails_the_run_before_any_worker_is_spawned(self):
        """orchestrator-engine/R4: P4/S3 — the same for a class the driver does not know."""
        path = self.routes_file(config(route(klass="tiny")))
        result = self.run_driver("--routes-file", path, expect=2)
        self.assertIn("tiny", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S1_a_run_with_no_route_config_keeps_the_legacy_model_decisions(self):
        """orchestrator-engine/R2: P4/S1 — no route config, the same model per class as the legacy table says."""
        self.run_driver()
        spawns = {call["args"]["name"]: call["args"] for call in self.calls("spawn")}
        worker = spawns["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("claude", "haiku", "low")
        )

    def test_S1_a_legacy_run_says_the_legacy_config_is_deprecated(self):
        """orchestrator-engine/R2: P4/S1 — the legacy path is kept for a transition, with an explicit warning."""
        result = self.run_driver()
        self.assertIn("legacy routing config", result.stderr)

    def test_S1_a_legacy_run_with_a_brain_backend_the_table_lacks_fails(self):
        """orchestrator-engine/R2: P4/S1 — the legacy path stays strict: a class the table does not name is an error, not a fallback."""
        self.models.write_text(
            "## Model classes\n\n| Class | Claude |\n|---|---|\n"
            '| small | `model: "haiku"` |\n| medium | `model: "sonnet"` |\n'
            '| large | `model: "opus"` |\n',
            encoding="utf-8",
        )
        result = self.run_driver("--brain-cli", "codex", expect=2)
        self.assertIn("lacks a codex model for class", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S1_a_typed_route_config_answers_a_brain_backend_the_table_lacks(self):
        """orchestrator-engine/R2: P4/S1 — with a route config the brain model comes from the config, not from the legacy table."""
        self.models.write_text(
            "## Model classes\n\n| Class | Claude |\n|---|---|\n"
            '| small | `model: "haiku"` |\n| medium | `model: "sonnet"` |\n'
            '| large | `model: "opus"` |\n',
            encoding="utf-8",
        )
        path = self.routes_file(
            config(
                route(klass="small", backend="claude", model="haiku"),
                route(klass="medium", backend="codex", model="gpt-m"),
                route(klass="large", backend="claude", model="opus"),
            )
        )
        self.run_driver("--brain-cli", "codex", "--routes-file", path)
        worker = {call["args"]["name"]: call["args"] for call in self.calls("spawn")}["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("claude", "haiku"))

    def test_S3_a_typed_route_config_answers_the_run_without_the_legacy_table(self):
        """orchestrator-engine/R2: P4/S3 — the route config is self-sufficient; the legacy table is a fallback, not a prerequisite."""
        self.models.unlink()
        path = self.routes_file(
            config(
                route(klass="small", backend="claude", model="haiku"),
                route(klass="medium", backend="claude", model="sonnet"),
                route(klass="large", backend="claude", model="opus"),
            )
        )
        self.run_driver("--routes-file", path)
        spawns = {call["args"]["name"]: call["args"] for call in self.calls("spawn")}
        self.assertEqual(
            (spawns["demo-t1"]["cli"], spawns["demo-t1"]["model"]), ("claude", "haiku")
        )
        self.assertEqual(
            (spawns["demo-t3"]["cli"], spawns["demo-t3"]["model"]), ("claude", "sonnet")
        )


if __name__ == "__main__":
    unittest.main()
