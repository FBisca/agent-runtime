"""An agent Lead reads a typed menu and answers it with JSON, `amend` among the options: the unit parks until the
amendment lands (plan-amend/S17)."""

import json
import unittest

import pytest
from fixtures import DRIVER, wait_result
from test_wake import WakeFixture

pytestmark = pytest.mark.integration

AMEND = {"action": "amend", "reason": "T1 needs a test path"}


def json_lines(text):
    """Every line of `text` that is one JSON object."""
    found = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


class DecideJson(WakeFixture, unittest.TestCase):
    def decision_file(self, decision):
        path = self.root / "decision-in.json"
        return path, json.dumps(decision)

    def json_step(self, decision):
        """A wait step during which an agent Lead writes its decision as JSON and hands it to `decide --json`."""
        path, text = self.decision_file(decision)
        argv = [
            *DRIVER,
            "decide",
            str(self.plan),
            "t1",
            "--json",
            str(path),
            "--state-dir",
            str(self.state_dir),
        ]
        return wait_result(write=[[str(path), text]], run=[argv])

    def decide_json(self, decision):
        path, text = self.decision_file(decision)
        path.write_text(text, encoding="utf-8")
        return self.cli(
            "decide", str(self.plan), "t1", "--json", str(path), "--state-dir", str(self.state_dir)
        )

    def decide_flags(self, action, reason):
        return self.cli(
            "decide",
            str(self.plan),
            "t1",
            action,
            "--reason",
            reason,
            "--state-dir",
            str(self.state_dir),
        )

    def decision(self):
        return json.loads((self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8"))

    def test_S17_the_menu_is_typed_and_offers_amend_with_its_inputs(self):
        self.waits([self.blocked(), self.json_step(AMEND)])
        self.run_driver()
        request = self.request()
        self.assertEqual(request["contract"], 3)
        menu = {option["action"]: option for option in request["menu"]}
        self.assertIn("amend", menu)
        self.assertIn("answer", menu)
        for option in menu.values():
            self.assertTrue(option["summary"])
            self.assertTrue(option["inputs"]["reason"]["required"])
        self.assertTrue(menu["answer"]["inputs"]["answer"]["required"])

    def test_S17_an_amend_decision_parks_the_unit_until_the_amendment_lands(self):
        self.waits([self.blocked(), self.json_step(AMEND)])
        self.run_driver()
        self.assertEqual(self.decision()["action"], "amend")
        self.assertEqual(self.task()["gate"], "awaiting-amendment")
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual([t for t in self.ledger()["tasks"] if t != "archive"], ["t1"])

    def test_S17_a_json_decision_is_answered_with_json(self):
        self.write_open_request()
        result = self.decide_json(AMEND)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["action"], "amend")

    def test_S17_a_json_decision_is_written_as_the_flag_form_writes_it(self):
        self.write_open_request()
        self.assertEqual(self.decide_flags("skip", "not needed").returncode, 0)
        by_flags = self.decision()
        (self.run_dir / "t1" / "decision.json").unlink()
        self.write_open_request()
        self.assertEqual(self.decide_json({"action": "skip", "reason": "not needed"}).returncode, 0)
        self.assertEqual(self.decision(), by_flags)

    def test_S17_a_refused_json_decision_is_one_json_error_with_its_way_out(self):
        self.write_open_request()
        result = self.decide_json({"action": "launch the rockets", "reason": "no"})
        self.assertEqual(result.returncode, 2)
        (refusal,) = [r for r in json_lines(result.stderr) if "error" in r]
        self.assertTrue(refusal["reason"])
        self.assertTrue(refusal["way_out"])
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(
            json.loads((self.run_dir / "t1" / "request.json").read_text())["status"], "open"
        )

    def test_control_a_flag_decision_still_resolves_the_request(self):
        self.write_open_request()
        result = self.decide_flags("skip", "not needed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["action"], "skip")
