"""The driver archives with no agent: the section it writes, the questions it asks, the refusal of an `archive` route."""

import json
import re
import sys
import unittest
from unittest import mock

import pytest
from fixtures import load_script, wait_result
from test_acceptance import SnapshotFixture
from test_archive import ARCHIVE, ArchiveFixture
from test_complete import PersonFixture

pytestmark = pytest.mark.integration


def failing_once():
    """The archive's gate fails once, so the archive asks the Lead with `retry` and `skip`. The archive's own gate
    passes (its pull request's CI is its gate); a failure stands in for anything that stops it."""
    real, calls = load_script().Driver.gate, []

    def gate(driver, task_id, packet, report):
        if packet["unit"] != "archive":
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


@pytest.mark.spec("Archive applies the change set")
class DriverArchive(ArchiveFixture, unittest.TestCase):
    @pytest.mark.spec("Archive applies the change set")
    def test_S1_the_archive_is_committed_pushed_and_opened_by_the_driver_with_no_trailer(self):
        self.waits(self.ticket_merged())
        self.run_driver()
        self.assertNotIn(ARCHIVE, self.spawned())
        (create,) = self.gh_calls("pr", "create")
        self.assertIn("--draft", create)
        self.assertNotIn("Co-Authored-By", " ".join(create))
        message = self.git(self.origin, "log", "-1", "--format=%B", "feat/demo-archive")
        self.assertNotRegex(message, r"(?i)co-authored|generated with")

    @pytest.mark.spec("Archive applies the change set")
    def test_S5_a_failing_archive_gate_asks_the_lead_with_retry_and_skip_and_spawns_no_worker(self):
        self.waits(self.ticket_merged() + [{}, self.decide_step("archive", "skip")])
        with failing_once():
            self.run_driver()
        self.assertNotIn(ARCHIVE, self.spawned())
        request = self.request("archive")
        self.assertEqual([o["action"] for o in request["menu"]], ["retry", "skip"])
        self.assertIn("S1 boom", request["reason"])
        self.assertEqual(self.task("archive")["gate"], "skipped")
        self.assertEqual(self.task("archive")["status"], "RELEASED")

    def test_S5_retry_rebuilds_the_branch_from_the_base_and_the_plan_is_archived(self):
        self.waits(self.ticket_merged() + [{}, self.decide_step("archive", "retry")])
        with failing_once():
            self.run_driver()
        self.assertEqual(len(self.gh_calls("pr", "create")), 1)  # the open pull request is reused
        self.assertEqual([h["state"] for h in self.ledger()["plan"]["history"]][-1], "ARCHIVED")
        self.assertEqual(
            self.git(self.origin, "rev-list", "--count", "main..feat/demo-archive"), "1"
        )


class ArchiveRetryRefusal(ArchiveFixture, unittest.TestCase):
    """The archive waits for an accepted plan: a retry over a plan that is not accepted is refused."""

    def seed_request(self, state):
        """A ledger whose plan is in `state`, and an open request for the archive unit, as the driver leaves them."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "demo.json").write_text(
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
        path = self.run_dir / "archive" / "request.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                load_script().contracts.make_request(
                    "archive",
                    "archive",
                    "archive",
                    "the archive failed its gate",
                    [str(self.run_dir / "archive" / "task.md")],
                    menu=("retry", "skip"),
                )
            ),
            encoding="utf-8",
        )
        return path

    def test_S6_the_lead_s_retry_for_the_archive_is_refused_while_the_plan_is_not_accepted(self):
        request = self.seed_request("RUNNING")
        result = self.cli(
            "decide",
            str(self.plan),
            "archive",
            "retry",
            "--reason",
            "build it again",
            "--state-dir",
            str(self.state_dir),
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("RUNNING", result.stderr)
        self.assertIn("run` again", result.stderr)  # the way out
        self.assertFalse((self.run_dir / "archive" / "decision.json").exists())
        self.assertEqual(json.loads(request.read_text(encoding="utf-8"))["status"], "open")

    def test_S6_a_retry_for_the_archive_over_an_accepted_plan_is_decided(self):
        request = self.seed_request("ACCEPTED")
        result = self.cli(
            "decide",
            str(self.plan),
            "archive",
            "retry",
            "--reason",
            "build it again",
            "--state-dir",
            str(self.state_dir),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(
            (self.run_dir / "archive" / "decision.json").read_text(encoding="utf-8")
        )
        self.assertEqual(decision["action"], "retry")
        self.assertEqual(json.loads(request.read_text(encoding="utf-8"))["status"], "resolved")

    def test_S6_the_json_form_refuses_the_archive_retry_while_the_plan_is_not_accepted(self):
        request = self.seed_request("RUNNING")
        path = self.root / "decision-in.json"
        path.write_text(json.dumps({"action": "retry", "reason": "again"}), encoding="utf-8")
        result = self.cli(
            "decide",
            str(self.plan),
            "archive",
            "--json",
            str(path),
            "--state-dir",
            str(self.state_dir),
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        refusal = json.loads(result.stderr)
        self.assertIn("RUNNING", refusal["reason"])
        self.assertTrue(refusal["way_out"])
        self.assertFalse((self.run_dir / "archive" / "decision.json").exists())
        self.assertEqual(json.loads(request.read_text(encoding="utf-8"))["status"], "open")

    def test_S7_the_driver_builds_no_archive_while_the_plan_is_not_accepted(self):
        """A retry the Lead's own CLI would have refused, written as an older CLI wrote it: the driver asks again."""
        self.waits(
            self.ticket_merged()
            + [{}, self.unaccept_the_plan(), self.write_the_retry(), self.skip()]
        )
        with failing_once():
            self.run_driver()
        states = [row["state"] for row in self.ledger()["plan"]["history"]]
        self.assertNotEqual(states[-1], "ARCHIVED", states)
        self.assertFalse(
            (self.run_dir / "archive" / "report.2.json").exists(),
            "the driver built the archive again while the plan was not accepted",
        )
        self.assertEqual(len(self.gh_calls("pr", "create")), 1, self.gh_calls("pr", "create"))
        asked = json.loads(
            (self.run_dir / "archive" / "request.2.json").read_text(encoding="utf-8")
        )
        self.assertIn("APPROVED", asked["reason"])
        self.assertRegex(asked["reason"], r"(?i)\baccepted\b")
        self.assertIn("archive", self.wake_lines("decision")[-1])

    def unaccept_the_plan(self):
        """A wait step in which the plan's state leaves ACCEPTED for a state the driver does not accept from."""
        return wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    "import json,sys;p=sys.argv[1];d=json.load(open(p));"
                    "d['plan']={'state':'APPROVED','history':[{'state':'APPROVED','at':'now'}]};"
                    "json.dump(d,open(p,'w'))",
                    str(self.state_dir / "demo.json"),
                ]
            ]
        )

    def write_the_retry(self):
        """A wait step in which a `retry` decision is written and the request resolved, as `decide` does."""
        decision = self.run_dir / "archive" / "decision.json"
        step = wait_result(
            write=[
                [str(decision), json.dumps({"contract": 3, "action": "retry", "reason": "again"})]
            ]
        )
        step["run"] = [
            [
                sys.executable,
                "-c",
                "import json,sys;p=sys.argv[1];r=json.load(open(p));"
                "r.update(status='resolved',decision=sys.argv[2]);json.dump(r,open(p,'w'))",
                str(self.run_dir / "archive" / "request.json"),
                str(decision),
            ]
        ]
        return step

    def skip(self):
        """A wait step in which the Lead answers the newest request with `skip`, so the run ends."""
        return wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    "import glob,json,os,re,sys;directory=os.path.dirname(sys.argv[1]);"
                    "found=glob.glob(os.path.join(directory,'request*.json'));"
                    "index=lambda p:int(re.search(r'request\\.(\\d+)\\.json$',p).group(1))"
                    "if re.search(r'request\\.(\\d+)\\.json$',p) else 1;"
                    "newest=max(found,key=index);number=index(newest);"
                    "decision=os.path.join(directory,'decision.json' if number==1 "
                    "else 'decision.%d.json'%number);"
                    "json.dump({'contract':3,'action':'skip','reason':'this run ends here'},"
                    "open(decision,'w'));"
                    "document=json.load(open(newest));document.update(status='resolved',decision=decision);"
                    "json.dump(document,open(newest,'w'))",
                    str(self.run_dir / "archive" / "request.json"),
                ]
            ]
        )


