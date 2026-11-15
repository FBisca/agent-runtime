"""The structure budget: five ceilings over every Python file, one baseline, a ratchet on growth.

The script is the seam: these cases drive its decisions in process, while the program's own contract
(its exit code and the line it prints) is the acceptance file's. A ceiling here is the spec's number,
written out rather than imported, so a changed ceiling fails these cases.
"""

import runpy
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_impacted.file_map import map_changed_files, parse_file_map

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
budget = runpy.run_path(str(REPO / "scripts" / "structure_budget.py"))

CEILINGS = {
    "module-lines": 1000,
    "class-methods": 25,
    "function-lines": 50,
    "function-branches": 12,
    "parameters": 7,
}

UNDER = dict.fromkeys(CEILINGS, 1)


def measured(**numbers):
    """A measured row: the metrics named, the rest of the file's five under every ceiling."""
    return {**UNDER, **numbers}


def baseline(rows=(), ceilings=None):
    """A baseline document: recorded rows by path, and the ceilings it declares."""
    return {
        "measured_at": "2026-11-15T00:00:00+00:00",
        "ceilings": ceilings or CEILINGS,
        "files": dict(rows),
    }


class BaselineTests(unittest.TestCase):
    """S1, S2, S3, S4, S5: what the committed baseline says and what a run answers."""

    def test_S1_a_tree_at_its_baseline_passes_and_the_line_names_the_ceilings_it_read(self):
        """structure-budget/S1"""
        tree = {"pkg/mod.py": measured(**{"module-lines": 120})}
        recorded = baseline({"pkg/mod.py": {"module-lines": 120}})
        self.assertEqual(budget["growth_lines"](tree, recorded), [])
        line = budget["summary"](tree, recorded)
        for metric, ceiling in CEILINGS.items():
            self.assertIn(metric, line)
            self.assertIn(str(ceiling), line)
        self.assertIn("recorded", line)

    def test_S2_a_file_that_grows_past_its_row_is_named_alone(self):
        """structure-budget/S2"""
        tree = {
            "pkg/mod.py": measured(**{"module-lines": 120}),
            "pkg/fine.py": measured(**{"module-lines": 120}),
        }
        recorded = baseline(
            {"pkg/mod.py": {"module-lines": 37}, "pkg/fine.py": {"module-lines": 120}}
        )
        self.assertEqual(
            budget["growth_lines"](tree, recorded),
            ["pkg/mod.py: module-lines 120 > recorded 37 (ceiling 1000)"],
        )
        self.assertNotIn("fine.py", " ".join(budget["growth_lines"](tree, recorded)))

    def test_S3_a_file_with_no_row_over_a_ceiling_is_named_with_the_ceiling(self):
        """structure-budget/S3"""
        tree = {"pkg/wide.py": measured(**{"class-methods": 26})}
        self.assertEqual(
            budget["growth_lines"](tree, baseline()),
            ["pkg/wide.py: class-methods 26 > ceiling 25"],
        )

    def test_S4_write_records_today_and_the_recorded_number_holds(self):
        """structure-budget/S4"""
        tree = {"pkg/wide.py": measured(**{"class-methods": 26})}
        document = budget["baseline_document"](tree)
        stamp = datetime.fromisoformat(document["measured_at"])
        age = abs((datetime.now(timezone.utc) - stamp).total_seconds())
        self.assertLess(age, 120, "the baseline records the day it was measured")
        self.assertEqual(document["files"], {"pkg/wide.py": {"class-methods": 26}})
        self.assertEqual(budget["growth_lines"](tree, document), [])

    def test_S5_the_baseline_names_the_ceilings_and_rows_only_the_oversized_files(self):
        """structure-budget/S5"""
        tree = {"pkg/wide.py": measured(**{"class-methods": 26}), "pkg/small.py": measured()}
        document = budget["baseline_document"](tree)
        self.assertEqual(document["ceilings"], CEILINGS)
        self.assertEqual(sorted(document["files"]), ["pkg/wide.py"])
        self.assertEqual(
            budget["growth_lines"]({"pkg/small.py": measured()}, baseline()),
            [],
            "a file under every ceiling needs no row",
        )


