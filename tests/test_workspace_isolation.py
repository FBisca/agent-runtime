"""The workspace contract: both members resolve (S1), the runtime stands alone (S5), CI runs the
toolchain (S6).

These checks drive the real boundaries: `uv` resolves and builds the members, a throwaway
environment installs the wheels, and the isolated runtime answers as a CLI and as an MCP server.
"""

import json
import os
import re
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
RUNTIME_MEMBER = REPO / "packages" / "agent-runtime"
ORCHESTRATOR_MEMBER = REPO / "packages" / "agent-orchestrator"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def run(command, **kwargs):
    """Run a command in the repository and return the completed process."""
    return subprocess.run(command, cwd=REPO, capture_output=True, text=True, **kwargs)


def build_wheels(out_dir):
    """Build the wheel of every workspace member into `out_dir`."""
    for member in (RUNTIME_MEMBER, ORCHESTRATOR_MEMBER):
        result = run(["uv", "build", "--wheel", "--out-dir", str(out_dir), str(member)])
        assert result.returncode == 0, f"building {member.name} failed: {result.stderr}"
    return sorted(out_dir.glob("*.whl"))


def wheel_metadata(path, name="METADATA"):
    """Read one metadata file out of a wheel."""
    with zipfile.ZipFile(path) as archive:
        for entry in archive.namelist():
            if entry.endswith(f".dist-info/{name}"):
                return archive.read(entry).decode("utf-8")
    raise AssertionError(f"{path.name} has no {name}")


class WorkspaceTests(unittest.TestCase):
    """S1: the workspace resolves reproducibly."""

    def test_S1_uv_lock_is_current_for_both_members(self):
        result = run(["uv", "lock", "--check"])
        self.assertEqual(result.returncode, 0, f"the lockfile is stale: {result.stderr}")

        lock = (REPO / "uv.lock").read_text(encoding="utf-8")
        for member in ("agent-runtime", "agent-orchestrator"):
            self.assertIn(f'name = "{member}"', lock)

    def test_S1_uv_sync_frozen_installs_both_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            environment = Path(tmp) / "env"
            result = run(
                ["uv", "sync", "--frozen", "--all-packages"],
                env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)},
            )
            self.assertEqual(result.returncode, 0, f"uv sync failed: {result.stderr}")
            for member in ("agent-runtime", "agent-orchestrator"):
                check = run(
                    [
                        str(environment / "bin" / "python"),
                        "-c",
                        f"import importlib.metadata as m; print(m.version({member!r}))",
                    ],
                )
                self.assertEqual(check.returncode, 0, f"{member} is not installed: {check.stderr}")
                self.assertEqual(check.stdout.strip(), "0.1.0")


class IsolationTests(unittest.TestCase):
    """S5: the runtime wheel stands alone."""

    def test_S5_runtime_wheel_works_without_orchestrator_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "wheels"
            out_dir.mkdir()
            build_wheels(out_dir)
            runtime_wheel = next(out_dir.glob("agent_runtime-*.whl"))

            venv = Path(tmp) / "venv"
            created = run(["uv", "venv", str(venv)])
            self.assertEqual(created.returncode, 0, created.stderr)
            python = venv / "bin" / "python"
            installed = run(
                ["uv", "pip", "install", "--python", str(python), "--no-deps", str(runtime_wheel)]
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)

            imported = run([str(python), "-c", "import agent_runtime, mcp_server"])
            self.assertEqual(
                imported.returncode, 0, f"the runtime wheel does not import: {imported.stderr}"
            )

            for orchestrator_only in ("pydantic", "yaml"):
                missing = run([str(python), "-c", f"import {orchestrator_only}"])
                self.assertNotEqual(
                    missing.returncode,
                    0,
                    f"{orchestrator_only} leaked into the runtime environment",
                )

            cli = run([str(venv / "bin" / "agent-runtime"), "--help"])
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertIn("usage", cli.stdout.lower())

            request = "\n".join(
                [
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                ]
            )
            mcp = subprocess.run(
                [str(venv / "bin" / "agent-runtime-mcp")],
                input=request + "\n",
                capture_output=True,
                text=True,
                timeout=60,
                cwd=tmp,
            )
            self.assertEqual(mcp.returncode, 0, mcp.stderr)
            replies = [json.loads(line) for line in mcp.stdout.splitlines() if line.strip()]
            self.assertEqual(replies[0]["result"]["serverInfo"]["name"], "agent-runtime")
            self.assertTrue(replies[1]["result"]["tools"], "the MCP server offers no tool")

    def test_S5_member_manifests_declare_their_own_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "wheels"
            out_dir.mkdir()
            build_wheels(out_dir)
            runtime = wheel_metadata(next(out_dir.glob("agent_runtime-*.whl")))
            orchestrator = wheel_metadata(next(out_dir.glob("agent_orchestrator-*.whl")))

        self.assertNotIn("Requires-Dist:", runtime)
        self.assertIn("requires-dist: pydantic", orchestrator.lower())
        self.assertIn("requires-dist: pyyaml", orchestrator.lower())


class ToolchainTests(unittest.TestCase):
    """S6: CI runs the tests, the linters and the isolated install checks."""

    def test_S6_workflow_runs_the_toolchain_on_the_matrix(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for command in ("ruff check .", "ruff format --check .", "ty check"):
            self.assertIn(command, workflow)
        self.assertIn("uv sync --frozen", workflow)
        self.assertIn("uv run pytest -n 4", workflow)
        self.assertIn("shellcheck", workflow)
        for entry in ("ubuntu-latest", "macos-latest", '"3.11"', '"3.12"', '"3.13"'):
            self.assertIn(entry, workflow)
        self.assertTrue(
            re.search(r"uv build --wheel", workflow), "CI does not build the member wheels"
        )
        self.assertTrue(
            re.search(r"--no-deps", workflow), "CI does not install the runtime wheel in isolation"
        )


if __name__ == "__main__":
    unittest.main()
