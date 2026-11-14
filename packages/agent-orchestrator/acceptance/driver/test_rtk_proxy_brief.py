"""The brief a worker receives: it names the RTK proxy for a shell that
rewrites commands, and it asks for the command the worker ran."""

import unittest

import pytest
from fixtures import RunFixture

pytestmark = pytest.mark.integration


class RtkProxyBriefAcceptance(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "checks:\n  fast: uv run pytest -q\n  full:\n    run: uv run pytest -q\n",
            encoding="utf-8",
        )
        self.set_plan(("T1",))
        self.run_driver()
        self.brief = (self.run_dir_of() / "t1" / "task.md").read_text(encoding="utf-8")

    def required_line(self):
        return [
            line
            for line in self.brief.splitlines()
            if line.startswith("Before you report COMPLETE, run:")
        ]

    def test_control_the_brief_names_the_required_check_on_one_line(self):
        """The rule the change must not break: the brief names the required
        check on a single line, and it names it once."""
        self.assertEqual(
            self.required_line(),
            ["Before you report COMPLETE, run: uv run pytest -q."],
        )

    def test_S3_the_brief_names_the_proxy_and_asks_for_the_command_ran(self):
        """The brief tells a worker whose shell rewrites commands to run the
        required check through the proxy, and to report the command it ran."""
        proxy_lines = [line for line in self.brief.splitlines() if "rtk proxy" in line]
        self.assertTrue(proxy_lines, "the brief never names the proxy")
        self.assertTrue(
            any("run" in line.lower() for line in proxy_lines),
            "the brief never tells the worker to run the check through the proxy: %s" % proxy_lines,
        )
        self.assertTrue(
            any(
                "report" in line.lower() and "command" in line.lower()
                for line in self.brief.splitlines()
            ),
            "the brief never asks the worker to report the command it ran",
        )
        self.assertEqual(
            self.required_line(),
            ["Before you report COMPLETE, run: uv run pytest -q."],
        )
