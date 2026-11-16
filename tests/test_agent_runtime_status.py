"""The status and list views: one contract state per hc or herdmates record, the next rows a lead
reads, and the list that only ever shows a caller its own children."""

import io
import json
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
    return dict(
        {
            "address": "t9",
            "name": "fix",
            "parent": parent,
            "state": state,
            "status": status,
            "turn": 3,
        },
        **extra,
    )


def hc_status(record, name="fix"):
    with patch.object(app, "run_command", side_effect=hc_world({name: record})):
        return app.status("hc", name, {"HC_TERMINAL_ID": "t0"})


class StatusTests(unittest.TestCase):
    def test_hc_status_has_all_fields_and_non_empty_next(self):
        """agent-runtime/R11"""
        info = hc_status(raw({"app": "claude-code", "state": "working"}))
        self.assertEqual(set(info), STATUS_KEYS)
        self.assertEqual(
            (info["name"], info["runtime"], info["role"], info["parent"], info["turn"]),
            ("fix", "hc", "worker", "t0", 3),
        )
        self.assertEqual(info["native"]["status"], {"app": "claude-code", "state": "working"})
        self.assertTrue(info["next"])
        for row in info["next"]:
            self.assertEqual(set(row), {"why", "do", "verb", "gap"})

    def test_native_field_is_trimmed_to_actionable_raw_fields(self):
        """agent-runtime/R11"""
        record = raw(
            {"app": "claude-code", "state": "working"},
            exit_code=None,
            reason=None,
            screen="exact",
            cols=216,
            records={"x": 1},
            foreground={"argv": ["claude"], "pid": 1},
        )
        info = hc_status(record)
        self.assertEqual(
            set(info["native"]),
            {"address", "name", "state", "status", "turn", "exit_code", "reason", "foreground"},
        )
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
            with (
                patch.object(app, "run_command", side_effect=hc_world(world)),
                patch.dict(app.os.environ, {}, clear=True),
                redirect_stdout(buf),
            ):
                self.assertEqual(app.main(["status", "fix", "--runtime", "hc", *flags]), 0)
            self.assertEqual("\n" in buf.getvalue().strip(), multiline)
            json.loads(buf.getvalue())

    def test_status_command_prints_json_through_symlink(self):
        """agent-runtime/R11"""
        with tempfile.TemporaryDirectory() as tmp:
            hc = Path(tmp) / "hc"
            hc.write_text("#!/bin/sh\necho '%s'\n" % json.dumps(raw("idle")))
            hc.chmod(0o755)
            done = subprocess.run(
                [sys.executable, "-B", str(BIN), "status", "fix", "--runtime", "hc"],
                env={"PATH": tmp + ":/usr/bin:/bin"},
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=15,
            )
        self.assertEqual(done.returncode, 0, done.stderr)
        info = json.loads(done.stdout)
        self.assertEqual(set(info), STATUS_KEYS)
        self.assertEqual(info["state"], "idle")

    def test_hc_states_map_to_contract_states(self):
        """agent-runtime/R12"""
        cases = [
            ({"app": "x", "state": "working"}, "live", "working"),
            ("working", "live", "working"),
            ("idle", "live", "idle"),
            ({"app": "x", "state": "done"}, "live", "done"),
            ("blocked", "live", "blocked"),
            ("error", "live", "error"),
            (None, "live", "unknown"),
            ({"state": "weird"}, "live", "unknown"),
            ("done", "finished", "exited"),
            (None, "finished", "exited"),
        ]
        for status, state, expected in cases:
            with self.subTest(status=status, state=state):
                self.assertEqual(hc_status(raw(status, state=state))["state"], expected)

    def test_exited_or_finished_in_state_or_status_maps_to_exited(self):
        """agent-runtime/R12"""
        cases = [
            ("exited", "live"),
            ({"app": "x", "state": "exited"}, "live"),
            ("finished", "live"),
            ({"state": "finished"}, "live"),
            ("idle", "exited"),
            (None, "finished"),
        ]
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
        env = {
            "HERDR_ENV": "1",
            "HERDR_SOCKET_PATH": "/tmp/example.sock",
            "HERDR_PANE_ID": "w1:p1",
            "HERDR_SESSION": "test",
        }
        with patch.object(app, "run_command", return_value='{"result":{}}') as run:
            app.herdr_snapshot("w1:p2", "test", env)
            run.assert_called_once_with(["herdr", "pane", "get", "w1:p2", "--session", "test"])
            with self.assertRaises(ValueError):
                app.herdr_snapshot("w1:p2", "different", env)

    def test_herdmates_maps_agent_status(self):
        """agent-runtime/R13"""
        env = {"HERDR_ENV": "1", "HERDR_SOCKET_PATH": "/tmp/example.sock", "HERDR_PANE_ID": "w1:p1"}
        for herdr, expected in (
            ("idle", "idle"),
            ("working", "working"),
            ("blocked", "blocked"),
            ("done", "done"),
            ("unknown", "unknown"),
            ("weird", "unknown"),
        ):
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
        world = {
            "a": raw("working", parent="t0", address="a"),
            "b": raw("idle", parent="t0", address="b"),
            "c": raw("idle", parent="t5", address="c"),
            "d": raw("idle", parent=None, address="d"),
            "e": raw("done", parent="t0", state="finished", address="e"),
        }
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
            with (
                patch.dict(app.os.environ, {}, clear=True),
                redirect_stdout(out),
                patch.object(app.sys, "stderr", err),
            ):
                self.assertEqual(app.main(["list", "--runtime", runtime]), 0)
            self.assertEqual(json.loads(out.getvalue()), [])
            self.assertIn("only available under hc", err.getvalue())

    def test_list_command_prints_json_array(self):
        """agent-runtime/R15"""
        world = {"a": raw("idle", parent="t0", address="a")}
        buf = io.StringIO()
        with (
            patch.object(app, "run_command", side_effect=hc_world(world)),
            patch.dict(app.os.environ, {"HC_TERMINAL_ID": "t0"}, clear=True),
            redirect_stdout(buf),
        ):
            self.assertEqual(app.main(["list", "--runtime", "hc"]), 0)
        self.assertEqual(len(json.loads(buf.getvalue())), 1)
