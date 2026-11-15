"""The durable record has one owner: `driver/book.py`, and a `Book` the Driver holds.

A run's ledger is what outlives a drive — its version, its kind marker and its tasks. Every read and
every write of it goes through the one collaborator that knows how it is locked, so a ledger the engine
wrote before the package was split opens in the engine after it with no migration, and a member the
package is split into books its state the way the driver does.
"""

import json
import runpy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[3]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
DRIVER = MEMBER / "driver"
BOOK = DRIVER / "book.py"
BUDGET = REPO / "acceptance" / "structure" / "structure_budget.json"

# Every write of the durable record: the claim that reserves an attempt, the bind that names its
# runtime agent, the checkpoint and the merge, the release, and the two summaries a reader asks for.
SPINE = (
    "claim",
    "bind",
    "checkpoint",
    "settle_merged",
    "release",
    "unit_summary",
    "task_summary",
)

# The ledger one engine wrote, in the shape it wrote it: the version, the kind marker, one attempt, and
# the top-level keys a run keeps beside its tasks. Literal on purpose — this is the file that is left on
# disk, and the engine after the split reads what an older one wrote, not what today's writer would.
BEFORE_THE_SPLIT = """{
  "version": 1,
  "kind": "agent-orchestrator/ledger",
  "tasks": {
    "t1": {
      "packet": {"task_id": "t1", "worker": "demo-t1", "unit": "t1", "backend": "claude"},
      "status": "ACTIVE",
      "runtime_id": "rt-1",
      "gate": "reported",
      "started_at": "2026-11-15T09:00:00+00:00"
    }
  },
  "ticket_status": {"T1": "IN PROGRESS"},
  "rounds": {"t1": 2}
}"""


def book_over(root, ledger_path):
    """The Book a Driver holds over one ledger file: a plan, a run directory and the ledger, nothing else."""
    plan = root / "plan.yaml"
    plan.write_text("schema: plan/1\n", encoding="utf-8")
    run = root / "run"
    run.mkdir()
    driver = load_script().Driver(SimpleNamespace(plan=str(plan), repo=str(root)))
    driver.slug, driver.run_dir, driver.ledger_path = "demo", run, ledger_path
    return driver.book


class PreSplitLedgerTests(unittest.TestCase):
    """S13: a ledger written before the split is read after it, with no migration."""

    @pytest.mark.integration
    def test_S13_a_ledger_written_before_the_split_opens_under_the_book(self):
        """structure-budget/S13"""
        from agent_orchestrator.driver import book as spine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "demo.json"
            path.write_text(BEFORE_THE_SPLIT, encoding="utf-8")
            book = book_over(root, path)
            self.assertIsInstance(book, spine.Book, "the Driver holds the record's one owner")

            # The read: the attempt the older engine recorded answers as it stands, on the book's own
            # surface — the split's reader, not a second one beside it.
            self.assertEqual(book.task("t1")["status"], "ACTIVE")
            self.assertEqual(book.unit_of("t1"), "t1")
            # The write: one mark, under the lock the older engine's own file was written with.
            book.mark("t1", gate="converged")

            after = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(after["version"], 1, "the version the older engine wrote is kept")
        self.assertEqual(
            after["kind"],
            "agent-orchestrator/ledger",
            "the kind marker the older engine wrote is kept",
        )
        self.assertEqual(list(after["tasks"]), ["t1"], "the attempt the older engine wrote is kept")
        self.assertEqual(after["tasks"]["t1"]["gate"], "converged")
        self.assertEqual(after["tasks"]["t1"]["packet"]["worker"], "demo-t1")
        self.assertIn("updated_at", after["tasks"]["t1"], "the mark was a write, not a read")
        # Nothing a run keeps beside its tasks is dropped on the way through: no migration ran.
        self.assertEqual(after["ticket_status"], {"T1": "IN PROGRESS"})
        self.assertEqual(after["rounds"], {"t1": 2})


class BookHomeTests(unittest.TestCase):
    """S13: the split's record module is `driver/book.py`, and the package still publishes every write."""

    def test_S13_the_book_is_the_one_owner_of_the_record(self):
        """structure-budget/S13"""
        from agent_orchestrator import driver
        from agent_orchestrator.driver import book as spine

        self.assertEqual(Path(spine.__file__).resolve(), BOOK.resolve())
        # The package publishes every write of the record as the module's own object: a caller, a test
        # and the entry points keep asking `driver.<name>`, and a second copy of a rule in either
        # module would answer from the wrong owner.
        for name in SPINE:
            with self.subTest(name=name):
                self.assertIs(getattr(driver, name), getattr(spine, name))


class BookBudgetTests(unittest.TestCase):
    """S11: the module this step adds is measured like the rest of the package, with no grandfather."""

    def test_S11_the_book_module_is_under_the_paid_ceilings(self):
        """structure-budget/S11"""
        budget = runpy.run_path(str(REPO / "scripts" / "structure_budget.py"))
        measured = budget["measure_tree"](DRIVER)
        self.assertIn("book.py", measured)
        ceilings = json.loads(BUDGET.read_text(encoding="utf-8"))["ceilings"]
        # No base: the module this step adds takes no grandfather, and the two metrics the driver
        # package is paid on are named as S11 names them.
        lines = budget["growth_lines"](
            {"book.py": measured["book.py"]},
            {},
            ceilings,
            ["book.py"],
            ["module-lines", "class-methods"],
        )
        self.assertEqual(lines, [], "the book module is over a ceiling it may not hold")


if __name__ == "__main__":
    unittest.main()
