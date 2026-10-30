"""Codex config.toml wiring and the codex() zsh wrapper."""

import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / "agents" / ".codex" / "config.toml"
ALIASES = REPO / "zsh" / ".zsh" / "aliases.zsh"
BIN = REPO / "bin" / "bin"
ENV_VARS = ["HC_TERMINAL_ID", "HC_SCOPE", "HC_TERMINAL_SOCKET", "HERDR_PANE_ID",
            "HERDR_ENV", "HERDR_SOCKET_PATH", "HERDR_SESSION"]


class CodexConfigTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tomllib.loads(CONFIG.read_text())

    def test_S19_mcp_server_has_env_vars_and_approval_modes(self):
        server = self.cfg["mcp_servers"]["agent-runtime"]
        self.assertEqual(server["env_vars"], ENV_VARS)
        self.assertEqual(server["default_tools_approval_mode"], "auto")
        self.assertEqual(server["tool_timeout_sec"], 600)
        for tool in ["spawn", "message", "resume", "stop", "report"]:
            self.assertEqual(server["tools"][tool]["approval_mode"], "approve", tool)
        self.assertNotIn("wait", server["tools"])

    def test_S19_mcp_command_runs_the_symlink_through_a_shell(self):
        server = self.cfg["mcp_servers"]["agent-runtime"]
        self.assertEqual(server["command"], "sh")
        self.assertIn("$HOME/bin/agent-runtime-mcp", " ".join(server["args"]))

    def test_S19_bash_hook_is_wired_and_session_start_pointer_is_gone(self):
        hooks = self.cfg["hooks"]
        self.assertNotIn("SessionStart", hooks)
        pre = hooks["PreToolUse"]
        self.assertEqual(len(pre), 1)
        self.assertEqual(pre[0]["matcher"], "^Bash$")
        self.assertEqual(pre[0]["hooks"][0]["command"], '"$HOME/bin/agent-runtime-hook" --cli codex')

    def test_S19_symlinks_resolve_into_the_plugin(self):
        for name, target in [("agent-runtime-mcp", "server/mcp_server.py"),
                             ("agent-runtime-hook", "hooks/pre_tool_use.py")]:
            link = BIN / name
            self.assertTrue(link.is_symlink(), name)
            self.assertEqual(os.readlink(link), f"../../agents/plugins/agent-runtime/{target}")


class CodexWrapperTest(unittest.TestCase):
    def run_codex(self, *args, hc=True):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "codex"
            fake.write_text('#!/bin/sh\necho "ARGS:$*"\n')
            fake.chmod(0o755)
            env = {"PATH": f"{tmp}:/usr/bin:/bin", "HOME": tmp}
            if hc:
                env["HC_TERMINAL_ID"] = "t1"
            out = subprocess.run(
                ["zsh", "-fc", f'source "{ALIASES}"; codex "$@"', "zsh", *args],
                env=env, text=True, capture_output=True, check=True).stdout
            return out.strip().splitlines()[-1]

    def test_S20_adds_no_daemon_under_hc(self):
        self.assertEqual(self.run_codex("exec", "hi"), "ARGS:--no-daemon exec hi")

    def test_S20_unchanged_without_hc(self):
        self.assertEqual(self.run_codex("exec", "hi", hc=False), "ARGS:exec hi")

    def test_S20_respects_explicit_daemon_flags(self):
        self.assertEqual(self.run_codex("--daemon", "x"), "ARGS:--daemon x")
        self.assertEqual(self.run_codex("--no-daemon", "x"), "ARGS:--no-daemon x")


if __name__ == "__main__":
    unittest.main()
