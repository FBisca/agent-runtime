"""The driver's processes have one home: `driver/runtime.py`, published by the package.

Every process the engine starts is here — the command a checkout owns, the runtime's own CLI, `gh`,
and a dispatch tool call through the runtime's client. Each is a seam: a test answers it in process,
so a driver run starts no process per call, and a collaborator the package is split into starts its
processes the same way the driver does.
"""

import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
DRIVER = MEMBER / "driver"
RUNTIME = DRIVER / "runtime.py"

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


class RuntimeHomeTests(unittest.TestCase):
    """S10: the process helpers moved into their own module, and every published name resolves."""

    def test_S10_the_runtime_helpers_resolve_from_the_driver_package(self):
        from agent_orchestrator import driver
        from agent_orchestrator.driver import runtime

        self.assertEqual(Path(runtime.__file__).resolve(), RUNTIME.resolve())
        # The package publishes each one as the module's own object: a caller, a test and the entry
        # points keep asking `driver.<name>`, and a second definition of the same call in either
        # module would start a process the test's seam never sees.
        for name in HELPERS:
            with self.subTest(name=name):
                self.assertIs(getattr(driver, name), getattr(runtime, name))


if __name__ == "__main__":
    unittest.main()
