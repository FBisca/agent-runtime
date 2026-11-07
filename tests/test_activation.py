"""Installed Claude and Codex wiring supports the same dispatch flow."""

import json
import re
import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN
sys.path.insert(0, str(PLUGIN / "server"))
import hc_tools  # noqa: E402
import mcp_server  # noqa: E402

import pytest

pytestmark = pytest.mark.unit


MCP_JSON = PLUGIN / ".mcp.json"


def count_words(node):
    if isinstance(node, dict):
        return sum(len(v.split()) if k == "description" and isinstance(v, str) else count_words(v)
                   for k, v in node.items())
    if isinstance(node, list):
        return sum(count_words(v) for v in node)
    return 0


class ActivationWiringTest(unittest.TestCase):
    def test_both_hosts_enable_dispatch_with_600_second_deadline(self):
        """agent-runtime/R56"""
        claude = json.loads(MCP_JSON.read_text())["mcpServers"]["agent-runtime"]
        self.assertEqual(claude["env"]["AGENT_RUNTIME_DISPATCH"], "1")
        self.assertEqual(claude["timeout"], 600000)
        self.assertLessEqual(hc_tools.WAIT_MAX, claude["timeout"] // 1000 - 10)

    def test_claude_hooks_match_every_tool(self):
        """agent-runtime/R56"""
        hooks = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())["hooks"]["PreToolUse"]
        for tool in mcp_server.TOOL_NAMES:
            name = f"mcp__plugin_agent-runtime_agent-runtime__{tool}"
            self.assertTrue(any(re.fullmatch(h["matcher"], name) for h in hooks), tool)

    def test_schema_carries_new_optional_fields(self):
        """agent-runtime/R56"""
        tools = {n: s["properties"] for n, _, s in mcp_server.TOOLS}
        for name, props in tools.items():
            self.assertIn("receipt", props, name)
        self.assertIn("kind", tools["report"])
        self.assertIn("assignment_id", tools["report"])
        self.assertIn("message_id", tools["report"])
        self.assertIn("message_id", tools["message"])
        self.assertIn("cursor", tools["task"])

    def test_descriptions_fit_400_words_and_teach_the_wait_flow(self):
        """agent-runtime/R56"""
        listing = [{"description": d, "inputSchema": s} for _, d, s in mcp_server.TOOLS]
        self.assertLessEqual(count_words(listing), 400)
        text = " ".join(d for _, d, _ in mcp_server.TOOLS).lower()
        for phrase in ("wait(all)", "receipt", "progress|complete|blocked|decision", "diagnostic"):
            self.assertIn(phrase, text)
        desc = {n: d.lower() for n, d, _ in mcp_server.TOOLS}
        self.assertIn("diagnostic", desc["status"])
        self.assertIn("diagnostic", desc["collect"])
        self.assertIn("promptly", desc["spawn"])

    def test_S4_wait_and_resume_descriptions_say_delivery_is_pull_only(self):
        """agent-runtime/R99"""
        desc = {n: d.lower() for n, d, _ in mcp_server.TOOLS}
        for name in ("wait", "resume"):
            self.assertIn("pull-only", desc[name], name)
            self.assertIn("new wait(all)", desc[name], name)
        listing = [{"description": d, "inputSchema": s} for _, d, s in mcp_server.TOOLS]
        self.assertLessEqual(count_words(listing), 400)

    def test_plugin_version_is_bumped_and_consistent(self):
        """agent-runtime/R56"""
        version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
        self.assertEqual(version, "0.2.4")
        self.assertEqual(mcp_server.SERVER_INFO["version"], version)
        market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
        for plugin in market["plugins"]:
            self.assertIn(plugin.get("version", version), (version,))

    def test_docs_list_all_ten_tools_and_state_no_stale_version(self):
        """agent-runtime/R81"""
        tools = [name for name, _, _ in mcp_server.TOOLS]
        self.assertEqual(len(tools), 10)
        readme = (REPO / "README.md").read_text()
        section = readme
        spec = (REPO / "docs/specs/agent-runtime.md").read_text()
        intro = spec.split("## Requirements")[0]
        description = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["description"]
        for label, text in (("README", section), ("plugin.json", description), ("spec intro", intro)):
            for tool in tools:
                self.assertRegex(text, r"\b%s\b" % tool, "%s lacks %s" % (label, tool))
        for label, text in (("README", readme), ("spec", spec)):
            for line in text.splitlines():
                self.assertIsNone(re.search(r"\b0\.1\.\d|\b0\.2\.0\b", line), "%s states a stale version: %s" % (label, line))
        self.assertIn("receipt", intro)

    def test_descriptions_state_lookup_and_dispatch_rules(self):
        """agent-runtime/R56"""
        desc = {n: d for n, d, _ in mcp_server.TOOLS}
        self.assertIn("lookup is rejected", desc["spawn"])
        self.assertIn("per work period", desc["wait"])
        self.assertIn("required", desc["report"])
        wait = next(s for n, _, s in mcp_server.TOOLS if n == "wait")
        self.assertIn("dispatch allows 0", wait["properties"]["timeout"]["description"])

    def test_spec_intro_says_diagnostics_authorize_store_first_then_fall_back(self):
        """agent-runtime/R92"""
        spec = (REPO / "docs/specs/agent-runtime.md").read_text()
        intro = spec[:spec.index("## Requirements")]
        self.assertRegex(intro, r"(?is)diagnostics authorize against the dispatch store first")
        self.assertIn("hc-only state with `delivery_error`", intro)
        self.assertRegex(intro, r"(?i)authorization failures? return no hc data")


if __name__ == "__main__":
    unittest.main()
