"""A pull request the merge gate saw closed is never the unit's pull request: the next attempt opens its own.

A fix round opens no pull request, so the driver falls back to the one an earlier attempt's report named. When the
Lead closed that pull request to send the work back, falling back to it turned `pr-ready` into a merge request over
a pull request nobody could merge - the Lead had already answered that question.
"""

import contextlib
import unittest

import pytest
from fixtures import load_script

pytestmark = pytest.mark.unit

CLOSED = "https://github.com/o/r/pull/7"
OPEN = "https://github.com/o/r/pull/9"


def driver_with(tasks, current=None):
    """A Driver that knows its ledger's tasks and nothing else: no run, no `gh`."""
    driver = load_script().Driver.__new__(load_script().Driver)

    @contextlib.contextmanager
    def book():
        yield {"tasks": tasks}

    driver.book = book
    driver.task = lambda task_id: dict(tasks[task_id], report_path=current)
    return driver


def attempt(unit, pr=None, closed=None):
    return {"packet": {"unit": unit}, "report": {"pr": pr} if pr else {}, "pr_closed": closed}


class PrUrlUnit(unittest.TestCase):
    def test_a_pull_request_the_gate_saw_closed_is_not_the_units(self):
        driver = driver_with({"t1r1": attempt("t1", CLOSED, closed=CLOSED)})
        with self.assertRaises(ValueError) as error:
            driver.pr_url("t1r1")
        self.assertIn(CLOSED, str(error.exception))
        self.assertIn("open a new one", str(error.exception), "the refusal names the way out")

    def test_an_earlier_attempts_closed_pull_request_is_skipped_for_an_open_one(self):
        driver = driver_with(
            {"t1r1": attempt("t1", CLOSED, closed=CLOSED), "t1r2": attempt("t1", OPEN)}
        )
        self.assertEqual(driver.pr_url("t1r2")[0], OPEN)

    def test_another_units_closed_pull_request_does_not_hide_this_units(self):
        driver = driver_with(
            {"t1r1": attempt("t1", CLOSED, closed=CLOSED), "t2": attempt("t2", OPEN)}
        )
        self.assertEqual(driver.pr_url("t2")[0], OPEN)

    def test_a_report_naming_no_pull_request_at_all_keeps_its_own_refusal(self):
        driver = driver_with({"t1r1": attempt("t1")})
        with self.assertRaises(ValueError) as error:
            driver.pr_url("t1r1")
        self.assertIn("names no pull request URL", str(error.exception))