class PaidTests(unittest.TestCase):
    """S6: a recorded row a paid metric declares is a debt, even under its ceiling."""

    ROWS = {"pkg/mod.py": {"module-lines": 120, "class-methods": 1}}
    TREE = {"pkg/mod.py": measured(**{"module-lines": 120, "class-methods": 1})}

    def test_S6_a_paid_metric_fails_under_its_ceiling_and_a_metric_not_paid_is_left_alone(self):
        """structure-budget/S6"""
        recorded = baseline(self.ROWS)
        self.assertEqual(budget["growth_lines"](self.TREE, recorded), [])
        self.assertEqual(
            budget["paid_lines"](self.TREE, recorded, "pkg/", ["module-lines"]),
            ["pkg/mod.py: module-lines paid (recorded 120)"],
        )
        self.assertEqual(budget["paid_lines"](self.TREE, recorded, "pkg/", ["function-lines"]), [])

    def test_S6_metric_may_be_repeated_and_every_named_metric_is_paid(self):
        """structure-budget/S6"""
        paid = budget["paid_lines"](
            self.TREE, baseline(self.ROWS), "pkg/", ["module-lines", "class-methods"]
        )
        self.assertEqual(
            paid,
            [
                "pkg/mod.py: module-lines paid (recorded 120)",
                "pkg/mod.py: class-methods paid (recorded 1)",
            ],
        )

    def test_S6_a_paid_path_that_measures_no_file_is_refused(self):
        """structure-budget/S6"""
        with self.assertRaises(budget["Refused"]) as caught:
            budget["paid_lines"](self.TREE, baseline(self.ROWS), "pkq/", ["module-lines"])
        self.assertIn("pkq/", str(caught.exception))

    def test_S6_a_file_over_a_ceiling_with_no_row_is_an_offender_on_a_paid_run_too(self):
        """structure-budget/S6"""
        tree = {"pkg/huge.py": measured(**{"module-lines": 1050})}
        self.assertEqual(
            budget["growth_lines"](tree, baseline()),
            ["pkg/huge.py: module-lines 1050 > ceiling 1000"],
        )


class MeasureTests(unittest.TestCase):
    """S8 and the five definitions: a test module is measured exactly as source is."""

    SOURCE = "".join(
        "def %s(a, b, c, d, e, f, g, h):\n    return 0\n\n" % name for name in ("one",)
    )

    def test_the_five_metrics_are_the_numbers_their_ceilings_name(self):
        numbers = budget["metrics_of"](
            "def wide(a, b, c, d, e, f, g, h):\n"
            "    if a and b:\n"
            "        return 1\n"
            "    for value in range(c):\n"
            "        if value:\n"
            "            return 2\n"
            "    return 3\n"
            "\n"
            "class Wide:\n"
            "    def m(self):\n"
            "        return 0\n"
            "    def _hidden(self):\n"
            "        return 0\n"
        )
        self.assertEqual(numbers["module-lines"], 13)
        self.assertEqual(numbers["parameters"], 8)
        self.assertEqual(numbers["function-lines"], 7)
        # one if, one `and` operand, one for, one more if: four ways through `wide`
        self.assertEqual(numbers["function-branches"], 4)
        self.assertEqual(numbers["class-methods"], 1, "a private method is not a public method")

    def test_S8_a_test_module_and_its_test_class_carry_the_same_ceilings_and_rows(self):
        """structure-budget/S8"""
        suite = "class Tests:\n" + "".join(
            "    def test_%d(self):\n        return 0\n" % index for index in range(26)
        )
        numbers = budget["metrics_of"]("import unittest\n\n\n" + suite)
        self.assertEqual(numbers["class-methods"], 26)
        recorded = baseline({"tests/test_wide.py": {"class-methods": 3}})
        self.assertEqual(
            budget["growth_lines"]({"tests/test_wide.py": numbers}, recorded),
            ["tests/test_wide.py: class-methods 26 > recorded 3 (ceiling 25)"],
        )
        walked = budget["walk"](str(REPO))
        self.assertIn("tests/test_structure_budget.py", walked, "the test tree is measured too")
        self.assertIn("scripts/structure_budget.py", walked)


class AffectedMapTests(unittest.TestCase):
    """S9: the affected tier runs the test that proves a change to the script."""

    def test_S9_the_affected_map_routes_the_script_to_its_own_test_module(self):
        """structure-budget/S9"""
        with open(REPO / "pyproject.toml", "rb") as handle:
            ini = tomllib.load(handle)["tool"]["pytest"]["ini_options"]
        pairs = parse_file_map(ini["impacted_map"])
        selected = map_changed_files(["scripts/structure_budget.py"], pairs, str(REPO))
        self.assertIn("tests/test_structure_budget.py", selected)
        installer = [path for path in selected if path.startswith("tests/installer")]
        self.assertEqual(installer, [], "the installer suite does not prove this change")


if __name__ == "__main__":
    unittest.main()
