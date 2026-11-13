"""`plan amend`: re-approval of an approved plan at its approval's own base, under a lock of its own."""

import fcntl
import io
import json
import os
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration


def contract():
    """Two tickets and two scenarios: T1 satisfies S1 (a check on status.txt), T2 satisfies S2 (a person's)."""
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "outcome": ["S1", "S2"],
        "scenarios": [
            {
                "id": "S1",
                "when": "a",
                "then": "b",
                "evidence": {"check": "grep -q alive status.txt", "baseline": "pass"},
            },
            {"id": "S2", "when": "c", "then": "d", "evidence": {"human": "confirm d"}},
        ],
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


class AmendFixture(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.plan = self.plan.with_suffix(".yaml")
        self.plan.with_name("demo.oracle").mkdir()
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

    def amend(self, reason="the plan moved"):
        return self.command("amend", "--reason", reason)

    def complete(self, *tickets):
        """The ledger marks `tickets` COMPLETE, as a driver does when their pull requests merge."""
        (ledger,) = (self.state_dir).glob("*/demo.json")
        data = json.loads(ledger.read_text())
        data["ticket_status"] = {t: "COMPLETE" for t in tickets}
        ledger.write_text(json.dumps(data))

    def lock_path(self, name):
        common = self.git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return os.path.join(common, "agent-orchestrator", name)

    def hold(self, name, holder):
        path = self.lock_path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stream = open(path, "w", encoding="utf-8")
        self.addCleanup(stream.close)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.write(holder)
        stream.flush()


class AmendRefusalTests(AmendFixture):
    def test_S6_an_unapproved_plan_is_refused_naming_plan_approve_and_nothing_is_written(self):
        before = self.plan.read_bytes()
        code, message = self.amend()
        self.assertEqual(code, 2, message)
        self.assertIn("plan approve", message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())

    def test_S6_a_worker_caller_is_refused_naming_the_lead_and_nothing_is_written(self):
        self.approve()
        data = self.read()
        data["tickets"][1]["delivers"] = "second, changed"
        self.write(dict(data))
        before, snapshots = self.plan.read_bytes(), sorted(os.listdir(self.state_dir / "approvals"))
        with mock.patch.object(
            load_script(), "resolve_runtime", return_value={"role": "worker", "parent": "lead-0"}
        ):
            code, message = self.amend()
        self.assertEqual(code, 2, message)
        self.assertIn("Lead", message)
        self.assertIn("lead-0", message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.state_dir / "approvals")), snapshots)

    def test_S7_an_edit_to_or_a_drop_of_a_complete_ticket_is_refused_naming_it(self):
        self.approve()
        self.complete("T1")
        changed = self.read()
        changed["tickets"][0]["paths"] = ["src/a/", "src/more/"]
        dropped = self.read()
        dropped["tickets"] = dropped["tickets"][1:]
        dropped["scenarios"] = dropped["scenarios"][1:]
        dropped["outcome"] = ["S2"]
        for case, edit in (("changed", changed), ("dropped", dropped)):
            with self.subTest(case):
                self.write(edit)
                before = self.plan.read_bytes()
                code, message = self.amend()
                self.assertEqual(code, 2, message)
                self.assertIn("T1", message)
                self.assertIn("COMPLETE", message)
                self.assertIn("add a new ticket", message)
                self.assertEqual(self.plan.read_bytes(), before)

    def test_S7_a_change_to_a_complete_tickets_scenario_is_refused_but_its_evidence_may_change(
        self,
    ):
        self.approve()
        self.complete("T1")
        edit = self.read()
        edit["scenarios"][0]["then"] = "b, differently"
        self.write(edit)
        code, message = self.amend()
        self.assertEqual(code, 2, message)
        self.assertIn("S1", message)
        self.assertIn("T1", message)
        self.assertIn("add a new ticket", message)
        edit["scenarios"][0]["then"] = "b"
        edit["scenarios"][0]["evidence"]["check"] = "grep -q ali status.txt"
        # a change to a ticket the ledger does not mark COMPLETE rides along
        edit["tickets"][1]["delivers"] = "second, changed"
        self.write(edit)
        code, message = self.amend()
        self.assertEqual(code, 0, message)


