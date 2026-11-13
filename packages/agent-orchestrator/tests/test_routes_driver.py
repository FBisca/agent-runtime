"""The driver and the typed route config: an invalid config stops the run before any worker is spawned.

S3 — an invalid route config fails the run before a worker exists.
"""

import json
import unittest

import pytest
from agent_orchestrator import narrate
from test_step_routes import RoutedRunFixture  # noqa: E402

pytestmark = pytest.mark.integration


def config(*entries, version=1):
    return {"version": version, "routes": [dict(entry) for entry in entries]}


def route(klass="small", backend="claude", model="haiku", **extra):
    entry = {"class": klass, "backend": backend, "model": model}
    entry.update(extra)
    return entry


class RouteDriverTests(RoutedRunFixture, unittest.TestCase):
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

    def test_a_run_opens_with_its_banner_naming_each_class_route(self):
        """orchestrator-engine/R90: the drive's first lines are the banner: the plan, its tickets and its routes."""
        path = self.routes_file(
            config(
                route(klass="small", backend="claude", model="haiku"),
                route(klass="medium", backend="codex", model="gpt-m", effort="high"),
                route(klass="large", backend="claude", model="opus"),
            )
        )
        head = self.settle("--routes-file", path).stderr
        self.assertIn(narrate.LOGO[0], head)
        self.assertIn("  plan     demo · ", head)
        self.assertRegex(head, r"\n    medium   codex  · gpt-m · high\n")
        self.assertIn("routes.json (explicit)", head)

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
        self.settle("--brain-cli", "codex", "--routes-file", path)
        small = self.settled.routes["small"]
        self.assertEqual((small.backend, small.model), ("claude", "haiku"))
        self.assertEqual(self.gate("reviewer", "large", "high")[:2], ("codex", "gpt-m"))

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
        self.settle("--routes-file", path)
        routes = self.settled.routes
        self.assertEqual((routes["small"].backend, routes["small"].model), ("claude", "haiku"))
        self.assertEqual((routes["medium"].backend, routes["medium"].model), ("claude", "sonnet"))


if __name__ == "__main__":
    unittest.main()
