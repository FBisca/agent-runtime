"""The driver's reporting has one home: `driver/report.py`, published by the package.

The tables and the rendered text a run is read by: the usage figures beside a route, the status a
`status` command prints, and the few lines a report or a fix round shows for a finding. Each takes
the record it is given and returns text, so a collaborator the package is split into prints what the
driver prints.
"""

import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
DRIVER = MEMBER / "driver"
REPORT = DRIVER / "report.py"

# Every rendering of a record the engine keeps: the usage table's columns, the status lines, and the
# one-line descriptions a report or a fix round shows.
HELPERS = (
    "USAGE_COLUMNS",
    "metrics_table",
    "render_status",
    "cell",
    "show",
    "item_text",
    "describe",
    "short",
)


class ReportHomeTests(unittest.TestCase):
    """S10: the reporting tables moved into their own module, and every published name resolves."""

    def test_S10_the_report_helpers_resolve_from_the_driver_package(self):
        """structure-budget/S10"""
        from agent_orchestrator import driver
        from agent_orchestrator.driver import report

        self.assertEqual(Path(report.__file__).resolve(), REPORT.resolve())
        # The package publishes each one as the module's own object: a caller, a test and the entry
        # points keep asking `driver.<name>`, and a table rendered in two places would drift.
        for name in HELPERS:
            with self.subTest(name=name):
                self.assertIs(getattr(driver, name), getattr(report, name))


if __name__ == "__main__":
    unittest.main()
