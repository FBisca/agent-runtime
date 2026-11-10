"""The orchestrator member's contracts: the seam to the runtime, the typed boundary, the CLI.

These are the behaviours the plan's scenarios name:

- S3: the orchestrator reaches the runtime through the public CLI, never through its store;
- S4: the typed boundary rejects unknown or invalid fields with a deterministic error;
- S8: the member keeps no state of its own and migrates none.
"""

import ast
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.unit


REPO = Path(__file__).resolve().parents[1]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
RUNTIME = REPO / "packages" / "agent-runtime" / "src" / "agent_runtime"

sys.path.insert(0, str(MEMBER.parent))
from agent_orchestrator import contracts, routes  # noqa: E402
from agent_orchestrator.runtime_client import CliRuntimeClient  # noqa: E402


def sources(package: Path):
    """Every source file of a package member."""
    return sorted(package.rglob("*.py"))


def imports_of(path: Path):
    """The modules a source file imports, absolute and relative."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


class SeamTests(unittest.TestCase):
    """R200: the orchestrator reaches the runtime through the public CLI."""

    def test_S3_no_source_of_the_member_touches_the_runtime_store(self):
        """python-workspace-contracts/R200"""
        for path in sources(MEMBER):
            names = imports_of(path)
            self.assertNotIn("sqlite3", names, "%s imports the database" % path.name)
            self.assertNotIn("agent_runtime", names, "%s imports the runtime's modules" % path.name)
            self.assertNotIn("dispatch_store", names, "%s imports the runtime's store" % path.name)

    def test_S3_the_client_calls_the_runtime_cli_and_returns_its_answer(self):
        """python-workspace-contracts/R200"""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "argv"
            fake = Path(tmp) / "agent-runtime"
            fake.write_text(
                "#!/bin/sh\nprintf '%s\\n' \"$@\" > "
                + str(log)
                + '\nprintf \'{"assignment_id": "a1"}\'\n',
                encoding="utf-8",
            )
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"PATH": "%s:%s" % (tmp, os.environ["PATH"])}):
                answer = CliRuntimeClient("lead-1").call("spawn", {"name": "w1"}, timeout=30)
            self.assertEqual(answer, {"assignment_id": "a1"})
            argv = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(argv[:4], ["call", "spawn", "--lead", "lead-1"])
            self.assertEqual(argv[4], "--json")
            self.assertEqual(json.loads(argv[5]), {"name": "w1"})

    def test_S3_a_failing_runtime_is_reported_not_swallowed(self):
        """python-workspace-contracts/R200"""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "agent-runtime"
            fake.write_text("#!/bin/sh\necho 'boom' >&2\nexit 3\n", encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
            with mock.patch.dict(os.environ, {"PATH": "%s:%s" % (tmp, os.environ["PATH"])}):
                with self.assertRaises(Exception) as caught:
                    CliRuntimeClient("lead-1").call("spawn", {}, timeout=30)
            self.assertIn("boom", str(caught.exception))


class BoundaryTests(unittest.TestCase):
    """R201, R202: the typed boundary rejects what it does not accept."""

    def report(self, **over):
        row = {
            "contract": 2,
            "task_id": "t1",
            "status": "COMPLETE",
            "summary": "done",
            "acceptance_met": True,
            "files": [],
            "decisions": [],
            "interfaces": [],
            "remaining": [],
            "checks": [],
            "head": "abc1234",
            "pr": None,
        }
        row.update(over)
        return row

    def test_S4_a_report_without_a_task_id_is_refused(self):
        """python-workspace-contracts/R201"""
        row = self.report()
        del row["task_id"]
        with self.assertRaises(contracts.ContractError) as caught:
            contracts.validate_report(row)
        self.assertIn("task_id", str(caught.exception))

    def test_S4_a_report_with_an_unknown_field_is_refused(self):
        """python-workspace-contracts/R201"""
        with self.assertRaises(contracts.ContractError) as caught:
            contracts.validate_report(self.report(surprise=1))
        self.assertIn("surprise", str(caught.exception))

    def test_S4_the_error_is_the_same_every_time(self):
        """python-workspace-contracts/R201"""
        row = self.report(status="MAYBE")
        messages = []
        for _ in range(2):
            try:
                contracts.validate_report(row)
            except contracts.ContractError as error:
                messages.append(str(error))
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0], messages[1])
        self.assertIn("status", messages[0])

    def test_S4_a_route_policy_naming_an_unknown_class_is_refused(self):
        """python-workspace-contracts/R202"""
        with self.assertRaises(routes.RouteError) as caught:
            routes.validate(
                {"version": 1, "routes": [{"class": "tiny", "backend": "claude", "model": "haiku"}]}
            )
        self.assertIn("class", str(caught.exception))

    def test_S4_a_yaml_route_policy_loads_and_its_unknown_field_is_refused(self):
        """python-workspace-contracts/R202"""
        policy = (
            "version: 1\n"
            "routes:\n"
            "  - class: small\n"
            "    backend: claude\n"
            "    model: haiku\n"
            "    effort: low\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routes.yaml"
            path.write_text(policy, encoding="utf-8")
            config = routes.load(path)
            self.assertEqual([route.klass for route in config.routes], ["small"])
            self.assertEqual(config.routes[0].effort, "low")
            path.write_text(policy + "    surprise: 1\n", encoding="utf-8")
            with self.assertRaises(routes.RouteError):
                routes.load(path)


class CliContractTests(unittest.TestCase):
    """The CLI the skill documented is the CLI the member ships."""

    def test_the_cli_offers_the_documented_commands(self):
        """python-workspace-contracts/R202"""
        env = dict(os.environ, PYTHONPATH=str(MEMBER.parent))
        result = subprocess.run(
            [sys.executable, "-m", "agent_orchestrator", "--help"],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            timeout=60,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in (
            "run",
            "launch",
            "decide",
            "await",
            "show",
            "claim",
            "bind",
            "checkpoint",
            "release",
            "context",
            "metrics",
            "evidence",
            "inspect",
        ):
            self.assertIn(command, result.stdout)


class StateTests(unittest.TestCase):
    """R204: the member keeps no state of its own and migrates none."""

    def test_S8_the_member_writes_no_state_into_the_runtime_store(self):
        """python-workspace-contracts/R204"""
        store = Path.home() / ".local" / "state" / "agent-runtime"
        schema = REPO / "docs" / "compat" / "compatibility-baseline.json"
        recorded = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(recorded["store"]["schema_version"], 2)
        self.assertIn("teams", recorded["store"]["tables"])
        self.assertFalse(store.exists() and not list(store.glob("*.sqlite3")))

    def test_S8_the_runtime_wheel_needs_neither_member_nor_its_dependencies(self):
        """python-workspace-contracts/R204"""
        manifest = (REPO / "packages" / "agent-runtime" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("pydantic", manifest.lower())
        self.assertNotIn("yaml", manifest.lower())
