"""The driver's processes have one home: `driver/runtime.py`, published by the package.

Every process the engine starts is here — the command a checkout owns, the runtime's own CLI, `gh`,
and a dispatch tool call through the runtime's client. Each is a seam: a test answers it in process,
so a driver run starts no process per call, and a collaborator the package is split into starts its
processes the same way the driver does.
"""

import json
import runpy
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
DRIVER = MEMBER / "driver"
RUNTIME = DRIVER / "runtime.py"
BUDGET = REPO / "acceptance" / "structure" / "structure_budget.json"

# Every process the engine starts, and the timeout its whole-tree calls take.
HELPERS = (
    "TREE_TIMEOUT",
    "run",
    "checkout_snapshot",
    "merged_snapshot",
    "head_of",
    "resolve_runtime",
    "runtime_process",
    "gh_process",
    "runtime_call",
)

# The modules this step adds: each is new, so each must be under every ceiling with no grandfather.
SPLIT = ("runtime.py", "report.py")
PAID = ("module-lines", "class-methods")


class RuntimeHomeTests(unittest.TestCase):
    """S10: the process helpers moved into their own module, and every published name resolves."""

    def test_S10_the_runtime_helpers_resolve_from_the_driver_package(self):
        """structure-budget/S10"""
        from agent_orchestrator import driver
        from agent_orchestrator.driver import runtime

        self.assertEqual(Path(runtime.__file__).resolve(), RUNTIME.resolve())
        # The package publishes each one as the module's own object: a caller, a test and the entry
        # points keep asking `driver.<name>`, and a second definition of the same call in either
        # module would start a process the test's seam never sees.
        for name in HELPERS:
            with self.subTest(name=name):
                self.assertIs(getattr(driver, name), getattr(runtime, name))


class DriverBudgetTests(unittest.TestCase):
    """S11: the modules this step adds are measured like the rest of the package, with no grandfather."""

    def test_S11_the_split_modules_are_under_the_paid_ceilings(self):
        """structure-budget/S11"""
        budget = runpy.run_path(str(REPO / "scripts" / "structure_budget.py"))
        # The measure takes the file list: a unit test names the module it means and starts no process.
        measured = budget["measure"](DRIVER, SPLIT)
        added = {name: measured[name] for name in SPLIT if name in measured}
        self.assertEqual(sorted(added), sorted(SPLIT))
        ceilings = json.loads(BUDGET.read_text(encoding="utf-8"))["ceilings"]
        # No base: every file in this measurement takes no grandfather, and the two metrics the
        # driver package is paid on are named as S11 names them.
        lines = budget["growth_lines"](added, {}, ceilings, ["."], list(PAID))
        self.assertEqual(lines, [], "a module this step adds is over a ceiling it may not hold")


if __name__ == "__main__":
    unittest.main()
