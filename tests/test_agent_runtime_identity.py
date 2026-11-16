"""Session identity: who the caller is, proved by the process ancestry; one generation per process,
whatever session ids it carries; and the one hook exception the docs must name."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
from agent_runtime import agent_runtime as app  # noqa: E402

pytestmark = pytest.mark.integration


class CallerIdentityTests(unittest.TestCase):
    """S16: resolve --json reports who the caller is, proved by the process ancestry.

    hc is faked in its real shapes: `hc ls` rows carry no pid; `hc get` carries pid and parent."""

    CHAIN = {50: (40, "bash"), 40: (30, "node worker"), 30: (1, "claude")}

    def caller(self, env, terminals):
        """terminals: address -> `hc get` reply; every one is listed live unless its state says else."""

        def get(address):
            if address not in terminals:
                raise RuntimeError("no such terminal")
            return {k: v for k, v in terminals[address].items() if k != "state"}

        def run(argv):
            self.assertEqual(argv[:2], ["hc", "ls"])
            rows = [
                {"address": a, "agent": "claude-code", "state": t.get("state", "live")}
                for a, t in terminals.items()
            ]
            return json.dumps({"terminals": rows})

        real_resolve = app.resolve
        buf = io.StringIO()
        with (
            patch.dict(app.os.environ, env, clear=True),
            patch.object(app.os, "getppid", return_value=50),
            patch.object(app, "ps_entry", side_effect=self.CHAIN.get),
            patch.object(app, "hc_get", get),
            patch.object(app, "run_command", run),
            patch.object(app, "resolve", side_effect=lambda e, c: real_resolve(e, c, get)),
            redirect_stdout(buf),
        ):
            app.main(["resolve", "--json", "--cli", "claude"])
        return json.loads(buf.getvalue())["caller"]

    def test_S16_ancestry_in_a_child_terminal_is_a_verified_worker(self):
        """agent-runtime/R255"""
        self.assertEqual(
            self.caller({}, {"t1": {"pid": 40, "parent": "tX"}}),
            {"kind": "worker", "verified": True},
            "observed worker ancestry wins over missing markers",
        )

    def test_S16_ancestry_in_a_parentless_terminal_is_a_verified_lead(self):
        """agent-runtime/R255"""
        self.assertEqual(
            self.caller({}, {"t1": {"pid": 40, "parent": None}}), {"kind": "lead", "verified": True}
        )

    def test_S16_own_terminal_id_is_verified_when_its_pid_is_an_ancestor(self):
        """agent-runtime/R255"""
        terminals = {"t1": {"pid": 30, "parent": "tX"}}
        self.assertEqual(
            self.caller({"HC_TERMINAL_ID": "t1"}, terminals), {"kind": "worker", "verified": True}
        )

    def test_S16_worker_markers_alone_are_never_verified(self):
        """agent-runtime/R255"""
        terminals = {"t1": {"pid": 999, "parent": "tX"}}
        caller = self.caller({"HC_TERMINAL_ID": "t1"}, terminals)
        self.assertEqual(caller, {"kind": "worker", "verified": False})

    def test_S16_a_finished_terminal_with_an_ancestor_pid_never_verifies(self):
        """agent-runtime/R255"""
        terminals = {"t1": {"pid": 40, "parent": "tX", "state": "finished"}}
        self.assertEqual(self.caller({}, terminals), {"kind": "unknown", "verified": False})

    def test_S16_a_marker_naming_a_finished_terminal_never_verifies(self):
        """agent-runtime/R255"""
        terminals = {"t1": {"pid": 30, "parent": "tX", "state": "finished"}}
        self.assertEqual(
            self.caller({"HC_TERMINAL_ID": "t1"}, terminals), {"kind": "worker", "verified": False}
        )

    def test_S16_the_nearest_live_ancestor_terminal_decides_the_kind(self):
        """agent-runtime/R255"""
        terminals = {
            "far": {"pid": 30, "parent": None},
            "near": {"pid": 40, "parent": "far"},
        }
        self.assertEqual(self.caller({}, terminals), {"kind": "worker", "verified": True})
        self.assertEqual(
            self.caller({"HC_TERMINAL_ID": "far"}, terminals), {"kind": "worker", "verified": True}
        )

    def test_S16_no_marker_and_no_ancestry_is_unknown(self):
        """agent-runtime/R255"""
        self.assertEqual(self.caller({}, {}), {"kind": "unknown", "verified": False})


class SharedGenerationTests(unittest.TestCase):
    PROCS = {
        100: (90, "/bin/zsh -c mcp"),
        90: (80, "/opt/homebrew/bin/codex exec"),
        80: (1, "launchd"),
    }
    STAMPS = {90: "Wed Nov  4 10:00:00 2026"}

    def generation(self, env=None):
        with (
            patch.object(app, "ps_entry", side_effect=self.PROCS.get),
            patch.object(app, "lstart", side_effect=self.STAMPS.get),
            patch.object(app.os, "getppid", return_value=100),
        ):
            return app.cli_generation()

    def test_generation_is_nearest_cli_pid_and_lstart_without_session_suffix(self):
        """agent-runtime/R107"""
        plain = self.generation({})
        self.assertEqual(plain, "90:Wed Nov  4 10:00:00 2026")
        env = {
            "CODEX_THREAD_ID": "thread-a",
            "CLAUDE_CODE_SESSION_ID": "sess-a",
            "CLAUDE_SESSION_ID": "sess-b",
        }
        self.assertEqual(self.generation(env), plain)

    def test_lstart_is_locale_independent(self):
        """agent-runtime/R107"""
        with tempfile.TemporaryDirectory() as tmp:
            ps = Path(tmp) / "ps"
            ps.write_text(
                "#!/bin/sh\n[ \"$LC_ALL\" = C ] && echo 'Wed Nov  4 10:00:00 2026' || echo 'Mi Nov  4 10:00:00 2026'\n"
            )
            ps.chmod(0o755)
            stamps = set()
            for loc in (
                {},
                {"LC_ALL": "de_DE.UTF-8"},
                {"LC_TIME": "en_GB.UTF-8", "LC_ALL": "en_GB.UTF-8"},
            ):
                with (
                    patch.dict(os.environ, dict(loc, PATH="%s:%s" % (tmp, os.environ["PATH"]))),
                    patch.object(app, "PS_TIMEOUT_SECONDS", 10),
                ):
                    stamps.add(app.lstart(os.getpid()))
        self.assertEqual(stamps, {"Wed Nov  4 10:00:00 2026"})

    def test_new_session_id_in_same_process_keeps_generation(self):
        """agent-runtime/R108"""
        before = self.generation({"CODEX_THREAD_ID": "thread-a"})
        after = self.generation({"CODEX_THREAD_ID": "thread-b"})
        self.assertEqual(before, after)


class HookExceptionDocsTests(unittest.TestCase):
    def test_S18_agents_md_and_spec_name_the_one_hook_only_exception(self):
        """agent-runtime/R257"""
        root = Path(__file__).resolve().parents[1]
        for doc in (root / "AGENTS.md", root / "docs" / "specs" / "agent-runtime.md"):
            text = " ".join(doc.read_text().split())
            self.assertIn("native in-process subagent cannot be told from its Lead", text, doc.name)
            self.assertIn("R74", text, doc.name)
            self.assertIn("R89", text, doc.name)
            self.assertIn("no other refusal is an exception", text.lower(), doc.name)
