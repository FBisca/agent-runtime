"""Compatibility contract (real boundaries): subprocesses, the durable store and git ancestry.

The in-process half of the contract lives in `tests/test_compatibility_contract.py`. This file
drives the boundaries a packaging move would break: the hook shim as a process, the CLI bridge as a
process, the durable store on disk and the commits that carry the PR #39 and PR #45 semantics.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
# The runtime modules live in a flat directory and import each other by bare name, so the
# directory goes on the path before they are imported.
sys.path.insert(0, str(PLUGIN / "packages" / "agent-runtime" / "src"))
from agent_runtime import dispatch_store  # noqa: E402  # pyright: ignore[reportMissingImports]

pytestmark = pytest.mark.integration

BASELINE = PLUGIN / "docs" / "compat" / "compatibility-baseline.json"


def baseline():
    return json.loads(BASELINE.read_text())


class EntrypointProcessTests(unittest.TestCase):
    """R199: the shims still answer as processes."""

    def test_S2_the_hook_shim_still_fails_open(self):
        """python-workspace-contracts/R199"""
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        done = subprocess.run([str(PLUGIN / "bin" / "agent-runtime-hook"), "--cli", "codex"],
                              input=payload, capture_output=True, text=True, timeout=20)
        self.assertEqual(0, done.returncode, done.stderr)
        if done.stdout.strip():
            json.loads(done.stdout)  # a hook that answers, answers with JSON

    def test_S2_the_cli_bridge_reaches_the_public_tools(self):
        """python-workspace-contracts/R199"""
        done = subprocess.run([sys.executable, "-B", str(PLUGIN / "server" / "agent_runtime.py"),
                               "call", "no-such-tool"], capture_output=True, text=True, timeout=20)
        self.assertNotEqual(0, done.returncode, done.stdout)
        for tool in baseline()["orchestration_verbs"].values():
            self.assertIn(tool, done.stdout + done.stderr, tool)


class PrAncestryTests(unittest.TestCase):
    """R205: the pinned PRs stay in the history of this branch."""

    def _git(self, *args, timeout=300):
        return subprocess.run(["git", *args], cwd=PLUGIN,
                              capture_output=True, text=True, timeout=timeout)

    def _ensure_the_history_is_there(self):
        """Answer the ancestry question in a checkout that has the history.

        A shallow checkout (CI checks out depth 1) does not hold the pinned commits, so
        `git merge-base` answers "I do not know" instead of the contract's question. The
        history is fetched first, so the check still runs against the real history.
        """
        if self._git("rev-parse", "--is-shallow-repository").stdout.strip() == "true":
            done = self._git("fetch", "--unshallow", "--quiet", "origin")
            if done.returncode != 0:
                # The checkout may carry a refspec the remote does not have (a pull
                # request merge ref, say); the branches carry the same history.
                done = self._git("fetch", "--unshallow", "--quiet", "origin",
                                 "+refs/heads/*:refs/remotes/origin/*")
            if done.returncode != 0:
                # Without the history the ancestry question cannot be answered at all, and
                # a runner that is offline or has no credentials is not a contract failure.
                self.skipTest("the checkout is shallow and its history could not be fetched "
                              f"(offline or no credentials): {done.stderr.strip()}")
        for pr, commit in baseline()["pr_pins"].items():
            present = self._git("cat-file", "-e", commit)
            self.assertEqual(0, present.returncode,
                             f"PR #{pr} ({commit}) is not in this checkout's object store; "
                             "fetch the history (git fetch --unshallow origin) and try again")

    def test_S7_the_pinned_pr_commits_are_ancestors_of_this_branch(self):
        """python-workspace-contracts/R205"""
        self._ensure_the_history_is_there()
        for pr, commit in baseline()["pr_pins"].items():
            done = self._git("merge-base", "--is-ancestor", commit, "HEAD", timeout=30)
            self.assertEqual(0, done.returncode, f"PR #{pr} ({commit}) is not in this branch")

    def test_S7_the_pinned_commits_carry_the_semantics_the_baseline_records(self):
        """python-workspace-contracts/R205

        The pinned commit must be the head that carries the semantics the plan names for
        that PR (#39 resume/undelivered attention, #45 session usage attribution), not an
        older commit that happens to be in the same branch.
        """
        self._ensure_the_history_is_there()
        data = baseline()
        for pr, commit in data["pr_pins"].items():
            subject = self._git("log", "-1", "--format=%s", commit).stdout.strip()
            self.assertTrue(subject, f"PR #{pr} ({commit}) has no subject")
            keyword = data["pr_semantics"][pr]
            self.assertIn(keyword.lower(), subject.lower(),
                          f"PR #{pr} is pinned to {commit} ({subject!r}), which does not carry "
                          f"the {keyword!r} semantics the plan describes")
            title = data["pr_titles"].get(pr)
            if title is not None:
                self.assertEqual(title, subject,
                                 f"PR #{pr} is pinned to {commit}, whose subject is not the "
                                 "recorded title")


class InstallerProcessTests(unittest.TestCase):
    """R199: the installer keeps its documented wiring and its refusals."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin_dir = Path(self.tmp.name) / "bin"
        self.bin_dir.mkdir()

    def _install(self, env=None):
        environment = dict(os.environ)
        environment["BIN_DIR"] = str(self.bin_dir)
        environment.update(env or {})
        return subprocess.run([str(PLUGIN / "install.sh")], capture_output=True, text=True,
                              timeout=60, env=environment)

    def test_S2_the_installer_links_every_shim(self):
        """python-workspace-contracts/R199"""
        installer = baseline()["installer"]
        done = self._install()
        self.assertEqual(0, done.returncode, done.stderr)
        for name in installer["shims"]:
            link = self.bin_dir / name
            self.assertTrue(link.is_symlink(), f"{name} was not linked")
            self.assertEqual((PLUGIN / "bin" / name).read_text(), link.read_text())
        self.assertEqual(sorted(baseline()["entrypoints"]), sorted(installer["shims"]))

    def test_S2_the_installer_takes_the_documented_bin_dir(self):
        """python-workspace-contracts/R199"""
        installer = baseline()["installer"]
        other = Path(self.tmp.name) / "other-bin"
        other.mkdir()
        done = self._install({installer["bin_dir_env"]: str(other)})
        self.assertEqual(0, done.returncode, done.stderr)
        for name in installer["shims"]:
            self.assertTrue((other / name).is_symlink(), f"{name} was not linked")

    def test_S2_the_installer_refuses_to_overwrite_a_non_symlink(self):
        """python-workspace-contracts/R199"""
        installer = baseline()["installer"]
        (self.bin_dir / "agent-runtime").write_text("not a symlink\n")
        done = self._install()
        self.assertNotEqual(0, done.returncode)
        self.assertIn(installer["refusal_message"], done.stderr)
        self.assertEqual("not a symlink\n", (self.bin_dir / "agent-runtime").read_text())


class StoreFormatTests(unittest.TestCase):
    """R203: the durable format is stable and no state migration happens."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"HOME": self.tmp.name}

    def test_S8_the_store_keeps_its_schema_version_and_tables(self):
        """python-workspace-contracts/R203"""
        store = dispatch_store.Store(self.env)
        with store.transaction() as db:
            self.assertEqual(baseline()["store"]["schema_version"],
                             db.execute("PRAGMA user_version").fetchone()[0])
            tables = sorted(r[0] for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
            self.assertEqual(sorted(baseline()["store"]["tables"]), tables)

    def test_S8_a_legacy_store_opens_without_a_state_migration(self):
        """python-workspace-contracts/R203"""
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
