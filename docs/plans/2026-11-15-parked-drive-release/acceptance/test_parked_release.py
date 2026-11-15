"""Acceptance for the parked-drive plan: a parked unit names the key it is released by, a parked drive says so and
names a way out that works, and a plan that lost its approval span is recovered instead of stranding the drive.

Frozen at approval; placed at packages/agent-orchestrator/acceptance/park/ by the runner; never edited by a ticket.
It drives `run` and `decide` through the gate fixture (fake runtime, fake gh), the seams the engine's own tests use.

The three cases are the plan's outcome, one per class:

  - S1: a unit parked `awaiting-amendment` is released by the amendment the drive adopted after its request was
    raised; the park names the approval the request was raised under, never the one the drive already holds
  - S2: a drive parked on a plan that no longer matches its approval records the park in its ledger, shows it in
    `attention.md` and names a way out that works
  - S4: a plan that lost its `approval:` span but is the plan the drive holds is recovered: the drive keeps running
    and does not drain

Each class has a control case that holds today and must keep holding: a park is released only by an approval the
drive does not hold, a healthy drive records no park, and a plan that was genuinely edited still drains.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest
import yaml

# The evidence runner runs this check in a fresh worktree whose venv holds no workspace member: the package under
# test is importable from its source, so the fixtures can import it.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from fixtures import DRIVER, assignment, load_script, report_wait, resume_id, wait_result  # noqa: E402
from test_gates import GateFixture  # noqa: E402

pytestmark = pytest.mark.integration

REASON = "the ticket must cover the test file"


class ParkFixture(GateFixture):
    """One ticket whose worker is blocked, so the Lead is asked and can decide `amend`."""

    def setUp(self):
        super().setUp()
        self.set_plan(("T1",))
        self.alias_state(self.plan)
        self.approved = self.plan.read_text(encoding="utf-8")
        # The Lead runs `plan amend`: a worker's own session must not turn it into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def edit(self, change):
        """The plan as the Lead's hand edit leaves it: the approval block kept."""
        data = yaml.safe_load(self.approved)
        change(data)
        return yaml.safe_dump(data, sort_keys=False)

    def amend_the_goal(self):
        """An edit that leaves every ticket alone: the drive adopts it without touching the open request."""

        def change(data):
            data["goal"] = data["goal"] + " (amended)"

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

    def amend_argv(self, reason=REASON):
        return [
            *DRIVER,
            "plan",
            "amend",
            str(self.plan),
            "--reason",
            reason,
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
        ]

    def blocked(self):
        """A wait step: the worker is blocked, so the driver asks the Lead."""
        return report_wait("demo-t1", "blocked", "cannot reach the database")

    def park(self):
        """The waits that raise a request, adopt an amendment and park the unit on it.

        The order is the one that strands the unit: the request is raised under the approval before the amendment,
        the amendment is adopted, and the Lead then decides `amend` on the request that is still open.
        """
        self.waits(
            [
                self.blocked(),
                wait_result(
                    self.working("demo-t1"),
                    write=self.writes(self.amend_the_goal()),
                    run=[self.amend_argv()],
                ),
                self.decide_step("t1", "amend", reason=REASON),
                self.timeout("demo-t1"),
            ]
        )

    def resumed_worker(self):
        """A wait step: the worker the release resumed reports again under the same task id."""
        step = report_wait("demo-t1", "complete", id=resume_id("demo-t1"))
        step["write"] = self.worker_done(tests=True)["write"]
        return step

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


class S1ReleaseTests(ParkFixture, unittest.TestCase):
    def test_S1_a_park_names_the_approval_its_request_was_raised_under(self):
        """A park is released by an amendment the drive adopted after the request was raised."""
        self.park()
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.task("t1")["gate"], "awaiting-amendment", "the unit did not park")
        # the next launch releases it: the park names the approval its request was raised under, not the one the
        # drive holds now
        self.waits([self.resumed_worker(), self.reviewer_done(), self.timeout("demo-t1")])
        self.run_driver("--max-workers", "1")
        self.assertIn(
            "amend-released",
            [event["kind"] for event in self.run_events()],
            "the parked unit was never released",
        )


class S1ControlTests(ParkFixture, unittest.TestCase):
    def test_S1_control_a_park_is_released_only_by_an_approval_the_drive_does_not_hold(self):
        """The drive never releases a park with the very approval it holds: that is what stranded the unit."""
        self.park()
        self.run_driver("--max-workers", "1")
        self.assertEqual(self.task("t1")["gate"], "awaiting-amendment")
        key = self.task("t1")["amend_from"]
        held = self.digest_of_the_plan()
        self.waits([self.resumed_worker(), self.reviewer_done(), self.timeout("demo-t1")])
        self.run_driver("--max-workers", "1")
        released = self.task("t1")["gate"] != "awaiting-amendment"
        self.assertEqual(
            released,
            key != held,
            "the drive released a park with the approval it holds (or kept one it does not)",
        )


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
                wait_result(self.working("demo-t1"), write=self.writes(self.amend_the_goal())),
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
