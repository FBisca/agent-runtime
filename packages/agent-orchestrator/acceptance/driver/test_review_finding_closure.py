"""A prior blocker remains open until its reviewer supplies a supported resolution."""

import json
import subprocess
import unittest

from fixtures import DRIVER

import pytest
from test_gates import BLOCKING

from test_reuse import ReuseFixture

pytestmark = pytest.mark.integration


class FindingClosureAcceptance(ReuseFixture, unittest.TestCase):
    def first_block(self, *items):
        self.expected_findings = list(items) or [BLOCKING]
        return super().first_block(*items)

    def assert_no_ci_advance(self):
        """Held before CI: no task of the unit ever reaches the CI gate.

        The claim is the requirement's (the unit cannot advance), not the number of `gh pr checks` reads: the
        driver polls a reviewing unit's checks by design (orchestrate-lean-loop/S20), so a poll count says
        nothing about advancement and would contradict a pinned requirement.
        """
        reached = {
            new
            for event in self.run_events("state")
            if event.get("unit") == "t1"
            for old, new in [event.get("gate") or (None, None)]
            if new
        }
        self.assertFalse(reached & {"ready-for-ci"}, "an unresolved blocker must be held before CI")

    def assert_held(self):
        path = self.run_dir / "t1" / "open-findings.json"
        self.assertTrue(
            path.is_file(), "the open finding record must survive independently of process memory"
        )
        record = json.loads(path.read_text())
        self.assertEqual(record["unit"], "t1")
        self.assertEqual(record["items"], self.expected_findings)
        text = (self.run_dir / "t1-review2" / "task.md").read_text()
        for item in self.expected_findings:
            self.assertIn(item["id"], text)
        self.assertIn(str(path), text)
        self.assertNotEqual(self.task("t1r1")["gate"], "done")
        self.assertNotIn("ready-for-ci", self.states_of("t1r1"))
        self.assert_no_ci_advance()

    def test_S5_omitting_a_previous_blocker_does_not_close_it(self):
        self.waits(self.first_block() + [self.fix_report(), self.re_review()])
        self.run_driver()
        self.assert_held()

    def test_S5_a_previous_blocker_cannot_be_downgraded_to_a_concern(self):
        concern = {"id": "B1", "kind": "concern", "summary": "rare case", "answer_required": False}
        self.waits(
            self.first_block() + [self.fix_report(), self.re_review(self.review2(items=[concern]))]
        )
        self.run_driver()
        self.assert_held()

    def test_S5_an_omitted_blocker_outside_the_new_delta_remains_open(self):
        blocker = dict(BLOCKING, file="src/zzz/other.py")
        self.waits(self.first_block(blocker) + [self.fix_report(), self.re_review()])
        self.run_driver()
        self.assert_held()

    def test_S6_a_bare_fixed_acknowledgment_is_not_closure(self):
        resolution = {"id": "B1", "status": "fixed", "commit": "@HEAD", "note": "done"}
        self.waits(
            self.first_block()
            + [self.fix_report(), self.re_review(self.review2(resolutions=[resolution]))]
        )
        self.run_driver()
        self.assert_held()

    def test_S6_stale_resolution_evidence_does_not_close_a_blocker(self):
        resolution = {
            "id": "B1",
            "status": "fixed",
            "commit": "deadbee",
            "note": "verified fix",
            "evidence": {
                "command": "pytest -k last",
                "exit": 0,
                "excerpt": "1 passed",
                "log_path": "/run/fix.log",
            },
        }
        checks = [{"command": "pytest -k last", "exit": 0, "at_commit": "deadbee"}]
        self.waits(
            self.first_block()
            + [
                self.fix_report(),
                self.re_review(self.review2(resolutions=[resolution], checks_run=checks)),
            ]
        )
        self.run_driver()
        self.assert_held()

    def test_S7_restart_does_not_forget_a_previously_recorded_blocker(self):
        self.waits(self.first_block())
        self.run_driver()
        self.waits([self.fix_report(), self.re_review()])
        result = subprocess.run(
            [*DRIVER, *self.driver_args()],
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_held()

    def test_control_a_clean_first_review_still_completes_without_resolutions(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done(self.review2())])
        self.run_driver()
        self.assertEqual(self.task()["gate"], "done")

    def resolve_and_complete(self, status):
        command = "pytest -k last"
        resolution = {
            "id": "B1",
            "status": status,
            "commit": "@HEAD",
            "note": "the named requirement now holds",
            "evidence": {
                "command": command,
                "exit": 0,
                "excerpt": "1 passed",
                "log_path": "/run/fix.log",
            },
        }
        checks = [{"command": command, "exit": 0, "at_commit": "@HEAD"}]
        self.waits(
            self.first_block()
            + [
                self.fix_report(),
                self.re_review(self.review2(resolutions=[resolution], checks_run=checks)),
            ]
        )
        self.run_driver()
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S6_fixed_with_current_commit_evidence_can_complete(self):
        self.resolve_and_complete("fixed")

    def test_S6_disproven_with_current_commit_evidence_can_complete(self):
        self.resolve_and_complete("disproven")

    def test_S3_a_requirement_concern_is_refused_before_ci_and_names_its_id(self):
        item = dict(
            BLOCKING,
            kind="concern",
            requirement_violation={
                "source": "docs/spec.md",
                "id": "R2",
                "expected": "exit 2 means no command ran",
                "observed": "exit 2 after execution",
            },
        )
        self.waits(
            [
                self.worker_done(tests=True),
                *self.repeated(self.reviewer_done(self.review2(items=[item]))),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assert_no_ci_advance()
        self.assertTrue(self.run_events("contract-error"))
        self.assertIn("B1", self.request()["reason"])

    def close_with(self, resolutions, checks):
        self.waits(
            self.first_block()
            + [
                self.fix_report(),
                self.re_review(self.review2(resolutions=resolutions, checks_run=checks)),
            ]
        )
        self.run_driver()
        self.assert_held()

    def resolution(self, **over):
        return dict(
            {
                "id": "B1",
                "status": "fixed",
                "commit": "@HEAD",
                "note": "verified",
                "evidence": {
                    "command": "pytest -k last",
                    "exit": 0,
                    "excerpt": "1 passed",
                    "log_path": "/run/fix.log",
                },
            },
            **over,
        )

    def check(self, **over):
        return dict({"command": "pytest -k last", "exit": 0, "at_commit": "@HEAD"}, **over)

    def test_S6_unknown_id_is_not_closure(self):
        self.close_with([self.resolution(id="B2")], [self.check()])

    def test_S6_blank_justification_is_not_closure(self):
        self.close_with([self.resolution(note=" ")], [self.check()])

    def test_S6_unsupported_status_is_not_closure(self):
        self.close_with([self.resolution(status="acknowledged")], [self.check()])

    def test_S6_failed_evidence_is_not_closure(self):
        evidence = dict(self.resolution()["evidence"], exit=1)
        self.close_with([self.resolution(evidence=evidence)], [self.check(exit=1)])

    def test_S6_missing_check_receipt_is_not_closure(self):
        self.close_with([self.resolution()], [])

    def test_S6_different_command_is_not_closure(self):
        self.close_with([self.resolution()], [self.check(command="pytest -k unrelated")])

    def test_S6_stale_check_with_current_resolution_is_not_closure(self):
        self.close_with([self.resolution()], [self.check(at_commit="deadbee")])

    def test_S6_duplicate_resolution_ids_are_refused(self):
        self.close_with([self.resolution(), self.resolution()], [self.check()])

    def test_S6_conflicting_resolution_statuses_are_refused(self):
        self.close_with([self.resolution(), self.resolution(status="disproven")], [self.check()])

    def test_S5_closing_one_of_two_blockers_preserves_the_other(self):
        other = dict(BLOCKING, id="B2", summary="second violation")
        self.waits(
            self.first_block(BLOCKING, other)
            + [
                self.fix_report(),
                self.re_review(
                    self.review2(resolutions=[self.resolution()], checks_run=[self.check()])
                ),
            ]
        )
        self.run_driver()
        self.expected_findings = [other]
        self.assert_held()

    def test_S4_optional_concern_remains_nonblocking_in_the_driver(self):
        concern = {
            "id": "C1",
            "kind": "concern",
            "summary": "optional cleanup",
            "answer_required": False,
        }
        self.waits(
            [self.worker_done(tests=True), self.reviewer_done(self.review2(items=[concern]))]
        )
        self.run_driver()
        self.assertEqual(self.task()["gate"], "done")
        self.assertTrue(self.checks_log())
        self.assertFalse((self.run_dir / "t1r1" / "findings.json").exists())

    def test_S4_legacy_clean_review_remains_readable_by_the_driver(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done(self.review())])
        self.run_driver()
        self.assertEqual(self.task()["gate"], "done")
