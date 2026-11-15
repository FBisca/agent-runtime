"""The per-module suite budget: a module that grows past its baseline is a regression, not a surprise."""

import runpy
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "suite_budget.py"
budget = runpy.run_path(str(SCRIPT))


def junit(*rows):
    """A junit document: (classname, seconds) pairs, one testcase each."""
    cases = "".join(
        '<testcase classname="%s.Tests" name="test_%d" time="%s" />' % (name, index, seconds)
        for index, (name, seconds) in enumerate(rows)
    )
    return ET.fromstring("<testsuites><testsuite>%s</testsuite></testsuites>" % cases)


class MeasureTests(unittest.TestCase):
    def test_S1_a_modules_seconds_are_the_sum_of_its_cases(self):
        modules, total = budget["measure"](
            junit(("tests.test_a", 1.25), ("tests.test_a", 0.75), ("packages.p.tests.test_b", 2.0))
        )
        self.assertEqual(modules, {"tests/test_a.py": 2.0, "packages/p/tests/test_b.py": 2.0})
        self.assertEqual(total, 4.0)

    def test_S1_a_plain_function_testcase_names_its_module_not_the_tests_package(self):
        # pytest reports a plain `def test_...` as `tests.test_x`, with no class segment.
        doc = ET.fromstring(
            '<testsuites><testsuite><testcase classname="tests.test_x" name="test_a" '
            'time="1.0" /></testsuite></testsuites>'
        )
        modules, _ = budget["measure"](doc)
        self.assertEqual(modules, {"tests/test_x.py": 1.0})


class BudgetTests(unittest.TestCase):
    BASELINE = {
        "tolerance": 2.0,
        "floor_seconds": 5.0,
        "new_share": 0.01,
        "modules": {"tests/test_a.py": 0.40},
    }

    def test_S1_a_module_may_take_twice_its_share_of_the_suite(self):
        self.assertEqual(
            budget["over_budget"]({"tests/test_a.py": 15.0, "tests/test_b.py": 5.0}, self.BASELINE),
            [],
        )
        self.assertEqual(
            budget["over_budget"]({"tests/test_a.py": 17.0, "tests/test_b.py": 3.0}, self.BASELINE),
            [("tests/test_a.py", 17.0, 16.0)],
        )

    def test_S1_a_new_module_is_free_under_the_floor_and_up_to_one_percent(self):
        baseline = {
            "tolerance": 2.0,
            "floor_seconds": 5.0,
            "new_share": 0.01,
            "modules": {"tests/test_a.py": 0.9},
        }
        self.assertEqual(
            budget["over_budget"]({"tests/test_a.py": 96.0, "tests/test_new.py": 4.0}, baseline), []
        )
        rows = budget["over_budget"]({"tests/test_a.py": 94.0, "tests/test_new.py": 6.0}, baseline)
        self.assertEqual([row[0] for row in rows], ["tests/test_new.py"])

    def test_S1_only_a_grown_module_at_the_hard_size_fails_the_build(self):
        rows = [("tests/test_small.py", 12.0, 5.0), ("tests/test_big.py", 30.0, 10.0)]
        self.assertEqual(budget["failing"](rows), [("tests/test_big.py", 30.0, 10.0)])

    def test_S1_the_regressions_are_named_slowest_first(self):
        baseline = {
            "tolerance": 2.0,
            "floor_seconds": 5.0,
            "modules": {"src/test_a.py": 0.2, "src/test_b.py": 0.2},
        }
        rows = budget["over_budget"]({"src/test_a.py": 29.0, "src/test_b.py": 21.0}, baseline)
        self.assertEqual([row[0] for row in rows], ["src/test_a.py", "src/test_b.py"])
