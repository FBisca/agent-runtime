"""The CLI surface a refactor of `main` must keep: the command names, and one `run_cli` behind them.

The cases drive the CLI as a program and import only the package's published entry points, so they
hold before and after the argument parser is decomposed.
"""

import contextlib
import io
import re
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

COMMANDS = (
    "plan",
    "plans",
    "workbench",
    "inspect",
    "claim",
    "bind",
    "checkpoint",
    "release",
    "show",
    "context",
    "metrics",
    "evidence",
    "run",
    "await",
    "decide",
    "status",
    "verify",
    "accept",
    "complete",
    "launch",
)

GROUPS = {
    "plan": ("schema", "check", "approve", "amend"),
    "plans": ("list",),
    "workbench": ("plans", "plan", "totals"),
}


def choices(text):
    """The command names argparse prints for a parser: the single `{a,b,c}` line of its help."""
    for line in text.splitlines():
        found = re.search(r"\{([a-z,\-]+)\}", line)
        if found:
            return tuple(found.group(1).split(","))
    raise AssertionError("no command choices in the help: %r" % text)


def help_of(*argv):
    """The help text of one command line, driven in its own process so argparse's exit cannot escape."""
    done = subprocess.run(
        [sys.executable, "-m", "agent_orchestrator", *argv, "--help"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


class CliSurfaceTests(unittest.TestCase):
    def test_the_top_level_offers_the_same_twenty_commands(self):
        """structure-budget/S14"""
        # A command added after approval is not a loss: the claim is that the names
        # offered at the base are all still offered, and a superset keeps every one.
        offered = choices(help_of())
        self.assertEqual([n for n in offered if n in COMMANDS], list(COMMANDS))
        self.assertEqual(len(offered), len(set(offered)), "a command is offered twice")

    def test_each_command_group_offers_the_same_subcommands(self):
        """structure-budget/S14"""
        for group, expected in GROUPS.items():
            with self.subTest(group=group):
                self.assertEqual(choices(help_of(group)), expected)

    def test_the_package_entry_and_the_module_entry_reach_the_same_run_cli(self):
        """structure-budget/S14"""
        import agent_orchestrator
        from agent_orchestrator import __main__ as module_entry
        from agent_orchestrator import driver

        self.assertIs(module_entry.run_cli, driver.run_cli)
        self.assertEqual(agent_orchestrator.__all__, ["main"])

        out = io.StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out):
            agent_orchestrator.main(["--help"])
        offered = choices(out.getvalue())
        self.assertEqual([n for n in offered if n in COMMANDS], list(COMMANDS))
