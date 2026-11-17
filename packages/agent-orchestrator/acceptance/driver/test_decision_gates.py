"""Acceptance for the lead-decision-gates plan (issue #306, claims 1 and 2):

  - a decision answers the request it was asked in, in the request's own contract version (S1); a JSON decision
    that names another version is refused, naming both (S2); a driver that meets a decision it cannot read asks
    the Lead again, naming the reason and the way out (S3);
  - the Lead's `retry` for the archive is refused while the plan is not accepted (S6), and the driver builds no
    archive while the plan is rejected (S7).

It drives `decide` and the driver through the package's own fixtures: a fake runtime, a fake gh and a real git
origin.
"""

import json
import sys
import unittest
from unittest import mock

import pytest
from agent_orchestrator import contracts
from fixtures import load_script, wait_result
from test_archive import ArchiveFixture
from test_wake import WakeFixture

pytestmark = pytest.mark.integration

ARCHIVE = load_script().ARCHIVE  # the unit (and task id) of the driver's own archive

# The request a decision answers: the driver marks it resolved and points it at the decision file, as `decide` does.
RESOLVE = (
    "import json,sys;p=sys.argv[1];r=json.load(open(p));"
    "r.update(status='resolved',decision=sys.argv[2]);json.dump(r,open(p,'w'))"
)

# The newest request of a unit, answered with a decision: the run ends instead of waiting on the Lead.
ANSWER_NEWEST = r"""
import glob, json, os, re, sys
directory = os.path.dirname(sys.argv[1])
action, reason = sys.argv[2], sys.argv[3]
found = glob.glob(os.path.join(directory, "request*.json"))
def index(path):
    numbered = re.search(r"request\.(\d+)\.json$", path)
    return int(numbered.group(1)) if numbered else 1
newest = max(found, key=index)
number = index(newest)
decision = os.path.join(directory, "decision.json" if number == 1 else "decision.%d.json" % number)
with open(decision, "w") as stream:
    json.dump({"contract": 3, "action": action, "reason": reason}, stream)
document = json.load(open(newest))
document.update(status="resolved", decision=decision)
with open(newest, "w") as stream:
    json.dump(document, stream)
"""


def failing_once():
    """The archive's gate fails once, so the archive asks the Lead with `retry` and `skip`. The archive's own gate
    passes (its pull request's CI is its gate); a failure stands in for anything that stops it."""
    real, calls = load_script().Driver.gate, []

    def gate(driver, task_id, packet, report):
        if packet["unit"] != ARCHIVE:
            return real(driver, task_id, packet, report)
        calls.append(task_id)
        if len(calls) == 1:
            return "FAIL", [
                {
                    "id": "E1",
                    "kind": "blocking",
                    "source": "verify",
                    "check": "archive",
                    "command": "x",
                    "exit": 1,
                    "excerpt": "S1 boom",
                    "log_path": "/nowhere",
                }
            ]
        return real(driver, task_id, packet, report)

    return mock.patch.object(load_script().Driver, "gate", gate)


class DecisionContract(WakeFixture, unittest.TestCase):
    """The decision answers the request it was asked in, in that request's own contract version."""

    def open_request(self, version=3):
        """An open request on disk, in the contract version asked for."""
        path = self.write_open_request()
        if version != 3:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["contract"] = version
            data["menu"] = ["retry", "skip"]
            path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def decide(self, *args):
        return self.cli("decide", str(self.plan), "t1", *args, "--state-dir", str(self.state_dir))

    def decide_json(self, decision):
        path = self.root / "decision-in.json"
        path.write_text(json.dumps(decision), encoding="utf-8")
        return self.cli(
            "decide", str(self.plan), "t1", "--json", str(path), "--state-dir", str(self.state_dir)
        )

    def decision(self):
        return json.loads((self.run_dir / "t1" / "decision.json").read_text(encoding="utf-8"))

    def the_request(self, unit="t1", nth=1):
        name = "request.json" if nth == 1 else "request.%d.json" % nth
        return json.loads((self.run_dir / unit / name).read_text(encoding="utf-8"))

    def test_control_a_contract_3_request_is_answered_with_a_contract_3_decision(self):
        self.open_request()
        result = self.decide("skip", "--reason", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decision()["contract"], 3)

    def test_S1_a_request_of_an_older_contract_is_answered_with_that_contract(self):
        """Both forms write it: the flag form and the JSON form answer in the request's version."""
        for form in ("flags", "json"):
            with self.subTest(form=form):
                self.open_request(version=2)
                if form == "flags":
                    result = self.decide("skip", "--reason", "x")
                else:
                    result = self.decide_json({"action": "skip", "reason": "x"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.decision()["contract"], 2)
                (self.run_dir / "t1" / "decision.json").unlink()

    def test_S2_a_json_decision_naming_another_contract_is_refused(self):
        self.open_request(version=2)
        result = self.decide_json({"contract": 3, "action": "skip", "reason": "x"})
        self.assertEqual(result.returncode, 2, result.stderr)
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, result.stderr)
        refusal = json.loads(lines[0])
        self.assertRegex(refusal["reason"], r"\b2\b")
        self.assertRegex(refusal["reason"], r"\b3\b")
        self.assertTrue(refusal["way_out"])
        self.assertFalse((self.run_dir / "t1" / "decision.json").exists())
        self.assertEqual(self.the_request()["status"], "open")

    def test_S3_a_driver_that_meets_a_decision_it_cannot_read_asks_the_lead_naming_the_way_out(
        self,
    ):
        """A decision in a contract version the driver does not read: the Lead is asked again, with the way out."""
        bad = self.run_dir / "t1" / "decision.json"
        resolve = wait_result(
            write=[[str(bad), json.dumps({"contract": 4, "action": "skip", "reason": "x"})]]
        )
        resolve["run"] = [
            [sys.executable, "-c", RESOLVE, str(self.run_dir / "t1" / "request.json"), str(bad)]
        ]
        self.waits([self.blocked(), resolve, self.decide_step("t1", "skip")])
        self.run_driver()
        self.assertEqual(len(self.wake_lines("decision")), 2, self.wake_lines("decision"))
        asked = self.the_request("t1", 2)
        self.assertIn("contract", asked["reason"])
        self.assertIn("restart", asked["reason"])


