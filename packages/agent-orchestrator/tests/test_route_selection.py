"""The route selection itself: what an explicit override says, and what the ledger keeps of it.

S2 — a configured route is chosen, validated and logged.
S3 — a route that names an unsupported backend or a class the driver does not know is refused.
"""

import unittest

import pytest
from agent_orchestrator import routes

pytestmark = pytest.mark.unit


class RouteSelectionTests(unittest.TestCase):
    """The route selection itself: what an explicit override says, and what the ledger keeps of it."""

    def test_S2_an_explicit_override_names_the_backend_model_and_effort(self):
        """orchestrator-engine/R2: P4/S2 — an override reads 'class=backend/model[@effort]' and names all three."""
        selected = routes.override("small=codex/gpt-s@high")
        self.assertEqual(
            (selected.klass, selected.backend, selected.model, selected.effort),
            ("small", "codex", "gpt-s", "high"),
        )

    def test_S2_an_override_without_an_effort_leaves_the_effort_to_the_matrix(self):
        """orchestrator-engine/R2: P4/S2 — an override that names no effort leaves the size/difficulty matrix in charge."""
        self.assertIsNone(routes.override("small=codex/gpt-s").effort)

    def test_S2_an_override_wins_over_the_config(self):
        """orchestrator-engine/R2: P4/S2 — the explicit override replaces the class it names and leaves the others alone."""
        resolved = {
            "small": routes.Route("small", "claude", "haiku"),
            "medium": routes.Route("medium", "claude", "sonnet"),
            "large": routes.Route("large", "claude", "opus"),
        }
        chosen = routes.apply_overrides(resolved, [routes.override("small=codex/gpt-s@high")])
        self.assertEqual(
            (chosen["small"].backend, chosen["small"].model, chosen["small"].effort),
            ("codex", "gpt-s", "high"),
        )
        self.assertEqual(chosen["medium"], resolved["medium"])
        self.assertEqual(chosen["large"], resolved["large"])

    def test_S2_the_route_ledger_records_the_resolved_routes(self):
        """orchestrator-engine/R2: P4/S2 — the ledger keeps where the routes came from and what each class runs on."""
        record = routes.record(
            {"small": routes.Route("small", "codex", "gpt-s", "high")},
            "explicit",
            "/tmp/routes.json",
        )
        self.assertEqual(record["source"], "explicit")
        self.assertEqual(record["path"], "/tmp/routes.json")
        self.assertEqual(
            record["classes"]["small"],
            {"class": "small", "backend": "codex", "model": "gpt-s", "effort": "high"},
        )

    def test_S3_an_override_for_an_unknown_class_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — an override for a class the driver does not know is an error."""
        with self.assertRaises(routes.RouteError) as caught:
            routes.override("tiny=codex/gpt-s")
        self.assertIn("tiny", str(caught.exception))

    def test_S1_an_override_may_name_the_pi_backend(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S1 — pi runs workers, so an override may name it."""
        selected = routes.override("small=pi/minimax/MiniMax-M3.1-Flash@max")
        self.assertEqual(
            (selected.klass, selected.backend, selected.model, selected.effort),
            ("small", "pi", "minimax/MiniMax-M3.1-Flash", "max"),
        )

    def test_S3_an_override_on_an_unknown_backend_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — a backend the driver cannot run is an error, not a fall back."""
        with self.assertRaises(routes.RouteError) as caught:
            routes.override("small=gemini/gem")
        self.assertIn("gemini", str(caught.exception))

    def test_S3_an_override_with_an_unknown_effort_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — the effort an override names must be one the driver knows."""
        with self.assertRaises(routes.RouteError) as caught:
            routes.override("small=codex/gpt-s@turbo")
        self.assertIn("turbo", str(caught.exception))

    def test_S3_an_override_that_names_no_model_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — an override without a model is an error."""
        with self.assertRaises(routes.RouteError) as caught:
            routes.override("small=codex")
        self.assertIn("model", str(caught.exception))

    def test_S3_an_override_that_names_no_class_is_refused(self):
        """orchestrator-engine/R2: P4/S3 — an override without a class is an error."""
        with self.assertRaises(routes.RouteError) as caught:
            routes.override("codex/gpt-s")
        self.assertIn("class", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
