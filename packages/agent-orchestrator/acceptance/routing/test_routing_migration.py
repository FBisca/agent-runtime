import json
import unittest

import pytest
from fixtures import RunFixture

pytestmark = pytest.mark.integration


def document(backend, prefix):
    return {
        "version": 1,
        "routes": [
            {"class": klass, "backend": backend, "model": prefix + klass}
            for klass in ("small", "medium", "large")
        ],
    }


class RoutingMigrationOracle(RunFixture, unittest.TestCase):
    def profile(self, name, backend, prefix):
        directory = self.root / "home" / ".config" / "orchestrate"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text(json.dumps(document(backend, prefix)), encoding="utf-8")
        return path

    def run_without_fixture_models(self, *args):
        self.alias_state(self.plan)
        return self.cli(
            "run",
            str(self.plan),
            "--state-dir",
            str(self.state_dir),
            "--wait-seconds",
            "1",
            "--idle-interval",
            "0",
            *args,
        )

    def test_S1_no_config_refuses_before_spawn_with_a_way_out(self):
        table = self.root / "home" / ".claude" / "CLAUDE.md"
        table.parent.mkdir(parents=True, exist_ok=True)
        table.write_text(self.models.read_text(encoding="utf-8"), encoding="utf-8")
        result = self.run_without_fixture_models()
        self.assertEqual(result.returncode, 2)
        self.assertIn("--routes-file", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S2_codex_selects_named_profile_instead_of_claude_default(self):
        self.profile("routes.json", "claude", "claude-")
        self.profile("routes-codex.json", "codex", "codex-")
        result = self.run_without_fixture_models("--cli", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "codex-small", "low")
        )

    def test_S2_missing_pi_profile_does_not_fall_back(self):
        self.profile("routes.json", "claude", "claude-")
        result = self.run_without_fixture_models("--cli", "pi")
        self.assertEqual(result.returncode, 2)
        self.assertIn("routes-pi", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S4_models_file_is_refused_even_with_valid_config(self):
        config = self.profile("routes.json", "claude", "claude-")
        result = self.run_without_fixture_models(
            "--models-file", str(self.models), "--routes-file", str(config)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--routes-file", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S6_explicit_config_wins_over_cli_profile(self):
        self.profile("routes-codex.json", "codex", "codex-")
        config = self.profile("other.json", "claude", "explicit-")
        result = self.run_without_fixture_models("--cli", "codex", "--routes-file", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("claude", "explicit-small"))

    def test_S3_conflicting_resume_leaves_ledger_and_workers_untouched(self):
        from agent_orchestrator import routes

        self.seed_status(T1="IN PROGRESS")
        path = self.state_dir / "demo.json"
        ledger = json.loads(path.read_text(encoding="utf-8"))
        ledger["routes"] = routes.record(
            {k: routes.Route(k, "claude", "old-" + k) for k in ("small", "medium", "large")},
            "legacy",
            steps={"reviewer": routes.StepRoute("reviewer", "codex", "old-review", "high")},
        )
        path.write_text(json.dumps(ledger), encoding="utf-8")
        before = path.read_bytes()
        config = self.profile("routes.json", "pi", "new-")
        result = self.run_without_fixture_models("--routes-file", str(config))
        self.assertEqual(result.returncode, 2)
        self.assertIn("route", result.stderr.lower())
        self.assertEqual(self.calls("spawn"), [])
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(path.read_bytes(), before)

    def test_S7_incompatible_brain_stops_before_any_worker(self):
        config = self.profile("routes.json", "claude", "claude-")
        result = self.run_without_fixture_models(
            "--routes-file", str(config), "--brain-cli", "codex"
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("codex", result.stderr.lower())
        self.assertTrue("--step" in result.stderr or "medium" in result.stderr.lower())
        self.assertEqual(self.calls("spawn"), [])
        self.assertEqual(self.calls("resume"), [])

    def test_S2_pi_profile_selects_pi_worker(self):
        self.profile("routes.json", "claude", "claude-")
        self.profile("routes-pi.json", "pi", "pi-")
        result = self.run_without_fixture_models("--cli", "pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("pi", "pi-small", "low")
        )

    def test_S3_recorded_legacy_route_drives_resume_without_config(self):
        from agent_orchestrator import routes

        self.seed_status(T1="TO-DO")
        path = self.state_dir / "demo.json"
        ledger = json.loads(path.read_text(encoding="utf-8"))
        ledger["routes"] = routes.record(
            {k: routes.Route(k, "claude", "old-" + k) for k in ("small", "medium", "large")},
            "legacy",
        )
        path.write_text(json.dumps(ledger), encoding="utf-8")
        result = self.run_without_fixture_models()
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("claude", "old-small"))
        self.assertEqual(self.ledger()["routes"]["classes"]["small"]["model"], "old-small")

    def test_S3_incomplete_ledger_migrates_from_explicit_file(self):
        self.seed_status(T1="TO-DO")
        config = self.profile("migration.json", "codex", "migrated-")
        result = self.run_without_fixture_models("--routes-file", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ledger()["routes"]["classes"]["small"]["model"], "migrated-small")
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("codex", "migrated-small"))

    def test_S3_equivalent_explicit_config_is_accepted(self):
        from agent_orchestrator import routes

        self.seed_status(T1="TO-DO")
        path = self.state_dir / "demo.json"
        ledger = json.loads(path.read_text(encoding="utf-8"))
        ledger["routes"] = routes.record(
            {k: routes.Route(k, "claude", "old-" + k) for k in ("small", "medium", "large")},
            "legacy",
        )
        path.write_text(json.dumps(ledger), encoding="utf-8")
        config = self.profile("equivalent.json", "claude", "old-")
        result = self.run_without_fixture_models("--routes-file", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ledger()["routes"]["classes"], ledger["routes"]["classes"])

    def test_S6_repository_policy_beats_backend_profile(self):
        self.profile("routes-codex.json", "codex", "user-")
        directory = self.repo / ".orchestrate"
        directory.mkdir(exist_ok=True)
        (directory / "routes.json").write_text(
            json.dumps(document("claude", "repo-")), encoding="utf-8"
        )
        result = self.run_without_fixture_models("--cli", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("claude", "repo-small"))

    def test_S7_both_explicit_brain_steps_enable_mixed_backends(self):
        config = self.profile("routes.json", "claude", "claude-")
        result = self.run_without_fixture_models(
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
        self.assertEqual(
            (steps["reviewer"]["backend"], steps["reviewer"]["model"]), ("codex", "review")
        )
        self.assertEqual((steps["gaps"]["backend"], steps["gaps"]["model"]), ("codex", "gaps"))

    def test_control_explicit_routes_file_drives_the_worker(self):
        config = self.profile("control.json", "claude", "control-")
        result = self.run_without_fixture_models("--routes-file", str(config))
        self.assertEqual(result.returncode, 0, result.stderr)
        worker = {x["args"]["name"]: x["args"] for x in self.calls("spawn")}["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("claude", "control-small"))
