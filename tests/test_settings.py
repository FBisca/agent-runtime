"""Tracked Claude settings enable and allow the agent-runtime plugin."""

import json
import unittest
from pathlib import Path

SETTINGS = Path(__file__).resolve().parents[3] / ".claude" / "settings.json"
SERVER = "mcp__plugin_agent-runtime_agent-runtime"


class TrackedSettingsTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads(SETTINGS.read_text())

    def test_runtime_server_is_allowed_whole(self):
        """agent-runtime/R75"""
        self.assertIn(SERVER, self.settings["permissions"]["allow"])

    def test_plugin_and_marketplace_are_enabled(self):
        """agent-runtime/R75"""
        self.assertIs(self.settings["enabledPlugins"].get("agent-runtime@dotfiles"), True)
        self.assertEqual(self.settings["extraKnownMarketplaces"]["dotfiles"],
                         {"source": {"source": "directory", "path": "/Users/fbisca/developer/dotfiles/agents"}})


if __name__ == "__main__":
    unittest.main()
