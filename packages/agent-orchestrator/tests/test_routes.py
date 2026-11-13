"""The typed route schema: what a worker runs on, validated before any worker is spawned.

S1 — a run with no typed route config is refused, naming the files it looked for.
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


class RouteSchemaTests(unittest.TestCase):
    """The schema itself: what a route config may say."""

    def rejects(self, data, needle):
        with self.assertRaises(routes.RouteError) as caught:
            routes.validate(data)
        self.assertIn(needle, str(caught.exception))

    def test_S2_an_unsupported_backend_is_refused_with_an_explanation(self):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S2 — a route on a backend the driver cannot run is an error, not a fall back."""
        self.rejects(config(route(backend="gemini")), "gemini")
        self.rejects(config(route(backend="gemini")), "claude, codex, pi")

    def test_S1_a_route_may_name_the_pi_backend(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S1 — pi runs workers, so the schema may name it."""
        resolved = routes.validate(config(route("small", "pi", "minimax/MiniMax-M3.1-Flash")))
        self.assertEqual(
            (resolved.routes[0].backend, resolved.routes[0].model),
            ("pi", "minimax/MiniMax-M3.1-Flash"),
        )

    def test_S1_the_efforts_above_high_are_valid_efforts(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S1 — the runtime speaks five efforts; the schema does too."""
        for effort in ("xhigh", "max"):
            with self.subTest(effort=effort):
                resolved = routes.validate(
                    config(route("small", "pi", "some-model", effort=effort))
                )
                self.assertEqual(resolved.routes[0].effort, effort)

    def test_S8_a_step_route_answers_its_own_step(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S8 — a config may name a step's backend, model and effort."""
        data = config(route("small", "claude", "haiku"))
        data["steps"] = [
            {"step": "reviewer", "backend": "codex", "model": "gpt-l", "effort": "xhigh"}
        ]
        resolved = routes.validate(data)
        self.assertEqual(len(resolved.steps), 1)
        step = resolved.steps[0]
        self.assertEqual(
            (step.step, step.backend, step.model, step.effort),
            ("reviewer", "codex", "gpt-l", "xhigh"),
        )

    def test_S10_a_step_route_naming_an_unknown_step_is_refused_by_name(self):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — the refusal names the step it named."""
        data = config(route("small", "claude", "haiku"))
        data["steps"] = [{"step": "janitor", "backend": "codex", "model": "gpt-l"}]
        with self.assertRaises(routes.RouteError) as caught:
            routes.validate(data)
        self.assertIn("janitor", str(caught.exception))

    def test_S10_a_step_route_naming_an_unknown_backend_is_refused_by_name(self):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — a backend the driver cannot run is an error, not a fall back."""
        data = config(route("small", "claude", "haiku"))
        data["steps"] = [{"step": "reviewer", "backend": "gemini", "model": "gpt-l"}]
        with self.assertRaises(routes.RouteError) as caught:
            routes.validate(data)
        self.assertIn("gemini", str(caught.exception))

    def test_S10_a_step_route_naming_no_model_is_refused(self):
        """orchestrator-engine/R4: orchestrator-routing-effort-role/S10 — a step route without a model is an error."""
        data = config(route("small", "claude", "haiku"))
        data["steps"] = [{"step": "reviewer", "backend": "codex"}]
        with self.assertRaises(routes.RouteError) as caught:
            routes.validate(data)
        self.assertIn("model", str(caught.exception))

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
            routes.resolve(routes.validate(config_data))
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
        resolved = routes.resolve(routes.validate(config_data))
        self.assertEqual(
            {klass: entry.model for klass, entry in resolved.items()},
            {"small": "haiku", "medium": "sonnet", "large": "opus"},
        )

    def test_S1_no_config_is_refused_naming_the_backend_profile_and_the_way_out(self):
        """orchestrator-engine/R19: a run with no typed config is refused, and the refusal says how to supply one."""
        with tempfile.TemporaryDirectory() as temp:
            for backend, name in (
                ("claude", "routes.json"),
                ("codex", "routes-codex.json"),
                ("pi", "routes-pi.json"),
            ):
                with self.assertRaises(routes.RouteError) as caught:
                    routes.find_config(repo=temp, home=temp, backend=backend)
                self.assertIn(name, str(caught.exception))
                self.assertIn("--routes-file", str(caught.exception))

    def test_S2_each_backend_reads_only_its_own_profile(self):
        """orchestrator-engine/R19: the Claude profile never answers a Codex or Pi run."""
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "routes.json").write_text("{}", encoding="utf-8")
            (Path(temp) / "routes-codex.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                routes.find_config(home=temp, backend="codex"),
                (str(Path(temp) / "routes-codex.json"), "user"),
            )
            with self.assertRaises(routes.RouteError):
                routes.find_config(home=temp, backend="pi")

    def test_S6_the_order_is_explicit_then_repository_then_profile(self):
        """orchestrator-engine/R19: an explicit file wins over repository policy, which wins over the profile."""
        with tempfile.TemporaryDirectory() as temp:
            home, repo = Path(temp) / "home", Path(temp) / "repo"
            (repo / ".orchestrate").mkdir(parents=True)
            home.mkdir()
            (home / "routes.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                routes.find_config(repo=repo, home=home), (str(home / "routes.json"), "user")
            )
            (repo / ".orchestrate" / "routes.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                routes.find_config(repo=repo, home=home),
                (str(repo / ".orchestrate" / "routes.json"), "repo"),
            )
            self.assertEqual(
                routes.find_config(explicit="x.json", repo=repo, home=home), ("x.json", "explicit")
            )

    def driver_with(self, config_data, brain_cli):
        """A driver that answered the routes, without running anything."""
        driver = object.__new__(orchestrate.Driver)
        driver.brain_cli = brain_cli
        driver.routes_file = "routes.json"
        driver.routes = routes.resolve(routes.validate(config_data))
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
