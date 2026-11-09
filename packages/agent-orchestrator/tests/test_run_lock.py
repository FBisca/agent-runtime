"""One driver per plan and repository: a second `run` of the same plan is refused while the first holds it."""

import fcntl
import os
import unittest

import pytest
from fixtures import RunFixture

pytestmark = pytest.mark.integration


class RunLockTests(RunFixture, unittest.TestCase):
    def lock_path(self, slug="demo"):
        common = self.git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return os.path.join(common, "agent-orchestrator", slug + ".lock")

    def hold(self, path, holder):
        """The lock a live driver holds, taken from outside the run under test."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        stream = open(path, "w", encoding="utf-8")
        self.addCleanup(stream.close)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stream.write(holder)
        stream.flush()

    def test_S1_a_second_driver_of_the_same_plan_is_refused(self):
        """orchestrator-engine/R17: orchestrator-run-lock/S1"""
        self.hold(self.lock_path(), "pid 4242 since 2026-11-09T10:00:00Z")
        result = self.run_driver(expect=2)
        self.assertIn("'demo' is already being driven by pid 4242", result.stderr)
        self.assertIn("await", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S1_the_lock_holds_whatever_the_state_dir(self):
        """orchestrator-engine/R17: orchestrator-run-lock/S1 — a different --state-dir is the same repository."""
        self.hold(self.lock_path(), "pid 4242 since 2026-11-09T10:00:00Z")
        self.state_dir = self.root / "other-state"
        self.run_driver(expect=2)
        self.assertEqual(self.calls("spawn"), [])

    def test_S2_another_plan_in_the_same_repository_runs(self):
        """orchestrator-engine/R17: orchestrator-run-lock/S2"""
        self.hold(self.lock_path("another-plan"), "pid 4242 since 2026-11-09T10:00:00Z")
        self.run_driver()
        self.assertTrue(self.calls("spawn"))

    def test_S2_a_finished_run_releases_the_lock_and_names_itself(self):
        """orchestrator-engine/R17: orchestrator-run-lock/S2 — the lock dies with the run, never left stale."""
        self.run_driver()
        with open(self.lock_path(), "r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if the run still held it
            self.assertRegex(stream.read(), r"^pid \d+ since \d{4}-")


if __name__ == "__main__":
    unittest.main()
