"""The file a report names: the path a reviewer writes, and the one the driver reads.

The wait and the warning are a gate behaviour and live in test_gates.py; the name a report carries and the file
the driver adopts from it are read here.
"""

import json
import tempfile
import unittest
from pathlib import Path

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit

REVIEW = {
    "contract": 2,
    "verdict": "pass",
    "items": [],
    "checks_run": [],
    "commit": "abc1234",
}


class AnnouncedPathTests(unittest.TestCase):
    def setUp(self):
        self.driver = load_script()
        self.root = Path("/run/demo")

    def test_S2_a_report_that_names_a_file_inside_the_run_directory_names_it(self):
        """orchestrator-engine/R155: review-payload-path/S2"""
        path = self.driver.announced_path("2 blocking - /run/demo/t1/review.json", self.root)
        self.assertEqual(path, Path("/run/demo/t1/review.json"))

    def test_S2_a_name_relative_to_the_run_directory_resolves_inside_it(self):
        """orchestrator-engine/R155: review-payload-path/S2"""
        path = self.driver.announced_path("0 blocking - t1/review.json", self.root)
        self.assertEqual(path, Path("/run/demo/t1/review.json"))

    def test_control_a_name_outside_the_run_directory_is_nobody_s(self):
        self.assertIsNone(self.driver.announced_path("2 blocking - /etc/shadow.json", self.root))

    def test_control_a_report_that_names_no_file_names_none(self):
        self.assertIsNone(self.driver.announced_path("the review passed", self.root))

    def test_control_the_last_file_named_inside_the_run_directory_wins(self):
        text = "wrote /run/demo/t1/review.json, evidence at /run/demo/t1/logs/run.json"
        path = self.driver.announced_path(text, self.root)
        self.assertEqual(path, Path("/run/demo/t1/logs/run.json"))


class AdoptReviewTests(unittest.TestCase):
    def setUp(self):
        self.driver = load_script()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.canonical = self.dir / "t1" / "review.json"
        self.elsewhere = self.dir / "t1-review" / "review.json"
        self.elsewhere.parent.mkdir(parents=True)

    def write_elsewhere(self, review=None):
        self.elsewhere.write_text(json.dumps(review or REVIEW), encoding="utf-8")

    def test_S2_a_review_named_under_another_name_is_copied_into_the_drivers_path(self):
        """orchestrator-engine/R155: review-payload-path/S2"""
        self.write_elsewhere()
        path = self.driver.adopt_review(
            {"path": str(self.canonical), "announced": str(self.elsewhere)}
        )
        self.assertEqual(path, self.canonical)
        self.assertTrue(self.canonical.is_file())
        self.assertEqual(json.loads(self.canonical.read_text()), REVIEW)

    def test_control_the_drivers_own_path_wins_when_it_holds_a_review(self):
        """A review at the driver's path is read whether or not the reviewer named it."""
        self.canonical.parent.mkdir(parents=True)
        self.canonical.write_text(json.dumps(REVIEW), encoding="utf-8")
        self.write_elsewhere(dict(REVIEW, verdict="block"))
        path = self.driver.adopt_review(
            {"path": str(self.canonical), "announced": str(self.elsewhere)}
        )
        self.assertEqual(path, self.canonical)
        self.assertEqual(json.loads(self.canonical.read_text())["verdict"], "pass")

    def test_control_a_named_file_that_breaks_the_contract_is_not_adopted(self):
        """Adoption is not a hole: a file the driver cannot read is refused as any other review."""
        self.elsewhere.write_text("{not json", encoding="utf-8")
        path = self.driver.adopt_review(
            {"path": str(self.canonical), "announced": str(self.elsewhere)}
        )
        self.assertEqual(path, self.canonical)
        self.assertFalse(self.canonical.exists())

    def test_control_a_review_with_no_named_file_stays_where_the_driver_puts_it(self):
        path = self.driver.adopt_review({"path": str(self.canonical)})
        self.assertEqual(path, self.canonical)
        self.assertFalse(self.canonical.exists())


if __name__ == "__main__":
    unittest.main()
