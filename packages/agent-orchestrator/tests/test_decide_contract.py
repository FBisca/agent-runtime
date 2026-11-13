"""The Lead's channel as contract 3: a typed menu with `amend`, `decide --json` for an agent Lead, and the
`awaiting-amendment` park that the driver releases when it adopts the amendment (plan-amend/S15-S18)."""

import io
import json
import unittest
from unittest import mock

import pytest
from agent_orchestrator import contracts
from fixtures import DRIVER, ROWS, assignment, wait_result
from test_drain import DrainFixture
from test_wake import WakeFixture

pytestmark = pytest.mark.integration

OLD_MENU = ["retry", "retry one class up", "fresh worker", "mark blocked", "skip", "answer"]


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
        self.assertEqual(list(menu), [*OLD_MENU, "amend"])
        for option in menu.values():
            self.assertTrue(option["summary"])
            self.assertEqual(option["inputs"]["reason"], {"type": "string", "required": True})
        self.assertEqual(menu["answer"]["inputs"]["answer"], {"type": "string", "required": True})
        self.assertEqual(contracts.validate_request(request), request)

    def test_S16_a_narrowed_menu_is_typed_and_has_no_amend(self):
        # a failed intent offers `retry` and `skip` only: the typed form narrows the same way
        request = contracts.make_request("t1", "t1", "intent", "x", [], menu=("retry", "skip"))
        self.assertEqual([o["action"] for o in request["menu"]], ["retry", "skip"])

    def test_S16_a_contract_2_request_with_a_string_menu_is_still_decided(self):
        path = self.write_open_request()
        old = dict(json.loads(path.read_text(encoding="utf-8")), contract=2, menu=OLD_MENU)
        path.write_text(json.dumps(old), encoding="utf-8")
        result = self.decide_flags("answer", "--reason", "scope", "--answer", "use the API")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["answer"], "use the API")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "resolved")

    def test_S16_a_contract_2_request_does_not_offer_amend(self):
        path = self.write_open_request()
        old = dict(json.loads(path.read_text(encoding="utf-8")), contract=2, menu=OLD_MENU)
        path.write_text(json.dumps(old), encoding="utf-8")
        result = self.decide_flags("amend", "--reason", "x")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.untouched(path)

    def test_S16_a_decision_is_contract_3(self):
        self.write_open_request()
        self.assertEqual(self.decide_flags("skip", "--reason", "x").returncode, 0)
        self.assertEqual(self.decision()["contract"], 3)

    def test_S17_the_decision_names_an_action_and_answers_with_one_json_object(self):
        self.write_open_request()
        result = self.decide_json({"action": "amend", "reason": "T1 needs a test path"})
        self.assertEqual(result.returncode, 0, result.stderr)
        answer = json.loads(result.stdout)
        self.assertEqual(answer["action"], "amend")
        self.assertEqual(answer["unit"], "t1")

    def test_S17_a_json_file_is_read_like_stdin(self):
        self.write_open_request()
        result = self.decide_json({"action": "skip", "reason": "x"}, source="file")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["action"], "skip")

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

    def test_S17_a_refused_json_decision_is_one_json_error_and_writes_nothing(self):
        path = self.write_open_request()
        for bad in (
            {"action": "launch the rockets", "reason": "no"},
            {"action": "amend"},
            {"action": "amend", "reason": "  "},
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
        result = self.decide_json({"action": "amend", "reason": "x"})
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

    def test_S17_an_amend_decision_parks_the_unit_with_no_resume_and_no_new_attempt(self):
        self.waits([self.blocked(), self.decide_step("t1", "amend", "T1 needs a test path")])
        self.run_driver()
        self.assertEqual(self.decision()["action"], "amend")
        self.assertEqual(self.task()["gate"], "awaiting-amendment")
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual([t for t in self.ledger()["tasks"] if t != "archive"], ["t1"])
        self.assertEqual(self.spawned(), ["demo-t1"])


class AwaitingAmendment(DrainFixture, WakeFixture, unittest.TestCase):
    """T1 and T3 run; T1 asks, the Lead answers `amend`, then edits the plan and amends it."""

    def amended_with_t5(self):
        return self.edit(lambda data: data["tickets"].append(dict(id="T5", **ROWS["T5"])))

    def park_t1(self):
        both = [assignment("demo-t1"), assignment("demo-t3")]
        return [
            self.blocked(),
            self.decide_step("t1", "amend", "T1 needs a test path", assignments=both),
        ]

    def idle_t1(self, **extra):
        return wait_result(
            [assignment("demo-t1", "working", "idle"), assignment("demo-t3")],
            reason="timeout",
            **extra,
        )

    def resumes_of(self, name):
        return [r["args"]["task"] for r in self.calls("resume") if r["args"]["name"] == name]

    def test_S15_an_adopted_amendment_resumes_the_parked_worker_whose_ticket_did_not_change(self):
        self.waits(
            [
                *self.park_t1(),
                self.idle_t1(write=self.writes(self.amended_with_t5())),
                self.idle_t1(run=[self.amend_argv()]),
                self.timeout("demo-t1", "demo-t3", "demo-t5"),
                self.timeout("demo-t1", "demo-t3", "demo-t5"),
            ]
        )
        self.run_driver("--idle-minutes", "9999")
        (adopted,) = self.run_events("amended")
        resumed = self.resumes_of("demo-t1")
        self.assertEqual(len(resumed), 1, resumed)
        self.assertIn("amendment was adopted", resumed[0])
        self.assertIn("contract is unchanged", resumed[0])
        self.assertIn(str(self.run_dir / "t1" / "task.md"), resumed[0])
        self.assertFalse((self.run_dir / "t1" / "task.v2.md").exists())
        self.assertEqual(self.spawned().count("demo-t1"), 1, "the same worker, not a new one")
        task = self.task()
        self.assertEqual((task["gate"], task["status"]), (None, "ACTIVE"))
        self.assertEqual([t for t in self.ledger()["tasks"] if t.startswith("t1")], ["t1"])
        self.assertTrue(adopted["digest"])

    def test_S15_the_worker_is_not_resumed_before_the_amendment_is_adopted(self):
        self.waits(
            [
                *self.park_t1(),
                self.idle_t1(write=self.writes(self.amended_with_t5())),
                self.idle_t1(),
                # a drain has no timeout: the edit is undone, so the run can end
                self.idle_t1(write=self.writes(self.approved)),
                self.idle_t1(),
            ]
        )
        self.run_driver("--idle-minutes", "9999")
        self.assertEqual(self.resumes_of("demo-t1"), [])
        self.assertEqual(self.task()["gate"], "awaiting-amendment")
        self.assertEqual(self.run_events("amended"), [])

    def test_S15_a_parked_worker_whose_ticket_changed_gets_its_new_packet_and_is_released(self):
        self.waits(
            [
                *self.park_t1(),
                self.idle_t1(write=self.writes(self.widen_t1_and_add_t5())),
                self.idle_t1(run=[self.amend_argv()]),
                self.timeout("demo-t1", "demo-t3", "demo-t5"),
                self.timeout("demo-t1", "demo-t3", "demo-t5"),
            ]
        )
        self.run_driver("--idle-minutes", "9999")
        resumed = self.resumes_of("demo-t1")
        self.assertEqual(len(resumed), 1, resumed)
        self.assertIn("task.v2.md", resumed[0])
        self.assertIn("tests/test_a.py", (self.run_dir / "t1" / "task.v2.md").read_text("utf-8"))
        self.assertEqual(self.task()["gate"], None)

    def test_S18_the_idle_tick_never_asks_about_a_unit_parked_for_an_amendment(self):
        # T3 shows nothing either, so the tick asks about it: the contrast, and the Lead's skip lets the run end
        self.waits(
            [*self.park_t1(), *[self.idle_t1() for _ in range(4)], self.decide_step("t3", "skip")]
        )
        self.run_driver("--idle-minutes", "0")
        asked = [e["unit"] for e in self.run_events("escalation")]
        self.assertEqual(asked, ["t1", "t3"], "T1 asked once, before it parked")
        self.assertFalse((self.run_dir / "t1" / "request.2.json").exists())
        self.assertEqual([e for e in self.run_events("idle") if e.get("unit") == "t1"], [])
        self.assertEqual(self.task()["gate"], "awaiting-amendment")


class OnlyParked(WakeFixture, unittest.TestCase):
    def test_S18_a_run_whose_only_unit_is_parked_for_an_amendment_ends_without_an_orphan_pass(self):
        # `demo-t1` is not in any wait answer: a task it held would be reconciled as orphaned and re-dispatched
        self.waits([self.blocked(), self.decide_step("t1", "amend"), wait_result(), wait_result()])
        self.run_driver()
        self.assertEqual(self.run_events("orphaned"), [])
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task()["gate"], "awaiting-amendment")
        self.assertEqual([e["kind"] for e in self.run_events("finish", "crash")], ["finish"])
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "ACTIVE")

    def test_control_a_unit_parked_as_blocked_ends_the_run_the_same_way(self):
        self.waits(
            [self.blocked(), self.decide_step("t1", "mark blocked"), wait_result(), wait_result()]
        )
        self.run_driver()
        self.assertEqual(self.task()["gate"], "blocked")
        self.assertEqual([e["kind"] for e in self.run_events("finish", "crash")], ["finish"])


