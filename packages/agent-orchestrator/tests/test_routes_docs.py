"""The routing documents describe typed routes and no model table.

S8 — the CLI help, the route module, the packaged models note, docs/pi.md and the living spec describe typed
configuration, precedence, migration and restart behaviour without relying on the model table.
"""

import contextlib
import io
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import driver, routes

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = Path(driver.__file__).parent


def help_text():
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
        driver.main(["run", "--help"])
    return " ".join(out.getvalue().split())


class RoutingDocsTests(unittest.TestCase):
    def test_S8_the_cli_help_points_to_typed_routes_and_no_table(self):
        text = help_text()
        self.assertIn("Typed route config", text)
        self.assertIn(".orchestrate/routes.json", text)
        self.assertIn("~/.config/orchestrate/routes.json", text)
        self.assertIn("A new run with none of them stops before any worker", text)
        self.assertIn("Removed: the driver reads no model table", text)
        self.assertNotIn("legacy 'Model classes' table answers", text)

    def test_S8_routes_py_describes_selection_without_a_table_fallback(self):
        doc = " ".join((routes.__doc__ or "").split())
        self.assertIn("reads no model table", doc)
        self.assertIn("routes-pi.json", doc)
        self.assertFalse(hasattr(routes, "legacy"))
        self.assertFalse(hasattr(routes, "legacy_warning"))

    def test_S8_the_packaged_models_note_holds_no_model_table(self):
        text = (PACKAGE / "models.md").read_text(encoding="utf-8")
        self.assertNotIn("| Class |", text)
        for word in ("route file", ".orchestrate/routes.json", "routes-pi.json", "Migrating"):
            self.assertIn(word, text)

    def test_S8_pi_docs_describe_precedence_migration_and_restart(self):
        text = " ".join((ROOT / "docs" / "pi.md").read_text(encoding="utf-8").split())
        for phrase in (
            "reads no model table",
            "routes-pi.json",
            "`--models-file` is refused",
            "keeps the routes its ledger recorded",
            "needs `--routes-file`",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("`Pi` column", text)

    def test_S8_the_spec_routing_requirements_name_typed_config_and_restart(self):
        spec = (ROOT / "docs" / "specs" / "orchestrator-engine.md").read_text(encoding="utf-8")
        # The check-tiers section numbers its own requirements from R93 again: read the first section only.
        section = spec.split("\n## Coverage", 1)[0]
        rows = {
            line.split("**")[1]: line for line in section.splitlines() if line.startswith("- **R")
        }
        self.assertIn("reads no model table", rows["R19"])
        self.assertIn("`--models-file` is refused", rows["R19"])
        self.assertIn("routes-codex.json", rows["R20"])
        self.assertIn("routes-pi.json", rows["R20"])
        self.assertIn("explicit `--routes-file`", rows["R94"])
        self.assertNotIn("legacy table with a brain backend", rows["R94"])


if __name__ == "__main__":
    unittest.main()
