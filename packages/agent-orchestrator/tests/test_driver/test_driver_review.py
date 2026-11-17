"""The reviewer's packet has one home: `driver/review.py` — the brief it reads and the trail behind it.

A review is opened on one attempt's report, and the trail it is given is the record's own: the newest
attempt is the one the record says was made last, not whichever attempt a dictionary happens to carry
last. A ledger whose tasks were written in another order therefore cannot hand a reviewer a stale
report as the change under review.
"""

import unittest
from contextlib import contextmanager
from pathlib import Path

import pytest
from agent_orchestrator import driver as engine

pytestmark = pytest.mark.unit


class Record:
    """The ledger a Driver stub reads: its tasks, in the order the file carries them."""

    def __init__(self, tasks):
        self.data = {"tasks": tasks}

    @contextmanager
    def __call__(self):
        yield self.data


def attempt(task_id, unit, started_at, report):
    """One attempt as the record holds it: the task it was claimed as, when, and the report it wrote."""
    return {
        "packet": {"task_id": task_id, "unit": unit},
        "started_at": started_at,
        "report_path": report,
    }


class TrailOrderTests(unittest.TestCase):
    """S24: the trail's newest attempt is the record's, never a dictionary's order."""

    def test_S24_the_trail_answers_the_newest_attempt_by_the_order_the_record_holds(self):
        driver = engine.Driver.__new__(engine.Driver)
        driver.run_dir = Path("/run")
        # The re-dispatched attempt was made an hour after the one it replaced, and the dictionary
        # carries it first: a reader that took the last value answered with the attempt it replaced.
        driver.__dict__["book"] = Record(
            {
                "t1o1": attempt("t1o1", "t1", "2026-11-16T11:00:00+00:00", "/run/t1o1/report.json"),
                "t1": attempt("t1", "t1", "2026-11-16T10:00:00+00:00", "/run/t1/report.json"),
            }
        )
        _reviewed, _findings, report = driver.unit_trail("t1")
        self.assertEqual(report, "/run/t1o1/report.json", "a stale attempt's report was answered")


if __name__ == "__main__":
    unittest.main()
