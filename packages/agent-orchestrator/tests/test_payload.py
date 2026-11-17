"""A report is read after a bounded grace: a report is the news; the file it names is the payload, and the wait for it is bounded.

The wait is asserted here, at its own seam: a fake clock or a real short window, no repository and no worker.
The warning that follows a missing payload is a gate behaviour and lives in test_gates.py.
"""

import tempfile
import unittest
from pathlib import Path

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit


class PayloadWaitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.driver = load_script()

    def clock(self):
        """A monotonic clock and a sleep that move together, so the grace costs no real time."""
        now = [1000.0]

        def sleep(seconds):
            now[0] += seconds

        return now, lambda: now[0], sleep

    @pytest.mark.spec("A report is read after a bounded grace")
    def test_S1_a_file_that_lands_inside_the_grace_is_read(self):
        """orchestrator-payload-race/S1"""
        path = self.dir / "review.json"
        now, clock, sleep = self.clock()

        def lands(seconds):
            sleep(seconds)
            path.write_text("{}", encoding="utf-8")

        self.assertTrue(self.driver.payload_waits(path, grace=3.0, clock=clock, sleep=lands))
        self.assertGreater(now[0] - 1000.0, 0.0)

    @pytest.mark.spec("A report is read after a bounded grace")
    def test_S1_a_file_that_never_lands_ends_the_wait_at_the_deadline(self):
        """orchestrator-payload-race/S1"""
        now, clock, sleep = self.clock()
        self.assertFalse(
            self.driver.payload_waits(self.dir / "absent.json", grace=0.2, clock=clock, sleep=sleep)
        )
        self.assertGreaterEqual(now[0] - 1000.0, 0.2, "the wait is bounded at the deadline")


if __name__ == "__main__":
    unittest.main()
