"""one order for a unit's attempts, and a question answered by the newest attempt that has the thing.

The unit's readers ask three questions — the newest report, the newest findings, and the attempt still open —
and each answer comes from the record's own order (the attempt's `started_at`, then its place in the ledger),
never from the order a dict happens to carry. A reader that finds nothing answers nothing; no reader searches
the unit's directory for a file to stand in.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_orchestrator import driver as engine
from agent_orchestrator.driver.book import Book

pytestmark = pytest.mark.integration


def packet(root, task_id):
    return {
        "task_id": task_id,
        "unit": "t1",
        "worker": task_id,
        "plan": "plan",
        "objective": "work",
        "checkout": str(root),
        "backend": "codex",
        "acceptance": ["done"],
        "checks": [],
        "owns": ["src"],
    }


def report(task_id, check):
    return {
        "task_id": task_id,
        "status": "PARTIAL",
        "summary": "work",
        "files": [],
        "acceptance_met": False,
        "decisions": [],
        "interfaces": [],
        "checks": [{"command": check, "outcome": "passed"}],
    }


class AttemptReadersTests(unittest.TestCase):
    def world(self):
        """A unit that was refused and re-dispatched: t1 released with a report and findings, t1o1 open."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        driver = engine.Driver.__new__(engine.Driver)
        driver.run_dir = root
        driver.ledger_path = root / "ledger.json"
        driver.log = lambda *_, **__: None
        driver.book = Book(driver)
        self.enterContext(
            patch.object(
                engine, "checkout_snapshot", return_value={"checkout": str(root), "head": "abc"}
            )
        )
        with driver.book() as data:
            engine.claim(data, packet(root, "t1"))
            engine.checkpoint(data, "t1", report("t1", "old"))
            data["tasks"]["t1"].update(started_at="2026-01-01")
            engine.release(data, "t1", "retry")
            engine.claim(data, packet(root, "t1o1"))
            data["tasks"]["t1o1"]["started_at"] = "2026-01-02"
        (root / "t1").mkdir()
        (root / "t1" / "findings.json").write_text('{"items": [{"id": "old"}]}')
        return driver, root

    def reported(self, driver, root):
        """The open attempt reports and leaves findings, and the ledger is re-serialised in the other order."""
        with driver.book() as data:
            engine.checkpoint(data, "t1o1", report("t1o1", "new"))
            data["tasks"] = dict(reversed(list(data["tasks"].items())))
        (root / "t1o1").mkdir(exist_ok=True)
        (root / "t1o1" / "findings.json").write_text('{"items": [{"id": "new"}]}')

    def test_S5_each_question_is_answered_by_the_newest_attempt_that_has_it(self):
        driver, root = self.world()
        # The open attempt has reported nothing and left no findings, so the unit's newest report and findings
        # are still the released attempt's — and neither answer is reconstructed by searching the directory.
        self.assertEqual(driver.unit_trail("t1")[1:2], (str(root / "t1" / "findings.json"),))
        self.assertEqual(
            driver.pull._reported_checks("t1o1"), [{"command": "old", "outcome": "passed"}]
        )
        self.assertEqual(driver.pull._open_findings("t1"), [{"id": "old"}])
        self.reported(driver, root)
        self.assertEqual(driver.unit_trail("t1")[2]["checks"][0]["command"], "new")
        self.assertEqual(
            driver.pull._reported_checks("t1"), [{"command": "new", "outcome": "passed"}]
        )
        self.assertEqual(driver.pull.units()["t1"]["packet"]["task_id"], "t1o1")
        self.assertEqual(driver.unit_trail("t1")[1], str(root / "t1o1" / "findings.json"))
        self.assertEqual(driver.pull._open_findings("t1"), [{"id": "new"}])

    def test_S5_equal_times_break_by_ledger_position_and_open_follows_status(self):
        driver, root = self.world()
        self.reported(driver, root)
        with driver.book() as data:
            data["tasks"]["t1"]["started_at"] = "2026-01-02"
        # Equal times use ledger position, even when that attempt is closed.
        self.assertEqual(driver.unit_trail("t1")[2]["checks"][0]["command"], "old")
        self.assertEqual(driver.pull.units()["t1"]["packet"]["task_id"], "t1o1")