class ArchiveRouteRefusal(ArchiveFixture, unittest.TestCase):
    @pytest.mark.spec("Archive has no agent route")
    def test_S7_a_route_config_naming_the_archive_step_is_refused_before_any_spawn(self):
        path = self.root / "routes.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "routes": [{"class": "small", "backend": "claude", "model": "haiku"}],
                    "steps": [{"step": "archive", "backend": "claude", "model": "haiku"}],
                }
            ),
            encoding="utf-8",
        )
        result = self.run_driver("--routes-file", str(path), expect=2)
        self.assertRegex(result.stderr, r"archive.*no agent.*remove the `archive` route")
        self.assertEqual(self.calls("spawn"), [])

    def test_S7_a_step_override_naming_the_archive_step_is_refused_before_any_spawn(self):
        result = self.run_driver("--step", "archive=claude/haiku", expect=2)
        self.assertRegex(result.stderr, r"archive.*no agent.*remove the `archive` route")
        self.assertEqual(self.calls("spawn"), [])

    def test_S7_the_other_step_routes_still_work(self):
        routes = load_script().routes
        self.assertEqual(routes.step_override("reviewer=claude/haiku").step, "reviewer")
        self.assertIsNone(re.search("archive", ",".join(routes.STEPS)))


class PersonTicketArchive(SnapshotFixture, PersonFixture, unittest.TestCase):
    def first_drive(self):
        self.from_snapshot(self.snapshot_key("first_drive"), super().first_drive)

    def test_control_the_plan_is_accepted_once_the_agent_ticket_is_complete(self):
        self.first_drive()
        self.assertIn("ACCEPTED", self.history())

    @pytest.mark.spec("Plan lifecycle")
    def test_S7_the_archive_neither_starts_nor_asks_for_a_node_while_a_person_ticket_is_open(self):
        self.first_drive()
        self.assertFalse((self.run_dir / "archive" / "request.json").exists())
        self.assertEqual(self.archive_branch(), "")
        self.assertNotIn("ARCHIVED", self.history())
        self.assertNotIn(ARCHIVE, self.ledger()["tasks"])

    def test_S7_the_run_ends_rather_than_wait_on_the_person(self):
        result = self.drive_once(
            self.ticket_merged()
        )  # no step left: a run that waited would fail here
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ACCEPTED", self.history())

    @pytest.mark.spec("Person tickets")
    def test_S7_after_complete_the_next_run_archives_with_the_person_tickets_requirement(self):
        self.first_drive()
        code, said = self.complete("T2")
        self.assertEqual(code, 0, said)
        self.drive_once([])
        self.assertEqual(self.history()[-1], "ARCHIVED")
        spec = self.origin_show("feat/demo-archive", "docs/specs/demo.md").decode()
        self.assertIn("### Requirement: Also S2", spec)
