"""The agent -> driver handoff files: versioned JSON, validated in code."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from agent_orchestrator import contracts, driver
from agent_orchestrator.driver.records import CHECK_INSTRUCTIONS

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
    "kind": "deferred",
    "summary": "should empty input raise?",
    "answer_required": True,
}
NIT = {"id": "N1", "kind": "deferred", "summary": "rename the helper", "answer_required": False}


def review2(**over):
    return dict(
        {"contract": 2, "verdict": "pass", "items": [], "checks_run": [], "commit": "abc1234"},
        **over,
    )


class ContractTests(unittest.TestCase):  # noqa: PLR0904
    def rejects(self, validate, value, needle):
        with self.assertRaises(contracts.ContractError) as caught:
            validate(value)
        self.assertIn(needle, str(caught.exception))

    def test_S6a_report_accepts_the_checkpoint_shape_with_head_and_pr(self):
        """code-driven-orchestrator/S6a"""
        self.assertEqual(
            contracts.validate_report(report(pr="https://example.invalid/pr/1"))["head"], "abc1234"
        )

    @pytest.mark.spec("Scenario evidence")
    def test_S9_report_contract_3_carries_covers_mapping_scenarios_to_test_nodes(self):
        covers = {"S1": ["tests/test_a.py::test_one", "tests/test_a.py::TestA::test_two"]}
        validated = contracts.validate_report(report(contract=3, covers=covers))
        self.assertEqual(validated["covers"], covers)
        self.assertEqual(contracts.Report.model_validate(validated).covers, covers)

    @pytest.mark.spec("Scenario evidence")
    def test_S9_report_contract_3_without_covers_is_refused_naming_the_field(self):
        self.rejects(contracts.validate_report, report(contract=3), "covers")

    @pytest.mark.spec("Scenario evidence")
    def test_S9_covers_belongs_to_contract_3_only(self):
        self.rejects(
            contracts.validate_report, report(contract=2, covers={"S1": ["a::b"]}), "contract 3"
        )

    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_malformed_covers_names_the_scenario_it_breaks(self):
        for covers, needle in (
            ({"one": ["a.py::t"]}, "one"),
            ({"S1": "a.py::t"}, "S1"),
            ({"S1": []}, "S1"),
            ({"S1": ["a.py::t", ""]}, "S1"),
        ):
            self.rejects(contracts.validate_report, report(contract=3, covers=covers), needle)

    @pytest.mark.spec("Scenario evidence")
    def test_S9_report_contract_3_carries_red_per_scenario(self):
        covers = {"S1": ["tests/test_a.py::test_one"]}
        red = {"S1": "FAILED tests/test_a.py::test_one - AssertionError: 1 != 2"}
        validated = contracts.validate_report(report(contract=3, covers=covers, red=red))
        self.assertEqual(contracts.Report.model_validate(validated).red, red)

    def test_S9_red_belongs_to_contract_3_only_and_is_keyed_by_scenario(self):
        self.rejects(contracts.validate_report, report(contract=2, red={"S1": "x"}), "red")
        covers = {"S1": ["a.py::t"]}
        self.rejects(
            contracts.validate_report, report(contract=3, covers=covers, red={"one": "x"}), "one"
        )
        self.rejects(contracts.validate_report, report(contract=3, covers=covers, red=["x"]), "red")

    @pytest.mark.spec("Scenario evidence")
    def test_S9_a_verify_finding_is_a_gate_item_of_source_verify(self):
        item = {
            "id": "V1",
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
        """code-driven-orchestrator/S6a"""
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

    def test_S26_clarifications_name_the_finding_who_asked_and_the_note(self):
        """orchestrate-lean-loop/S26"""
        clarification = {
            "finding": "Q1",
            "asked_by": "implementer",
            "note": "does empty input raise?",
        }
        contracts.validate_report(report(clarifications=[clarification]))
        for key in clarification:
            broken = {k: v for k, v in clarification.items() if k != key}
            self.rejects(contracts.validate_report, report(clarifications=[broken]), key)

    def test_S14_converge_and_ci_items_carry_the_evidence_the_driver_filled(self):
        """orchestrate-lean-loop/S14"""
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

    def test_S11_a_decision_names_one_action_from_the_menu(self):
        """code-driven-orchestrator/S11"""
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
        """orchestrate-lean-loop/S1"""
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

    @pytest.mark.spec("Lead decisions")
    def test_a_new_request_is_contract_3_with_typed_menu_options(self):
        request = contracts.make_request("t1", "t1", "blocked", "stuck", ["/run/t1/task.md"])
        self.assertEqual(request["contract"], 3)
        menu = {option["action"]: option for option in request["menu"]}
        self.assertEqual(list(menu), list(contracts.ACTIONS))
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

    @pytest.mark.spec("Lead decisions")
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

    def test_a_decision_may_be_contract_1_2_or_3_and_amend_is_no_action(self):
        for version in (1, 2, 3):
            decision = {"contract": version, "action": "skip", "reason": "T1 is not needed"}
            self.assertEqual(contracts.validate_decision(decision)["action"], "skip")
        self.rejects(
            contracts.validate_decision,
            {"contract": 3, "action": "skip", "reason": "x", "answer": "y"},
            "answer",
        )
        self.rejects(
            contracts.validate_decision,
            {"contract": 3, "action": "amend", "reason": "the plan's verb is plan approve"},
            "action",
        )

    def test_findings_carry_a_source_an_attempt_and_the_items_a_fix_round_fixes(self):
        item = {
            "id": "R1",
            "kind": "blocking",
            "source": "review",
            "file": "a.py",
            "line": 3,
            "quote": "x = 1",
            "claim": "x is never read",
        }
        good = contracts.make_findings("review", 2, [item])
        self.assertEqual((good["contract"], good["source"], good["attempt"]), (2, "review", 2))
        self.rejects(lambda v: contracts.make_findings(*v), ("lint", 1, [item]), "source")
        self.rejects(lambda v: contracts.make_findings(*v), ("review", 0, [item]), "attempt")
        self.rejects(lambda v: contracts.make_findings(*v), ("review", 1, []), "items")
        self.rejects(
            lambda v: contracts.make_findings(*v), ("review", 1, [dict(item, line=0)]), "line"
        )
        self.rejects(lambda v: contracts.make_findings(*v), ("review", 1, ["text"]), "items")

    def test_load_names_a_missing_unparseable_or_oversized_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "findings.json"
            load = lambda p: contracts.load(p, contracts.validate_findings)  # noqa: E731
            self.rejects(load, path, "findings.json")
            path.write_text("{not json", encoding="utf-8")
            self.rejects(load, path, "findings.json")
            path.write_text("x" * 70000, encoding="utf-8")
            self.rejects(load, path, "64 KiB")


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
    @pytest.mark.spec("The report contract matches Report")
    def test_S1_report_accepts_the_context_object_workers_write(self):
        """orchestrator-lead-alerts/S1"""
        validated = contracts.validate_report(report(context={"status": "unknown"}))
        self.assertEqual(validated["context"], {"status": "unknown"})

    @pytest.mark.spec("The report contract matches Report")
    def test_S2_every_key_the_contract_example_names_is_a_report_field(self):
        """orchestrator-lead-alerts/S2"""
        from agent_orchestrator import driver

        keys = contract_report_keys(driver.CONTRACT)
        self.assertIn("context", keys)
        self.assertEqual([k for k in keys if k not in contracts.Report.model_fields], [])


if __name__ == "__main__":
    unittest.main()


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
        checks, full = run.tier_checks(self.OWNS)
        packet = {"task_id": "t1", "worker": "demo-t1", "checkout": "/w", "owns": self.OWNS}
        packet.update(checks=checks)
        packet.update(**({"full": full} if full else {}))
        ticket = {"#": "T1", "What it delivers": "x"}
        return packet, run.task_text(None, ticket, packet, [], repo / "run" / "t1")

    @pytest.mark.spec("Check tiers brief the worker")
    def test_S2_the_brief_lists_fast_and_the_rendered_affected_and_names_full_as_not_run(self):
        packet, text = self.drive()
        self.assertEqual(packet["checks"], ["uv run ruff check", self.AFFECTED])
        self.assertIn("Before you hand in, run: uv run ruff check; " + self.AFFECTED + ".", text)
        others = [line for line in text.splitlines() if "uv run pytest -n auto" in line]
        self.assertEqual(len(others), 1, text)
        self.assertIn("do not run", others[0])
        self.assertNotIn("full CI gate", text)

    @pytest.mark.spec("Workers run tests in the foreground")
    def test_every_brief_keeps_the_worker_on_targeted_tests_run_in_the_foreground(self):
        """A worker that runs a whole test directory, or backgrounds a run and polls its log, stalls the drive."""
        for gate in ((), ["scripts/checks.sh"]):
            _packet, text = self.drive(gate=gate)
            self.assertIn(CHECK_INSTRUCTIONS, text.splitlines())
        rule = CHECK_INSTRUCTIONS
        self.assertIn("run only the test files you wrote or changed", rule)
        self.assertIn("foreground", rule)
        # The driver briefs workers in any repository: the rule names no runner of its own.
        for runner_word in ("pytest", "node id", "--lf", ".pytest"):
            self.assertNotIn(runner_word, rule)

    @pytest.mark.spec("Check tiers brief the worker")
    def test_S3_a_named_gate_is_the_required_checks_alone_and_the_tiers_are_not_applied(self):
        packet, text = self.drive(gate=["scripts/checks.sh"])
        self.assertEqual(packet["checks"], ["scripts/checks.sh"])
        self.assertIn("Before you hand in, run: scripts/checks.sh.", text)
        self.assertNotIn("uv run ruff check", text)
        self.assertNotIn("uv run pytest -n auto", text)

    @pytest.mark.spec("Check tiers brief the worker")
    def test_S1_overlapping_tiers_require_one_command_and_never_forbid_it(self):
        config = "checks:\n  fast: uv run pytest -q\n  affected: uv run pytest -q\n  full:\n    run: uv run pytest -q\n"
        packet, text = self.drive(config=config)
        self.assertEqual(packet["checks"], ["uv run pytest -q"])
        self.assertNotIn("full", packet)
        self.assertEqual(
            [line for line in text.splitlines() if line.startswith("Before you hand in, run:")],
            ["Before you hand in, run: uv run pytest -q."],
        )
        self.assertNotIn("do not run", text)

    @pytest.mark.spec("Workers are briefed on the tdd skill")
    def test_the_brief_names_the_tdd_skill_and_the_contract_asks_no_test_cost_or_scenario_names(
        self,
    ):
        packet, text = self.drive()
        self.assertIn("Read the `tdd` skill first", text)
        self.assertIn("TDD:", driver.CONTRACT)
        self.assertNotIn("test_cost", driver.CONTRACT)
        for brief in (text, driver.CONTRACT):
            self.assertNotIn("S-ID", brief)

    @pytest.mark.spec("Workers are briefed on the tdd skill")
    def test_S4_the_tdd_path_is_the_environment_then_the_hosts_place(self):
        with mock.patch.dict(os.environ, {driver.TDD_SKILL_ENV: "/tmp/tdd/SKILL.md"}):
            self.assertEqual(driver.tdd_skill_path(), "/tmp/tdd/SKILL.md")

    @pytest.mark.spec("Check tiers brief the worker")
    def test_S1_whitespace_spellings_of_one_command_are_required_once(self):
        config = (
            'checks:\n  fast: uv run pytest -q\n  affected: "uv run  pytest -q"\n'
            '  full:\n    run: "uv run pytest   -q"\n'
        )
        packet, text = self.drive(config=config)
        self.assertEqual(packet["checks"], ["uv run pytest -q"])
        self.assertNotIn("do not run", text)

    @pytest.mark.spec("Check tiers brief the worker")
    def test_S2_a_distinct_full_is_ci_owned_while_fast_and_affected_are_kept(self):
        packet, text = self.drive()
        self.assertEqual(packet["checks"], ["uv run ruff check", self.AFFECTED])
        self.assertEqual(packet["full"], "uv run pytest -n auto")
        (full,) = [line for line in text.splitlines() if "uv run pytest -n auto" in line]
        self.assertIn("do not run", full)
        self.assertIn("CI", full)
