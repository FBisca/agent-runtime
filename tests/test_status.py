"""Status interruption detection and argv trimming; hc is faked, never called."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("agent_runtime", HERE.parent / "server" / "agent_runtime.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

CONTINUE = "continue the task where you stopped"


def screen(name):
    return (HERE / "fixtures" / name).read_text(encoding="utf-8")


def terminal(state, cli="claude-code", argv=None):
    return {"address": "t9", "name": "fix", "parent": "t0", "state": "live", "turn": 3,
            "status": {"app": cli, "state": state},
            "foreground": {"argv": argv or [("codex" if cli == "codex" else "claude"), "--agent", "implementer"]}}


def world(raw, text=None, read_fails=False, calls=None):
    def fake(argv):
        if calls is not None:
            calls.append(list(argv))
        if argv[:2] == ["hc", "get"]:
            return json.dumps(raw)
        if argv[:2] == ["hc", "ls"]:
            return json.dumps({"terminals": [{"address": "t9", "parent": "t0"}]})
        if argv[:2] == ["hc", "read"]:
            if read_fails:
                raise RuntimeError("read failed")
            return json.dumps({"complete": True, "how": "history", "text": text})
        raise AssertionError(argv)
    return fake


def status_of(raw, text=None, **kw):
    with patch.object(app, "run_command", side_effect=world(raw, text, **kw)):
        return app.status("hc", "fix", {"HC_TERMINAL_ID": "t0"})


def block(name, app_name):
    return [l.strip() for l in app.last_prompt_block(screen(name), app_name) if l.strip()]


class PromptBlockTests(unittest.TestCase):
    def test_S13_claude_block_skips_the_input_box(self):
        """agent-runtime-tools/S13"""
        lines = block("claude_interrupted.txt", "claude-code")
        self.assertIn("Interrupted", lines[-1])
        self.assertNotIn("? for shortcuts", " ".join(lines))

    def test_S13_claude_typed_box_text_is_not_the_task_line(self):
        """agent-runtime-tools/S13"""
        lines = block("claude_interrupted_typed_box.txt", "claude-code")
        self.assertTrue(any("Working on it" in l for l in lines))
        self.assertFalse(any("also add logging" in l for l in lines))

    def test_S13_codex_block_skips_composer_and_suggestion(self):
        """agent-runtime-tools/S13"""
        for name in ("codex_working_interrupted.txt", "codex_suggestion_composer.txt"):
            with self.subTest(name=name):
                lines = block(name, "codex")
                self.assertTrue(any("Running npm run build" in l for l in lines))
                self.assertFalse(any(l.startswith("›") or "gpt-5" in l for l in lines))

    def test_S13_unknown_app_or_no_prompt_gives_empty_block(self):
        """agent-runtime-tools/S13"""
        self.assertEqual(app.last_prompt_block("plain text", "claude-code"), [])
        self.assertEqual(app.last_prompt_block(screen("claude_finished.txt"), "other"), [])


class InterruptionTests(unittest.TestCase):
    def test_S11_claude_idle_with_marker_is_interrupted_and_resumable(self):
        """agent-runtime-tools/S11"""
        for state in ("idle", "done"):
            with self.subTest(state=state):
                info = status_of(terminal(state), screen("claude_interrupted.txt"))
                self.assertEqual(info["state"], "interrupted")
                self.assertEqual(info["next"][0]["verb"], "resume")
                self.assertEqual(info["next"][0]["do"], CONTINUE)
                self.assertIn("interrupted", app.CONTRACT_STATES)

    def test_S11_codex_working_with_marker_is_interrupted(self):
        """agent-runtime-tools/S11"""
        info = status_of(terminal("working", "codex"), screen("codex_working_interrupted.txt"))
        self.assertEqual(info["state"], "interrupted")
        self.assertEqual(info["next"][0]["verb"], "resume")

    def test_S11_claude_working_is_never_read(self):
        """agent-runtime-tools/S11"""
        calls = []
        info = status_of(terminal("working"), screen("claude_interrupted.txt"), calls=calls)
        self.assertEqual(info["state"], "working")
        self.assertFalse([c for c in calls if c[:2] == ["hc", "read"]])

    def test_S11_normal_finish_stays_idle(self):
        """agent-runtime-tools/S11"""
        self.assertEqual(status_of(terminal("idle"), screen("claude_finished.txt"))["state"], "idle")

    def test_S11_read_uses_200_lines(self):
        """agent-runtime-tools/S11"""
        calls = []
        status_of(terminal("idle"), screen("claude_interrupted.txt"), calls=calls)
        reads = [c for c in calls if c[:2] == ["hc", "read"]]
        self.assertEqual(len(reads), 1)
        self.assertIn("200", reads[0])
        self.assertIn("--json", reads[0])

    def test_S12_two_markers_ask_the_user_instead_of_resuming(self):
        """agent-runtime-tools/S12"""
        info = status_of(terminal("idle"), screen("claude_two_interrupts.txt"))
        self.assertEqual(info["state"], "interrupted")
        first = info["next"][0]
        self.assertIsNone(first["verb"])
        self.assertTrue(first["gap"])
        self.assertIn("ask the user", first["do"])
        self.assertNotIn("resume", [r["verb"] for r in info["next"]])

    def test_S12_single_marker_has_resume_first(self):
        """agent-runtime-tools/S12"""
        self.assertEqual(status_of(terminal("idle"), screen("claude_interrupted.txt"))["next"][0]["verb"], "resume")

    def test_S13_marker_only_before_last_prompt_is_not_interrupted(self):
        """agent-runtime-tools/S13"""
        self.assertEqual(status_of(terminal("idle"), screen("claude_resumed.txt"))["state"], "idle")
        info = status_of(terminal("working", "codex"), screen("codex_resumed.txt"))
        self.assertEqual(info["state"], "working")

    def test_S13_codex_composer_is_not_a_task_line(self):
        """agent-runtime-tools/S13"""
        for name in ("codex_working_interrupted.txt", "codex_suggestion_composer.txt"):
            with self.subTest(name=name):
                self.assertEqual(status_of(terminal("working", "codex"), screen(name))["state"], "interrupted")

    def test_S11_typed_text_in_claude_box_still_interrupted(self):
        """agent-runtime-tools/S11"""
        self.assertEqual(status_of(terminal("idle"), screen("claude_interrupted_typed_box.txt"))["state"],
                         "interrupted")

    def test_S11_marker_text_printed_mid_line_is_not_a_marker(self):
        """agent-runtime-tools/S11"""
        self.assertEqual(status_of(terminal("idle"), screen("claude_printed_marker.txt"))["state"], "idle")

    def test_S12_redrawn_history_counts_one_event(self):
        """agent-runtime-tools/S12"""
        info = status_of(terminal("idle"), screen("claude_redrawn_once.txt"))
        self.assertEqual(info["state"], "interrupted")
        self.assertEqual(info["next"][0]["verb"], "resume")

    def test_blocked_next_has_ask_the_user_gap_not_raw_keys(self):
        """agent-runtime-tools/S14"""
        rows = status_of(terminal("blocked"), "")["next"]
        self.assertFalse(any("hc keys" in r["do"] for r in rows))
        self.assertTrue(any(r["gap"] and "ask the user" in r["do"] for r in rows))

    def test_S14_failed_read_keeps_plain_state(self):
        """agent-runtime-tools/S14"""
        info = status_of(terminal("idle"), None, read_fails=True)
        self.assertEqual(info["state"], "idle")
        self.assertEqual(info["next"][0]["verb"], "message")

    def test_S14_unparseable_read_keeps_plain_state(self):
        """agent-runtime-tools/S14"""
        raw = terminal("idle")
        with patch.object(app, "run_command", side_effect=lambda argv: json.dumps(raw)
                          if argv[:2] == ["hc", "get"] else "not json"):
            self.assertEqual(app.status("hc", "fix", {"HC_TERMINAL_ID": "t0"})["state"], "idle")

    def test_markers_live_in_data_table(self):
        """agent-runtime-tools/S11"""
        table = json.loads((HERE.parent / "server" / "markers.json").read_text(encoding="utf-8"))
        self.assertEqual(set(table), {"claude-code", "codex"})


class ArgvTests(unittest.TestCase):
    ARGV = ["codex", "--no-daemon", "do the whole thing now", "x" * 41, "--model", "gpt-5"]
    WANT = ["codex", "--no-daemon", "<task>", "<task>", "--model", "gpt-5"]

    def test_S15_status_trims_whitespace_and_long_args(self):
        """agent-runtime-tools/S15"""
        info = status_of(terminal("idle", "codex", argv=self.ARGV), screen("claude_finished.txt"))
        self.assertEqual(info["native"]["foreground"]["argv"], self.WANT)

    def test_S15_list_trims_too(self):
        """agent-runtime-tools/S15"""
        raw = terminal("idle", "codex", argv=self.ARGV)
        with patch.object(app, "run_command", side_effect=world(raw, screen("claude_finished.txt"))):
            infos = app.list_children("hc", {"HC_TERMINAL_ID": "t0"})
        self.assertEqual(infos[0]["native"]["foreground"]["argv"], self.WANT)

    def test_S15_exactly_40_characters_is_kept(self):
        """agent-runtime-tools/S15"""
        self.assertEqual(app.trim_argv(["y" * 40, "y" * 41]), ["y" * 40, "<task>"])


if __name__ == "__main__":
    unittest.main()
