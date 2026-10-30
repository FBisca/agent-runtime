"""Resolver checks with injected env and hc runner; the real hc is never called."""

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys
import os
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "server" / "agent_runtime.py"
BIN = Path(__file__).resolve().parents[4] / "bin" / "bin" / "agent-runtime"
spec = importlib.util.spec_from_file_location("agent_runtime", SCRIPT)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
app.ps_entry = lambda pid: None  # tests never read the real process tree


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


def raw(status, parent="t0", state="live", **extra):
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
                 ("blocked", "live", "blocked"), ("error", "live", "unknown"), (None, "live", "unknown"),
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

    def test_hc_error_state_maps_to_unknown(self):
        """agent-runtime/R12: hc error state maps to unknown."""
        self.assertEqual(hc_status(raw("error"))["state"], "unknown")

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


if __name__ == "__main__":
    unittest.main()
