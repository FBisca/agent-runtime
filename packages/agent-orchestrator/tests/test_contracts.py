"""The agent -> driver handoff files: versioned JSON, validated in code."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agent_orchestrator import contracts, driver

pytestmark = pytest.mark.unit


def report(**over):
    return dict(
        {
            "contract": 1,
            "task_id": "t1",
            "status": "COMPLETE",
            "summary": "done",
            "acceptance_met": True,
            "files": [],
            "decisions": [],
            "interfaces": [],
            "remaining": [],
            "checks": [],
            "head": "abc1234",
            "pr": None,
        },
        **over,
    )


MAJOR = {"id": "M1", "file": "a.py", "line": 3, "problem": "wrong", "fix": "right"}


def review(**over):
    return dict(
        {"contract": 1, "verdict": "pass", "major": [], "deferred": [], "commit": "abc1234"}, **over
    )


EVIDENCE = {
    "command": "pytest -k last",
    "exit": 1,
    "excerpt": "AssertionError: 3 != 4",
    "log_path": "/run/t1/pytest.log",
}
BLOCKING = {
    "id": "B1",
    "kind": "blocking",
    "summary": "drops the last row",
    "answer_required": False,
    "file": "a.py",
    "line": 3,
    "fix": "include the final row",
    "evidence": EVIDENCE,
}
QUESTION = {
    "id": "Q1",
    "kind": "question",
    "summary": "should empty input raise?",
    "answer_required": True,
}
NIT = {"id": "N1", "kind": "nit", "summary": "rename the helper", "answer_required": False}


def review2(**over):
    return dict(
        {"contract": 2, "verdict": "pass", "items": [], "checks_run": [], "commit": "abc1234"},
        **over,
    )


class ContractTests(unittest.TestCase):
    def rejects(self, validate, value, needle):
        with self.assertRaises(contracts.ContractError) as caught:
            validate(value)
        self.assertIn(needle, str(caught.exception))

    def test_S6a_report_accepts_the_checkpoint_shape_with_head_and_pr(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        self.assertEqual(
            contracts.validate_report(report(pr="https://example.invalid/pr/1"))["head"], "abc1234"
        )

    def test_S9_report_contract_3_carries_covers_mapping_scenarios_to_test_nodes(self):
        covers = {"S1": ["tests/test_a.py::test_one", "tests/test_a.py::TestA::test_two"]}
        validated = contracts.validate_report(report(contract=3, covers=covers))
        self.assertEqual(validated["covers"], covers)
        self.assertEqual(contracts.Report.model_validate(validated).covers, covers)

    def test_S9_report_contract_3_without_covers_is_refused_naming_the_field(self):
        self.rejects(contracts.validate_report, report(contract=3), "covers")

    def test_S9_covers_belongs_to_contract_3_only(self):
        self.rejects(
            contracts.validate_report, report(contract=2, covers={"S1": ["a::b"]}), "contract 3"
        )

    def test_S9_a_malformed_covers_names_the_scenario_it_breaks(self):
        for covers, needle in (
            ({"one": ["a.py::t"]}, "one"),
            ({"S1": "a.py::t"}, "S1"),
            ({"S1": []}, "S1"),
            ({"S1": ["a.py::t", ""]}, "S1"),
        ):
            self.rejects(contracts.validate_report, report(contract=3, covers=covers), needle)

    def test_S9_a_verify_finding_is_a_gate_item_of_source_verify(self):
        item = {
            "kind": "blocking",
            "source": "verify",
            "check": "covers",
            "command": "verify T1",
            "exit": 1,
            "excerpt": "S1: test node a.py::t does not exist",
            "log_path": "/run/t1/verify.log",
        }
        self.assertEqual(contracts.make_findings("verify", 1, [item])["items"], [item])

    def test_S6a_report_rejects_a_missing_version_head_or_bad_pr(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        bare = report()
        del bare["contract"]
        self.rejects(contracts.validate_report, bare, "contract")
        self.rejects(contracts.validate_report, report(contract=4), "contract")
        for key in ("head", "task_id", "summary"):
            broken = report()
            del broken[key]
            self.rejects(contracts.validate_report, broken, key)
        self.rejects(contracts.validate_report, report(pr=7), "pr")
        self.rejects(contracts.validate_report, report(status="DONE"), "status")
        self.rejects(contracts.validate_report, report(files="x"), "files")
        self.rejects(contracts.validate_report, [], "object")

    def test_S6a_review_block_holds_exactly_when_major_is_not_empty(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        contracts.validate_review(review())
        contracts.validate_review(
            review(verdict="block", major=[MAJOR], deferred=[{"id": "D1", "note": "n"}])
        )
        self.rejects(contracts.validate_review, review(verdict="block"), "block")
        self.rejects(contracts.validate_review, review(verdict="pass", major=[MAJOR]), "pass")
        self.rejects(contracts.validate_review, review(verdict="maybe"), "verdict")

    def test_S6a_review_items_need_every_field(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        for key in MAJOR:
            item = {k: v for k, v in MAJOR.items() if k != key}
            self.rejects(contracts.validate_review, review(verdict="block", major=[item]), key)
        self.rejects(contracts.validate_review, review(deferred=[{"id": "D1"}]), "note")
        self.rejects(contracts.validate_review, review(commit=""), "commit")

    def test_S6a_findings_carry_a_source_an_attempt_and_the_blocking_items(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        good = contracts.make_findings("converge", 1, ["FAIL coverage: S1"], version=1)
        self.assertEqual(
            good,
            {"contract": 1, "source": "converge", "attempt": 1, "items": ["FAIL coverage: S1"]},
        )
        contracts.make_findings("review", 2, [MAJOR], version=1)
        contracts.make_findings("ci", 1, ["FAIL pr 4"], version=1)
        self.rejects(lambda v: contracts.make_findings(*v, version=1), ("lint", 1, ["x"]), "source")
        self.rejects(
            lambda v: contracts.make_findings(*v, version=1), ("converge", 0, ["x"]), "attempt"
        )
        self.rejects(lambda v: contracts.make_findings(*v, version=1), ("converge", 1, []), "items")
        self.rejects(
            lambda v: contracts.make_findings(*v, version=1), ("review", 1, ["text"]), "items"
        )
        self.rejects(
            lambda v: contracts.make_findings(*v, version=1), ("converge", 1, [MAJOR]), "items"
        )
        self.rejects(lambda v: contracts.make_findings(*v), ("review", 1, [BLOCKING, NIT]), "N1")

    def test_S6a_load_names_a_missing_unparseable_or_oversized_file(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S6a"""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "review.json"
            self.rejects(
                lambda p: contracts.load(p, contracts.validate_review), path, "review.json"
            )
            path.write_text("{not json", encoding="utf-8")
            self.rejects(
                lambda p: contracts.load(p, contracts.validate_review), path, "review.json"
            )
            path.write_text(json.dumps(review()), encoding="utf-8")
            self.assertEqual(contracts.load(path, contracts.validate_review)["verdict"], "pass")
            path.write_text(json.dumps(review(commit="x" * 70000)), encoding="utf-8")
            self.rejects(lambda p: contracts.load(p, contracts.validate_review), path, "64 KiB")

    def test_S12_a_review_item_needs_id_kind_summary_and_answer_required(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S12"""
        contracts.validate_review(review2(verdict="block", items=[BLOCKING, QUESTION, NIT]))
        for key in ("id", "kind", "summary", "answer_required"):
            item = {k: v for k, v in QUESTION.items() if k != key}
            self.rejects(contracts.validate_review, review2(items=[item]), key)
            self.rejects(contracts.validate_review, review2(items=[item]), "item")
        self.rejects(
            contracts.validate_review,
            review2(items=[dict(QUESTION, answer_required="yes")]),
            "answer_required",
        )
        self.rejects(
            contracts.validate_review, review2(items=[dict(QUESTION, kind="major")]), "kind"
        )
        self.rejects(contracts.validate_review, review2(items=["text"]), "item")
        self.rejects(
            contracts.validate_review,
            review2(items=[dict(QUESTION, id="Q7"), {"kind": "nit"}]),
            "id",
        )

    def test_S12_a_blocking_item_needs_file_line_fix_and_evidence_and_names_the_item(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S12"""
        for key in ("file", "line", "fix", "evidence"):
            item = {k: v for k, v in BLOCKING.items() if k != key}
            self.rejects(
                contracts.validate_review, review2(verdict="block", items=[item]), "item B1"
            )
            self.rejects(contracts.validate_review, review2(verdict="block", items=[item]), key)
        for key in EVIDENCE:
            item = dict(BLOCKING, evidence={k: v for k, v in EVIDENCE.items() if k != key})
            self.rejects(
                contracts.validate_review, review2(verdict="block", items=[item]), "evidence"
            )
            self.rejects(contracts.validate_review, review2(verdict="block", items=[item]), key)
        self.rejects(
            contracts.validate_review,
            review2(verdict="block", items=[dict(BLOCKING, line=0)]),
            "line",
        )

    def test_S12_fields_outside_the_core_are_kept_verbatim(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S12"""
        item = dict(
            BLOCKING,
            severity="high",
            scenario="S4",
            why="the total is wrong",
            verify="pytest",
            team={"x": [1]},
        )
        review = contracts.validate_review(
            review2(verdict="block", items=[item, dict(NIT, anything="goes")])
        )
        blocking, rest = contracts.review_items(review)
        self.assertEqual((blocking, rest), ([item], [dict(NIT, anything="goes")]))

    def test_S13_the_verdict_and_the_items_must_agree(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S13"""
        contracts.validate_review(review2(items=[QUESTION, NIT]))
        self.rejects(
            contracts.validate_review, review2(verdict="block", items=[QUESTION, NIT]), "block"
        )
        self.rejects(contracts.validate_review, review2(verdict="pass", items=[BLOCKING]), "pass")

    def test_S17_responses_are_fixed_or_disputed_with_a_note(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S17"""
        response = {"id": "Q1", "status": "fixed", "commit": "abc1234", "note": "done"}
        contracts.validate_report(report(responses=[response]))
        contracts.validate_report(
            report(responses=[dict(response, status="disputed", changes_code=True)])
        )
        for broken in (
            dict(response, status="maybe"),
            dict(response, note=""),
            {"status": "fixed", "note": "n"},
            dict(response, changes_code="yes"),
        ):
            self.rejects(contracts.validate_report, report(responses=[broken]), "response")
        self.assertEqual(
            contracts.missing_responses(report(responses=[response]), ["Q1", "Q2"]), ["Q2"]
        )

    def test_S26_clarifications_name_the_finding_who_asked_and_the_note(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S26"""
        clarification = {
            "finding": "Q1",
            "asked_by": "implementer",
            "note": "does empty input raise?",
        }
        contracts.validate_report(report(clarifications=[clarification]))
        contracts.validate_review(
            review2(clarifications=[dict(clarification, asked_by="reviewer")])
        )
        for key in clarification:
            broken = {k: v for k, v in clarification.items() if k != key}
            self.rejects(contracts.validate_report, report(clarifications=[broken]), key)
        self.rejects(contracts.validate_review, review2(clarifications="x"), "clarifications")

    def test_S15_checks_run_name_the_command_the_exit_and_the_commit(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S15"""
        run = {"command": "pytest", "exit": 0, "at_commit": "abc1234"}
        contracts.validate_review(review2(checks_run=[run]))
        for key in run:
            self.rejects(
                contracts.validate_review,
                review2(checks_run=[{k: v for k, v in run.items() if k != key}]),
                key,
            )
        finding = contracts.make_findings("review", 1, [BLOCKING, QUESTION], checks_run=[run])
        self.assertEqual((finding["contract"], finding["checks_run"]), (2, [run]))

    def test_S14_converge_and_ci_items_carry_the_evidence_the_driver_filled(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S14"""
        item = {
            "id": "C1",
            "kind": "blocking",
            "source": "converge",
            "check": "coverage",
            "command": "converge plan",
            "exit": 1,
            "excerpt": "FAIL coverage: S1",
            "log_path": "/run/t1/converge.log",
        }
        self.assertEqual(contracts.make_findings("converge", 1, [item])["items"], [item])
        for key in ("kind", "source", "check", "command", "exit", "excerpt", "log_path"):
            self.rejects(
                lambda v: contracts.make_findings(*v),
                ("converge", 1, [{k: v for k, v in item.items() if k != key}]),
                key,
            )
        self.rejects(lambda v: contracts.make_findings(*v), ("ci", 1, [item]), "source")

    def test_S11_a_decision_names_one_action_from_the_menu(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S11"""
        for action in ("retry", "retry one class up", "fresh worker", "mark blocked", "skip"):
            decision = {"contract": 1, "action": action, "reason": "because"}
            self.assertEqual(contracts.validate_decision(decision)["action"], action)
        answer = {
            "contract": 1,
            "action": "answer",
            "reason": "scope",
            "answer": "use the public API",
        }
        self.assertEqual(contracts.validate_decision(answer)["answer"], "use the public API")
        self.rejects(
            contracts.validate_decision,
            {"contract": 1, "action": "answer", "reason": "x"},
            "answer",
        )
        self.rejects(
            contracts.validate_decision,
            {"contract": 1, "action": "skip", "reason": "x", "answer": "y"},
            "answer",
        )
        self.rejects(
            contracts.validate_decision,
            {"contract": 1, "action": "ask user", "reason": "x"},
            "action",
        )
        self.rejects(
            contracts.validate_decision,
            {"contract": 1, "action": "retry now", "reason": "x"},
            "action",
        )
        self.rejects(contracts.validate_decision, {"contract": 1, "action": "skip"}, "reason")
        self.rejects(
            contracts.validate_decision,
            {"contract": 4, "action": "skip", "reason": "x"},
            "contract",
        )
        self.rejects(contracts.validate_decision, ["skip"], "object")

    def test_S1_a_request_names_the_unit_the_event_the_evidence_and_the_menu(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1"""
        request = contracts.make_request(
            "t1", "t1", "blocked", "cannot reach the database", ["/run/t1/task.md"]
        )
        self.assertEqual(
            (request["status"], request["decision"], [o["action"] for o in request["menu"]]),
            ("open", None, list(contracts.ACTIONS)),
        )
        resolved = dict(request, status="resolved", decision="/run/t1/decision.json")
        self.assertEqual(contracts.validate_request(resolved)["status"], "resolved")
        self.rejects(contracts.validate_request, dict(request, status="resolved"), "decision")
        self.rejects(contracts.validate_request, dict(request, decision="/x"), "decision")
        self.rejects(contracts.validate_request, dict(request, menu=["retry", "ask user"]), "menu")
        self.rejects(
            contracts.validate_request, dict(request, menu=[{"action": "ask user"}]), "menu"
        )
        self.rejects(contracts.validate_request, dict(request, evidence="a"), "evidence")
        self.rejects(contracts.validate_request, dict(request, reason=""), "reason")

    def test_S16_a_new_request_is_contract_3_with_typed_menu_options_and_amend(self):
        request = contracts.make_request("t1", "t1", "blocked", "stuck", ["/run/t1/task.md"])
        self.assertEqual(request["contract"], 3)
        menu = {option["action"]: option for option in request["menu"]}
        self.assertIn("amend", menu)
        for option in menu.values():
            self.assertEqual(sorted(option), ["action", "inputs", "summary"])
            self.assertTrue(option["summary"].strip())
            self.assertEqual(option["inputs"]["reason"], {"type": "string", "required": True})
        self.assertEqual(menu["answer"]["inputs"]["answer"], {"type": "string", "required": True})
        self.assertEqual(
            [name for name, option in menu.items() if "answer" in option["inputs"]], ["answer"]
        )
        self.assertEqual(contracts.validate_request(request), request)

    def test_S16_a_menu_narrowed_to_actions_is_typed_too(self):
        request = contracts.make_request("t1", "t1", "intent", "x", [], menu=("retry", "skip"))
        self.assertEqual([o["action"] for o in request["menu"]], ["retry", "skip"])
        self.assertEqual(contracts.menu_actions(request), ["retry", "skip"])

    def test_S16_a_request_can_still_be_written_with_a_string_menu_as_contract_2(self):
        request = contracts.make_request(
            "t1", "t1", "intent", "x", [], menu=("retry", "skip"), typed=False
        )
        self.assertEqual((request["contract"], request["menu"]), (2, ["retry", "skip"]))
        self.assertEqual(contracts.validate_request(request), request)

    def test_S16_a_contract_2_request_with_a_string_menu_is_still_read(self):
        old = {
            "contract": 2,
            "unit": "t1",
            "task": "t1",
            "event": "blocked",
            "reason": "stuck",
            "evidence": [],
            "menu": ["retry", "skip", "answer"],
            "status": "open",
            "decision": None,
            "created_at": "2026-11-12T00:00:00+00:00",
        }
        self.assertEqual(contracts.validate_request(old), old)
        self.assertEqual(contracts.menu_actions(old), ["retry", "skip", "answer"])

    def test_S16_a_request_menu_must_match_its_contract(self):
        request = contracts.make_request("t1", "t1", "blocked", "stuck", [])
        self.rejects(contracts.validate_request, dict(request, menu=["retry"]), "menu")
        self.rejects(
            contracts.validate_request,
            dict(request, contract=2, menu=request["menu"]),
            "menu",
        )
        option = dict(request["menu"][0])
        for broken in (
            dict(option, summary=""),
            {k: v for k, v in option.items() if k != "inputs"},
            dict(option, inputs={}),
            dict(option, inputs={"reason": {"type": "string"}}),
            dict(option, inputs={"reason": {"type": "string", "required": False}}),
        ):
            self.rejects(contracts.validate_request, dict(request, menu=[broken]), "menu")

    def test_S16_a_decision_may_be_contract_2_or_3_and_amend_is_an_action(self):
        for version in (1, 2, 3):
            decision = {"contract": version, "action": "amend", "reason": "T1 needs a test path"}
            self.assertEqual(contracts.validate_decision(decision)["action"], "amend")
        self.rejects(
            contracts.validate_decision,
            {"contract": 3, "action": "amend", "reason": "x", "answer": "y"},
            "answer",
        )


VIOLATION = {
    "source": "docs/spec.md",
    "id": "R2",
    "expected": "exit 2 means no command ran",
    "observed": "state save failed after the command ran, and exit 2 was returned",
}


class RequirementViolationTests(unittest.TestCase):
    """An asserted requirement violation blocks with its metadata; an optional observation is untouched."""

    def rejects(self, value, needle):
        """Both readers refuse it, each naming the item and what it lacks."""
        for validate in (contracts.validate_review, contracts.ReviewV2.model_validate):
            with self.assertRaises(ValueError) as caught:
                validate(value)
            self.assertIn(needle, str(caught.exception))

    def item(self, **over):
        return dict(BLOCKING, **dict({"requirement_violation": VIOLATION}, **over))

    def test_S3_an_asserted_requirement_violation_must_be_a_blocking_item(self):
        for kind in ("concern", "question", "nit", "deferred"):
            self.rejects(review2(items=[self.item(kind=kind)]), "B1")
        validated = contracts.validate_review(review2(verdict="block", items=[self.item()]))
        self.assertEqual(validated, review2(verdict="block", items=[self.item()]))

    def test_S3_a_requirement_violation_names_its_source_requirement_expected_and_observed(self):
        for field in ("source", "id", "expected", "observed"):
            for bad in (None, "", " ", 7):
                violation: dict[str, Any] = dict(VIOLATION)
                if bad is None:
                    del violation[field]
                else:
                    violation[field] = bad
                review = review2(
                    verdict="block", items=[self.item(requirement_violation=violation)]
                )
                self.rejects(review, "B1")
                self.rejects(review, field)
        self.rejects(review2(verdict="block", items=[self.item(requirement_violation={})]), "B1")
        self.rejects(review2(verdict="block", items=[self.item(requirement_violation="R2")]), "B1")
        # An explicit null asserts nothing: it names no source, requirement, expectation or observed behaviour,
        # so both readers read it as absent rather than sending the reviewer to repair a shape that claims nothing.
        for kind, verdict in (("concern", "pass"), ("blocking", "block")):
            review = review2(
                verdict=verdict, items=[self.item(kind=kind, requirement_violation=None)]
            )
            self.assertEqual(contracts.validate_review(review)["items"], review["items"])
            self.assertEqual(
                contracts.ReviewV2.model_validate(review).model_dump(exclude_unset=True), review
            )

    def test_S4_a_concern_without_a_violation_and_a_legacy_review_still_read(self):
        concern = dict(NIT, kind="concern", summary="optional refactor")
        self.assertEqual(contracts.validate_review(review2(items=[concern]))["items"], [concern])
        self.assertEqual(
            contracts.ReviewV2.model_validate(review2(items=[concern])).model_dump(
                exclude_unset=True
            ),
            review2(items=[concern]),
        )
        legacy = review(verdict="pass")
        self.assertEqual(contracts.validate_review(legacy), legacy)


CLOSURE = {
    "command": "pytest -k last",
    "exit": 0,
    "excerpt": "1 passed",
    "log_path": "/run/fix.log",
}
RECEIPT = {"command": "pytest -k last", "exit": 0, "at_commit": "abc1234"}


def resolution(**over):
    return dict(
        {
            "id": "B1",
            "status": "fixed",
            "commit": "abc1234",
            "note": "the fix holds",
            "evidence": dict(CLOSURE),
        },
        **over,
    )


class ResolutionTests(unittest.TestCase):
    """R139: only a fixed or disproven resolution for an open id, with a nonempty note and passing current-commit
    evidence that a checks_run entry agrees with, is a closure."""

    def rejects(self, value, *needles):
        """Both readers refuse it; `validate_review`'s message names the resolution and what it lacks."""
        with self.assertRaises(ValueError) as caught:
            contracts.validate_review(value)
        for needle in needles:
            self.assertIn(needle, str(caught.exception))
        with self.assertRaises(ValueError):
            contracts.ReviewV2.model_validate(value)  # the Pydantic reader refuses the same payload

    def closing(self, *resolutions, checks=None):
        return review2(
            resolutions=list(resolutions), checks_run=[RECEIPT] if checks is None else checks
        )

    def test_S6_a_fixed_or_disproven_resolution_with_current_commit_evidence_reads(self):
        for status in ("fixed", "disproven"):
            review = self.closing(resolution(status=status))
            self.assertEqual(
                contracts.validate_review(review)["resolutions"], review["resolutions"]
            )
            self.assertEqual(
                contracts.ReviewV2.model_validate(review).model_dump(exclude_unset=True), review
            )
        self.assertEqual(contracts.validate_review(review2()), review2())

    def test_S6_an_unsupported_status_or_a_blank_note_is_refused(self):
        self.rejects(self.closing(resolution(status="acknowledged")), "B1", "status")
        for note in (None, "", "   "):
            self.rejects(self.closing(resolution(note=note)), "B1", "note")

    def test_S6_closure_evidence_must_pass_and_match_a_current_commit_checks_run_entry(self):
        self.rejects(
            self.closing(
                resolution(evidence=dict(CLOSURE, exit=1)), checks=[dict(RECEIPT, exit=1)]
            ),
            "B1",
            "exit",
        )
        self.rejects(self.closing(resolution(), checks=[]), "B1", "checks_run")
        self.rejects(
            self.closing(resolution(), checks=[dict(RECEIPT, command="pytest -k unrelated")]),
            "B1",
            "checks_run",
        )
        self.rejects(
            self.closing(resolution(), checks=[dict(RECEIPT, at_commit="deadbee")]),
            "B1",
            "checks_run",
        )
        self.rejects(self.closing(resolution(commit="deadbee")), "B1", "commit")
        missing = {"id": "B1", "status": "fixed", "commit": "abc1234", "note": "done"}
        self.rejects(self.closing(missing), "B1", "evidence")

    def test_S6_one_resolution_per_open_finding(self):
        self.rejects(self.closing(resolution(), resolution()), "B1", "twice")
        self.rejects(self.closing(resolution(), resolution(status="disproven")), "B1", "twice")
        self.rejects(self.closing(resolution(id="")), "id")

    def test_S6_a_contract_1_review_cannot_carry_resolutions(self):
        """R139: the closure rule has no bypass through the older contract."""
        legacy = review(verdict="pass", resolutions=[{"id": "B1"}])
        with self.assertRaises(contracts.ContractError) as caught:
            contracts.validate_review(legacy)
        self.assertIn("contract 2", str(caught.exception))
        with self.assertRaises(ValueError):
            contracts.ReviewV1.model_validate(legacy)
        with self.assertRaises(ValueError):
            contracts.ReviewV2.model_validate(
                dict({"contract": 1, "verdict": "pass", "commit": "abc1234"}, resolutions=[])
            )

    def test_S6_a_resolution_and_a_blocking_item_naming_the_same_id_are_refused(self):
        review = review2(
            verdict="block",
            items=[BLOCKING],
            resolutions=[resolution()],
            checks_run=[RECEIPT],
        )
        self.rejects(review, "B1", "blocking")


class MixedFindingsTests(unittest.TestCase):
    """R138: an open legacy major item and a new blocking item reach one fix round, which writes and reads."""

    def test_S5_a_legacy_major_and_a_blocking_item_share_one_fix_round(self):
        findings = contracts.make_findings("review", 2, [MAJOR, BLOCKING], [])
        self.assertEqual(findings["contract"], 2)
        self.assertEqual(findings["items"], [MAJOR, BLOCKING])
        self.assertEqual(contracts.validate_findings(findings), findings)
        # a round that is legacy throughout is still written as contract 1, for an old reader
        legacy = contracts.make_findings("review", 1, [MAJOR], [], version=1)
        self.assertEqual(legacy["contract"], 1)


class OpenFindingsRecordTests(unittest.TestCase):
    """R138: the record is `{contract, unit, items}` of the original blocking payloads, one per id."""

    def test_S5_a_blocking_and_a_legacy_major_payload_are_kept_as_they_were_raised(self):
        record = contracts.make_open_findings("t1", [MAJOR, BLOCKING])
        self.assertEqual(record, {"contract": 1, "unit": "t1", "items": [MAJOR, BLOCKING]})
        self.assertEqual(contracts.validate_open_findings(record), record)
        self.assertEqual(
            contracts.make_open_findings("t1", []), {"contract": 1, "unit": "t1", "items": []}
        )

    def test_S5_a_record_names_its_unit_and_lists_each_open_id_once(self):
        for bad in (
            {"contract": 1, "unit": "", "items": []},
            {"contract": 2, "unit": "t1", "items": []},
            {"contract": 1, "unit": "t1", "items": [BLOCKING, BLOCKING]},
            {"contract": 1, "unit": "t1", "items": [NIT]},
            {"contract": 1, "unit": "t1", "items": "B1"},
        ):
            with self.assertRaises(contracts.ContractError) as caught:
                contracts.validate_open_findings(bad)
            self.assertIn("open-findings", str(caught.exception))

    def test_S5_applying_a_review_twice_keeps_one_payload_and_a_resolution_closes(self):
        """R138: replaying a review is idempotent; the payload it was first raised with survives a re-raise."""
        once = driver.Driver.folded_findings([], [BLOCKING], None)
        self.assertEqual(once, [BLOCKING])
        self.assertEqual(driver.Driver.folded_findings(once, [BLOCKING], None), [BLOCKING])
        self.assertEqual(
            driver.Driver.folded_findings(once, [dict(BLOCKING, summary="later")], None),
            [BLOCKING],
        )
        self.assertEqual(driver.Driver.folded_findings(once, [], [{"id": "B1"}]), [])


def contract_report_keys(text):
    """The top-level keys of the example report in the driver's CONTRACT text."""
    line = next(row.strip() for row in text.splitlines() if row.strip().startswith('{"contract"'))
    keys, depth, i, previous = [], 0, 0, ""
    while i < len(line):
        char = line[i]
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == '"':
            end = line.index('"', i + 1)
            if depth == 1 and previous in "{,":
                keys.append(line[i + 1 : end])
            i, previous = end + 1, '"'
            continue
        if not char.isspace():
            previous = char
        i += 1
    return keys


class ContractParityTests(unittest.TestCase):
    def test_S1_report_accepts_the_context_object_workers_write(self):
        """orchestrator-engine/R12: orchestrator-lead-alerts/S1"""
        validated = contracts.validate_report(report(context={"status": "unknown"}))
        self.assertEqual(validated["context"], {"status": "unknown"})

    def test_S2_every_key_the_contract_example_names_is_a_report_field(self):
        """orchestrator-engine/R12: orchestrator-lead-alerts/S2"""
        from agent_orchestrator import driver

        keys = contract_report_keys(driver.CONTRACT)
        self.assertIn("context", keys)
        self.assertEqual([k for k in keys if k not in contracts.Report.model_fields], [])


if __name__ == "__main__":
    unittest.main()


class EvidenceReasonTests(unittest.TestCase):
    """A request's reason is the excerpt's first line, so it names the failing test, not a bare header."""

    def item(self, log, detail=None):
        from types import SimpleNamespace

        from agent_orchestrator import driver

        receipt = {"label": "FAIL", "check": "pytest", "exit": 1, "detail": detail}
        result = SimpleNamespace(receipt=receipt, log=log, path=Path("/r/S23.ticket.1.json"))
        return driver, driver.Driver.__new__(driver.Driver).evidence_item(1, "S23", result)

    def test_the_reason_names_the_failing_test(self):
        log = "collected 3\n_ test_x _\nE   AssertionError: 1 != 2\nFAILED t.py::test_x - AssertionError\n1 failed\n"
        driver, item = self.item(log)
        self.assertEqual(
            driver.short(item), "S23 FAIL: FAILED t.py::test_x - AssertionError — log tail:"
        )

    def test_a_receipt_detail_still_wins(self):
        driver, item = self.item("FAILED t.py::test_x\n", detail="snapshot missing")
        self.assertEqual(driver.short(item), "S23 FAIL: snapshot missing — log tail:")

    def test_a_log_without_a_failure_line_keeps_the_header(self):
        driver, item = self.item("nothing useful\n")
        self.assertEqual(driver.short(item), "S23 FAIL — log tail:")


class DescribeTests(unittest.TestCase):
    """A review item that is not blocking may name a file without a line: the fix round shows it as is."""

    def test_a_concern_with_a_file_and_no_line_is_described(self):
        from agent_orchestrator import driver

        item = {"id": "C1", "kind": "concern", "file": "src/a.py", "summary": "racy"}
        self.assertEqual(driver.describe(item).splitlines()[0], "C1 (concern): src/a.py racy")
        lined = {**item, "line": 7}
        self.assertEqual(driver.describe(lined).splitlines()[0], "C1 (concern): src/a.py:7 racy")


class AffectedNotRunTests(unittest.TestCase):
    AFFECTED = "pytest --base origin/main src/a/"

    def task(self):
        return {
            "packet": {
                "checks": ["ruff check", self.AFFECTED],
                "affected": self.AFFECTED,
                "unit": "t1",
            }
        }

    def report(self, affected, fast=None):
        return report(
            checks=[
                fast or {"command": "ruff check", "outcome": "passed"},
                dict(command=self.AFFECTED, **affected),
            ]
        )

    def test_S5_affected_not_run_with_a_reason_is_accepted(self):
        driver.require_completable(
            self.task(), self.report({"outcome": "not_run", "reason": "no test selected"})
        )

    def test_S5_affected_not_run_without_a_reason_is_refused(self):
        for affected in ({"outcome": "not_run"}, {"outcome": "not_run", "reason": "  "}):
            with self.assertRaises(ValueError):
                driver.require_completable(self.task(), self.report(affected))

    def test_S5_fast_not_run_is_still_refused(self):
        fast = {"command": "ruff check", "outcome": "not_run", "reason": "skipped"}
        with self.assertRaises(ValueError):
            driver.require_completable(self.task(), self.report({"outcome": "passed"}, fast=fast))

    def test_S1_an_affected_command_another_tier_also_requires_is_never_waived_not_run(self):
        # A packet written before equivalent tiers were folded together: the same command twice, a launcher
        # spelling apart, so the `not_run` allowance would waive a required `fast` check.
        task = {
            "packet": {
                "checks": ["uv run pytest -q", "python3 -m pytest -q"],
                "affected": "python3 -m pytest -q",
                "unit": "t1",
            }
        }
        skipped = report(
            checks=[
                {
                    "command": "python3 -m pytest -q",
                    "outcome": "not_run",
                    "reason": "no test selected",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "required checks"):
            driver.require_completable(task, skipped)


class TierBriefTests(unittest.TestCase):
    """The brief and the required checks a ticket worker gets, from the repo config and `--gate`."""

    CONFIG = (
        "checks:\n"
        "  fast: uv run ruff check\n"
        "  affected: uv run pytest --base {base} {paths}\n"
        "  full:\n"
        "    run: uv run pytest -n auto\n"
    )
    OWNS = ["src/a/", "docs/with space.md"]
    AFFECTED = "uv run pytest --base origin/main src/a/ 'docs/with space.md'"

    def drive(self, gate=(), config=CONFIG):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        if config:
            (repo / ".orchestrate").mkdir()
            (repo / ".orchestrate" / "config.yaml").write_text(config, encoding="utf-8")
        run = object.__new__(driver.Driver)
        run.repo = repo
        run.run_dir = repo / "run"
        run.plan_path = repo / "plan.yaml"
        run.args = SimpleNamespace(gate=list(gate), base="origin/main")
        checks, affected, full = run.tier_checks(self.OWNS)
        packet = {"task_id": "t1", "worker": "demo-t1", "checkout": "/w", "owns": self.OWNS}
        packet.update(checks=checks, **({"affected": affected} if affected else {}))
        packet.update(**({"full": full} if full else {}))
        ticket = {"#": "T1", "What it delivers": "x"}
        return packet, run.task_text(None, ticket, packet, [], repo / "run" / "t1")

    def test_S2_the_brief_lists_fast_and_the_rendered_affected_and_names_full_as_not_run(self):
        packet, text = self.drive()
        self.assertEqual(packet["checks"], ["uv run ruff check", self.AFFECTED])
        self.assertIn(
            "Before you report COMPLETE, run: uv run ruff check; " + self.AFFECTED + ".", text
        )
        others = [line for line in text.splitlines() if "uv run pytest -n auto" in line]
        self.assertEqual(len(others), 1, text)
        self.assertIn("do not run", others[0])
        self.assertNotIn("full CI gate", text)

    def test_S3_the_brief_names_the_proxy_and_asks_for_the_command_the_worker_ran(self):
        packet, text = self.drive()
        # The required-check line is the ticket's own: it stays as it is.
        self.assertEqual(
            [
                line
                for line in text.splitlines()
                if line.startswith("Before you report COMPLETE, run:")
            ],
            ["Before you report COMPLETE, run: uv run ruff check; " + self.AFFECTED + "."],
        )
        proxy = [line for line in text.splitlines() if "rtk proxy" in line]
        self.assertTrue(proxy, text)
        self.assertTrue(any("run" in line.lower() for line in proxy), text)
        self.assertTrue(
            any(
                "report" in line.lower() and "command" in line.lower() for line in text.splitlines()
            ),
            text,
        )

    def test_S3_a_named_gate_is_the_required_checks_alone_and_the_tiers_are_not_applied(self):
        packet, text = self.drive(gate=["scripts/checks.sh"])
        self.assertEqual(packet["checks"], ["scripts/checks.sh"])
        self.assertIn("Before you report COMPLETE, run: scripts/checks.sh.", text)
        self.assertNotIn("uv run ruff check", text)
        self.assertNotIn("uv run pytest -n auto", text)

    def test_S1_overlapping_tiers_require_one_command_and_never_forbid_it(self):
        config = "checks:\n  fast: uv run pytest -q\n  affected: uv run pytest -q\n  full:\n    run: uv run pytest -q\n"
        packet, text = self.drive(config=config)
        self.assertEqual(packet["checks"], ["uv run pytest -q"])
        self.assertNotIn("full", packet)
        self.assertEqual(
            [
                line
                for line in text.splitlines()
                if line.startswith("Before you report COMPLETE, run:")
            ],
            ["Before you report COMPLETE, run: uv run pytest -q."],
        )
        self.assertNotIn("do not run", text)

    def test_S1_launcher_spellings_of_one_command_are_required_once(self):
        config = (
            "checks:\n  fast: uv run pytest -q\n  affected: python3 -m pytest -q\n"
            "  full:\n    run: pytest -q\n"
        )
        packet, text = self.drive(config=config)
        self.assertEqual(packet["checks"], ["uv run pytest -q"])
        self.assertNotIn("affected", packet)
        self.assertNotIn("do not run", text)
        driver.require_completable(
            {"packet": dict(packet, unit="t1")},
            report(checks=[{"command": "python3 -m pytest -q", "outcome": "passed"}]),
        )

    def test_S2_a_distinct_full_is_verify_owned_while_fast_and_affected_are_kept(self):
        packet, text = self.drive()
        self.assertEqual(packet["checks"], ["uv run ruff check", self.AFFECTED])
        self.assertEqual(packet["affected"], self.AFFECTED)
        self.assertEqual(packet["full"], "uv run pytest -n auto")
        (full,) = [line for line in text.splitlines() if "uv run pytest -n auto" in line]
        self.assertIn("do not run", full)
        self.assertIn("verify", full)
        self.assertIn("CI", full)
