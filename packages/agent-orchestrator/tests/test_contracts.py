"""The agent -> driver handoff files: versioned JSON, validated in code."""

import json
import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import contracts

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
            {"contract": 3, "action": "skip", "reason": "x"},
            "contract",
        )
        self.rejects(contracts.validate_decision, ["skip"], "object")

    def test_S1_a_request_names_the_unit_the_event_the_evidence_and_the_menu(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S1"""
        request = contracts.make_request(
            "t1", "t1", "blocked", "cannot reach the database", ["/run/t1/task.md"]
        )
        self.assertEqual(
            (request["status"], request["decision"], request["menu"]),
            ("open", None, list(contracts.ACTIONS)),
        )
        resolved = dict(request, status="resolved", decision="/run/t1/decision.json")
        self.assertEqual(contracts.validate_request(resolved)["status"], "resolved")
        self.rejects(contracts.validate_request, dict(request, status="resolved"), "decision")
        self.rejects(contracts.validate_request, dict(request, decision="/x"), "decision")
        self.rejects(contracts.validate_request, dict(request, menu=["retry", "ask user"]), "menu")
        self.rejects(contracts.validate_request, dict(request, evidence="a"), "evidence")
        self.rejects(contracts.validate_request, dict(request, reason=""), "reason")


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


class DescribeTests(unittest.TestCase):
    """A review item that is not blocking may name a file without a line: the fix round shows it as is."""

    def test_a_concern_with_a_file_and_no_line_is_described(self):
        from agent_orchestrator import driver

        item = {"id": "C1", "kind": "concern", "file": "src/a.py", "summary": "racy"}
        self.assertEqual(driver.describe(item).splitlines()[0], "C1 (concern): src/a.py racy")
        lined = {**item, "line": 7}
        self.assertEqual(driver.describe(lined).splitlines()[0], "C1 (concern): src/a.py:7 racy")
