"""Acceptance for the engine-gaps plan: a person-owned ticket is recorded done with `complete`, its `--cover` nodes
reach the archive's Coverage table, and a promoting archive waits until every ticket, person tickets included, is
COMPLETE.

Frozen at approval; placed at packages/agent-orchestrator/acceptance/driver/ by the runner; never edited by a ticket.
It drives `run`, `plan check` and the new `complete` command through the archive fixture of the folder-plan tests
(fake runtime, fake gh, a real git origin). The contract it pins (plan Decisions D4, D5):

  complete <plan> <T#> --note TEXT [--cover S<n>=<node>]... --repo R --state-dir D

  - the ticket's status becomes `COMPLETE <date> · <note>`; a spawned worker, an agent ticket, an unknown ticket and
    a `--cover` for a scenario the ticket does not satisfy are each refused (non-zero exit)
  - the archive's Coverage row of a scenario lists the person ticket's cover nodes as given
  - verify starts once every agent ticket is COMPLETE; the archive starts only once every ticket is

Each class has a control case that passes at base and after.
"""

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
        else:
            local = {"role": "lead"}
            verified = {"role": "lead", "caller": {"kind": "lead", "verified": True}}

        def answer(argv, timeout=None):
            return subprocess.CompletedProcess(argv, 0, json.dumps(verified), "")

        with (
            mock.patch.object(load_script(), "resolve_runtime", return_value=local),
            mock.patch.object(runtime_client, "runtime_process", answer),
        ):
            result = self.in_process(load_script(), list(args))
        return result.returncode, result.stdout + result.stderr

    def complete(self, ticket, *covers, note=NOTE, role="lead"):
        argv = ["complete", str(self.plan), ticket, "--note", note]
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
                "--routes-file",
                str(self.route_file),
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

    def test_complete_records_the_person_ticket_done_with_its_note(self):
        self.first_drive()
        code, said = self.complete("T2", "S2=" + NODE)
        self.assertEqual(code, 0, said)
        status = self.ticket_status("T2")
        self.assertTrue(status.startswith("COMPLETE "), status)
        self.assertIn(NOTE, status)

    def test_complete_refuses_a_worker_an_agent_ticket_an_unknown_ticket_and_a_foreign_scenario(
        self,
    ):
        self.first_drive()
        refusals = {
            "a spawned worker": self.complete("T2", "S2=" + NODE, role="worker"),
            "an agent ticket": self.complete("T1"),
            "an unknown ticket": self.complete("T9"),
            "a scenario T2 does not satisfy": self.complete("T2", "S1=" + NODE),
        }
        for what, (code, said) in refusals.items():
            self.assertNotEqual(code, 0, "complete accepted %s: %s" % (what, said))
        self.assertFalse(
            self.ticket_status("T2").startswith("COMPLETE"), "a refused complete was recorded"
        )
        code, said = self.complete("T2", "S2=" + NODE)
        self.assertEqual(code, 0, "after the refusals, a valid complete failed: %s" % said)


class PersonTicketArchive(PersonFixture, unittest.TestCase):
    def test_control_verify_runs_once_the_agent_ticket_is_complete(self):
        self.first_drive()
        self.assertIn("ACCEPTED", self.history())

    def test_the_archive_waits_for_the_person_ticket_then_carries_its_cover_nodes(self):
        self.first_drive()
        self.assertFalse(
            (self.run_dir / "archive" / "request.json").exists(),
            "the archive started with a person ticket open and asked for its test node",
        )
        self.assertEqual(self.archive_branch(), "", "the archive started with a person ticket open")
        self.assertNotIn("ARCHIVED", self.history())
        code, said = self.complete("T2", "S2=" + NODE)
        self.assertEqual(code, 0, said)
        self.drive_once([self.gaps_done()])
        self.assertEqual(self.history()[-1], "ARCHIVED")
        spec = self.origin_show("feat/demo-archive", "docs/specs/demo.md").decode()
        rows = [line for line in spec.splitlines() if line.startswith("| R") and "| S2 |" in line]
        self.assertEqual(len(rows), 1, spec)
        self.assertIn("`%s`" % NODE, rows[0])


if __name__ == "__main__":
    unittest.main()
