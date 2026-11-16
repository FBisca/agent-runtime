import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_orchestrator import driver as engine
from agent_orchestrator.driver.book import Book

pytestmark = pytest.mark.integration


class AttemptReadersTests(unittest.TestCase):
    def test_S5_readers_use_the_recorded_newest_and_open_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = engine.Driver.__new__(engine.Driver)
            driver.run_dir = root
            driver.ledger_path = root / "ledger.json"
            driver.log = lambda *args, **kwargs: None
            driver.book = Book(driver)
            snapshot = {"checkout": str(root), "head": "abc"}

            def packet(task_id):
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
                    "remaining": [],
                    "checks": [{"command": check, "outcome": "passed"}],
                }

            with patch.object(engine, "checkout_snapshot", return_value=snapshot):
                with driver.book() as data:
                    engine.claim(data, packet("t1"))
                    engine.checkpoint(data, "t1", report("t1", "old"))
                    data["tasks"]["t1"].update(started_at="2026-01-01", report_path="old.json")
                    engine.release(data, "t1", "retry")
                    engine.claim(data, packet("t1o1"))
                    data["tasks"]["t1o1"]["started_at"] = "2026-01-02"
                (root / "t1").mkdir()
                (root / "t1" / "findings.json").write_text('{"items": [{"id": "old"}]}')
                self.assertEqual(driver.unit_trail("t1")[1:], ("", ""))
                self.assertEqual(driver.pull._reported_checks("t1o1"), [])
                self.assertEqual(driver.pull._open_findings("t1"), [])
                with driver.book() as data:
                    engine.checkpoint(data, "t1o1", report("t1o1", "new"))
                    data["tasks"]["t1o1"]["report_path"] = "new.json"
                    data["tasks"] = dict(reversed(list(data["tasks"].items())))
                self.assertEqual(driver.unit_trail("t1")[2], "new.json")
                self.assertEqual(
                    driver.pull._reported_checks("t1"), [{"command": "new", "outcome": "passed"}]
                )
                self.assertEqual(driver.pull.units()["t1"]["packet"]["task_id"], "t1o1")
                (root / "t1o1").mkdir()
                findings = root / "t1o1" / "findings.json"
                findings.write_text('{"items": [{"id": "new"}]}')
                self.assertEqual(driver.unit_trail("t1")[1], str(findings))
                self.assertEqual(driver.pull._open_findings("t1"), [{"id": "new"}])
                with driver.book() as data:
                    data["tasks"]["t1"]["started_at"] = "2026-01-02"
                # Equal times use ledger position, even when that attempt is closed.
                self.assertEqual(driver.unit_trail("t1")[2], "old.json")
                self.assertEqual(driver.pull.units()["t1"]["packet"]["task_id"], "t1o1")
