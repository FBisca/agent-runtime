"""The command line after the parser is decomposed: the same commands, and one `run_cli` behind them.

`main` was one four-hundred-line function that built the whole parser in one body. It is now one
builder per command group in `driver/cli.py`, so these are the two things that must not move with it:
the surface a caller types — the nineteen command names and each group's subcommands — and the entry
points, which import one `run_cli` and must keep reaching the same one.
"""

import contextlib
import io
import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# <repo>/packages/agent-orchestrator/tests/test_driver/<this file>
REPO = Path(__file__).resolve().parents[4]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"

# The commands the parser offered when this refactor was planned, in the order its help printed them,
# less `accept`, removed with the evidence items it accepted. Literal on purpose: the set is the contract
# a caller has, not something re-derived from the parser under test. A command added since is free; one
# of these going missing is not.
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


def help_of(parser):
    """The help argparse prints for one parser, captured in process instead of raising SystemExit."""
    out = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out):
        parser.parse_args(["--help"])
    return out.getvalue()


def group_help(parser, group):
    """The help of one command group, below the top level the parser already holds."""
    out = io.StringIO()
    with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out):
        parser.parse_args([group, "--help"])
    return out.getvalue()


class CliSurfaceTests(unittest.TestCase):
    """S14: the decomposition keeps every command name, every group's subcommands and its help."""

    def test_S14_the_parser_still_offers_its_commands(self):
        """structure-budget/S14"""
        from agent_orchestrator import driver

        offered = choices(help_of(driver.build_parser()))
        # Every name it offered before, in the order its help printed them: a command added later does
        # not disturb that list, a dropped or renamed one does.
        self.assertEqual(tuple(name for name in offered if name in COMMANDS), COMMANDS)

    def test_S14_each_command_group_still_offers_its_subcommands(self):
        """structure-budget/S14"""
        from agent_orchestrator import driver

        parser = driver.build_parser()
        for group, expected in GROUPS.items():
            with self.subTest(group=group):
                self.assertEqual(choices(group_help(parser, group)), expected)


class CliHomeTests(unittest.TestCase):
    """S10/S14: the entries and the published names reach the one `run_cli` in `driver/cli.py`."""

    def test_S14_the_package_entry_and_the_module_entry_reach_the_same_run_cli(self):
        """structure-budget/S14"""
        import agent_orchestrator
        from agent_orchestrator import __main__ as module_entry
        from agent_orchestrator import driver
        from agent_orchestrator.driver import cli

        # One parser and one dispatch: the package re-exports the cli module's own objects rather than
        # holding a second copy behind the entry point.
        self.assertIs(driver.main, cli.main)
        self.assertIs(driver.run_cli, cli.run_cli)
        # `python -m agent_orchestrator` imports the same run_cli...
        self.assertIs(module_entry.run_cli, driver.run_cli)
        # ...and the package's own `main` reaches the same parser, so both entries drive the same
        # commands with the same arguments.
        out = io.StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(out):
            agent_orchestrator.main(["--help"])
        self.assertEqual(
            out.getvalue(), help_of(cli.build_parser()), "the package entry drives the same parser"
        )

    def test_S10_the_names_the_package_publishes_are_the_cli_modules_own(self):
        """structure-budget/S10"""
        from agent_orchestrator import driver
        from agent_orchestrator.driver import cli

        # The CLI is a module of the driver package, so the engine still has one home and the names
        # `agent_orchestrator.driver` publishes resolve from there.
        self.assertEqual(Path(cli.__file__).resolve().parent.parent, MEMBER.resolve())
        self.assertEqual(Path(cli.__file__).name, "cli.py")
        for name in ("main", "run_cli", "build_parser"):
            with self.subTest(name=name):
                self.assertIs(getattr(driver, name), getattr(cli, name))


if __name__ == "__main__":
    unittest.main()
