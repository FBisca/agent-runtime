"""Compatibility contract (in-process): the entrypoints, the public surface and the durable format.

These tests are characterization tests for the migration to a uv workspace (plan
`2026-11-07-uv-workspace-python-standards.plan.md`, scenarios S2, S3, S7 and S8). They pin what
today's tree exposes so a later packaging move cannot change it silently:

* S2: the `agent-runtime*` shims, the CLI subcommands and the installer wiring keep working.
* S3: orchestration reaches the runtime through the public tool surface, not through the store.
* S7: the semantics merged by PR #39 and PR #45 stay pinned to their commits and keep their
  regression coverage instead of being duplicated here.
* S8: the runtime stays standard-library only and the durable format is recorded.

The baseline the tests assert on lives in `docs/compat/compatibility-baseline.json`; the narrative
inventory lives in `docs/compat/2026-11-07-compatibility-baseline.md`. A change to the tree that is
not a change to the baseline must fail here first.

The boundary-crossing half of the contract (subprocesses, the filesystem, git ancestry) lives in
`tests/test_compatibility_contract_integration.py`.
"""

import argparse
import ast
import json
import os
import sys
import unittest
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "server"))
import agent_runtime  # noqa: E402
import mcp_server  # noqa: E402

pytestmark = pytest.mark.unit

BASELINE = PLUGIN / "docs" / "compat" / "compatibility-baseline.json"


def baseline():
    return json.loads(BASELINE.read_text())


class BaselineTests(unittest.TestCase):
    """The recorded inventory must exist and describe this tree."""

    def test_the_baseline_inventory_is_recorded(self):
        """uv-workspace/S2"""
        data = baseline()
        for key in ("entrypoints", "cli_subcommands", "mcp_tools", "orchestration_verbs",
                    "store", "runtime_dependencies", "pr_pins", "sqlite_imports",
                    "regression_coverage"):
            self.assertIn(key, data, key)


class EntrypointTests(unittest.TestCase):
    """S2: the documented entrypoints keep their behaviour."""

    def test_S2_entrypoint_shims_are_present_and_executable(self):
        """uv-workspace/S2"""
        for name, entry in baseline()["entrypoints"].items():
            path = PLUGIN / entry["path"]
            self.assertTrue(path.is_file(), name)
            self.assertTrue(os.access(path, os.X_OK), f"{name} is not executable")
            self.assertTrue(path.read_text().startswith("#!/bin/sh"), name)

    def test_S2_entrypoint_shims_resolve_to_their_python_entrypoints(self):
        """uv-workspace/S2"""
        for name, entry in baseline()["entrypoints"].items():
            target = PLUGIN / entry["python"]
            self.assertTrue(target.is_file(), f"{name} -> {entry['python']}")
            self.assertIn(entry["python"], (PLUGIN / entry["path"]).read_text(), name)

    def test_S2_recorded_entrypoints_match_the_bin_directory(self):
        """uv-workspace/S2"""
        recorded = {entry["path"] for entry in baseline()["entrypoints"].values()}
        present = {f"bin/{p.name}" for p in (PLUGIN / "bin").iterdir() if p.is_file()}
        self.assertEqual(present, recorded)

    def test_S2_the_installer_links_every_entrypoint_and_wires_the_hooks(self):
        """uv-workspace/S2"""
        installer = (PLUGIN / "install.sh").read_text()
        for name in baseline()["entrypoints"]:
            self.assertIn(name, installer, name)
        self.assertIn("[mcp_servers.agent-runtime]", installer)
        self.assertIn("agent-runtime-mcp", installer)
        self.assertIn("agent-runtime-hook", installer)
        self.assertIn("[[hooks.PreToolUse]]", installer)

    def test_S2_the_cli_keeps_its_subcommands(self):
        """uv-workspace/S2"""
        parser = agent_runtime.build_parser()
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        self.assertEqual(1, len(subs))
        self.assertEqual(sorted(baseline()["cli_subcommands"]), sorted(subs[0].choices))


class PublicSurfaceTests(unittest.TestCase):
    """S3: orchestration talks to the runtime through the public surface."""

    def test_S3_the_public_tool_surface_covers_the_orchestration_verbs(self):
        """uv-workspace/S3"""
        tools = set(mcp_server.TOOL_NAMES)
        self.assertEqual(sorted(baseline()["mcp_tools"]), sorted(tools))
        for verb, tool in baseline()["orchestration_verbs"].items():
            self.assertIn(tool, tools, f"{verb} -> {tool}")

    def test_S3_direct_sqlite_access_stays_inside_the_runtime_store_layer(self):
        """uv-workspace/S3"""
        recorded = sorted(baseline()["sqlite_imports"])
        found = []
        for path in sorted((PLUGIN / "server").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name == "sqlite3" or name.startswith("sqlite3.") for name in names):
                    found.append(f"server/{path.name}")
                    break
        self.assertEqual(recorded, found)


