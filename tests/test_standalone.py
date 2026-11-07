"""Standalone-repo scenarios: public marketplace install, bin shims, CI."""

import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ["spawn", "message", "resume", "wait", "status", "list", "collect", "stop", "report", "task"]
INIT = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}


def converse(argv, env):
    """Drive one MCP stdio server through initialize + tools/list; return the tool names."""
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": INIT},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    done = subprocess.run(argv, input="".join(json.dumps(m) + "\n" for m in lines), env=env,
                          capture_output=True, text=True, timeout=30)
    replies = {r["id"]: r for r in map(json.loads, done.stdout.splitlines()) if "id" in r}
    assert replies[1]["result"]["serverInfo"]["name"], done.stderr
    return [t["name"] for t in replies[2]["result"]["tools"]]


class MarketplaceInstallTest(unittest.TestCase):
    def test_S4_marketplace_lists_the_plugin_and_its_server_starts_and_lists_tools(self):
        """agent-runtime-standalone/S4"""
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertTrue(market["name"])
        self.assertTrue(market["owner"]["name"])
        entry = {p["name"]: p for p in market["plugins"]}["agent-runtime"]
        self.assertEqual(entry["source"], "./")
        plugin = ROOT / entry["source"]
        self.assertEqual(json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["name"], "agent-runtime")
        # Launched the way Claude launches it from .mcp.json, with the plugin root substituted.
        spec = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["agent-runtime"]
        argv = [spec["command"], *[a.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin)) for a in spec["args"]]]
        with tempfile.TemporaryDirectory() as home:
            env = {"PATH": os.environ["PATH"], "HOME": home}
            self.assertEqual(sorted(converse(argv, env)), sorted(TOOLS))
            # And the way Codex launches it, through the bin shim.
            self.assertEqual(sorted(converse([str(ROOT / "bin" / "agent-runtime-mcp")], env)), sorted(TOOLS))


class BinShimTest(unittest.TestCase):
    def test_shims_run_from_a_symlink_and_exec_the_matching_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "agent-runtime"
            link.symlink_to(ROOT / "bin" / "agent-runtime")
            env = {"PATH": os.environ["PATH"], "HOME": tmp}
            done = subprocess.run([str(link), "resolve"], env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(done.returncode, 0, done.stderr)
            # The cli names the harness of the session that ran the shim: claude, codex or pi.
            self.assertRegex(done.stdout.strip(), r"^runtime=native role=lead cli=(claude|codex|pi)$")

    def test_hook_shim_denies_raw_hc_mutation_for_a_subagent(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "hc kill t1"}, "agent_id": "a1"}
        done = subprocess.run([str(ROOT / "bin" / "agent-runtime-hook")], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=15)
        self.assertIn("deny", done.stdout)

    def test_install_script_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"PATH": os.environ["PATH"], "HOME": tmp, "BIN_DIR": tmp + "/bin"}
            outs = [subprocess.run([str(ROOT / "install.sh")], env=env, capture_output=True, text=True, timeout=15)
                    for _ in range(2)]
            for out in outs:
                self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(outs[0].stdout.replace("note: ", ""), outs[1].stdout.replace("note: ", ""))
            for name in ("agent-runtime", "agent-runtime-hook", "agent-runtime-mcp"):
                self.assertTrue((Path(tmp) / "bin" / name).resolve() == (ROOT / "bin" / name))
            self.assertIn("/plugin marketplace add FBisca/agent-runtime", outs[0].stdout)
            self.assertIn("agent-runtime-mcp", outs[0].stdout)


