"""Seam tests for the live hc checks: the printed spawn lines and the settings they rely on."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from test_hooks import REPORT, run_hook

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "agent-runtime.py"
SETTINGS = SKILL.parents[2] / ".claude" / "settings.json"


def hc_contract():
    r = subprocess.run([sys.executable, "-B", str(SCRIPT), "contract", "--runtime", "hc"],
                       capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return r.stdout


def spawn_line(marker):
    lines = [l for l in hc_contract().splitlines() if marker in l and "hc run" in l]
    assert len(lines) == 1, lines
    return lines[0]


class LiveSeamTests(unittest.TestCase):
    def test_claude_worker_spawn_nests_task_in_run_and_worker_gets_report_hint(self):
        """agent-runtime/R24"""
        line = spawn_line("claude --agent implementer")
        self.assertIn("hc run --bg", line)
        self.assertRegex(line, r'claude --agent implementer "<task>"')
        r = run_hook("session_start.py", {"hook_event_name": "SessionStart"}, ["--cli", "claude"],
                     {"HC_TERMINAL_ID": "w1"}, '{"parent": "lead1"}')
        self.assertIn("role=worker parent=lead1", r.stdout)
        self.assertIn(REPORT, r.stdout)
        self.assertIn('report: hc send lead1 "<result>"', r.stdout)

    def test_codex_worker_spawn_is_no_daemon_with_implementer_toml_source(self):
        """agent-runtime/R25"""
        line = spawn_line("codex --no-daemon")
        self.assertIn("hc run --bg", line)
        self.assertIn("codex --no-daemon", line)
        self.assertIn("implementer.toml", line)

    def test_hc_spawn_lines_never_detach_from_the_caller(self):
        """agent-runtime/R26"""
        out = hc_contract()
        for line in (spawn_line("claude --agent implementer"), spawn_line("codex --no-daemon")):
            self.assertNotIn("--no-parent", line)
            self.assertNotIn("--parent", line)
        self.assertNotIn("--no-parent", out)

    def test_settings_turn_agent_teams_off(self):
        """agent-runtime/R27"""
        env = json.loads(SETTINGS.read_text())["env"]
        self.assertEqual(env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"], "0")


if __name__ == "__main__":
    unittest.main()
