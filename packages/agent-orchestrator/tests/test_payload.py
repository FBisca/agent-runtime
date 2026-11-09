"""R18: a report is the news; the file it names is the payload, and the wait for it is bounded.

The wait is asserted here, at its own seam: a fake clock or a real short window, no repository and no worker.
The warning that follows a missing payload is a gate behaviour and lives in test_gates.py.
"""

import tempfile
import threading
import time
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

    def test_S1_a_file_that_lands_inside_the_grace_is_read(self):
        """orchestrator-engine/R18: orchestrator-payload-race/S1"""
        path = self.dir / "review.json"
        late = threading.Timer(0.2, lambda: path.write_text("{}", encoding="utf-8"))
        late.start()
        self.addCleanup(late.cancel)
        self.assertTrue(self.driver.payload_waits(path, grace=3.0))

    def test_S1_a_file_that_never_lands_ends_the_wait_at_the_deadline(self):
        """orchestrator-engine/R18: orchestrator-payload-race/S1"""
        started = time.monotonic()
        self.assertFalse(self.driver.payload_waits(self.dir / "absent.json", grace=0.2))
        self.assertLess(time.monotonic() - started, 2.0, "the wait is bounded, not a poll that never ends")


if __name__ == "__main__":
    unittest.main()
