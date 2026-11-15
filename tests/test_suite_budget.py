"""The suite's cost budget: a test over fifteen seconds, and the whole suite's summed call time.

R208's whole-suite time is a person's check. This is the machine check that keeps it true: CI writes
the junit its run already produces and the gate fails the build naming a test over the ceiling, or the
suite when its summed call time passed the one ceiling the committed budget names.

Two absolute numbers, so a slower runner is not the gate and no per-module row has to be edited by
every rename, split or move. The ceilings only shrink: the number in the committed file is the number
the suite may not pass.
"""

import json
import runpy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "suite_budget.py"
BUDGET = REPO / "acceptance" / "suite" / "suite_budget.json"
budget = runpy.run_path(str(SCRIPT))

# The spec's numbers, written out rather than imported: a ceiling this ticket changed on purpose has to
# fail these cases, and reading the file's own number would make the gate agree with itself.
TEST_CEILING = 15.0
SUITE_CEILING = 2700.0


def junit(*rows):
    """A junit document: (classname, test name, seconds) triples, one testcase each."""
    cases = "".join(
        '<testcase classname="%s" name="%s" time="%s" />' % (classname, name, seconds)
        for classname, name, seconds in rows
    )
    return ET.fromstring("<testsuites><testsuite>%s</testsuite></testsuites>" % cases)


def measured(*rows):
    """{node id: seconds} for (classname, test name, seconds) triples."""
    return {
        budget["case_id"](case): float(case.get("time") or 0)
        for case in junit(*rows).iter("testcase")
    }


class CaseTests(unittest.TestCase):
    def test_S18_a_case_is_named_by_its_module_and_class_so_a_line_points_at_one_test(self):
        cases = budget["cases"](
            junit(
                ("tests.test_x.MyTests", "test_a", 1.5),
                ("tests.test_y", "test_b", 0.5),
                ("packages.p.tests.test_z.Suite", "test_c", 2.0),
            )
        )
        self.assertEqual(
            cases,
            {
                "tests/test_x.py::MyTests::test_a": 1.5,
                "tests/test_y.py::test_b": 0.5,
                "packages/p/tests/test_z.py::Suite::test_c": 2.0,
            },
        )

    def test_S18_the_suites_summed_call_time_is_the_sum_of_its_cases(self):
        self.assertEqual(budget["summed"]({"a": 1.25, "b": 2.75, "c": 0.5}), 4.5)


class CeilingTests(unittest.TestCase):
    def test_S18_a_test_over_the_ceiling_is_an_offender_naming_it_its_seconds_and_the_ceiling(self):
        cases = measured(
            ("tests.test_slow.MyTests", "test_a", 15.24),
            ("tests.test_slow.MyTests", "test_b", 15.0),
            ("tests.test_slow.MyTests", "test_c", 14.99),
        )
        self.assertEqual(
            budget["offender_lines"](cases, TEST_CEILING, SUITE_CEILING),
            ["tests/test_slow.py::MyTests::test_a: 15.2s > ceiling 15.0s"],
        )

    def test_S18_the_suites_summed_time_over_its_ceiling_is_an_offender_naming_it(self):
        # 601 tests of 4.5 s each: no test is over its own ceiling, and the suite is over its own.
        cases = {"tests/test_a.py::MyTests::test_%d" % index: 4.5 for index in range(601)}
        self.assertEqual(
            budget["offender_lines"](cases, TEST_CEILING, SUITE_CEILING),
            ["the whole suite: 2704.5s > ceiling 2700.0s"],
        )
        self.assertEqual(
            budget["offender_lines"](
                {"tests/test_a.py::MyTests::test_a": 3.0}, TEST_CEILING, SUITE_CEILING
            ),
            [],
        )

    def test_S18_the_committed_budget_carries_the_two_ceilings_and_no_per_module_row(self):
        document = json.loads(BUDGET.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(document), ["measured_at", "suite_ceiling_seconds", "test_ceiling_seconds"]
        )
        self.assertEqual(budget["ceilings_of"](document), (TEST_CEILING, SUITE_CEILING))

    def test_S18_a_ceiling_that_is_not_a_number_is_refused_naming_the_budget(self):
        with self.assertRaisesRegex(Exception, "suite ceiling is not a number"):
            budget["ceilings_of"](
                {"test_ceiling_seconds": 15.0, "suite_ceiling_seconds": "an hour"}
            )


class RunTests(unittest.TestCase):
    """S18 across the boundary: the exit code and the lines the program prints."""

    def write(self, directory, rows, suite_ceiling=SUITE_CEILING):
        """(junit path, budget path) for one run: the cases, and a budget holding the two ceilings."""
        junit_path = Path(directory) / "suite.xml"
        junit_path.write_text(ET.tostring(junit(*rows), encoding="unicode"), encoding="utf-8")
        budget_path = Path(directory) / "budget.json"
        budget_path.write_text(
            json.dumps(
                {
                    "measured_at": "2026-11-15T00:00:00+00:00",
                    "test_ceiling_seconds": TEST_CEILING,
                    "suite_ceiling_seconds": suite_ceiling,
                }
            ),
            encoding="utf-8",
        )
        return junit_path, budget_path

    @pytest.mark.integration
    def test_S18_a_run_names_each_offender_and_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            junit_path, budget_path = self.write(
                directory,
                (
                    ("tests.test_slow.MyTests", "test_a", 15.5),
                    ("tests.test_slow.MyTests", "test_b", 0.1),
                ),
                suite_ceiling=15.0,
            )
            result = budget["run"](junit_path, budget_path)
        self.assertEqual(result.code, 1)
        self.assertEqual(
            result.lines,
            [
                "tests/test_slow.py::MyTests::test_a: 15.5s > ceiling 15.0s",
                "the whole suite: 15.6s > ceiling 15.0s",
            ],
        )
        self.assertEqual(
            result.errors,
            [
                "suite budget: 2 tests in 15.6s against ceilings 15.0s per test and 15.0s for the "
                "suite: make the test cheaper, or lower the suite's cost, rather than raise the "
                "ceiling"
            ],
        )

    @pytest.mark.integration
    def test_S18_a_run_under_both_ceilings_exits_zero_and_prints_the_ceilings_it_read(self):
        with tempfile.TemporaryDirectory() as directory:
            junit_path, budget_path = self.write(
                directory, (("tests.test_fast.MyTests", "test_a", 0.4),)
            )
            result = budget["run"](junit_path, budget_path)
        self.assertEqual(result.code, 0)
        self.assertEqual(
            result.lines,
            ["suite budget: ceilings 15.0s per test and 2700.0s for the suite, 1 test in 0.4s"],
        )

    @pytest.mark.integration
    def test_S18_an_unreadable_junit_is_refused_rather_than_passed(self):
        with tempfile.TemporaryDirectory() as directory:
            _, budget_path = self.write(directory, ())
            result = budget["run"](Path(directory) / "missing.xml", budget_path)
        self.assertEqual(result.code, 2)
        self.assertTrue(
            result.errors[0].startswith(
                "suite budget: cannot read %s/missing.xml" % Path(directory)
            ),
            result.errors,
        )
