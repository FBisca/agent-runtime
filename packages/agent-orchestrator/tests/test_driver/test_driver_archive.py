"""The plan's end of life has one home: `driver/archive.py`, and an `Archive` the Driver holds.

A run that can go no further parks — the ledger keeps one record, attention.md shows it, the Lead is
woken and asked again — and a plan whose tickets are all done is archived: its oracles placed, its
files copied, its spec section written, its pull request opened and its folder deleted. Reopening a park
the Lead answered and the report a crashed drive wakes the Lead with belong to the same lane, so the
plan's last states are read in one module instead of six thousand lines apart. The park still writes
through the Book, so a ledger the engine wrote before the package was split opens in the engine after
it with no migration.
"""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit

# <repo>/packages/agent-orchestrator/tests/test_driver/<this file>
REPO = Path(__file__).resolve().parents[4]
MEMBER = REPO / "packages" / "agent-orchestrator" / "src" / "agent_orchestrator"
LANE = MEMBER / "driver" / "archive.py"

# The park: what the ledger keeps, what the drain and the Ask read, the exit that names the units and
# the two assignments a parked unit holds in the runtime.
PARK = (
    "held_approval",
    "park_here",
    "end_park",
    "park_due",
    "ask_parked",
    "park_at_exit",
    "parked_assignments",
    "park_for_amendment",
)

# The archive: what a plan's last ticket builds, and what the gate steps, the pull request lane and the
# suite ask of it — the unit it starts, the branch, the spec, the pull request, the evidence re-run.
ARCHIVED = (
    "archive_evidence",
    "start_archive",
    "discard_plan",
    "remove_plan_folder",
    "build_archive",
    "archive_missing",
    "undraft_archive",
    "retry_archive",
    "scenario_targets",
    "yaml_copies",
    "yaml_places",
)

# The names the lane answers for itself: no other module and no test asks the Driver for them, so the
# lane keeps them and the Driver keeps no second copy.
INTERNAL = (
    "park_snapshot",
    "park_record",
    "book_park",
    "parked_units",
    "archive_mismatches",
    "archive_mode",
    "plan_folder_rel",
    "folder_tracked",
    "guard_plan_folder",
    "archive_owns",
    "claim_archive",
    "archive_body",
    "archive_branch",
    "archive_pr",
    "oracle_targets",
    "place_oracles",
    "beside_plan",
)

PUBLISHED = PARK + ARCHIVED + ("reopen_parked",)

# The rules only this lane reads: the two asks of a park, the acceptance the archive's worker is held
# to, and the runtime id of the unit the driver does itself. They keep one home; the package publishes
# that home's object rather than a second copy.
OWNED = ("PARK_ASK_FIRST", "PARK_ASK_EVERY", "YAML_ARCHIVE_ACCEPTANCE", "DRIVER_ID")

# The crash report: the function the run's guard calls and the line it wakes the Lead with.
REPORTS = ("report_crash", "crash_line")

# The ledger one engine wrote, at the gate where it waits for the Lead to amend the plan: the version,
# the kind marker, one attempt, and the keys a run keeps beside its tasks. Literal on purpose — this is
# the file left on disk, and the engine after the split reads what an older one wrote, not what today's
# writer would.
BEFORE_THE_SPLIT = """{
  "version": 1,
  "kind": "agent-orchestrator/ledger",
  "tasks": {
    "t1": {
      "packet": {"task_id": "t1", "worker": "demo-t1", "unit": "t1", "backend": "claude"},
      "status": "ACTIVE",
      "runtime_id": "rt-1",
      "gate": "awaiting-amendment",
      "amend": {"reason": "the release is not this one", "since": "2026-11-15T09:10:00+00:00"},
      "started_at": "2026-11-15T09:00:00+00:00"
    }
  },
  "ticket_status": {"T1": "IN PROGRESS · awaiting amendment: the release is not this one"},
  "rounds": {"t1": 2}
}"""


def driver_over(root, ledger_path):
    """The Driver a run holds over one ledger file: a plan, a run directory and the ledger, nothing else."""
    plan = root / "plan.yaml"
    plan.write_text("schema: plan/1\n", encoding="utf-8")
    run = root / "run"
    run.mkdir()
    driver = load_script().Driver(SimpleNamespace(plan=str(plan), repo=str(root)))
    driver.slug, driver.run_dir, driver.ledger_path = "demo", run, ledger_path
    return driver


