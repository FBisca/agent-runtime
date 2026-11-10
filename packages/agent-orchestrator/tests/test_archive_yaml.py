"""The archive brief for a YAML plan: its spec, its plan files and oracles copied byte-identical, no Status, no renames."""

import unittest

import pytest
from test_archive import ARCHIVE, ArchiveFixture

pytestmark = pytest.mark.integration


class YamlArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_S29_yaml_archive_brief_copies_plan_files_and_places_oracles(self):
        self.use_yaml_plan(oracle=True)
        plan_before = self.plan.read_bytes()
        self.waits(self.ticket_merged() + [self.archive_done(complete=False), self.gaps_done()])
        self.run_driver()
        self.assertIn(ARCHIVE, self.spawned())
        self.assertEqual(self.plan.read_bytes(), plan_before)

        task_md = (self.run_dir / "archive" / "task.md").read_text(encoding="utf-8")
        owns = next(line for line in task_md.splitlines() if line.startswith("Owns"))
        oracle_dir = self.plan.parent / "demo.oracle"
        narrative = self.plan.parent / "demo.plan.md"
        for needle in (
            "docs/specs/demo.md",
            str(self.plan),
            str(narrative),
            str(oracle_dir),
            "src/a/",
            "byte-identical",
        ):
            self.assertIn(needle, task_md)
        self.assertIn("docs/specs/demo.md", owns)
        self.assertNotIn("docs/specs/,", owns)
        self.assertNotIn("Status: ARCHIVED", task_md)
        self.assertNotIn("Rename each test", task_md)
        self.assertNotIn("Create the living spec", task_md)
