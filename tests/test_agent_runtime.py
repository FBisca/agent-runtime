"""Resolver checks with injected env and hc runner; the real hc is never called."""

import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration



SCRIPT = Path(__file__).resolve().parents[1] / "server" / "agent_runtime.py"
BIN = SCRIPT
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
from agent_runtime import agent_runtime as app  # noqa: E402

app.ps_entry = lambda pid, timeout=0.2: None  # tests never read the real process tree


def hc_returning(parent):
    return lambda terminal_id: {"id": terminal_id, "parent": parent}


def hc_failing(terminal_id):
    raise RuntimeError("hc failed")


def line(env, cli="claude", hc=None):
    return app.format_line(app.resolve(env, cli, hc or hc_failing))


class ResolveTests(unittest.TestCase):
    def test_herdr_pane_with_claude_is_herdmates(self):
        """agent-runtime/R1"""
        self.assertEqual(line({"HERDR_PANE_ID": "p1"}), "runtime=herdmates role=unknown cli=claude")

    def test_herdr_pane_with_codex_is_native(self):
        """agent-runtime/R2"""
        self.assertEqual(line({"HERDR_PANE_ID": "p1"}, cli="codex"), "runtime=native role=lead cli=codex")

    def test_hc_terminal_without_parent_is_lead(self):
        """agent-runtime/R3"""
        self.assertEqual(line({"HC_TERMINAL_ID": "t1"}, hc=hc_returning(None)), "runtime=hc role=lead cli=claude")

    def test_hc_terminal_with_parent_is_worker(self):
        """agent-runtime/R4"""
        self.assertEqual(line({"HC_TERMINAL_ID": "t1"}, hc=hc_returning("tX")),
                         "runtime=hc role=worker parent=tX cli=claude")

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
            with patch.dict(app.os.environ, {"HC_TERMINAL_ID": "t1"}, clear=True), redirect_stdout(buf):
                code = app.main(["resolve"])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue().strip(), "runtime=hc role=unknown cli=claude")
        self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_json_output_has_same_fields(self):
        """agent-runtime/R5"""
        buf = io.StringIO()
        with patch.dict(app.os.environ, {}, clear=True), redirect_stdout(buf):
            self.assertEqual(app.main(["resolve", "--json", "--cli", "codex"]), 0)
        self.assertEqual(json.loads(buf.getvalue()), {"runtime": "native", "role": "lead", "cli": "codex"})




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

    def run_cli(self, *args, env=None):
        full = {"PATH": str(self.bindir) + ":/usr/bin:/bin"}
        full.update(env or {})
        done = subprocess.run([sys.executable, "-B", str(BIN), *args], env=full,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def hc_out(self, text):
        self.fake_hc("cat <<'EOF'\n%s\nEOF" % text)
        return self.run_cli("resolve", env={"HC_TERMINAL_ID": "t1"})

    def test_null_parent_through_symlink_is_lead(self):
        """agent-runtime/R3"""
        self.assertEqual(self.hc_out('{"parent": null}'), "runtime=hc role=lead cli=claude")

    def test_parent_through_symlink_is_worker(self):
        """agent-runtime/R4"""
        self.assertEqual(self.hc_out('{"parent": "tX"}'), "runtime=hc role=worker parent=tX cli=claude")

    def test_non_string_empty_or_spaced_parent_is_unknown(self):
        """agent-runtime/R4"""
        for parent in ("5", '""', '"a b"', "[]", "true"):
            with self.subTest(parent=parent):
                self.assertEqual(self.hc_out('{"parent": %s}' % parent), "runtime=hc role=unknown cli=claude")

    def test_missing_hc_is_unknown(self):
        """agent-runtime/R7"""
        self.assertEqual(self.run_cli("resolve", env={"HC_TERMINAL_ID": "t1"}),
                         "runtime=hc role=unknown cli=claude")

    def test_nonzero_exit_is_unknown(self):
        """agent-runtime/R7"""
        self.fake_hc("exit 3")
        self.assertEqual(self.run_cli("resolve", env={"HC_TERMINAL_ID": "t1"}),
                         "runtime=hc role=unknown cli=claude")

    def test_non_json_is_unknown(self):
        """agent-runtime/R7"""
        self.assertEqual(self.hc_out("not json"), "runtime=hc role=unknown cli=claude")

    def test_json_list_or_missing_parent_is_unknown(self):
        """agent-runtime/R7"""
        self.assertEqual(self.hc_out("[1]"), "runtime=hc role=unknown cli=claude")
        self.assertEqual(self.hc_out("{}"), "runtime=hc role=unknown cli=claude")

    def test_bad_cli_value_falls_back_and_exits_zero(self):
        """agent-runtime/R5"""
        self.assertEqual(self.run_cli("resolve", "--cli", "gemini"), "runtime=native role=unknown cli=claude")
        self.assertEqual(self.run_cli("resolve", "--bogus", "--cli", "codex"),
                         "runtime=native role=unknown cli=codex")


def run_main(*argv, env=None):
    buf = io.StringIO()
    with patch.dict(app.os.environ, env or {}, clear=True), redirect_stdout(buf):
        code = app.main(list(argv))
    return code, buf.getvalue()


STATUS_KEYS = {"name", "runtime", "role", "parent", "state", "turn", "native", "next"}


def hc_world(terminals, calls=None):
    """Fake hc: `get <t>` answers from raw get-records; `ls --json [--all]` rows carry parent when non-null."""
    def fake(argv):
        if calls is not None:
            calls.append(list(argv))
        if argv[:2] == ["hc", "get"]:
            if argv[2] not in terminals:
                raise RuntimeError("not found")
            return json.dumps(terminals[argv[2]])
        if argv[:2] == ["hc", "ls"]:
            rows = []
            for a, r in terminals.items():
                if r.get("state") == "finished" and "--all" not in argv:
                    continue
                row_ = {"address": a, "state": r.get("state"), "turn": r.get("turn")}
                if r.get("parent"):
                    row_["parent"] = r["parent"]
                rows.append(row_)
            return json.dumps({"hidden_finished": 0, "terminals": rows})
        raise AssertionError("unexpected command %r" % (argv,))
    return fake


def raw(status, parent: str | None = "t0", state: str = "live", **extra):
    return dict({"address": "t9", "name": "fix", "parent": parent, "state": state, "status": status, "turn": 3}, **extra)


def hc_status(record, name="fix"):
    with patch.object(app, "run_command", side_effect=hc_world({name: record})):
        return app.status("hc", name, {"HC_TERMINAL_ID": "t0"})


class StatusTests(unittest.TestCase):
    def test_hc_status_has_all_fields_and_non_empty_next(self):
        """agent-runtime/R11"""
        info = hc_status(raw({"app": "claude-code", "state": "working"}))
        self.assertEqual(set(info), STATUS_KEYS)
        self.assertEqual((info["name"], info["runtime"], info["role"], info["parent"], info["turn"]),
                         ("fix", "hc", "worker", "t0", 3))
        self.assertEqual(info["native"]["status"], {"app": "claude-code", "state": "working"})
        self.assertTrue(info["next"])
        for row in info["next"]:
            self.assertEqual(set(row), {"why", "do", "verb", "gap"})

    def test_native_field_is_trimmed_to_actionable_raw_fields(self):
        """agent-runtime/R11"""
        record = raw({"app": "claude-code", "state": "working"}, exit_code=None, reason=None,
                     screen="exact", cols=216, records={"x": 1}, foreground={"argv": ["claude"], "pid": 1})
        info = hc_status(record)
        self.assertEqual(set(info["native"]), {"address", "name", "state", "status", "turn", "exit_code",
                                               "reason", "foreground"})
        self.assertEqual(info["native"]["foreground"], {"argv": ["claude"]})
        self.assertEqual(app.status("native", "helper", {})["native"], {})
        env = {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/s", "HERDR_PANE_ID": "w1:p1"}
        reply = json.dumps({"result": {"pane": {"agent_status": "idle", "extra": 1}}})
        with patch.object(app, "run_command", return_value=reply):
            herd = app.status("herdmates", "w1:p2", env, session="test")["native"]
        self.assertEqual(set(herd), {"pane", "session", "agent_status", "note"})
        self.assertIn("verify live agent identity", herd["note"])

    def test_status_output_is_compact_json_and_pretty_is_optional(self):
        """agent-runtime/R11"""
        world = {"fix": raw("idle")}
        for flags, multiline in (([], False), (["--pretty"], True)):
            buf = io.StringIO()
            with patch.object(app, "run_command", side_effect=hc_world(world)), \
                    patch.dict(app.os.environ, {}, clear=True), redirect_stdout(buf):
                self.assertEqual(app.main(["status", "fix", "--runtime", "hc", *flags]), 0)
            self.assertEqual("\n" in buf.getvalue().strip(), multiline)
            json.loads(buf.getvalue())

    def test_status_command_prints_json_through_symlink(self):
        """agent-runtime/R11"""
        with tempfile.TemporaryDirectory() as tmp:
            hc = Path(tmp) / "hc"
            hc.write_text("#!/bin/sh\necho '%s'\n" % json.dumps(raw("idle")))
            hc.chmod(0o755)
            done = subprocess.run([sys.executable, "-B", str(BIN), "status", "fix", "--runtime", "hc"],
                                  env={"PATH": tmp + ":/usr/bin:/bin"}, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)
        info = json.loads(done.stdout)
        self.assertEqual(set(info), STATUS_KEYS)
        self.assertEqual(info["state"], "idle")

    def test_hc_states_map_to_contract_states(self):
        """agent-runtime/R12"""
        cases = [({"app": "x", "state": "working"}, "live", "working"), ("working", "live", "working"),
                 ("idle", "live", "idle"), ({"app": "x", "state": "done"}, "live", "done"),
                 ("blocked", "live", "blocked"), ("error", "live", "error"), (None, "live", "unknown"),
                 ({"state": "weird"}, "live", "unknown"), ("done", "finished", "exited"),
                 (None, "finished", "exited")]
        for status, state, expected in cases:
            with self.subTest(status=status, state=state):
                self.assertEqual(hc_status(raw(status, state=state))["state"], expected)

    def test_exited_or_finished_in_state_or_status_maps_to_exited(self):
        """agent-runtime/R12"""
        cases = [("exited", "live"), ({"app": "x", "state": "exited"}, "live"), ("finished", "live"),
                 ({"state": "finished"}, "live"), ("idle", "exited"), (None, "finished")]
        for status, state in cases:
            with self.subTest(status=status, state=state):
                self.assertEqual(hc_status(raw(status, state=state))["state"], "exited")

    def test_blocked_next_has_ask_the_user_gap_row(self):
        """agent-runtime/R12: blocked offers an ask-the-user gap row, not raw hc keys."""
        rows = hc_status(raw("blocked"))["next"]
        gaps = [r for r in rows if r["gap"]]
        self.assertTrue(gaps)
        self.assertTrue(all(r["verb"] is None for r in gaps))
        self.assertTrue(any("ask the user" in r["do"] for r in gaps))
        self.assertTrue(all(r["verb"] is not None for r in rows if not r["gap"]))

    def test_next_rows_lead_with_contract_verbs(self):
        """agent-runtime/R12"""
        expect = {"done": "collect", "idle": "message", "working": "wait", "exited": "collect"}
        for state, verb in expect.items():
            status, term = ("done", "finished") if state == "exited" else (state, "live")
            with self.subTest(state=state):
                rows = hc_status(raw(status, state=term))["next"]
                self.assertEqual(rows[0]["verb"], verb)

    def test_failing_hc_get_is_unknown_with_next(self):
        """agent-runtime/R12"""
        with patch.object(app, "run_command", side_effect=RuntimeError("boom")):
            info = app.status("hc", "fix", {"HC_TERMINAL_ID": "t0"})
        self.assertEqual(info["state"], "unknown")
        self.assertTrue(info["next"])

    def test_hc_error_state_maps_to_error(self):
        """agent-runtime/R12: hc error state maps to error (updated by lead-delivery-gaps/S9)."""
        self.assertEqual(hc_status(raw("error"))["state"], "error")

    def test_herdmates_missing_identity_never_calls_cli(self):
        """agent-runtime/R13"""
        with patch.object(app, "run_command") as run:
            info = app.status("herdmates", "w1:p1", {}, session="default")
            run.assert_not_called()
        self.assertEqual(info["state"], "unknown")
        with patch.object(app, "run_command") as run:
            with self.assertRaises(ValueError):
                app.herdr_snapshot("w1:p1", "default", {})
            run.assert_not_called()

    def test_herdmates_inspection_uses_explicit_session_and_no_mutation(self):
        """agent-runtime/R13"""
        env = {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/tmp/example.sock",
               "HERDR_PANE_ID": "w1:p1", "HERDR_SESSION": "test"}
        with patch.object(app, "run_command", return_value='{"result":{}}') as run:
            app.herdr_snapshot("w1:p2", "test", env)
            run.assert_called_once_with(["herdr", "pane", "get", "w1:p2", "--session", "test"])
            with self.assertRaises(ValueError):
                app.herdr_snapshot("w1:p2", "different", env)

    def test_herdmates_maps_agent_status(self):
        """agent-runtime/R13"""
        env = {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/tmp/example.sock", "HERDR_PANE_ID": "w1:p1"}
        for herdr, expected in (("idle", "idle"), ("working", "working"), ("blocked", "blocked"),
                                ("done", "done"), ("unknown", "unknown"), ("weird", "unknown")):
            with self.subTest(herdr=herdr):
                reply = json.dumps({"result": {"pane": {"agent_status": herdr}}})
                with patch.object(app, "run_command", return_value=reply):
                    info = app.status("herdmates", "w1:p2", env, session="test")
                self.assertEqual(set(info), STATUS_KEYS)
                self.assertEqual((info["runtime"], info["state"]), ("herdmates", expected))
                self.assertTrue(info["next"])

    def test_native_status_is_unknown_pointing_to_return_value_or_sendmessage(self):
        """agent-runtime/R14"""
        info = app.status("native", "helper", {})
        self.assertEqual(set(info), STATUS_KEYS)
        self.assertEqual(info["state"], "unknown")
        text = " ".join(r["do"] for r in info["next"])
        self.assertIn("return value", text)
        self.assertIn("SendMessage", text)

    def test_hc_list_returns_one_status_per_child_of_caller_and_never_gets_foreign_terminals(self):
        """agent-runtime/R15"""
        world = {"a": raw("working", parent="t0", address="a"), "b": raw("idle", parent="t0", address="b"),
                 "c": raw("idle", parent="t5", address="c"), "d": raw("idle", parent=None, address="d"),
                 "e": raw("done", parent="t0", state="finished", address="e")}
        for address, record in world.items():
            record["name"] = "n" + address
        calls = []
        with patch.object(app, "run_command", side_effect=hc_world(world, calls)):
            infos = app.list_children("hc", {"HC_TERMINAL_ID": "t0"})
        self.assertEqual(sorted(i["name"] for i in infos), ["na", "nb", "ne"])
        self.assertEqual({i["name"]: i["state"] for i in infos}["ne"], "exited")
        self.assertIn(["hc", "ls", "--all", "--json"], calls)
        self.assertEqual(sorted(c[2] for c in calls if c[1] == "get"), ["a", "b", "e"])
        self.assertTrue(all(set(i) == STATUS_KEYS for i in infos))

    def test_list_outside_hc_is_empty_with_stderr_note(self):
        """agent-runtime/R15"""
        for runtime in ("herdmates", "native"):
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(app.os.environ, {}, clear=True), redirect_stdout(out), \
                    patch.object(app.sys, "stderr", err):
                self.assertEqual(app.main(["list", "--runtime", runtime]), 0)
            self.assertEqual(json.loads(out.getvalue()), [])
            self.assertIn("only available under hc", err.getvalue())

    def test_list_command_prints_json_array(self):
        """agent-runtime/R15"""
        world = {"a": raw("idle", parent="t0", address="a")}
        buf = io.StringIO()
        with patch.object(app, "run_command", side_effect=hc_world(world)), \
                patch.dict(app.os.environ, {"HC_TERMINAL_ID": "t0"}, clear=True), redirect_stdout(buf):
            self.assertEqual(app.main(["list", "--runtime", "hc"]), 0)
        self.assertEqual(len(json.loads(buf.getvalue())), 1)


class CodexDetectionTests(unittest.TestCase):
    @staticmethod
    def table(**chain):
        """Fake process table: {pid: (ppid, args)}."""
        return lambda pid: chain.get(pid)

    def detect(self, env, table, ppid=100):
        with patch.object(app, "ps_entry", side_effect=table), patch.object(app.os, "getppid", return_value=ppid):
            return app.detect_cli(env)

    def test_nearest_codex_ancestor_wins_over_inherited_claudecode(self):
        """agent-runtime/R32"""
        procs = {100: (90, "/bin/zsh -c run"), 90: (80, "/opt/homebrew/bin/codex exec"), 80: (70, "claude")}
        self.assertEqual(self.detect({"CLAUDECODE": "1"}, procs.get), "codex")

    def test_nearest_claude_ancestor_wins_over_inherited_codex_markers(self):
        """agent-runtime/R32"""
        procs = {100: (90, "/bin/zsh"), 90: (80, "claude"), 80: (70, "codex")}
        self.assertEqual(self.detect({"CODEX_THREAD_ID": "x"}, procs.get), "claude")

    def test_node_wrapped_cli_is_matched(self):
        """agent-runtime/R32"""
        procs = {100: (90, "node /usr/lib/node_modules/@openai/codex/bin/codex.js"), 90: (1, "node /x/bin/claude")}
        self.assertEqual(self.detect({}, {100: (90, "sh"), 90: (80, "node /x/bin/codex"), 80: (1, "launchd")}.get), "codex")

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
        with patch.object(app, "ps_entry", side_effect=procs.get), patch.object(app.os, "getppid", return_value=100):
            info = app.resolve({"HERDR_PANE_ID": "p1", "CLAUDECODE": "1"}, None, hc_failing)
        self.assertEqual((info["runtime"], info["cli"]), ("native", "codex"))

    def test_explicit_cli_wins_over_ancestry(self):
        """agent-runtime/R32"""
        procs = {100: (90, "codex")}
        with patch.object(app, "ps_entry", side_effect=procs.get), patch.object(app.os, "getppid", return_value=100):
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
        info = app.resolve({"HC_TERMINAL_ID": "t2"}, None, lambda t: {"parent": None, "foreground": {"argv": ["claude"]}})
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


class SharedGenerationTests(unittest.TestCase):
    PROCS = {100: (90, "/bin/zsh -c mcp"), 90: (80, "/opt/homebrew/bin/codex exec"), 80: (1, "launchd")}
    STAMPS = {90: "Wed Nov  4 10:00:00 2026"}

    def generation(self, env=None):
        with patch.object(app, "ps_entry", side_effect=self.PROCS.get), \
                patch.object(app, "lstart", side_effect=self.STAMPS.get), \
                patch.object(app.os, "getppid", return_value=100):
            return app.cli_generation()

    def test_generation_is_nearest_cli_pid_and_lstart_without_session_suffix(self):
        """agent-runtime/R107"""
        plain = self.generation({})
        self.assertEqual(plain, "90:Wed Nov  4 10:00:00 2026")
        env = {"CODEX_THREAD_ID": "thread-a", "CLAUDE_CODE_SESSION_ID": "sess-a", "CLAUDE_SESSION_ID": "sess-b"}
        self.assertEqual(self.generation(env), plain)

    def test_lstart_is_locale_independent(self):
        """agent-runtime/R107"""
        with tempfile.TemporaryDirectory() as tmp:
            ps = Path(tmp) / "ps"
            ps.write_text("#!/bin/sh\n[ \"$LC_ALL\" = C ] && echo 'Wed Nov  4 10:00:00 2026' || echo 'Mi Nov  4 10:00:00 2026'\n")
            ps.chmod(0o755)
            stamps = set()
            for loc in ({}, {"LC_ALL": "de_DE.UTF-8"}, {"LC_TIME": "en_GB.UTF-8", "LC_ALL": "en_GB.UTF-8"}):
                with patch.dict(os.environ, dict(loc, PATH="%s:%s" % (tmp, os.environ["PATH"]))), \
                        patch.object(app, "PS_TIMEOUT_SECONDS", 10):
                    stamps.add(app.lstart(os.getpid()))
        self.assertEqual(stamps, {"Wed Nov  4 10:00:00 2026"})

    def test_new_session_id_in_same_process_keeps_generation(self):
        """agent-runtime/R108"""
        before = self.generation({"CODEX_THREAD_ID": "thread-a"})
        after = self.generation({"CODEX_THREAD_ID": "thread-b"})
        self.assertEqual(before, after)


FAKE_HC = """#!%(python)s
import json, sys
argv = sys.argv[1:]
with open(%(log)r, "a") as log:
    log.write(json.dumps(argv) + "\\n")
replies = json.load(open(%(replies)r))
reply = replies.get(" ".join(argv[:2]), replies.get(argv[0], {"rc": 0, "out": ""}))
sys.stdout.write(reply.get("out", ""))
sys.exit(reply.get("rc", 0))
"""


# sh and awk, not python: the walk gives each ps a 0.2 s timeout, which a python start-up can miss under load
FAKE_PS = """#!/bin/sh
[ "$1 $2" = "-o ppid=,args=" ] && [ "$3" = "-p" ] || exit 1
awk -v pid="$4" '$1 == pid { found = 1; $1 = ""; sub(/^ /, ""); print } END { exit !found }' %(table)s
"""


class CallTests(unittest.TestCase):
    """`agent-runtime call` against a fake hc on PATH that logs argv; the dispatch store lives in a temp dir."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.bin = self.dir / "bin"
        self.bin.mkdir()
        self.table = self.dir / "ps-table"
        self.set_ancestors([(5000, "/bin/zsh -l"), (6000, "/Users/x/.local/bin/herdr-core hold --socket s")])
        (self.bin / "ps").write_text(FAKE_PS % {"table": str(self.table)})
        (self.bin / "ps").chmod(0o755)
        self.log = self.dir / "log"
        self.log.write_text("")
        self.replies = self.dir / "replies"
        self.set_replies({})
        hc = self.bin / "hc"
        hc.write_text(FAKE_HC % {"python": sys.executable, "log": str(self.log), "replies": str(self.replies)})
        hc.chmod(0o755)

    def set_ancestors(self, chain, end=1):
        """Fake process table for `call`: the test runner is its parent, then each (pid, args), nearest first;
        the last one's parent is `end`, which has no entry (pid 1 ends the walk, any other pid is unreadable)."""
        pids = [os.getpid()] + [pid for pid, _ in chain]
        names = ["python3 -m unittest"] + [args for _, args in chain]
        lines = ["%d %d %s" % (pid, (pids + [end])[index + 1], names[index]) for index, pid in enumerate(pids)]
        self.table.write_text("\n".join(lines) + "\n")

    def set_replies(self, replies):
        base = {"get t0": {"out": json.dumps({"address": "t0", "name": "lead", "parent": None, "pid": 6000})},
                "get t1": {"out": json.dumps({"address": "t1", "name": "w1", "parent": "t0"})},
                "get t9": {"out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})},
                "get w1": {"out": json.dumps({"address": "t9", "name": "w1", "parent": "t0"})},
                "run": {"out": json.dumps({"first_message": {"sent": True}})}}
        base.update(replies)
        self.replies.write_text(json.dumps(base))

    def hc_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def call(self, *args, hc_terminal: str | None = "t0", extra_env=None):
        env = {"PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.dir / "home"),
               "XDG_STATE_HOME": str(self.dir / "state"), "XDG_CONFIG_HOME": str(self.dir / "config")}
        if hc_terminal:
            env["HC_TERMINAL_ID"] = hc_terminal
        env.update(extra_env or {})
        done = subprocess.run([sys.executable, "-B", str(BIN), "call", *args], env=env, capture_output=True,
                              text=True, stdin=subprocess.DEVNULL, timeout=30)
        return done.returncode, done.stdout, done.stderr

    def test_S1_hc_terminal_runs_the_tool_and_prints_its_json(self):
        """agent-runtime/R150"""
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        info = json.loads(out)
        self.assertEqual((info["name"], info["parent"]), ("w1", "t0"))

    def test_S1_plain_text_tool_result_is_wrapped_as_json(self):
        """agent-runtime/R150"""
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        code, out, err = self.call("stop", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        self.assertIn("stopped", json.loads(out)["result"])

    def test_S2_unexpected_exception_prints_error_json_not_a_traceback(self):
        """agent-runtime/R151"""
        (self.dir / "state").write_text("not a directory")  # the store cannot open under XDG_STATE_HOME
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 1)
        self.assertTrue(json.loads(out)["isError"])
        self.assertNotIn("Traceback", err)

    def test_S2_tool_error_prints_result_json_and_exits_1(self):
        """agent-runtime/R151"""
        code, out, err = self.call("status", "--lead", "L1", "--json", "{}")
        self.assertEqual(code, 1, err)
        result = json.loads(out)
        self.assertTrue(result["isError"])
        self.assertIn("name", result["content"][0]["text"])

    def test_S3_outside_hc_exits_2_with_require_hc_message_and_starts_nothing(self):
        """agent-runtime/R152"""
        code, out, err = self.call("spawn", "--lead", "L1", "--json", '{"kind": "worker", "name": "w1"}',
                                   hc_terminal=None)
        self.assertEqual(code, 2)
        self.assertIn("worker tools", err)
        self.assertEqual(self.hc_calls(), [])

    def test_S4_bad_json_or_unknown_tool_exits_2_with_usage_and_starts_nothing(self):
        """agent-runtime/R153"""
        for args in (("status", "--json", "{not json"), ("status", "--json", "[1]"), ("bogus", "--lead", "L1")):
            with self.subTest(args=args):
                code, out, err = self.call(*args)
                self.assertEqual(code, 2)
                self.assertIn("usage", err)
                self.assertEqual(self.hc_calls(), [])

    def test_S5_same_lead_in_two_processes_shares_assignments(self):
        """agent-runtime/R154"""
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        assignment = json.loads(out)["assignment_id"]
        self.set_replies({"get t9": {"out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})}})
        code, out, err = self.call("report", "--json",
                                   json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}), hc_terminal="t9",
                                   extra_env={"CODEX_THREAD_ID": "W1"})
        self.assertEqual(code, 0, err)
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual([(m["assignment_id"], m["kind"], m["text"]) for m in result["messages"]],
                         [(assignment, "complete", "done")])

    def test_S7_other_generation_with_unfinished_work_is_refused_and_the_live_one_keeps_working(self):
        """agent-runtime/R156"""
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        assignment = json.loads(out)["assignment_id"]
        before = [c for c in self.hc_calls() if c[0] in ("run", "kill", "rm", "send")]
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("L1", err)
        self.assertEqual([c for c in self.hc_calls() if c[0] in ("run", "kill", "rm", "send")], before)
        self.set_replies({"get t9": {"out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})}})
        code, out, err = self.call("report", "--json",
                                   json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}), hc_terminal="t9",
                                   extra_env={"CODEX_THREAD_ID": "W1"})
        self.assertEqual(code, 0, err)
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["reason"], "completed")

    def test_S7_other_generation_is_refused_while_the_live_one_has_an_unacknowledged_report(self):
        """agent-runtime/R156"""
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, err)
        assignment = json.loads(out)["assignment_id"]
        self.set_replies({"get t9": {"out": json.dumps({"address": "t9", "name": "w1", "parent": "t0", "pid": 6000})}})
        code, out, err = self.call("report", "--json",
                                   json.dumps({"text": "done", "kind": "complete", "assignment_id": assignment}), hc_terminal="t9",
                                   extra_env={"CODEX_THREAD_ID": "W1"})
        self.assertEqual(code, 0, err)
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 2)
        self.assertIn("L1", err)
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["reason"], "completed")
        self.assertEqual([(m["assignment_id"], m["kind"], m["text"]) for m in result["messages"]],
                         [(assignment, "complete", "done")])

    def test_S7_other_generation_may_start_once_the_live_one_has_nothing_unfinished(self):
        """agent-runtime/R156"""
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        code, out, err = self.call("status", "--lead", "L2", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)

    def test_ancestry_S5_parented_terminal_forced_lead_owns_worker_and_receives_report(self):
        self.set_replies({"get t0": {"out": json.dumps({"address": "t0", "parent": "outer", "pid": 6000})}})
        spawn_args = {"kind": "worker", "name": "w1", "task": "do x", "cwd": str(self.dir)}
        code, out, err = self.call("spawn", "--lead", "L1", "--json", json.dumps(spawn_args))
        self.assertEqual(code, 0, out + err)
        assignment = json.loads(out)["assignment_id"]
        run = next(c for c in self.hc_calls() if c[0] == "run")
        self.assertEqual(run[run.index("--parent") + 1], "t0")
        code, out, err = self.call("report", "--json", json.dumps({"text": "done", "kind": "complete",
                                                                  "assignment_id": assignment}),
                                   hc_terminal="t9", extra_env={"CODEX_THREAD_ID": "W1"})
        self.assertEqual(code, 0, out + err)
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assertEqual(code, 0, out + err)
        self.assertEqual([(m["assignment_id"], m["text"]) for m in json.loads(out)["messages"]],
                         [(assignment, "done")])

    def test_ancestry_S6_parented_terminal_without_lead_remains_worker(self):
        self.set_replies({"get t0": {"out": json.dumps({"address": "t0", "parent": "outer", "pid": 6000})}})
        code, out, err = self.call("status", "--json", '{"name": "w1"}', extra_env={"CODEX_THREAD_ID": "W1"})
        self.assertEqual(code, 1, out + err)
        self.assertIn("caller has no matching active assignment", out)

    def test_ancestry_S5_forced_lead_override_does_not_outlive_its_call(self):
        """The forced lead role belongs to the call that asked for it: a later call in the same process
        resolves the same terminal as a worker again."""
        self.set_replies({"get t0": {"out": json.dumps({"address": "t0", "parent": "outer", "pid": 6000})}})
        env = {"PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.dir / "home"),
               "XDG_STATE_HOME": str(self.dir / "state"), "XDG_CONFIG_HOME": str(self.dir / "config"),
               "HC_TERMINAL_ID": "t0"}
        forced = SimpleNamespace(tool="status", json='{"name": "w1"}', lead="L1")
        plain = SimpleNamespace(tool="status", json='{"name": "w1"}', lead=None)
        chain = {os.getppid(): (5000, "/bin/zsh -l"), 5000: (6000, "/Users/x/.local/bin/herdr-core hold"),
                 6000: (1, "/sbin/launchd")}
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch.object(app, "ps_entry", lambda pid: chain.get(pid)), \
                redirect_stdout(out):
            self.assertEqual(app.cmd_call(forced), 0, out.getvalue())
            self.assertEqual(app.cmd_call(plain), 1, out.getvalue())  # a worker, not the forced lead

    SERVER = (6000, "/Users/x/.local/bin/herdr-core hold --socket s")

    def assert_refused_without_dispatch(self, code, err, needle):
        self.assertEqual(code, 2, err)
        self.assertIn(needle, err)
        self.assertEqual([c for c in self.hc_calls() if c[:1] != ["get"]], [])

    def test_ancestry_S1_claude_or_codex_below_the_server_is_refused_naming_pid_and_name(self):
        for pid, args, name in ((7001, "claude --agent implementer", "claude"),
                                (7002, "/opt/bin/codex exec", "codex"),
                                (7003, "node /usr/lib/codex/bin/codex", "codex")):
            self.set_ancestors([(5000, "/bin/zsh -c call"), (pid, args), self.SERVER])
            code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
            self.assert_refused_without_dispatch(code, err, "%d (%s)" % (pid, name))
            self.assertEqual(out, "")

    def test_ancestry_S2_walk_reaching_the_server_with_no_llm_ancestor_runs_the_call(self):
        self.set_ancestors([(5000, "/bin/zsh -l"), (5001, "python3 driver.py"), self.SERVER])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["name"], "w1")

    def test_ancestry_S2_llm_above_the_server_is_not_an_ancestor_below_it(self):
        self.set_ancestors([(5000, "/bin/zsh -l"), self.SERVER, (8000, "claude")])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assertEqual(code, 0, err)

    def test_ancestry_S3_unreadable_process_table_refuses(self):
        (self.bin / "ps").write_text("#!/bin/sh\nexit 1\n")
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "process")

    def test_ancestry_S3_walk_ending_on_an_unreadable_pid_refuses(self):
        self.set_ancestors([(5000, "/bin/zsh -l"), (5001, "python3 driver.py")], end=4242)
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "process")

    def test_ancestry_S3_walk_longer_than_the_hop_limit_refuses(self):
        self.set_ancestors([(5000 + n, "/bin/sh") for n in range(app.MAX_CALL_ANCESTOR_HOPS + 2)] + [self.SERVER])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "process")

    def test_ancestry_S3_reaching_pid_1_without_reaching_the_terminal_refuses(self):
        self.set_ancestors([(5000, "/bin/zsh -l")])
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S3_unreadable_terminal_pid_refuses(self):
        self.set_replies({"get t0": {"rc": 1}})
        code, out, err = self.call("status", "--lead", "L1", "--json", '{"name": "w1"}')
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S4_shell_forms_that_hide_call_from_the_hook_are_refused_by_the_cli(self):
        # The CLI never sees the command text: whatever shell form launched it, the claude ancestor decides.
        for form in ("/bin/zsh -c $(printf call)", "/usr/bin/env agent-runtime call wait", "/usr/bin/nohup agent-runtime call"):
            self.set_ancestors([(5000, form), (5001, "/bin/zsh -c eval"), (7001, "claude"), self.SERVER])
            code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
            self.assert_refused_without_dispatch(code, err, "7001 (claude)")

    def test_ancestry_S4_orphaned_nohup_shell_reparented_to_pid_1_is_refused(self):
        self.set_ancestors([(5000, "sh -c sleep 1; agent-runtime call wait")])
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S4_process_named_like_the_server_does_not_stop_the_walk(self):
        self.set_ancestors([(5000, "herdr-core bash -c probe"), (7001, "claude"), self.SERVER])
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assert_refused_without_dispatch(code, err, "7001 (claude)")

    def test_ancestry_S4_hc_exec_daemon_reparented_to_pid_1_is_refused(self):
        self.set_ancestors([(5000, "/bin/sh -c probe"), (5001, "hc --hc-launchd")])
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assert_refused_without_dispatch(code, err, "pid 1")

    def test_ancestry_S4_claude_as_the_terminal_root_is_refused(self):
        self.set_ancestors([(5000, "/bin/zsh -c probe"), (6000, "claude --agent implementer")])
        code, out, err = self.call("wait", "--lead", "L1", "--json", '{"target": "all", "timeout": 0}')
        self.assert_refused_without_dispatch(code, err, "6000 (claude)")


if __name__ == "__main__":
    unittest.main()
