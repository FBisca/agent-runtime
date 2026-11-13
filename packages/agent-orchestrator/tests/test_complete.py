"""`complete <plan> <T#> --note TEXT [--cover S<n>=<node>]...`: the Lead records a person-owned ticket done, with the
test nodes that prove the scenarios it satisfies. The archive's side of it is in test_archive_driver."""

import json
import subprocess
import sys
import unittest
from unittest import mock

import pytest
import yaml
from agent_orchestrator import runtime_client
from fixtures import load_script, wait_result
from test_archive_modes import FolderArchiveFixture, promote_plan

pytestmark = pytest.mark.integration

NODE = "other-repo/tests/test_manual.py::test_the_runbook_step"
NOTE = "ran the runbook step in other-repo"

# The Lead skips an archive request only once one is open: a `decide` with nothing to answer would fail the step.
SKIP_IF_ASKED = """
import os, subprocess, sys
request, decision = sys.argv[1], os.path.join(os.path.dirname(sys.argv[1]), "decision.json")
if os.path.exists(request) and not os.path.exists(decision):
    subprocess.run(sys.argv[2:], check=False, capture_output=True)
"""


def plan_with_a_person_ticket():
    data = promote_plan()
    data["scenarios"].append(
        dict(id="S2", when="the person runs the runbook step", then="it is recorded")
    )
    data["tickets"].append(
        dict(
            id="T2",
            delivers="the runbook step, run by hand",
            satisfies=["S2"],
            paths=["docs/runbook.md"],
            owner="fbisca",
            repo="../other-repo",
        )
    )
    return data


class PersonFixture(FolderArchiveFixture):
    """A promoting plan with an agent ticket (T1, S1) and a person ticket (T2, S2) in its own folder."""

    def setUp(self):
        super().setUp()
        self.folder_plan(plan_with_a_person_ticket(), tracked=True)

    def as_caller(self, role, *args):
        """A command run by the Lead (`lead`) or by a spawned worker (`worker`), with the fakes answering. Both caller
        checks the engine has say the same: the runtime's verified answer and the local resolve."""
        if role == "worker":
            local = {"role": "worker", "parent": "orchestrate:demo"}
            verified = {
                "role": "worker",
                "parent": "orchestrate:demo",
                "caller": {"kind": "worker"},
            }
        elif role == "unverified":
            local = {"role": "lead"}
            verified = {"role": "lead", "caller": {"kind": "lead", "verified": False}}
        else:
            local = {"role": "lead"}
            verified = {"role": "lead", "caller": {"kind": "lead", "verified": True}}

        def answer(argv, timeout=None):
            return subprocess.CompletedProcess(argv, 0, json.dumps(verified), "")

        with (
            mock.patch.object(load_script(), "resolve_runtime", return_value=local),
            mock.patch.object(runtime_client, "runtime_process", answer),
            mock.patch.dict("os.environ", {"HC_TERMINAL_ID": "test-lead"}),
        ):
            result = self.in_process(load_script(), list(args))
        return result.returncode, result.stdout + result.stderr

    def complete(self, ticket, *covers, note=NOTE, role="lead", plan=None):
        argv = ["complete", str(plan or self.plan), ticket, "--note", note]
        for cover in covers:
            argv += ["--cover", cover]
        argv += ["--repo", str(self.repo), "--state-dir", str(self.state_dir)]
        return self.as_caller(role, *argv)

    def skip_archive_if_asked(self):
        """A wait step in which the Lead skips an open archive request, so a run that asks ends instead of waiting."""
        request = self.run_dir / "archive" / "request.json"
        argv = self.decide_argv("archive", "skip", "this run ends here")
        return wait_result(run=[[sys.executable, "-c", SKIP_IF_ASKED, str(request), *argv]])

    def first_drive(self):
        """T1 is built and merged; a run that would then wait on an archive request ends instead."""
        return self.drive_once(self.ticket_merged() + [self.skip_archive_if_asked()] * 8)

    def drive_once(self, steps):
        self.waits(steps)
        self.alias_state(self.plan)
        return self.in_process(
            load_script(),
            [
                "run",
                str(self.plan),
                "--repo",
                str(self.repo),
                "--state-dir",
                str(self.state_dir),
                "--models-file",
                str(self.models),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
            ],
        )

    def archive_branch(self):
        return self.git(self.origin, "branch", "--list", "feat/demo-archive")