class ArchiveRetryGate(ArchiveFixture, unittest.TestCase):
    """The archive waits for an accepted plan: the Lead's retry is refused, and no archive is built."""

    def ledger_path(self):
        self.alias_state(self.plan)
        return self.state_dir / "demo.json"

    def write_ledger(self, state):
        path = self.ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "agent-orchestrator/ledger",
                    "tasks": {},
                    "plan": {"state": state, "history": [{"state": state, "at": "now"}]},
                }
            ),
            encoding="utf-8",
        )

    def open_archive_request(self):
        path = self.run_dir / ARCHIVE / "request.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                contracts.make_request(
                    ARCHIVE,
                    ARCHIVE,
                    "archive",
                    "the archive could not be built",
                    [str(self.run_dir / ARCHIVE / "task.md")],
                    menu=("retry", "skip"),
                )
            ),
            encoding="utf-8",
        )
        return path

    def decide_archive(self, action="retry"):
        return self.cli(
            "decide",
            str(self.plan),
            ARCHIVE,
            action,
            "--reason",
            "again",
            "--state-dir",
            str(self.state_dir),
        )

    def test_control_an_accepted_plan_answers_the_archive_retry(self):
        self.open_archive_request()
        self.write_ledger("ACCEPTED")
        result = self.decide_archive()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.run_dir / ARCHIVE / "decision.json").exists())

    def test_S6_the_leads_retry_is_refused_while_the_plan_is_rejected(self):
        request = self.open_archive_request()
        self.write_ledger("REJECTED")
        result = self.decide_archive()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("REJECTED", result.stderr)
        self.assertRegex(result.stderr, r"(?i)\bverify\b|\baccept\b")
        self.assertFalse((self.run_dir / ARCHIVE / "decision.json").exists())
        self.assertEqual(json.loads(request.read_text(encoding="utf-8"))["status"], "open")

    def test_control_the_driver_rebuilds_the_archive_while_the_plan_is_accepted(self):
        """The Lead's retry, answered while the plan is accepted: the archive is built again."""
        self.waits(self.ticket_merged() + [{}, self.decide_step(ARCHIVE, "retry")])
        with failing_once():
            self.run_driver()
        self.assertTrue(
            (self.run_dir / ARCHIVE / "report.2.json").exists(),
            "the driver did not rebuild the archive the Lead asked for",
        )
        states = [row["state"] for row in self.ledger()["plan"]["history"]]
        self.assertEqual(states[-1], "ARCHIVED", states)

    def test_S7_the_driver_builds_no_archive_while_the_plan_is_rejected(self):
        """The Lead's retry is on disk, as an older CLI wrote it, and the plan was rejected before the driver read it."""
        decision = self.run_dir / ARCHIVE / "decision.json"
        reject = wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    "import json,sys;p=sys.argv[1];d=json.load(open(p));"
                    "d['plan']={'state':'REJECTED','history':[{'state':'REJECTED','at':'now'}]};"
                    "json.dump(d,open(p,'w'))",
                    str(self.ledger_path()),
                ]
            ]
        )
        retry = wait_result(
            write=[
                [str(decision), json.dumps({"contract": 3, "action": "retry", "reason": "again"})]
            ]
        )
        retry["run"] = [
            [
                sys.executable,
                "-c",
                RESOLVE,
                str(self.run_dir / ARCHIVE / "request.json"),
                str(decision),
            ]
        ]
        skip = wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    ANSWER_NEWEST,
                    str(self.run_dir / ARCHIVE / "request.json"),
                    "skip",
                    "this run ends here",
                ]
            ]
        )
        self.waits(self.ticket_merged() + [{}, reject, retry, skip])
        with failing_once():
            self.run_driver()
        states = [row["state"] for row in self.ledger()["plan"]["history"]]
        self.assertNotEqual(states[-1], "ARCHIVED", states)
        self.assertFalse(
            (self.run_dir / ARCHIVE / "report.2.json").exists(),
            "the driver built the archive again while the plan was rejected",
        )
        self.assertEqual(len(self.gh_calls("pr", "create")), 1, self.gh_calls("pr", "create"))
        asked = json.loads((self.run_dir / ARCHIVE / "request.2.json").read_text(encoding="utf-8"))
        self.assertIn("REJECTED", asked["reason"])
        self.assertRegex(asked["reason"], r"(?i)\bverify\b|\baccept\b")
        self.assertIn("archive", self.wake_lines("decision")[-1])


if __name__ == "__main__":
    unittest.main()
