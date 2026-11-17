"""The Lead's channel as contract 3: a typed menu, `decide --json` for an agent Lead, and a re-approval adopted under
a live drive that leaves an open blocker open."""

import io
import json
import unittest
from unittest import mock

import pytest
from agent_orchestrator import contracts
from fixtures import wait_result
from test_wake import WakeFixture

pytestmark = pytest.mark.integration

MENU = ["retry", "retry one class up", "fresh worker", "mark blocked", "skip", "answer"]


def refusal_of(result):
    """The one JSON object a refused `decide --json` leaves on stderr."""
    lines = [line for line in result.stderr.splitlines() if line.strip()]
    assert len(lines) == 1, result.stderr
    return json.loads(lines[0])


class DecideJson(WakeFixture, unittest.TestCase):
    def decide_json(self, decision, unit="t1", source="-"):
        """`decide <plan> <unit> --json <source>`: `-` reads the decision from stdin, else it is written to a file."""
        text = decision if isinstance(decision, str) else json.dumps(decision)
        where = source
        if source != "-":
            where = str(self.root / "decision-in.json")
            (self.root / "decision-in.json").write_text(text, encoding="utf-8")
        with mock.patch("sys.stdin", io.StringIO(text)):
            return self.cli(
                "decide", str(self.plan), unit, "--json", where, "--state-dir", str(self.state_dir)
            )

    def decide_flags(self, *args):
        return self.cli("decide", str(self.plan), "t1", *args, "--state-dir", str(self.state_dir))

    def decision(self):
        return json.loads((self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8"))

    def untouched(self, request_path):
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(json.loads(request_path.read_text(encoding="utf-8"))["status"], "open")

    def test_S16_a_blocked_worker_writes_a_contract_3_request_with_a_typed_menu(self):
        self.waits([self.blocked(), self.decide_step("t1", "skip")])
        self.run_driver()
        request = self.request()
        self.assertEqual(request["contract"], 3)
        menu = {option["action"]: option for option in request["menu"]}
        self.assertEqual(list(menu), MENU)
        for option in menu.values():
            self.assertTrue(option["summary"])
            self.assertEqual(option["inputs"]["reason"], {"type": "string", "required": True})
        self.assertEqual(menu["answer"]["inputs"]["answer"], {"type": "string", "required": True})
        self.assertEqual(contracts.validate_request(request), request)

    def test_S16_a_narrowed_menu_is_typed(self):
        # a failed intent offers `retry` and `skip` only: the typed form narrows the same way
        request = contracts.make_request("t1", "t1", "intent", "x", [], menu=("retry", "skip"))
        self.assertEqual([o["action"] for o in request["menu"]], ["retry", "skip"])

    @pytest.mark.spec("Lead decisions")
    def test_S16_a_contract_2_request_with_a_string_menu_is_still_decided(self):
        path = self.write_open_request()
        old = dict(json.loads(path.read_text(encoding="utf-8")), contract=2, menu=MENU)
        path.write_text(json.dumps(old), encoding="utf-8")
        result = self.decide_flags("answer", "--reason", "scope", "--answer", "use the API")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["answer"], "use the API")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "resolved")

    def test_S16_a_decision_is_contract_3(self):
        self.write_open_request()
        self.assertEqual(self.decide_flags("skip", "--reason", "x").returncode, 0)
        self.assertEqual(self.decision()["contract"], 3)

    @pytest.mark.spec("Decisions match the request's contract")
    def test_S1_a_decision_is_written_in_the_contract_version_of_the_request_it_answers(self):
        """The decision answers the request it was asked in: a contract-2 request gets a contract-2 decision, so the
        driver that wrote the request reads it."""
        path = self.write_open_request()
        old = dict(json.loads(path.read_text(encoding="utf-8")), contract=2, menu=MENU)
        path.write_text(json.dumps(old), encoding="utf-8")
        result = self.decide_flags("skip", "--reason", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["contract"], 2)

    def test_S17_the_decision_names_an_action_and_answers_with_one_json_object(self):
        self.write_open_request()
        result = self.decide_json({"action": "retry", "reason": "T1 needs another go"})
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(answer["action"], "retry")
        self.assertEqual(answer["unit"], "t1")

    def test_S17_a_json_file_is_read_like_stdin(self):
        self.write_open_request()
        result = self.decide_json({"action": "skip", "reason": "x"}, source="file")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["action"], "skip")

    @pytest.mark.spec("Lead decisions")
    def test_S17_a_json_decision_is_written_as_the_flag_form_writes_it(self):
        self.write_open_request()
        self.assertEqual(
            self.decide_flags("answer", "--reason", "scope", "--answer", "use the API").returncode,
            0,
        )
        by_flags = self.decision()
        (self.run_dir / "t1" / "decision.json").unlink()
        self.write_open_request()
        result = self.decide_json({"action": "answer", "reason": "scope", "answer": "use the API"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision(), by_flags)

    @pytest.mark.spec("Lead decisions")
    def test_S17_a_refused_json_decision_is_one_json_error_and_writes_nothing(self):
        path = self.write_open_request()
        for bad in (
            {"action": "launch the rockets", "reason": "no"},
            {"action": "amend", "reason": "the plan's own verb, never a decision"},
            {"action": "retry"},
            {"action": "retry", "reason": "  "},
            {"action": "answer", "reason": "x"},
            {"action": "skip", "reason": "x", "answer": "y"},
            {"action": "skip", "reason": "x", "colour": "red"},
            {"action": 3, "reason": "x"},
            ["skip"],
            "{not json",
        ):
            with self.subTest(bad=bad):
                result = self.decide_json(bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                refusal = refusal_of(result)
                self.assertEqual(sorted(refusal), ["error", "reason", "way_out"])
                self.assertTrue(refusal["error"] and refusal["reason"] and refusal["way_out"])
                self.assertEqual(result.stdout, "")
                self.untouched(path)

    def test_S17_a_json_decision_off_the_menu_or_without_a_request_is_refused_as_json(self):
        result = self.decide_json({"action": "skip", "reason": "x"})  # no request yet
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("no open request", refusal_of(result)["reason"])
        path = self.write_open_request()
        request = json.loads(path.read_text(encoding="utf-8"))
        request["menu"] = [contracts.menu_option("retry"), contracts.menu_option("skip")]
        path.write_text(json.dumps(request), encoding="utf-8")
        result = self.decide_json({"action": "mark blocked", "reason": "x"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("retry, skip", refusal_of(result)["way_out"])
        self.untouched(path)

    def test_S17_a_missing_json_file_is_refused_as_json(self):
        self.write_open_request()
        result = self.cli(
            "decide",
            str(self.plan),
            "t1",
            "--json",
            str(self.root / "nowhere.json"),
            "--state-dir",
            str(self.state_dir),
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("nowhere.json", refusal_of(result)["reason"])

    def test_S17_the_json_form_and_the_flag_form_exclude_each_other(self):
        path = self.write_open_request()
        both = self.cli(
            "decide",
            str(self.plan),
            "t1",
            "skip",
            "--json",
            "-",
            "--state-dir",
            str(self.state_dir),
        )
        neither = self.decide_flags()
        for result in (both, neither):
            self.assertEqual(result.returncode, 2, result.stderr)
        self.untouched(path)

    def test_S17_the_flag_form_keeps_working_and_its_refusal_names_the_way_out(self):
        path = self.write_open_request()
        refused = self.decide_flags("launch the rockets", "--reason", "x")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("menu", json.loads(refused.stderr)["error"])
        self.untouched(path)
        result = self.decide_flags("skip", "--reason", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["action"], "skip")


class OnlyParked(WakeFixture, unittest.TestCase):
    def test_control_a_unit_parked_as_blocked_ends_the_run_the_same_way(self):
        self.waits(
            [self.blocked(), self.decide_step("t1", "mark blocked"), wait_result(), wait_result()]
        )
        self.run_driver()
        self.assertEqual(self.task()["gate"], "blocked")
        self.assertEqual([e["kind"] for e in self.run_events("finish", "crash")], ["finish"])
