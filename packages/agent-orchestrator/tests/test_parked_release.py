"""Acceptance for the parked-drive plan: a parked drive says so and names a way out that works, and a plan that lost
its approval span is recovered instead of stranding the drive.

It drives `run` through the gate fixture (fake runtime, fake gh), the seams the engine's own tests use.

  - S2: a drive parked on a plan that no longer matches its approval records the park in its ledger, shows it in
    `attention.md` and names a way out that works
  - S4: a plan that lost its `approval:` span but is the plan the drive holds is recovered: the drive keeps running
    and does not drain

Each class has a `control` test that holds whatever the change: a healthy drive records no park, and a plan that was
genuinely edited still drains.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest
import yaml

# A fresh worktree's venv holds no workspace member: the package under test is importable from its source, so the
# fixtures can import it.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fixtures import assignment, load_script, wait_result  # noqa: E402
from test_gates import GateFixture  # noqa: E402

pytestmark = pytest.mark.integration


class ParkFixture(GateFixture):
    """One ticket, and the Lead's edits to its plan as wait-step writes."""

    def setUp(self):
        super().setUp()
        self.set_plan(("T1",))
        self.alias_state(self.plan)
        self.approved = self.plan.read_text(encoding="utf-8")
        # The Lead's own commands: a worker's own session must not turn them into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def edit(self, change):
        """The plan as the Lead's hand edit leaves it: the approval block kept."""
        data = yaml.safe_load(self.approved)
        change(data)
        return yaml.safe_dump(data, sort_keys=False)

    def edit_the_goal(self):
        """An edit that leaves every ticket alone but changes the contract."""

        def change(data):
            data["goal"] = data["goal"] + " (edited)"

        return self.edit(change)

    def without_approval(self):
        """The plan as a hand edit that dropped the `approval` span: the same contract, no approval."""
        data = yaml.safe_load(self.approved)
        data.pop("approval", None)
        return yaml.safe_dump(data, sort_keys=False)

    def writes(self, text):
        return [[str(self.plan), text]]

    def working(self, *names):
        return [assignment(name) for name in names]

    def timeout(self, *names):
        return wait_result(self.working(*names), reason="timeout")

    def snapshot(self):
        """A wait step that copies the ledger and `attention.md` aside: the state of the drive while it is parked.

        A parked drive keeps waiting, so the state that matters is read while it waits, not after the run ends.
        """
        out = self.root / "parked"
        out.mkdir(exist_ok=True)
        return wait_result(
            self.working("demo-t1"),
            run=[
                [
                    "sh",
                    "-c",
                    "cp '%s' '%s' 2>/dev/null; true"
                    % (self.state_dir / "demo.json", out / "ledger.json"),
                ],
                [
                    "sh",
                    "-c",
                    "cp '%s' '%s' 2>/dev/null; true"
                    % (self.state_dir / "demo" / "attention.md", out / "attention.md"),
                ],
            ],
        )

    def parked_ledger(self):
        return yaml.safe_load((self.root / "parked" / "ledger.json").read_text(encoding="utf-8"))

    def parked_attention(self):
        return (self.root / "parked" / "attention.md").read_text(encoding="utf-8")

    def digest_of_the_plan(self):
        return yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]["digest"]


class S2VisibleTests(ParkFixture, unittest.TestCase):
    def test_S2_a_parked_drive_records_the_park_and_names_a_way_out_that_works(self):
        """A drive parked on a plan it cannot read says so, and names a way out that works."""
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes("tickets: [unclosed\n")),
                self.timeout("demo-t1"),
                self.snapshot(),
                # the plan comes back: the park ends and the run can end
                wait_result(self.working("demo-t1"), write=self.writes(self.approved)),
                self.timeout("demo-t1"),
            ]
        )
        self.run_driver("--max-workers", "1")
        parked = self.parked_ledger().get("park")
        self.assertIsNotNone(
            parked, "the ledger records no park: the drive is parked and nobody can see it"
        )
        self.assertEqual(parked["cause"], "unloadable")
        self.assertIn("invalid YAML", parked["error"])
        self.assertEqual(parked["digest"], self.digest_of_the_plan())
        attention = self.parked_attention()
        self.assertIn("park", attention.lower())
        self.assertIn("plan approve --base", attention)


class S2ControlTests(ParkFixture, unittest.TestCase):
    def test_S2_control_a_healthy_drive_records_no_park(self):
        """A drive whose plan is its approved one records no park."""
        self.waits([self.worker_done(tests=True), self.reviewer_done(), self.timeout("demo-t1")])
        self.run_driver("--max-workers", "1")
        self.assertIsNone(self.ledger().get("park"), "a healthy drive recorded a park")
        self.assertEqual(self.run_events("drain"), [])


class S4RecoveryTests(ParkFixture, unittest.TestCase):
    def test_S4_a_plan_that_lost_its_approval_span_is_recovered(self):
        """A plan that lost its `approval:` span but is the plan the drive holds is recovered, not drained."""
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(self.without_approval())),
                self.timeout("demo-t1"),
                # the plan comes back: at base the drain ends here, and after the fix the drive keeps working
                wait_result(self.working("demo-t1"), write=self.writes(self.approved)),
                self.worker_done(tests=True),
                self.reviewer_done(),
                self.timeout("demo-t1"),
            ]
        )
        self.run_driver("--max-workers", "1")
        self.assertEqual(
            [event for event in self.run_events("drain") if event.get("state") != "ended"],
            [],
            "the drive drained on a plan that lost only its approval span",
        )
        self.assertTrue(self.spawned(), "the drive stopped working")


class S4ControlTests(ParkFixture, unittest.TestCase):
    def test_S4_control_a_genuinely_edited_plan_still_drains(self):
        """A plan whose contract changed is still a drain: the recovery covers the lost span, not the edit."""
        self.waits(
            [
                wait_result(self.working("demo-t1"), write=self.writes(self.edit_the_goal())),
                self.timeout("demo-t1"),
                wait_result(self.working("demo-t1"), write=self.writes(self.approved)),
                self.timeout("demo-t1"),
            ]
        )
        self.run_driver("--max-workers", "1")
        self.assertTrue(
            [event for event in self.run_events("drain") if event.get("state") != "ended"],
            "an edited plan did not drain",
        )
