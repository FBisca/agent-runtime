"""A new run is routed by a typed route file, chosen per backend; the model table answers nothing.

S1 — no applicable typed config stops the run before any worker, saying how to supply one.
S2 — `--cli` picks the backend's own user profile; a missing or invalid one stops the run naming it.
S4 — `--models-file` is refused and no table is read.
S6 — an explicit file beats the repository policy, which beats the backend profile; overrides beat files.
S7 — review gates on another backend need explicit step routes; an incompatible brain stops the run first.
"""

import json
import unittest

import pytest
from fixtures import RunFixture

pytestmark = pytest.mark.integration


def document(backend, prefix, **extra):
    return dict(
        {
            "version": 1,
            "routes": [
                {"class": klass, "backend": backend, "model": prefix + klass}
                for klass in ("small", "medium", "large")
            ],
        },
        **extra,
    )


class TypedSelectionTests(RunFixture, unittest.TestCase):
    def config_dir(self):
        directory = self.root / "home" / ".config" / "orchestrate"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def profile(self, name, backend, prefix, **extra):
        path = self.config_dir() / name
        path.write_text(json.dumps(document(backend, prefix, **extra)), encoding="utf-8")
        return path

    def drive(self, *args):
        """A run with no model table argument: the routes come from the files alone."""
        self.alias_state(self.plan)
        return self.cli(*self.driver_args(*args, routed=False))

    def worker(self, name="demo-t1"):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}[name]

    def test_S1_a_run_without_a_typed_config_stops_before_any_worker_and_names_the_way_out(self):
        table = self.root / "home" / ".claude" / "CLAUDE.md"
        table.parent.mkdir(parents=True)
        table.write_text(self.models.read_text(encoding="utf-8"), encoding="utf-8")
        result = self.drive()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--routes-file", result.stderr)
        self.assertIn("routes.json", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S2_codex_selects_its_own_profile_over_the_claude_one(self):
        self.profile("routes.json", "claude", "claude-")
        self.profile("routes-codex.json", "codex", "codex-")
        result = self.drive("--cli", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = self.worker()
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "codex-small", "low")
        )

    def test_S2_pi_selects_its_own_profile(self):
        self.profile("routes.json", "claude", "claude-")
        self.profile("routes-pi.json", "pi", "pi-")
        result = self.drive("--cli", "pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("pi", "pi-small"))

    def test_S2_claude_is_the_default_backend_and_reads_routes_json(self):
        self.profile("routes-codex.json", "codex", "codex-")
        self.profile("routes.json", "claude", "claude-")
        result = self.drive()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("claude", "claude-small"))

    def test_S2_a_missing_profile_names_the_expected_file_and_does_not_fall_back(self):
        self.profile("routes.json", "claude", "claude-")
        result = self.drive("--cli", "pi")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("routes-pi.json", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S2_an_invalid_profile_stops_the_run_and_does_not_fall_back(self):
        self.profile("routes.json", "claude", "claude-")
        (self.config_dir() / "routes-codex.json").write_text("{not json", encoding="utf-8")
        result = self.drive("--cli", "codex")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("routes-codex.json", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S4_models_file_is_refused_even_beside_a_valid_config(self):
        config = self.profile("routes.json", "claude", "claude-")
        result = self.drive("--models-file", str(self.models), "--routes-file", str(config))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--models-file", result.stderr)
        self.assertIn("--routes-file", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S4_the_model_table_is_never_read(self):
        table = self.root / "home" / ".claude" / "CLAUDE.md"
        table.parent.mkdir(parents=True)
        table.write_text("## Model classes\n\n| Class | Claude |\n|---|---|\n", encoding="utf-8")
        result = self.drive()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertNotIn("Model classes", result.stderr)
        self.assertNotIn("lacks a", result.stderr)

    def test_S6_the_explicit_file_beats_the_repository_policy_and_the_profile(self):
        self.profile("routes-codex.json", "codex", "user-")
        (self.repo / ".orchestrate").mkdir()
        (self.repo / ".orchestrate" / "routes.json").write_text(
            json.dumps(document("claude", "repo-")), encoding="utf-8"
        )
        explicit = self.profile("other.json", "claude", "explicit-")
        result = self.drive("--cli", "codex", "--routes-file", str(explicit))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.worker()["model"], "explicit-small")

    def test_S6_the_repository_policy_beats_the_backend_profile(self):
        self.profile("routes-codex.json", "codex", "user-")
        (self.repo / ".orchestrate").mkdir()
        (self.repo / ".orchestrate" / "routes.json").write_text(
            json.dumps(document("claude", "repo-")), encoding="utf-8"
        )
        result = self.drive("--cli", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.worker()["cli"], self.worker()["model"]), ("claude", "repo-small"))

    def test_S6_a_class_override_beats_the_selected_profile(self):
        self.profile("routes-codex.json", "codex", "user-")
        result = self.drive("--cli", "codex", "--route", "small=codex/over@high")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.worker()["model"], self.worker()["effort"]), ("over", "high"))

    def test_S6_an_invalid_explicit_file_does_not_fall_back_to_the_profile(self):
        self.profile("routes.json", "claude", "claude-")
        broken = self.config_dir() / "broken.json"
        broken.write_text(json.dumps({"version": 1, "routes": []}), encoding="utf-8")
        result = self.drive("--routes-file", str(broken))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S7_a_brain_the_medium_route_does_not_run_on_stops_before_any_worker(self):
        config = self.profile("routes.json", "claude", "claude-")
        result = self.drive("--routes-file", str(config), "--brain-cli", "codex")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--step reviewer=codex/", result.stderr)
        self.assertIn("medium", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S7_explicit_gate_steps_keep_a_brain_on_another_backend_usable(self):
        config = self.profile("routes.json", "claude", "claude-")
        result = self.drive(
            "--routes-file",
            str(config),
            "--brain-cli",
            "codex",
            "--step",
            "reviewer=codex/review@high",
            "--step",
            "gaps=codex/gaps@medium",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        steps = self.ledger()["routes"]["steps"]
        self.assertEqual((steps["reviewer"]["backend"], steps["gaps"]["model"]), ("codex", "gaps"))

    def test_S7_a_medium_route_on_the_brain_backend_needs_no_step_routes(self):
        config = self.config_dir() / "mixed.json"
        data = document("claude", "claude-")
        data["routes"][1] = {"class": "medium", "backend": "codex", "model": "gpt-m"}
        config.write_text(json.dumps(data), encoding="utf-8")
        result = self.drive("--routes-file", str(config), "--brain-cli", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
