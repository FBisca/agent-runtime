"""`plan approve` under a live driver: the re-approval of an approved plan at its approval's own base, under a lock of
its own, with every ticket the ledger marks COMPLETE frozen."""

import fcntl
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script

pytestmark = [pytest.mark.integration, pytest.mark.spec("Re-approval under a live driver")]

DRIVER = "orchestrate-demo, pid 4242 since 2026-11-09T10:00:00Z"


def contract():
    """Two tickets and two scenarios: T1 satisfies S1, T2 satisfies S2."""
    return {
        "schema": "plan/2",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "requirements": {
            "added": [
                {
                    "title": "Demo",
                    "shall": "The demo SHALL deliver.",
                    "scenarios": [
                        {"id": "S1", "when": "a", "then": "b"},
                        {"id": "S2", "when": "c", "then": "d"},
                    ],
                }
            ]
        },
        "tickets": [
            {
                "id": "T1",
                "delivers": "first",
                "satisfies": ["S1"],
                "paths": ["src/a/"],
                "size": "small",
                "difficulty": "simple",
            },
            {
                "id": "T2",
                "delivers": "second",
                "satisfies": ["S2"],
                "paths": ["src/b/"],
                "size": "small",
                "difficulty": "simple",
            },
        ],
    }


def scenarios(data):
    return data["requirements"]["added"][0]["scenarios"]


T3 = {
    "id": "T3",
    "delivers": "third",
    "satisfies": ["S2"],
    "paths": ["src/c/"],
    "size": "small",
    "difficulty": "simple",
}


class ReapproveFixture(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.plan = self.plan.with_suffix(".yaml")
        self.push("status.txt", "alive\n")
        self.write(contract())
        self.env_patch = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead-test"}
        )
        self.env_patch.start()
        self.resolve_patch = mock.patch.object(
            load_script(), "resolve_runtime", return_value={"role": "lead"}
        )
        self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def push(self, name, text):
        """Commit `name` and push it to origin/main: a ticket that merged."""
        (self.repo / name).write_text(text)
        self.git(self.repo, "add", name)
        self.git(self.repo, "commit", "-q", "-m", "change " + name)
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")
        self.git(self.repo, "fetch", "-q", "origin")
        return self.git(self.repo, "rev-parse", "origin/main")

    def write(self, data):
        self.plan.write_text("# retained comment\n" + yaml.safe_dump(data, sort_keys=False))

    def read(self):
        return yaml.safe_load(self.plan.read_text())

    def command(self, verb, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    verb,
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                    *extra,
                ]
            )
        return code, out.getvalue() + err.getvalue()

    def approve(self):
        code, message = self.command("approve")
        self.assertEqual(code, 0, message)
        return self.read()["approval"]

    def reapprove(self, reason="the plan moved"):
        """`plan approve --reason` while a driver holds the plan's run lock."""
        if not getattr(self, "driving", False):
            self.hold("demo.lock", DRIVER)
            self.driving = True
        return self.command("approve", "--reason", reason)

    def edit_t2(self):
        data = self.read()
        data["tickets"][1]["delivers"] = "second, changed"
        self.write(data)

    def complete(self, *tickets):
        """The ledger marks `tickets` COMPLETE, as a driver does when their pull requests merge."""
        keyed = self.state_dir / load_script().repo_key(self.repo)
        keyed.mkdir(parents=True, exist_ok=True)
        ledger = keyed / "demo.json"
        data = (
            json.loads(ledger.read_text())
            if ledger.is_file()
            else {"version": 1, "kind": "agent-orchestrator/ledger", "tasks": {}}
        )
        data["ticket_status"] = {t: "COMPLETE" for t in tickets}
        ledger.write_text(json.dumps(data))

    def hold(self, name, holder):
        common = self.git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        path = os.path.join(common, "agent-orchestrator", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stream = open(path, "w", encoding="utf-8")
        self.addCleanup(stream.close)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.write(holder)
        stream.flush()


class ReapproveRefusalTests(ReapproveFixture):
    def test_an_unapproved_plan_under_a_live_driver_is_refused_naming_plan_approve(self):
        before = self.plan.read_bytes()
        code, message = self.reapprove()
        self.assertEqual(code, 2, message)
        self.assertIn("plan approve", message)
        self.assertIn("pid 4242", message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())

    def test_a_worker_caller_is_refused_naming_the_lead_and_nothing_is_written(self):
        self.approve()
        self.edit_t2()
        before, snapshots = self.plan.read_bytes(), sorted(os.listdir(self.state_dir / "approvals"))
        with mock.patch.object(
            load_script(), "resolve_runtime", return_value={"role": "worker", "parent": "lead-0"}
        ):
            code, message = self.reapprove()
        self.assertEqual(code, 2, message)
        self.assertIn("Lead", message)
        self.assertIn("lead-0", message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.state_dir / "approvals")), snapshots)

    def test_an_edit_to_or_a_drop_of_a_complete_ticket_is_refused_naming_it(self):
        self.approve()
        self.complete("T1")
        changed = self.read()
        changed["tickets"][0]["paths"] = ["src/a/", "src/more/"]
        dropped = self.read()
        dropped["tickets"] = dropped["tickets"][1:]
        scenarios(dropped)[:] = scenarios(dropped)[1:]
        for case, edit in (("changed", changed), ("dropped", dropped)):
            with self.subTest(case):
                self.write(edit)
                before = self.plan.read_bytes()
                code, message = self.reapprove()
                self.assertEqual(code, 2, message)
                self.assertIn("T1", message)
                self.assertIn("COMPLETE", message)
                self.assertIn("add a new ticket", message)
                self.assertEqual(self.plan.read_bytes(), before)

    def test_a_change_to_a_complete_tickets_scenario_is_refused(self):
        self.approve()
        self.complete("T1")
        edit = self.read()
        scenarios(edit)[0]["then"] = "b, differently"
        self.write(edit)
        code, message = self.reapprove()
        self.assertEqual(code, 2, message)
        self.assertIn("S1", message)
        self.assertIn("T1", message)
        self.assertIn("add a new ticket", message)
        scenarios(edit)[0]["then"] = "b"
        # a change to a ticket the ledger does not mark COMPLETE rides along
        edit["tickets"][1]["delivers"] = "second, changed"
        self.write(edit)
        code, message = self.reapprove()
        self.assertEqual(code, 0, message)

    def test_a_reapproval_that_changes_nothing_is_refused(self):
        self.approve()
        before = self.plan.read_bytes()
        code, message = self.reapprove()
        self.assertEqual(code, 2, message)
        self.assertIn("nothing to re-approve", message)
        self.assertEqual(self.plan.read_bytes(), before)

    def test_a_second_concurrent_reapproval_is_refused_naming_the_holder(self):
        self.approve()
        self.edit_t2()
        self.hold("demo.approve.lock", "plan approve, pid 4343 since 2026-11-09T10:00:00Z")
        before = self.plan.read_bytes()
        code, message = self.reapprove()
        self.assertEqual(code, 2, message)
        self.assertIn("pid 4343", message)
        self.assertEqual(self.plan.read_bytes(), before)