class PrPinTests(unittest.TestCase):
    """S7: the semantics merged by PR #39 and PR #45 stay pinned."""

    def test_S7_the_pr_commits_are_pinned_in_the_baseline(self):
        """uv-workspace/S7"""
        pins = baseline()["pr_pins"]
        self.assertIn("39", pins)
        self.assertIn("45", pins)

    def test_S7_resume_and_usage_semantics_keep_their_own_regression_tests(self):
        """uv-workspace/S7"""
        coverage = baseline()["regression_coverage"]
        for name, needles in coverage.items():
            path = PLUGIN / name
            self.assertTrue(path.is_file(), name)
            text = path.read_text()
            for needle in needles:
                self.assertIn(needle, text, f"{name} lost its {needle} coverage")

    def test_S7_the_baseline_pins_the_semantics_the_plan_describes_for_each_pr(self):
        """uv-workspace/S7

        The plan's Sources table names PR #39 as the resume/undelivered-attention work and
        PR #45 as the session-usage attribution work. The baseline must say which semantics
        each pinned PR carries, so pinning the wrong commit is caught here and not by a
        reader squinting at the JSON.
        """
        data = baseline()
        semantics = data.get("pr_semantics")
        self.assertIsNotNone(semantics, "the baseline does not record the semantics per PR")
        for pr in data["pr_pins"]:
            self.assertIn(pr, semantics, f"PR #{pr} has no recorded semantics")
            self.assertTrue(semantics[pr].strip(), f"PR #{pr} has empty semantics")

    def test_S7_each_pr_pins_the_regression_tests_that_own_its_semantics(self):
        """uv-workspace/S7

        The regression tests that own the live resume/session semantics must be pinned per
        PR: deleting them must break the contract, not just shrink the suite.
        """
        data = baseline()
        coverage = data.get("pr_regression_coverage")
        self.assertIsNotNone(coverage, "the baseline does not record the regression tests per PR")
        for pr in data["pr_pins"]:
            self.assertIn(pr, coverage, f"PR #{pr} pins no regression test")
            self.assertTrue(coverage[pr], f"PR #{pr} pins an empty regression test list")
            for name, needles in coverage[pr].items():
                path = PLUGIN / name
                self.assertTrue(path.is_file(), name)
                text = path.read_text()
                for needle in needles:
                    self.assertIn(needle, text, f"{name} lost its {needle} coverage")

    def test_S7_this_contract_does_not_duplicate_the_existing_test_names(self):
        """uv-workspace/S7"""
        mine = {name for name in dir(PrPinTests) if name.startswith("test_")}
        for path in (PLUGIN / "tests").glob("test_*.py"):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text()
            for name in mine:
                if name in text:
                    self.fail(f"{path.name} duplicates the contract test {name}")


class DependencyTests(unittest.TestCase):
    """S2/S8: the runtime stays standard-library only at deployment."""

    def test_the_runtime_declares_no_production_dependency(self):
        """uv-workspace/S2"""
        pyproject = (PLUGIN / "pyproject.toml").read_text()
        self.assertEqual(baseline()["runtime_dependencies"], [])
        self.assertIn("dependencies = []", pyproject)

    def test_the_runtime_imports_only_the_standard_library(self):
        """uv-workspace/S2"""
        siblings = {p.stem for p in (PLUGIN / "server").glob("*.py")}
        third_party = set()
        for path in sorted((PLUGIN / "server").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [(node.module or "").split(".")[0]] if node.level == 0 else []
                else:
                    continue
                for name in names:
                    if name and name not in sys.stdlib_module_names and name not in siblings:
                        third_party.add(name)
        self.assertEqual(set(), third_party)


class NotYetBuiltTests(unittest.TestCase):
    """Red contracts for the tickets that build the workspace; they fail until then."""

    @pytest.mark.xfail(strict=True, reason="T2/T3: the workspace packages do not exist yet")
    def test_S2_the_workspace_packages_exist_beside_the_legacy_shims(self):
        """uv-workspace/S2"""
        self.assertTrue((PLUGIN / "packages" / "agent-runtime" / "pyproject.toml").is_file())

    @pytest.mark.xfail(strict=True, reason="T4: the orchestrator has no public runtime client yet")
    def test_S3_the_orchestrator_talks_to_a_public_runtime_client(self):
        """uv-workspace/S3"""
        self.assertTrue((PLUGIN / "packages" / "agent-orchestrator").is_dir())