class InstallWiringTest(unittest.TestCase):
    def run_install(self, bin_dir, home):
        return subprocess.run([str(ROOT / "install.sh")], env={"PATH": os.environ["PATH"], "HOME": home, "BIN_DIR": bin_dir},
                              capture_output=True, text=True, timeout=15)

    def test_printed_codex_block_is_complete_valid_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = self.run_install(tmp + '/my "bin', tmp)
            self.assertEqual(out.returncode, 0, out.stderr)
            cfg = tomllib.loads(out.stdout[out.stdout.index("[mcp_servers.agent-runtime]"):])
            server = cfg["mcp_servers"]["agent-runtime"]
            self.assertEqual(server["command"], tmp + '/my "bin/agent-runtime-mcp')
            self.assertEqual(server["env"]["AGENT_RUNTIME_DISPATCH"], "1")
            self.assertEqual(server["tool_timeout_sec"], 600)
            self.assertEqual(sorted(server["tools"]), sorted(TOOLS))
            for name, tool in server["tools"].items():
                self.assertEqual(tool["output_token_limit"], 524288, name)
                if name in ("spawn", "message", "resume", "stop", "report", "task"):
                    self.assertEqual(tool["approval_mode"], "approve", name)
                else:
                    self.assertNotIn("approval_mode", tool, name)
            pre = cfg["hooks"]["PreToolUse"]
            self.assertRegex("Bash", pre[0]["matcher"])
            self.assertRegex("mcp__agent-runtime__task", pre[1]["matcher"])
            for entry in pre:
                hook = entry["hooks"][0]
                self.assertEqual(hook["timeout"], 5)
                self.assertEqual(hook["command"], '"%s/my "bin/agent-runtime-hook" --cli codex' % tmp)

    def test_refuses_to_overwrite_a_real_file_or_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "bin" / "agent-runtime-hook").mkdir(parents=True)
            out = self.run_install(tmp + "/bin", tmp)
            self.assertNotEqual(out.returncode, 0)
            self.assertIn("refusing", out.stderr)
            self.assertFalse((Path(tmp) / "bin" / "agent-runtime").exists())

    def test_installer_resolves_its_root_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "install.sh"
            link.symlink_to(ROOT / "install.sh")
            out = subprocess.run([str(link)], env={"PATH": os.environ["PATH"], "HOME": tmp, "BIN_DIR": tmp + "/bin"},
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual((Path(tmp) / "bin" / "agent-runtime").resolve(), ROOT / "bin" / "agent-runtime")


class HostsSectionTest(unittest.TestCase):
    POINTS = {"server/hc_tools.py": ["CLIS", "spawn", "dispatch_identity", "codex_instructions", "SUBAGENT_TOOL"],
              "server/agent_runtime.py": ["cli_of_args", "detect_cli", "foreground_cli", "hc_app", "CODEX_ENV_MARKERS", "resolve"],
              "server/mcp_server.py": ["TOOLS"],
              "server/markers.json": ["claude-code", "codex"],
              "hooks/pre_tool_use.py": ["agent_id", "TOOL_NAME"]}
    SERVER_POINTS = {"server/hc_tools.py": ["hc", "run", "spawn", "hc_server", "me", "collect", "wait", "stop"],
                     "server/agent_runtime.py": ["hc_get", "run_command", "hc_state", "hc_record"],
                     "server/markers.json": ["prompt", "interrupt"]}

    def test_S16_readme_says_hosts_are_pluggable_names_the_tested_ones_and_points_to_the_code(self):
        """agent-runtime-standalone/S16"""
        readme = (ROOT / "README.md").read_text()
        self.assertIn("Works with any coding agent that speaks MCP; tested with Claude Code and Codex.", readme)
        start = readme.index("## Hosts")
        end = readme.find("\n## ", start + 1)
        section = readme[start:end if end > 0 else None]
        self.assertRegex(section, r"(?i)pluggable")
        self.assertIn("Claude Code", section)
        self.assertIn("Codex", section)
        self.assertIn("integration points", section)
        for path, symbols in self.POINTS.items():
            self.assertIn(f"`{path}`", section, path)
            code = (ROOT / path).read_text()
            for symbol in symbols:
                self.assertIn(symbol, code, f"{symbol} missing from {path}")
                self.assertIn(f"`{symbol}`", section, f"README does not point to {symbol}")


    def test_S16_readme_names_herdr_hc_as_the_server_and_points_to_its_integration_points(self):
        """agent-runtime-standalone/S16"""
        readme = (ROOT / "README.md").read_text()
        start = readme.index("### Servers")
        end = readme.find("\n## ", start + 1)
        section = readme[start:end if end > 0 else None]
        self.assertIn("(https://herdr.dev/core/)", section)
        self.assertIn("`hc`", section)
        self.assertRegex(section, r"(?i)no backend interface")
        for path, symbols in self.SERVER_POINTS.items():
            self.assertIn(f"`{path}`", section, path)
            code = (ROOT / path).read_text()
            for symbol in symbols:
                if path.endswith(".py"):
                    self.assertRegex(code, r"(?m)^def %s\(" % symbol, f"{symbol} missing from {path}")
                    self.assertIn(f"`{symbol}`", section, f"README does not point to {symbol}")
                else:
                    self.assertIn(symbol, code)
        for call in ("run --bg", "get <id> --json", "send", "read", "wait", "ls --all --json", "kill", "rm"):
            self.assertIn(f"hc {call}", section)
            self.assertIn(call.split()[0], (ROOT / "server/hc_tools.py").read_text() + (ROOT / "server/agent_runtime.py").read_text())

    def test_readme_shows_an_architecture_image_that_exists_and_is_small(self):
        readme = (ROOT / "README.md").read_text()
        match = re.search(r"!\[[^\]]*\]\((docs/assets/[^)]+\.png)\)", readme)
        self.assertIsNotNone(match, "README has no image under docs/assets")
        image = ROOT / match.group(1)
        self.assertTrue(image.is_file())
        self.assertLess(image.stat().st_size, 600_000)
        self.assertEqual(image.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertLess(readme.index(match.group(0)), readme.index("## Architecture"), "image sits under the intro")


class CiWorkflowTest(unittest.TestCase):
    def test_S5_ci_runs_the_pytest_suite_on_python_311_and_up(self):
        """agent-runtime-standalone/S5"""
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("astral-sh/setup-uv", text)
        self.assertIn("uv sync", text)
        self.assertIn("uv run pytest", text)
        self.assertNotIn("unittest discover", text)
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(f'"{version}"', text)
        self.assertIn("shellcheck", text)
        self.assertNotRegex(text, r'"3\.(?:[0-9]|10)"')


if __name__ == "__main__":
    unittest.main()