class AmendTests(AmendFixture):
    def test_S8_baselines_run_at_the_approvals_base_never_at_origin_main(self):
        first = self.approve()
        self.push(
            "status.txt", "dead\n"
        )  # a ticket merged: S1's `baseline: pass` is red on origin/main now
        edit = self.read()
        edit["tickets"].append(
            {
                "id": "T3",
                "delivers": "third",
                "satisfies": ["S2"],
                "paths": ["src/c/"],
                "size": "small",
                "difficulty": "simple",
            }
        )
        self.write(edit)
        # the same edit approved fresh at origin/main is still refused
        code, message = self.command("approve")
        self.assertEqual(code, 2, message)
        self.assertIn("S1", message)
        self.assertEqual(self.read().get("approval"), first)
        code, message = self.amend()
        self.assertEqual(code, 0, message)
        approval = self.read()["approval"]
        self.assertEqual(approval["base"], first["base"])
        self.assertNotEqual(approval["digest"], first["digest"])
        (entry,) = self.read()["amendments"]
        self.assertEqual(entry["prev_digest"], first["digest"])
        self.assertEqual(entry["digest"], approval["digest"])
        self.assertEqual(self.command("check")[0], 0)

    def test_S10_one_entry_is_appended_and_the_approval_replaced_with_comments_kept(self):
        first = self.approve()
        data = self.read()
        data["tickets"][1]["delivers"] = "second, changed"
        data["tickets"].append(
            {
                "id": "T3",
                "delivers": "third",
                "satisfies": ["S2"],
                "paths": ["src/c/"],
                "size": "small",
                "difficulty": "simple",
            }
        )
        self.write_commented(data)
        code, message = self.amend("reason one")
        self.assertEqual(code, 0, message)
        after = self.read()
        self.assertIn("# retained comment", self.plan.read_text())
        self.assertIn("# why T2 exists", self.plan.read_text())
        (entry,) = after["amendments"]
        self.assertEqual(sorted(entry), ["at", "by", "digest", "prev_digest", "reason", "tickets"])
        self.assertEqual(entry["prev_digest"], first["digest"])
        self.assertEqual(entry["digest"], after["approval"]["digest"])
        self.assertEqual(entry["reason"], "reason one")
        self.assertEqual(entry["tickets"], ["T2", "T3"])
        email = subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email"], capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(entry["by"], email + " (lead-test)")
        self.assertEqual(after["approval"]["by"], email)
        # neither block enters the digest: the amended plan verifies against its snapshot
        self.assertEqual(self.command("check")[0], 0)
        # a second amendment appends, and names the first
        again = self.read()
        again["tickets"][0]["delivers"] = "first, changed"
        self.write_commented(again)
        code, message = self.amend("reason two")
        self.assertEqual(code, 0, message)
        entries = self.read()["amendments"]
        self.assertEqual([e["reason"] for e in entries], ["reason one", "reason two"])
        self.assertEqual(entries[1]["prev_digest"], entries[0]["digest"])
        self.assertEqual(entries[1]["tickets"], ["T1"])
        self.assertIn("# why T2 exists", self.plan.read_text())

    def write_commented(self, data):
        body = "# retained comment\n" + yaml.safe_dump(data, sort_keys=False)
        self.plan.write_text(body.replace("- id: T2", "# why T2 exists\n- id: T2", 1))

    def test_S10_an_amendment_that_changes_nothing_is_refused(self):
        self.approve()
        before = self.plan.read_bytes()
        code, message = self.amend()
        self.assertEqual(code, 2, message)
        self.assertIn("nothing", message)
        self.assertEqual(self.plan.read_bytes(), before)

    def test_S11_a_driver_holding_the_run_lock_does_not_stop_an_amendment(self):
        self.approve()
        self.hold("demo.lock", "pid 4242 since 2026-11-09T10:00:00Z")
        edit = self.read()
        edit["tickets"][1]["delivers"] = "second, changed"
        self.write(edit)
        code, message = self.amend()
        self.assertEqual(code, 0, message)

    def test_S11_a_second_amend_is_refused_naming_the_holder(self):
        self.approve()
        edit = self.read()
        edit["tickets"][1]["delivers"] = "second, changed"
        self.write(edit)
        self.hold("demo.amend.lock", "plan amend, pid 4343 since 2026-11-09T10:00:00Z")
        before = self.plan.read_bytes()
        code, message = self.amend()
        self.assertEqual(code, 2, message)
        self.assertIn("pid 4343", message)
        self.assertEqual(self.plan.read_bytes(), before)

    def test_S11_the_snapshot_is_written_before_the_plan_file_is_replaced(self):
        first = self.approve()
        edit = self.read()
        edit["tickets"][1]["delivers"] = "second, changed"
        self.write(edit)
        seen = []
        real = os.replace

        def replace(source, target, *args, **kwargs):
            if str(target) == str(self.plan):
                seen.append(sorted(os.listdir(self.state_dir / "approvals")))
            return real(source, target, *args, **kwargs)

        with mock.patch("os.replace", replace):
            code, message = self.amend()
        self.assertEqual(code, 0, message)
        (listing,) = seen
        approval = self.read()["approval"]
        self.assertIn(approval["digest"], listing)  # the new snapshot is already there
        self.assertIn(first["digest"], listing)  # and the old one: a reader in between loads either


if __name__ == "__main__":
    unittest.main()
