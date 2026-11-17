"""The driver's evidence findings and clarifications."""

import json
import unittest

import pytest
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class EnvelopeFixture(GateFixture):
    def read(self, *parts):
        return json.loads(self.run_dir.joinpath(*parts).read_text(encoding="utf-8"))


class EvidenceTests(EnvelopeFixture, unittest.TestCase):
    def assert_item(self, item, source, check):
        for key in ("kind", "source", "check", "command", "exit", "excerpt", "log_path"):
            self.assertIn(key, item)
        self.assertEqual((item["kind"], item["source"], item["check"]), ("blocking", source, check))
        self.assertTrue(item["command"])
        self.assertIsInstance(item["exit"], int)

    def test_S14_a_failing_verify_fills_each_item_with_its_evidence(self):
        """orchestrate-lean-loop/S14"""
        self.waits([self.worker_done()])
        self.run_driver()
        findings = self.read("t1r1", "findings.json")
        self.assertEqual((findings["contract"], findings["source"]), (2, "verify"))
        item = findings["items"][0]
        self.assert_item(item, "verify", "covers")
        excerpt = "S1: test node src/a/test_a.py::test_a does not exist (no file src/a/test_a.py)"
        self.assertEqual((item["exit"], item["excerpt"]), (1, excerpt))
        self.assertIn("verify T1", item["command"])
        with open(item["log_path"], encoding="utf-8") as log:
            self.assertIn("FAIL covers: " + excerpt, log.read())
        self.assertIn(excerpt, (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8"))
        self.assertIn(
            item["log_path"], (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        )

    def test_S14_a_failing_ci_check_takes_its_excerpt_from_the_failing_log(self):
        """orchestrate-lean-loop/S14"""
        self.checks([("fail", "unit tests", "lint"), "green"])
        self.waits([self.worker_done(tests=True), self.reviewer_done(), self.worker_done("t1r1")])
        self.run_driver()
        findings = self.read("t1r1", "findings.json")
        self.assertEqual((findings["contract"], findings["source"]), (2, "ci"))
        checks = [i["check"] for i in findings["items"]]
        self.assertEqual(checks, ["unit tests", "lint"])
        for item in findings["items"]:
            self.assert_item(item, "ci", item["check"])
            self.assertIn("FAILED STEP LOG TAIL for run 77", item["excerpt"])
            with open(item["log_path"], encoding="utf-8") as log:
                self.assertIn("FAILED STEP LOG TAIL for run 77", log.read())
        self.assertEqual(findings["items"][0]["command"], "gh run view 77 --log-failed")


class ClarificationTests(EnvelopeFixture, unittest.TestCase):
    ASKED = {"finding": "Q1", "asked_by": "implementer", "note": "does empty input raise?"}

    def test_S26_each_clarification_in_a_report_is_logged_with_its_finding_id(self):
        """orchestrate-lean-loop/S26"""
        second = dict(self.ASKED, finding="B1", note="which row is last?")
        self.waits(
            [
                self.worker_done(
                    report=self.report("t1", clarifications=[self.ASKED, second]), tests=True
                )
            ]
        )
        self.run_driver()
        rows = self.run_events("clarification")
        self.assertEqual(
            [(r["finding"], r["asked_by"], r["note"], r["source"]) for r in rows],
            [
                ("Q1", "implementer", "does empty input raise?", "report"),
                ("B1", "implementer", "which row is last?", "report"),
            ],
        )
        self.assertEqual({r["unit"] for r in rows}, {"t1"})
