"""Compatibility contract (real boundaries): subprocesses, the durable store and git ancestry.

The in-process half of the contract lives in `tests/test_compatibility_contract.py`. This file
drives the boundaries a packaging move would break: the hook shim as a process, the CLI bridge as a
process, the durable store on disk and the commits that carry the PR #39 and PR #45 semantics.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "server"))
import dispatch_store  # noqa: E402

pytestmark = pytest.mark.integration

BASELINE = PLUGIN / "docs" / "compat" / "compatibility-baseline.json"


def baseline():
    return json.loads(BASELINE.read_text())


class EntrypointProcessTests(unittest.TestCase):
    """S2: the shims still answer as processes."""

    def test_S2_the_hook_shim_still_fails_open(self):
        """uv-workspace/S2"""
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        done = subprocess.run([str(PLUGIN / "bin" / "agent-runtime-hook"), "--cli", "codex"],
                              input=payload, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, done.returncode, done.stderr)
        if done.stdout.strip():
            json.loads(done.stdout)  # a hook that answers, answers with JSON

    def test_S2_the_cli_bridge_reaches_the_public_tools(self):
        """uv-workspace/S2"""
        done = subprocess.run([sys.executable, "-B", str(PLUGIN / "server" / "agent_runtime.py"),
                               "call", "no-such-tool"], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(0, done.returncode, done.stdout)
        for tool in baseline()["orchestration_verbs"].values():
            self.assertIn(tool, done.stdout + done.stderr, tool)


class PrAncestryTests(unittest.TestCase):
    """S7: the pinned PRs stay in the history of this branch."""

    def test_S7_the_pinned_pr_commits_are_ancestors_of_this_branch(self):
        """uv-workspace/S7"""
        for pr, commit in baseline()["pr_pins"].items():
            done = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                                  cwd=PLUGIN, capture_output=True, text=True, timeout=30)
            self.assertEqual(0, done.returncode, f"PR #{pr} ({commit}) is not in this branch")


class StoreFormatTests(unittest.TestCase):
    """S8: the durable format is stable and no state migration happens."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name}

    def test_S8_the_store_keeps_its_schema_version_and_tables(self):
        """uv-workspace/S8"""
        store = dispatch_store.Store(self.env)
        with store.transaction() as db:
            self.assertEqual(baseline()["store"]["schema_version"],
                             db.execute("PRAGMA user_version").fetchone()[0])
            tables = sorted(r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
            self.assertEqual(sorted(baseline()["store"]["tables"]), tables)

    def test_S8_a_legacy_store_opens_without_a_state_migration(self):
        """uv-workspace/S8"""
        path = Path(self.env["HOME"]) / ".local" / "state" / "agent-runtime" / "dispatch.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE workers (id TEXT PRIMARY KEY, team TEXT, name TEXT, address TEXT,"
                   " active INTEGER, UNIQUE(team,name))")
        db.execute("INSERT INTO workers VALUES ('w1','t','worker','addr',1)")
        db.execute("PRAGMA user_version=1")
        db.commit()
        db.close()

        store = dispatch_store.Store(self.env)  # the legacy file is opened as it is
        with store.transaction() as db:
            self.assertEqual(2, db.execute("PRAGMA user_version").fetchone()[0])
            row = db.execute("SELECT id, name, address FROM workers WHERE id='w1'").fetchone()
            self.assertEqual(("w1", "worker", "addr"), tuple(row))
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("tasks", tables)  # additive, not a rewrite
