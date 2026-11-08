"""The rollback contract: the pre-workspace tree still answers, and the store needs no migration.

The plan's S8 says a rollback of the workspace change must leave the legacy CLI shims, the durable
store format and the agent skills working, with no state migration. These tests drive the real
pre-workspace tree, materialised from git, against the tree that replaced it.

The tree under test is the commit before the members landed: its `server/` holds the runtime as flat
modules and its `bin/` holds the shims that exec them. Both are checked here as processes, and the
store is checked in both directions, because a format that only one side can read is a migration in
waiting.

The pre-workspace commit is pinned in the compatibility baseline, so a rollback target that moves is a
contract failure rather than a surprise. A shallow checkout without the history cannot answer the
question at all, so those cases skip with the reason, like the PR-ancestry contract does.
"""

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
RUNTIME_SRC = PLUGIN / "packages" / "agent-runtime" / "src"
BASELINE = PLUGIN / "docs" / "compat" / "compatibility-baseline.json"

pytestmark = pytest.mark.integration


def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def git(*args, timeout=300):
    """Run a git command in the repository."""
    return subprocess.run(
        ["git", *args], cwd=PLUGIN, capture_output=True, text=True, timeout=timeout
    )


class RollbackTree:
    """The pre-workspace tree, materialised in a temporary directory."""

    def __init__(self, directory: Path, commit: str):
        self.directory = directory
        self.commit = commit

    @property
    def server(self) -> Path:
        return self.directory / "server"

    def run(self, argv, env=None, stdin=None, timeout=60):
        """Run a command inside the pre-workspace tree."""
        environment = dict(os.environ)
        environment.update(env or {})
        return subprocess.run(
            argv,
            cwd=self.directory,
            capture_output=True,
            text=True,
            input=stdin,
            env=environment,
            timeout=timeout,
        )


def materialise(commit: str, directory: Path) -> RollbackTree:
    """Extract the pre-workspace commit into `directory`."""
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=PLUGIN,
        capture_output=True,
        timeout=300,
    )
    assert archive.returncode == 0, f"the tree at {commit} is not in this checkout"
    with tarfile.open(fileobj=BytesIO(archive.stdout)) as tar:
        tar.extractall(directory)
    return RollbackTree(directory, commit)


def open_store(tree: RollbackTree, env):
    """Open the dispatch store with the tree's own runtime code, in its own process.

    The store module is imported under the tree's own `server/` directory, so the legacy flat-module
    layout resolves its siblings the way it did before the move.
    """
    script = (
        "import json, sys\n"
        "sys.path.insert(0, 'server')\n"
        "from dispatch_store import Store\n"
        "store = Store(json.loads(sys.argv[1]))\n"
        "with store.transaction() as db:\n"
        "    version = db.execute('PRAGMA user_version').fetchone()[0]\n"
        "    tables = sorted(r[0] for r in db.execute(\n"
        "        \"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'\"))\n"
        "    rows = [tuple(r) for r in db.execute('SELECT id, name, address FROM workers ORDER BY id')]\n"
        "    teams = [tuple(r) for r in db.execute('SELECT id, lead FROM teams ORDER BY id')]\n"
        "print(json.dumps({'version': version, 'tables': tables, 'workers': rows, 'teams': teams}))\n"
    )
    done = tree.run(
        [sys.executable, "-B", "-c", script, json.dumps(env)],
        timeout=120,
    )
    assert done.returncode == 0, f"the store did not open: {done.stderr}"
    return json.loads(done.stdout.strip().splitlines()[-1])


def write_store(tree: RollbackTree, env):
    """Write one team and one worker with the tree's own runtime code."""
    script = (
        "import json, sys\n"
        "sys.path.insert(0, 'server')\n"
        "from dispatch_store import Store\n"
        "store = Store(json.loads(sys.argv[1]))\n"
        "with store.transaction() as db:\n"
        "    db.execute(\"INSERT INTO teams (id, scope, lead, generation) VALUES ('t1','scope','lead','gen')\")\n"
        '    db.execute("INSERT INTO workers (id, team, name, address, active) '
        "VALUES ('w1','t1','rollback-worker','addr1',1)\")\n"
        "print('written')\n"
    )
    done = tree.run([sys.executable, "-B", "-c", script, json.dumps(env)], timeout=120)
    assert done.returncode == 0, f"the store did not open: {done.stderr}"