class ReapproveTests(ReapproveFixture):
    def test_the_reapproval_keeps_the_approvals_base_never_origin_main(self):
        first = self.approve()
        moved = self.push(
            "status.txt", "dead\n"
        )  # a ticket merged: origin/main moved past the base
        edit = self.read()
        edit["tickets"].append(dict(T3))
        self.write(edit)
        self.assertNotEqual(moved, first["base"])
        code, message = self.reapprove("add T3")
        self.assertEqual(code, 0, message)
        approval = self.read()["approval"]
        self.assertEqual(approval["base"], first["base"])
        self.assertNotEqual(approval["digest"], first["digest"])
        self.assertEqual(approval["reason"], "add T3")
        self.assertIn("# retained comment", self.plan.read_text())
        self.assertEqual(self.command("check")[0], 0)

    def test_the_snapshot_is_written_before_the_plan_file_is_replaced(self):
        first = self.approve()
        self.edit_t2()
        seen = []
        real = os.replace

        def replace(source, target, *args, **kwargs):
            if str(target) == str(self.plan):
                seen.append(sorted(os.listdir(self.state_dir / "approvals")))
            return real(source, target, *args, **kwargs)

        with mock.patch("os.replace", replace):
            code, message = self.reapprove()
        self.assertEqual(code, 0, message)
        (listing,) = seen
        approval = self.read()["approval"]
        self.assertIn(approval["digest"], listing)  # the new snapshot is already there
        self.assertIn(first["digest"], listing)  # and the old one: a reader in between loads either


class LostSpanTests(ReapproveFixture):
    """A re-approval of a plan whose `approval` span was lost while a drive held it: the ledger keeps the approval, so
    the re-approval runs at the approval's own base and publishes the span back."""

    def drive_held(self, approval):
        """The ledger of a drive that held the plan: the approval it gated on, as the driver records it."""
        keyed = self.state_dir / load_script().repo_key(self.repo)
        keyed.mkdir(parents=True, exist_ok=True)
        (keyed / "demo.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "kind": "agent-orchestrator/ledger",
                    "tasks": {},
                    "approval": {"base": approval["base"], "digest": approval["digest"]},
                }
            ),
            encoding="utf-8",
        )

    def lost(self, change=None):
        """`change(data)` applied and the `approval` span dropped, as a hand edit that lost the block leaves it."""
        data = self.read()
        data.pop("approval", None)
        if change:
            change(data)
        self.write(data)

    def widen_t2(self, data):
        data["tickets"][1]["delivers"] = "second, changed"

    def test_a_reapproval_succeeds_on_a_plan_whose_approval_span_was_lost(self):
        first = self.approve()
        self.drive_held(first)
        self.lost(self.widen_t2)
        code, message = self.reapprove()
        self.assertEqual(code, 0, message)
        after = self.read()
        self.assertEqual(after["approval"]["base"], first["base"], "re-approved at another base")
        self.assertNotEqual(after["approval"]["digest"], first["digest"])
        self.assertEqual(self.command("check")[0], 0)

    def test_the_reapproval_publishes_the_span_back_when_the_span_is_all_that_was_lost(self):
        first = self.approve()
        self.drive_held(first)
        self.lost()
        code, message = self.reapprove("the approval span was lost")
        self.assertEqual(code, 0, message)
        after = self.read()
        self.assertEqual(after["approval"]["base"], first["base"])
        self.assertEqual(after["approval"]["digest"], first["digest"], "the contract changed")
        self.assertEqual(self.command("check")[0], 0)

    def test_control_a_lost_span_no_ledger_holds_is_refused_naming_plan_approve(self):
        self.approve()
        self.lost(self.widen_t2)
        before = self.plan.read_bytes()
        code, message = self.reapprove()
        self.assertEqual(code, 2, message)
        self.assertIn("plan approve", message)
        self.assertEqual(self.plan.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
