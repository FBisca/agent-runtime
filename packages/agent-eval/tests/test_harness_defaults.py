"""What agent-eval reads when no override is set: each harness's own home, and Codex's injected
messages told apart from the user's first prompt (R237-R238)."""

import os
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.unit

HOME = Path("/fake-home")
OVERRIDES = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "PI_CODING_AGENT_DIR", "AGENT_EVALS_HOME")


class DefaultHomeTests(unittest.TestCase):
    """The capture fixtures set every override, so only these tests see the defaults."""

    def defaults(self) -> dict[str, Path]:
        from agent_eval import claude, codex, pi, store

        env = {k: v for k, v in os.environ.items() if k not in OVERRIDES}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(Path, "home", return_value=HOME),
        ):
            return {
                "claude": claude.claude_dir(),
                "codex": codex.codex_dir(),
                "pi": pi.pi_dir(),
                "store": store.home(),
            }

    def test_R238_each_harness_home_defaults_under_the_user_home_when_unset(self):
        """R238"""
        self.assertEqual(
            self.defaults(),
            {
                "claude": HOME / ".claude",
                "codex": HOME / ".codex",
                "pi": HOME / ".pi/agent",
                "store": HOME / ".local/state/agent-evals",
            },
        )

    def test_R238_an_empty_override_counts_as_unset(self):
        """R238"""
        from agent_eval import pi

        with (
            mock.patch.dict(os.environ, {"PI_CODING_AGENT_DIR": ""}),
            mock.patch.object(Path, "home", return_value=HOME),
        ):
            self.assertEqual(pi.pi_dir(), HOME / ".pi/agent")


class CodexInjectedTests(unittest.TestCase):
    def test_R237_codex_own_messages_are_injected(self):
        """R237"""
        from agent_eval import codex

        for text in (
            "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>",
            "<environment_context>\n  <cwd>/repo</cwd>\n</environment_context>",
            "<user_instructions>\nbe brief\n</user_instructions>",
            "<guardian_tool_descriptions>\n</guardian_tool_descriptions>",
            "<no retained transcript delta entries>",
        ):
            self.assertTrue(codex.injected(text), text)

    def test_R237_a_user_prompt_that_starts_with_a_bracket_is_not_injected(self):
        """R237"""
        from agent_eval import codex

        for text in ("<task>\nfix it", "<b>bold</b> request", "# Gather packet", "fix the bug"):
            self.assertFalse(codex.injected(text), text)
