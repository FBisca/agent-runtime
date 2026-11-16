"""Which runtime, role and CLI a process is: the resolver with an injected env and hc runner, the CLI
detected from the nearest ancestor, and the symlinked executable as the boundary."""

import io
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_runtime_harness import BIN

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
from agent_runtime import agent_runtime as app  # noqa: E402

pytestmark = pytest.mark.integration


def hc_returning(parent):
    return lambda terminal_id: {"id": terminal_id, "parent": parent}


def hc_failing(terminal_id):
    raise RuntimeError("hc failed")


def line(env, cli="claude", hc=None):
    return app.format_line(app.resolve(env, cli, hc or hc_failing))


app.ps_entry = lambda pid, timeout=0.2: None  # tests never read the real process tree


def run_main(*argv, env=None):
    buf = io.StringIO()
    with patch.dict(app.os.environ, env or {}, clear=True), redirect_stdout(buf):
        code = app.main(list(argv))
    return code, buf.getvalue()


class ResolveTests(unittest.TestCase):
    def test_herdr_pane_with_claude_is_herdmates(self):
        """agent-runtime/R1"""
        self.assertEqual(line({"HERDR_PANE_ID": "p1"}), "runtime=herdmates role=unknown cli=claude")

    def test_herdr_pane_with_codex_is_native(self):
        """agent-runtime/R2"""
        self.assertEqual(
            line({"HERDR_PANE_ID": "p1"}, cli="codex"), "runtime=native role=lead cli=codex"
        )

    def test_hc_terminal_without_parent_is_lead(self):
        """agent-runtime/R3"""
        self.assertEqual(
            line({"HC_TERMINAL_ID": "t1"}, hc=hc_returning(None)), "runtime=hc role=lead cli=claude"
        )

    def test_hc_terminal_with_parent_is_worker(self):
        """agent-runtime/R4"""
        self.assertEqual(
            line({"HC_TERMINAL_ID": "t1"}, hc=hc_returning("tX")),
            "runtime=hc role=worker parent=tX cli=claude",
        )

    def test_no_variables_is_native_lead(self):
        """agent-runtime/R5"""
        self.assertEqual(line({}), "runtime=native role=lead cli=claude")

    def test_herdr_pane_wins_over_hc_terminal(self):
        """agent-runtime/R6"""
        out = line({"HERDR_PANE_ID": "p1", "HC_TERMINAL_ID": "t1"}, hc=hc_returning("tX"))
        self.assertEqual(out, "runtime=herdmates role=unknown cli=claude")

    def test_hc_failure_or_timeout_is_unknown_and_exits_zero(self):
        """agent-runtime/R7"""
        self.assertEqual(line({"HC_TERMINAL_ID": "t1"}), "runtime=hc role=unknown cli=claude")
        timeout = subprocess.TimeoutExpired("hc", 2)
        with patch.object(app.subprocess, "run", side_effect=timeout) as run:
            buf = io.StringIO()
            with (
                patch.dict(app.os.environ, {"HC_TERMINAL_ID": "t1"}, clear=True),
                redirect_stdout(buf),
            ):
                code = app.main(["resolve"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue().strip(), "runtime=hc role=unknown cli=claude")
        self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_json_output_has_same_fields(self):
        """agent-runtime/R5"""
        buf = io.StringIO()
        with patch.dict(app.os.environ, {}, clear=True), redirect_stdout(buf):
            self.assertEqual(app.main(["resolve", "--json", "--cli", "codex"]), 0)
        self.assertEqual(
            json.loads(buf.getvalue()),
            {
                "runtime": "native",
                "role": "lead",
                "cli": "codex",
                "caller": {"kind": "unknown", "verified": False},
            },
        )


class CodexDetectionTests(unittest.TestCase):
    @staticmethod
    def table(**chain):
        """Fake process table: {pid: (ppid, args)}."""
        return lambda pid: chain.get(pid)

    def detect(self, env, table, ppid=100):
        with (
            patch.object(app, "ps_entry", side_effect=table),
            patch.object(app.os, "getppid", return_value=ppid),
        ):
            return app.detect_cli(env)

    def test_nearest_codex_ancestor_wins_over_inherited_claudecode(self):
        """agent-runtime/R32"""
        procs = {
            100: (90, "/bin/zsh -c run"),
            90: (80, "/opt/homebrew/bin/codex exec"),
            80: (70, "claude"),
        }
        self.assertEqual(self.detect({"CLAUDECODE": "1"}, procs.get), "codex")

    def test_nearest_claude_ancestor_wins_over_inherited_codex_markers(self):
        """agent-runtime/R32"""
        procs = {100: (90, "/bin/zsh"), 90: (80, "claude"), 80: (70, "codex")}
        self.assertEqual(self.detect({"CODEX_THREAD_ID": "x"}, procs.get), "claude")

    def test_node_wrapped_cli_is_matched(self):
        """agent-runtime/R32"""
        procs = {
            100: (90, "node /usr/lib/node_modules/@openai/codex/bin/codex.js"),
            90: (1, "node /x/bin/claude"),
        }
        self.assertEqual(
            self.detect(
                {}, {100: (90, "sh"), 90: (80, "node /x/bin/codex"), 80: (1, "launchd")}.get
            ),
            "codex",
        )

    def test_walk_failure_falls_back_to_env_markers(self):
        """agent-runtime/R32"""
        none = lambda pid: None
        self.assertEqual(self.detect({"CODEX_THREAD_ID": "x", "CLAUDECODE": "1"}, none), "codex")
        self.assertEqual(self.detect({"CODEX_TOOL_CALL_ID": "x"}, none), "codex")
        self.assertEqual(self.detect({"CLAUDECODE": "1"}, none), "claude")
        self.assertIsNone(self.detect({}, none))

    def test_walk_is_capped_and_survives_cycles(self):
        """agent-runtime/R32"""
        loop = {100: (90, "sh"), 90: (100, "sh")}
        self.assertIsNone(self.detect({}, loop.get))
        deep = {n: (n + 1, "sh") for n in range(100, 400)}
        self.assertIsNone(self.detect({}, deep.get))

    def test_codex_without_cli_resolves_native_in_herdr(self):
        """agent-runtime/R32"""
        procs = {100: (90, "codex")}
        with (
            patch.object(app, "ps_entry", side_effect=procs.get),
            patch.object(app.os, "getppid", return_value=100),
        ):
            info = app.resolve({"HERDR_PANE_ID": "p1", "CLAUDECODE": "1"}, None, hc_failing)
        self.assertEqual((info["runtime"], info["cli"]), ("native", "codex"))

    def test_explicit_cli_wins_over_ancestry(self):
        """agent-runtime/R32"""
        procs = {100: (90, "codex")}
        with (
            patch.object(app, "ps_entry", side_effect=procs.get),
            patch.object(app.os, "getppid", return_value=100),
        ):
            info = app.resolve({"HERDR_PANE_ID": "p1"}, "claude", hc_failing)
        self.assertEqual((info["runtime"], info["cli"]), ("herdmates", "claude"))

    def test_no_marker_defaults_to_claude(self):
        """agent-runtime/R32"""
        self.assertEqual(app.resolve({}, None, hc_failing)["cli"], "claude")

    def test_hc_foreground_codex_is_fallback_after_env(self):
        """agent-runtime/R32"""
        reply = {"parent": "t1", "foreground": {"argv": ["/opt/homebrew/bin/codex", "--no-daemon"]}}
        info = app.resolve({"HC_TERMINAL_ID": "t2"}, None, lambda t: reply)
        self.assertEqual((info["runtime"], info["role"], info["cli"]), ("hc", "worker", "codex"))
        info = app.resolve(
            {"HC_TERMINAL_ID": "t2"},
            None,
            lambda t: {"parent": None, "foreground": {"argv": ["claude"]}},
        )
        self.assertEqual(info["cli"], "claude")

    def test_commands_without_cli_flag_use_detected_cli_even_on_fallback(self):
        """agent-runtime/R32"""
        env = {"CODEX_THREAD_ID": "x"}
        code, out = run_main("resolve", env=env)
        self.assertEqual(out.strip(), "runtime=native role=lead cli=codex")
        code, out = run_main("resolve", "--bogus", env=env)
        self.assertEqual((code, out.strip()), (0, "runtime=native role=unknown cli=codex"))

    def test_ps_entry_failure_never_raises(self):
        """agent-runtime/R32"""
        with patch.object(app.subprocess, "run", side_effect=OSError("no ps")):
            self.assertIsNone(app.real_ps_entry(1))


class CliTests(unittest.TestCase):
    """Run the symlinked executable with a controlled env and a fake hc on PATH."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bindir = Path(self.temp.name)
        ps = self.bindir / "ps"
        ps.write_text("#!/bin/sh\nexit 1\n")
        ps.chmod(0o755)

    def fake_hc(self, body):
        path = self.bindir / "hc"
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def run_cli(self, *args, env=None, process=False):
        """The resolve logic runs in process; `process=True` starts the symlinked executable (the boundary)."""
        full = {"PATH": str(self.bindir) + ":/usr/bin:/bin"}
        full.update(env or {})
        if not process:
            code, out = run_main(*args, env=full)
            self.assertEqual(code, 0)
            return out.strip()
        done = subprocess.run(
            [sys.executable, "-B", str(BIN), *args],
            env=full,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def hc_out(self, text, process=False):
        self.fake_hc("cat <<'EOF'\n%s\nEOF" % text)
        return self.run_cli("resolve", env={"HC_TERMINAL_ID": "t1"}, process=process)

    def test_null_parent_through_symlink_is_lead(self):
        """agent-runtime/R3; S13: the executable through its symlink is the boundary"""
        self.assertEqual(
            self.hc_out('{"parent": null}', process=True), "runtime=hc role=lead cli=claude"
        )

    def test_parent_through_symlink_is_worker(self):
        """agent-runtime/R4"""
        self.assertEqual(
            self.hc_out('{"parent": "tX"}', process=True),
            "runtime=hc role=worker parent=tX cli=claude",
        )

    def test_non_string_empty_or_spaced_parent_is_unknown(self):
        """agent-runtime/R4"""
        for parent in ("5", '""', '"a b"', "[]", "true"):
            with self.subTest(parent=parent):
                self.assertEqual(
                    self.hc_out('{"parent": %s}' % parent), "runtime=hc role=unknown cli=claude"
                )

    def test_missing_hc_is_unknown(self):
        """agent-runtime/R7"""
        self.assertEqual(
            self.run_cli("resolve", env={"HC_TERMINAL_ID": "t1"}),
            "runtime=hc role=unknown cli=claude",
        )

    def test_nonzero_exit_is_unknown(self):
        """agent-runtime/R7"""
        self.fake_hc("exit 3")
        self.assertEqual(
            self.run_cli("resolve", env={"HC_TERMINAL_ID": "t1"}),
            "runtime=hc role=unknown cli=claude",
        )

    def test_non_json_is_unknown(self):
        """agent-runtime/R7"""
        self.assertEqual(self.hc_out("not json"), "runtime=hc role=unknown cli=claude")

    def test_json_list_or_missing_parent_is_unknown(self):
        """agent-runtime/R7"""
        self.assertEqual(self.hc_out("[1]"), "runtime=hc role=unknown cli=claude")
        self.assertEqual(self.hc_out("{}"), "runtime=hc role=unknown cli=claude")

    def test_bad_cli_value_falls_back_and_exits_zero(self):
        """agent-runtime/R5"""
        self.assertEqual(
            self.run_cli("resolve", "--cli", "gemini"), "runtime=native role=unknown cli=claude"
        )
        self.assertEqual(
            self.run_cli("resolve", "--bogus", "--cli", "codex"),
            "runtime=native role=unknown cli=codex",
        )