class PreSplitLedgerTests(unittest.TestCase):
    """S13: a ledger written before the split is read after it, with no migration."""

    @pytest.mark.integration
    def test_S13_a_ledger_written_before_the_split_still_parks_under_the_archive(self):
        from agent_orchestrator.driver import archive

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "demo.json"
            path.write_text(BEFORE_THE_SPLIT, encoding="utf-8")
            driver = driver_over(root, path)
            self.assertIsInstance(driver.archive, archive.Archive, "the Driver holds the lane")

            # The park the run ends in, booked by the collaborator on the file an older engine wrote, and
            # then taken away when the Lead answers: a read and two writes through the Book, not a
            # migration.
            self.assertEqual(driver.park_at_exit(), ["t1"], "the exit names the unit it parked")
            parked = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(parked["park"]["units"], ["t1"])
            self.assertEqual(parked["park"]["cause"], "awaiting-amendment")
            self.assertEqual((driver.end_park("units") or {}).get("units"), ["t1"])

            after = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(after["version"], 1, "the version the older engine wrote is kept")
        self.assertEqual(
            after["kind"],
            "agent-orchestrator/ledger",
            "the kind marker the older engine wrote is kept",
        )
        self.assertEqual(list(after["tasks"]), ["t1"], "the attempt the older engine wrote is kept")
        self.assertEqual(after["tasks"]["t1"]["gate"], "awaiting-amendment")
        self.assertEqual(after["tasks"]["t1"]["packet"]["worker"], "demo-t1")
        self.assertNotIn("park", after, "the park the Lead answered is over")
        # Nothing a run keeps beside its tasks is dropped on the way through: no migration ran.
        self.assertEqual(
            after["ticket_status"],
            {"T1": "IN PROGRESS · awaiting amendment: the release is not this one"},
        )
        self.assertEqual(after["rounds"], {"t1": 2})


class ArchiveHomeTests(unittest.TestCase):
    """S13: the lane lives in `driver/archive.py`, and the Driver reaches it as one collaborator."""

    def test_S13_the_lane_members_live_in_one_module(self):
        from agent_orchestrator import driver
        from agent_orchestrator.driver import archive

        self.assertEqual(Path(archive.__file__).resolve(), LANE.resolve())
        for name in PUBLISHED:
            with self.subTest(name=name):
                self.assertIn(
                    name, vars(archive.Archive), "%s is not the collaborator's own" % name
                )
                self.assertTrue(
                    callable(getattr(driver.Driver, name)),
                    "a gate step, the suite and the Lead's commands ask `Driver.%s`" % name,
                )
        # A member only the lane calls answers here and nowhere else: a second copy on the Driver
        # would be two answers to one question.
        for name in INTERNAL:
            with self.subTest(name=name):
                self.assertIn("_" + name, vars(archive.Archive))
                self.assertNotIn(name, vars(driver.Driver))
                self.assertNotIn("_" + name, vars(driver.Driver))
        # The crash report is the lane's own function, and the package publishes the one copy.
        for name in REPORTS:
            with self.subTest(name=name):
                self.assertIn(name, vars(archive))
                self.assertIs(getattr(driver, name), getattr(archive, name))
        # A rule only the lane reads has one copy too: left behind in the package it would be the second
        # answer to the same question. The package publishes it, so `driver.<name>` is the lane's own
        # object and never a restatement of it.
        for name in OWNED:
            with self.subTest(name=name):
                self.assertIn(name, vars(archive))
                self.assertIs(getattr(driver, name), getattr(archive, name))

    def test_S13_the_driver_reaches_every_moved_member_through_the_collaborator(self):
        from agent_orchestrator import driver as engine

        driver = engine.Driver.__new__(engine.Driver)
        lane = driver.archive
        self.assertIsInstance(lane, engine.Archive)
        self.assertIs(lane.driver, driver, "a Driver a test stubs without `__init__` still answers")
        self.assertIs(driver.archive, lane, "the lane is built once, not per ask")
        for name in PUBLISHED:
            with self.subTest(name=name):
                body = [
                    line.strip()
                    for line in inspect.getsource(getattr(engine.Driver, name)).splitlines()
                    if line.strip()
                ]
                self.assertTrue(
                    body[-1].startswith("return self.archive.%s(" % name),
                    "%s answers for itself instead of the collaborator" % name,
                )
                self.assertLessEqual(len(body), 5, "%s carries more than the delegation" % name)
        # The delegation is what runs: the park's exit asked once, and the kind handed on as it was given.
        with mock.patch.object(engine.Archive, "park_at_exit", return_value=["t1"]):
            self.assertEqual(driver.park_at_exit(), ["t1"])
        with mock.patch.object(engine.Archive, "end_park", return_value={"kind": "units"}) as end:
            self.assertEqual(driver.end_park("units"), {"kind": "units"})
        end.assert_called_once_with("units")


if __name__ == "__main__":
    unittest.main()
