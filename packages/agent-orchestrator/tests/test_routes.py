"""The typed route schema: what a worker runs on, validated before any worker is spawned.

S1 — a run with no route config keeps the legacy 'Model classes' decisions.
S3 — an invalid route config fails the run before any worker is spawned.
"""

import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import driver as orchestrate
from agent_orchestrator import routes

pytestmark = pytest.mark.unit


def config(*entries, version=1):
    return {"version": version, "routes": [dict(entry) for entry in entries]}


def route(klass="small", backend="claude", model="haiku", **extra):
    entry = {"class": klass, "backend": backend, "model": model}
    entry.update(extra)
    return entry


LEGACY_TABLE = (
    "## Model classes\n\n| Class | Claude | Codex |\n|---|---|---|\n"
    '| small | `model: "haiku"` | `gpt-s` |\n| medium | `model: "sonnet"` | `gpt-m` |\n'
    '| large | `model: "opus"` | `gpt-l` |\n'
)


class RouteSchemaTests(unittest.TestCase):
    """The schema itself: what a route config may say."""

    def rejects(self, data, needle):
        with self.assertRaises(routes.RouteError) as caught:
            routes.validate(data)
        self.assertIn(needle, str(caught.exception))

    def test_S3_an_unsupported_backend_is_refused_with_an_explanation(self):
        """orchestrator-engine/R4: P4/S3 — a route on a backend the driver cannot run is an error, not a fall back."""
        self.rejects(config(route(backend="gemini")), "gemini")
        self.rejects(config(route(backend="pi")), "lead-only")

    def test_S3_a_route_for_an_unknown_class_is_refused(self):
        """orchestrator-engine/R4: P4/S3 — a route for a class the driver does not know is an error."""
        self.rejects(config(route(klass="tiny")), "tiny")

    def test_S3_a_route_without_a_model_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — a route that names no model is an error."""
        self.rejects(config({"class": "small", "backend": "claude"}), "model")

    def test_S3_a_route_with_an_unknown_field_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — the schema is strict; an unrecognised field is an error."""
        self.rejects(config(route(command="rm -rf /")), "command")

    def test_S3_an_unsupported_config_version_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — the config carries its version, and an unknown one is an error."""
        self.rejects(config(route(), version=99), "version")

    def test_S3_a_config_that_leaves_a_class_out_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — a class with no route must not fall back to another billable model."""
        config_data = config(route(klass="small"), route(klass="medium"))
        with self.assertRaises(routes.RouteError) as caught:
            routes.resolve(routes.validate(config_data), "claude", {})
        self.assertIn("large", str(caught.exception))

    def test_S3_a_route_with_an_unknown_effort_is_refused(self):
        """orchestrator-engine/R4: P4/S3 — the effort a route names must be one the driver knows."""
        self.rejects(config(route(effort="turbo")), "turbo")

    def test_S3_a_valid_config_resolves_to_one_route_per_class(self):
        """orchestrator-engine/R2: P4/S3 — a valid config resolves to the routes it names."""
        config_data = config(
            route(klass="small", model="haiku"),
            route(klass="medium", model="sonnet"),
            route(klass="large", model="opus"),
        )
        resolved = routes.resolve(routes.validate(config_data), "claude", {})
        self.assertEqual(
            {klass: entry.model for klass, entry in resolved.items()},
            {"small": "haiku", "medium": "sonnet", "large": "opus"},
        )

    def test_S1_the_legacy_table_answers_the_same_models_as_before(self):
        """orchestrator-engine/R2: P4/S1 — with no route config the legacy 'Model classes' table answers, class for class."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "CLAUDE.md"
            path.write_text(LEGACY_TABLE, encoding="utf-8")
            models = orchestrate.class_models(str(path), "claude")
            legacy = routes.legacy("claude", models)
        self.assertEqual({klass: entry.model for klass, entry in legacy.items()}, models)
        self.assertEqual(
            {klass: entry.backend for klass, entry in legacy.items()},
            {klass: "claude" for klass in models},
        )

    def test_S1_a_legacy_route_leaves_the_effort_to_the_matrix(self):
        """orchestrator-engine/R2: P4/S1 — the legacy table names no effort; the size/difficulty matrix does."""
        legacy = routes.legacy("claude", {"small": "haiku"})
        self.assertIsNone(legacy["small"].effort)

    def test_S3_an_unreadable_legacy_table_is_no_table_when_it_is_only_a_fallback(self):
        """orchestrator-engine/R2: P4/S3 — with a route config the legacy table is a fallback; a missing one is not an error."""
        with tempfile.TemporaryDirectory() as temp:
            missing = str(Path(temp) / "no-such-CLAUDE.md")
            self.assertEqual(orchestrate.class_models(missing, "claude", required=False), {})
            with self.assertRaises(orchestrate.PlanError):
                orchestrate.class_models(missing, "claude")

    def driver_with(self, config_data, brain_cli):
        """A driver that answered the routes, without running anything."""
        driver = object.__new__(orchestrate.Driver)
        driver.brain_cli = brain_cli
        driver.routes_file = "routes.json"
        driver.routes = routes.resolve(routes.validate(config_data), "claude", {})
        driver.brain_models = {}
        driver.args = type("Args", (), {"models_file": "CLAUDE.md"})()
        return driver

    def test_S3_a_typed_route_config_supplies_the_brain_model(self):
        """orchestrator-engine/R2: P4/S3 — with a route config the brain backend's model comes from the config."""
        config_data = config(
            route(klass="small", backend="claude", model="haiku"),
            route(klass="medium", backend="codex", model="gpt-m"),
            route(klass="large", backend="claude", model="opus"),
        )
        driver = self.driver_with(config_data, "codex")
        self.assertEqual(driver.gate_model("large"), "gpt-m")

    def test_S3_a_route_config_without_a_brain_route_fails_closed(self):
        """orchestrator-engine/R2: P4/S3 — a config that names no route on the brain backend is an error, not another backend's model."""
        config_data = config(
            route(klass="small", backend="claude", model="haiku"),
            route(klass="medium", backend="claude", model="sonnet"),
            route(klass="large", backend="claude", model="opus"),
        )
        driver = self.driver_with(config_data, "codex")
        with self.assertRaises(orchestrate.PlanError) as caught:
            driver.gate_model("large")
        self.assertIn("codex", str(caught.exception))