class PersonTicketComplete(PersonFixture, unittest.TestCase):
    def test_control_a_plan_with_a_person_ticket_is_approved(self):
        approval = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]
        self.assertIn("digest", approval)

    def test_S6_complete_records_the_ticket_done_with_its_note_and_its_cover_nodes(self):
        self.first_drive()
        code, said = self.complete("T2", "S2=" + NODE)
        self.assertEqual(code, 0, said)
        status = self.ticket_status("T2")
        self.assertRegex(status, r"^COMPLETE \d{4}-\d{2}-\d{2} · " + NOTE + "$")
        self.assertEqual(self.ledger()["person_covers"], {"T2": {"S2": [NODE]}})

    def test_S6_a_ticket_may_be_completed_with_no_cover(self):
        self.first_drive()
        code, said = self.complete("T2")
        self.assertEqual(code, 0, said)
        self.assertTrue(self.ticket_status("T2").startswith("COMPLETE "))

    def test_S6_a_spawned_worker_is_refused_naming_why(self):
        self.first_drive()
        code, said = self.complete("T2", "S2=" + NODE, role="worker")
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"spawned worker.*Lead")
        self.assertFalse(self.ticket_status("T2").startswith("COMPLETE"))

    def test_S6_a_caller_the_runtime_does_not_verify_as_the_lead_is_refused(self):
        self.first_drive()
        code, said = self.complete("T2", "S2=" + NODE, role="unverified")
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"does not verify the caller as the Lead")
        self.assertFalse(self.ticket_status("T2").startswith("COMPLETE"))

    def test_S6_an_agent_ticket_is_refused_naming_why(self):
        self.first_drive()
        code, said = self.complete("T1")
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"T1.*agent")

    def test_S6_an_unknown_ticket_is_refused_naming_why(self):
        self.first_drive()
        code, said = self.complete("T9")
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"no ticket T9")

    def test_S6_a_cover_for_a_scenario_the_ticket_does_not_satisfy_is_refused(self):
        self.first_drive()
        code, said = self.complete("T2", "S1=" + NODE)
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"S1.*T2 does not satisfy")
        self.assertFalse(self.ticket_status("T2").startswith("COMPLETE"))
        self.assertNotIn("person_covers", self.ledger())

    def test_S6_a_cover_that_is_not_scenario_equals_node_is_refused(self):
        self.first_drive()
        code, said = self.complete("T2", NODE)
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"not S<n>=<node>")

    def test_S6_a_blank_note_is_refused(self):
        self.first_drive()
        code, said = self.complete("T2", note="  ")
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"--note")

    def test_S6_a_plan_with_no_ledger_is_refused_and_none_is_made(self):
        (self.state_dir / "demo.json").unlink()
        code, said = self.complete("T2")
        self.assertNotEqual(code, 0, said)
        self.assertRegex(said, r"no run yet")
        self.assertFalse((self.state_dir / "demo.json").exists())

    def test_S6_a_refusal_records_nothing_and_a_valid_complete_still_works(self):
        self.first_drive()
        for covers, role in ((("S2=" + NODE,), "worker"), (("S1=" + NODE,), "lead")):
            self.assertNotEqual(self.complete("T2", *covers, role=role)[0], 0)
        self.assertFalse(self.ticket_status("T2").startswith("COMPLETE"))
        code, said = self.complete("T2", "S2=" + NODE)
        self.assertEqual(code, 0, said)

    def test_S6_the_covers_are_written_under_the_ledger_lock(self):
        self.first_drive()
        with mock.patch.object(
            load_script().ledgers, "locked", wraps=load_script().ledgers.locked
        ) as locked:
            code, said = self.complete("T2", "S2=" + NODE)
        self.assertEqual(code, 0, said)
        self.assertEqual(locked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
