"""The driver's record helpers have one home: `driver/records.py`, published by the package.

A record is what the engine reads between two steps — a worker's packet, its report, a plan ticket's
row, the runtime's reply, a review verdict. Each helper here answers a question about one without
reading a file, starting a process or changing a ledger, which is what lets a collaborator split off
the driver ask the same question the driver does.
"""

import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
DRIVER = MEMBER / "driver"
RECORDS = DRIVER / "records.py"

# Every pure reading of a record the engine keeps: the packet's route, the report's rules, the plan
# ticket's row, the runtime's reply, the review's verdict.
HELPERS = (
    "ROUTE",
    "routed",
    "whole",
    "CHECK_INSTRUCTIONS",
    "normalise_check",
    "unique_checks",
    "require_completable",
    "context_verdict",
    "split_row",
    "is_done",
    "is_agent",
    "ready",
    "scenario_number",
    "satisfied",
    "terminal_gone",
    "assignment_of",
    "merge_state",
    "check_commit",
)


class RecordHomeTests(unittest.TestCase):
    """S10: the record helpers moved into their own module, and every published name resolves."""

    def test_S10_the_record_helpers_resolve_from_the_driver_package(self):
        from agent_orchestrator import driver
        from agent_orchestrator.driver import records

        self.assertEqual(Path(records.__file__).resolve(), RECORDS.resolve())
        # The package publishes each one as the module's own object: a caller, a test and the
        # entry points keep asking `driver.<name>`, and a second definition of the same rule in
        # either module would answer from the wrong home.
        for name in HELPERS:
            with self.subTest(name=name):
                self.assertIs(getattr(driver, name), getattr(records, name))


if __name__ == "__main__":
    unittest.main()