class RestartAfterAmend(DrainFixture, WakeFixture, unittest.TestCase):
    """The run that parked a unit ends (only parked units are left); the Lead amends the plan with no driver running;
    a new `run` of the plan goes on with the unit."""

    rows = ("T1",)

    def park_and_end(self):
        self.waits(
            [self.blocked(), self.decide_step("t1", "amend", "T1 needs a test path"), wait_result()]
        )
        self.run_driver()
        self.assertEqual(self.task()["gate"], "awaiting-amendment")
        self.assertEqual([e["kind"] for e in self.run_events("finish", "crash")], ["finish"])

    def amend_now(self, change):
        self.plan.write_text(self.edit(change), encoding="utf-8")
        amended = self.cli(*self.amend_argv()[len(DRIVER) :])
        self.assertEqual(amended.returncode, 0, amended.stderr)

    def restart(self):
        self.waits([wait_result(), wait_result()])
        self.run_driver()

    def test_S15_a_new_run_resumes_the_worker_parked_by_a_run_that_ended(self):
        self.park_and_end()
        self.amend_now(lambda data: data.update(goal="deliver, with the amended contract"))
        self.restart()
        resumed = [
            r["args"]["task"] for r in self.calls("resume") if r["args"]["name"] == "demo-t1"
        ]
        self.assertEqual(len(resumed), 1, resumed)
        self.assertIn("amendment was adopted", resumed[0])
        self.assertIn("contract is unchanged", resumed[0])
        task = self.task()
        self.assertEqual((task["gate"], task["status"]), (None, "ACTIVE"))
        self.assertEqual(self.spawned().count("demo-t1"), 1, "the same worker, not a new one")
        self.assertEqual(len(self.run_events("amend-released")), 1)

    def test_S15_a_new_run_hands_the_parked_worker_the_packet_its_changed_ticket_needs(self):
        self.park_and_end()
        self.amend_now(lambda data: data["tickets"][0].update(paths=["src/a/", "tests/test_a.py"]))
        self.restart()
        resumed = [
            r["args"]["task"] for r in self.calls("resume") if r["args"]["name"] == "demo-t1"
        ]
        self.assertEqual(len(resumed), 1, resumed)
        self.assertIn("task.v2.md", resumed[0])
        self.assertIn("tests/test_a.py", (self.run_dir / "t1" / "task.v2.md").read_text("utf-8"))
        self.assertIn("tests/test_a.py", self.task()["packet"]["owns"])
        self.assertEqual(self.task()["gate"], None)

    def test_S15_a_new_run_with_no_amendment_leaves_the_unit_parked(self):
        self.park_and_end()
        self.restart()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.task()["gate"], "awaiting-amendment")

    def test_S18_a_unit_parked_for_an_amendment_reopens_when_its_status_is_set_to_todo(self):
        self.park_and_end()
        self.reset_status("IN PROGRESS", "TO-DO")
        self.restart()
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "RELEASED")
        self.assertEqual(self.spawned().count("demo-t1"), 2, "a fresh attempt of the unit")