class RollbackTests(unittest.TestCase):
    """R203: the tree before the move still works, and the store survives the round trip."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.commit = baseline()["rollback"]["pre_workspace_commit"]
        self.store_env = {
            "HOME": str(self.directory / "home"),
            "XDG_STATE_HOME": str(self.directory / "state"),
        }
        (self.directory / "home").mkdir()
        (self.directory / "state").mkdir()

    def _ensure_the_history_is_there(self):
        """Answer the rollback question only in a checkout that holds the history.

        A shallow checkout (CI checks out depth 1) does not hold the pre-workspace commit, so the
        question cannot be answered at all. The history is fetched first; a runner that is offline
        or has no credentials skips instead of failing, because that is not a contract failure.
        """
        if git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
            done = git("fetch", "--unshallow", "--quiet", "origin")
            if done.returncode != 0:
                done = git(
                    "fetch",
                    "--unshallow",
                    "--quiet",
                    "origin",
                    "+refs/heads/*:refs/remotes/origin/*",
                )
            if done.returncode != 0:
                self.skipTest(
                    "the checkout is shallow and its history could not be fetched "
                    f"(offline or no credentials): {done.stderr.strip()}"
                )
        present = git("cat-file", "-e", self.commit)
        if present.returncode != 0:
            self.skipTest(
                f"the pre-workspace commit {self.commit} is not in this checkout's object store"
            )

    def _tree(self) -> RollbackTree:
        self._ensure_the_history_is_there()
        return materialise(self.commit, self.directory / "tree")

    def test_S8_the_legacy_tree_still_answers_through_its_shims(self):
        """python-workspace-contracts/R203"""
        tree = self._tree()

        # The CLI shim: the shim in `bin/` resolves itself and execs the flat module in `server/`.
        done = tree.run(
            [str(tree.directory / "bin" / "agent-runtime"), "resolve", "--cli", "claude"]
        )
        self.assertEqual(0, done.returncode, done.stderr)
        self.assertIn("runtime=", done.stdout)

        # The MCP server: it answers the handshake and publishes the same tool surface.
        request = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        )
        served = tree.run(
            [sys.executable, "-B", str(tree.server / "mcp_server.py")],
            stdin=request + "\n",
            timeout=60,
        )
        self.assertEqual(0, served.returncode, served.stderr)
        replies = [json.loads(line) for line in served.stdout.splitlines() if line.strip()]
        self.assertEqual("agent-runtime", replies[0]["result"]["serverInfo"]["name"])
        tools = sorted(tool["name"] for tool in replies[1]["result"]["tools"])
        self.assertEqual(sorted(baseline()["mcp_tools"]), tools)

    def test_S8_a_store_written_by_the_new_tree_opens_in_the_legacy_tree_without_a_migration(self):
        """python-workspace-contracts/R203"""
        tree = self._tree()

        sys.path.insert(0, str(RUNTIME_SRC))
        from agent_runtime import dispatch_store  # noqa: E402

        store = dispatch_store.Store(self.store_env)
        with store.transaction() as db:
            db.execute(
                "INSERT INTO teams (id, scope, lead, generation) VALUES ('t1','scope','lead','gen')"
            )
            db.execute(
                "INSERT INTO workers (id, team, name, address, active) VALUES ('w1','t1','rollback-worker','addr1',1)"
            )

        opened = open_store(tree, self.store_env)
        self.assertEqual(baseline()["store"]["schema_version"], opened["version"])
        self.assertEqual(sorted(baseline()["store"]["tables"]), opened["tables"])
        self.assertEqual([["w1", "rollback-worker", "addr1"]], opened["workers"])
        self.assertEqual([["t1", "lead"]], opened["teams"])

    def test_S8_a_store_written_by_the_legacy_tree_opens_in_the_new_tree_without_a_migration(self):
        """python-workspace-contracts/R203"""
        tree = self._tree()
        write_store(tree, self.store_env)

        sys.path.insert(0, str(RUNTIME_SRC))
        from agent_runtime import dispatch_store  # noqa: E402

        store = dispatch_store.Store(self.store_env)
        with store.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = sorted(
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            )
            workers = [
                tuple(r) for r in db.execute("SELECT id, name, address FROM workers ORDER BY id")
            ]

        self.assertEqual(baseline()["store"]["schema_version"], version)
        self.assertEqual(sorted(baseline()["store"]["tables"]), tables)
        self.assertEqual([("w1", "rollback-worker", "addr1")], workers)


if __name__ == "__main__":
    unittest.main()
