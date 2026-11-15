"""The spec the archive writes: one section per plan, one number per requirement, and nothing a hand-resolved
conflict left behind. The suite reads the repository's own specs, so the living contract is held to the same grammar
the archive writes."""

import pathlib
import unittest
from types import SimpleNamespace

import pytest
from agent_orchestrator.driver import (
    merge_requirements,
    spec_defects,
    without_coverage,
)

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[3]
SPECS = ROOT / "docs" / "specs"

DEMO = """# Demo

## Requirements: other

- **R7** WHEN x, THEN y (from other/S1)

## Coverage: other

| Requirement | Scenario | Test nodes |
|---|---|---|
| R7 | S1 | `a.py::t` |

## Requirements: demo

- **R8** WHEN a, THEN b (from demo/S1)

## Coverage: demo

| Requirement | Scenario | Test nodes |
|---|---|---|
| R8 | S1 | `a.py::t` |
"""


def spec_of(scenarios, nodes, slug="demo"):
    """A Driver that knows its plan's scenarios and every scenario's nodes, nothing else."""
    driver = SimpleNamespace(
        slug=slug,
        flat=lambda text: " ".join(str(text).split()),
        plan_model=lambda: SimpleNamespace(scenarios=scenarios),
        scenario_nodes=lambda: nodes,
    )
    from agent_orchestrator.driver import Driver

    driver.spec_section = Driver.spec_section.__get__(driver)
    return driver


def scenario(sid, when="a", then="b"):
    return SimpleNamespace(id=sid, when=when, then=then, evidence=None)


class WithoutCoverage(unittest.TestCase):
    def test_a_rebuild_keeps_one_coverage_table_for_the_slug(self):
        once = without_coverage(DEMO, "demo")
        self.assertEqual(once, DEMO[: DEMO.index("## Coverage: demo")])

    def test_another_plans_table_is_left_alone(self):
        self.assertIn("## Coverage: other", without_coverage(DEMO, "demo"))
        self.assertEqual(without_coverage(DEMO, "absent"), DEMO)

    def test_a_rebuild_write_the_same_bytes_twice(self):
        nodes = {"S1": ["a.py::t"]}
        driver = spec_of([scenario("S1")], nodes)
        first = without_coverage(DEMO, "demo")
        first = merge_requirements(
            first.rstrip("\n") + "\n\n" + driver.spec_section(first, {"S1": "R8"}), "demo"
        )
        self.assertEqual(first, DEMO)

    def test_a_held_scenario_keeps_its_number_and_the_table_is_replaced_not_added(self):
        nodes = {"S1": ["a.py::t"], "S2": ["b.py::t"]}
        driver = spec_of([scenario("S1"), scenario("S2")], nodes)
        body = without_coverage(DEMO, "demo")
        rebuild = body.rstrip("\n") + "\n\n" + driver.spec_section(body, {"S1": "R8"})
        self.assertEqual(rebuild.count("## Coverage: demo"), 1)
        self.assertIn("| R8 | S1 | `a.py::t` |", rebuild)
        self.assertIn("| R9 | S2 | `b.py::t` |", rebuild)

    def test_a_second_rebuild_writes_the_same_bytes(self):
        """What a `retry` does: the same section over the file it already wrote, with both scenarios now held."""
        nodes = {"S1": ["a.py::t"], "S2": ["b.py::t"]}
        driver = spec_of([scenario("S1"), scenario("S2")], nodes)
        body = without_coverage(DEMO, "demo")
        first = merge_requirements(
            body.rstrip("\n") + "\n\n" + driver.spec_section(body, {"S1": "R8"}), "demo"
        )
        kept = without_coverage(first, "demo")
        second = merge_requirements(
            kept.rstrip("\n") + "\n\n" + driver.spec_section(kept, {"S1": "R8", "S2": "R9"}),
            "demo",
        )
        self.assertEqual(second, first)
        self.assertEqual(second.count("## Requirements: demo"), 1)
        self.assertEqual(second.count("## Coverage: demo"), 1)


class MergeRequirements(unittest.TestCase):
    TWICE = """# Demo

## Requirements: demo

- **R120** WHEN a, THEN b (from demo/S1)

## Requirements: other

- **R121** WHEN c, THEN d (from other/S1)

## Requirements: demo

- **R122** WHEN e, THEN f (from demo/S2)

## Coverage: demo
"""

    def test_a_later_block_joins_the_first_one_and_one_blank_line_stays(self):
        self.assertEqual(
            merge_requirements(self.TWICE, "demo"),
            "# Demo\n\n## Requirements: demo\n\n- **R120** WHEN a, THEN b (from demo/S1)\n"
            "- **R122** WHEN e, THEN f (from demo/S2)\n\n## Requirements: other\n\n"
            "- **R121** WHEN c, THEN d (from other/S1)\n\n## Coverage: demo\n",
        )

    def test_one_section_is_left_alone(self):
        self.assertEqual(merge_requirements(self.TWICE, "other"), self.TWICE)

    def test_the_folded_spec_holds_one_section_for_the_slug(self):
        folded = merge_requirements(self.TWICE, "demo")
        self.assertEqual(folded.count("## Requirements: demo"), 1)
        self.assertEqual(spec_defects(folded), [])


class TheWrite(unittest.TestCase):
    def driver(self, scenarios, nodes):
        driver = spec_of(scenarios, nodes)
        driver.plan_model = lambda: SimpleNamespace(scenarios=scenarios, spec="docs/specs/demo.md")
        from agent_orchestrator.driver import Driver

        driver.spec_text = Driver.spec_text.__get__(driver)
        return driver

    def test_a_clean_base_takes_the_section_and_folds_it_into_one(self):
        driver = self.driver([scenario("S1"), scenario("S2")], {"S1": ["a.py::t"], "S2": []})
        text = driver.spec_text(DEMO, {"S1": "R8"})
        self.assertEqual(spec_defects(text), [])
        self.assertEqual(text.count("## Requirements: demo"), 1)
        self.assertEqual(text.count("## Coverage: demo"), 1)
        self.assertIn("| R8 | S1 | `a.py::t` |", text)
        self.assertIn("| R9 | S2 |  |", text)

    def test_a_section_that_would_break_the_spec_is_refused_naming_the_line(self):
        driver = self.driver([scenario("S1")], {"S1": ["a.py::t"]})
        driver.spec_section = lambda *_: "## Coverage: demo\n\n| R99 | S1 | `a.py::t` |\n"
        with self.assertRaises(ValueError) as refused:
            driver.spec_text(DEMO, {"S1": "R8"})
        self.assertIn("docs/specs/demo.md", str(refused.exception))
        self.assertIn("a Coverage row for R99", str(refused.exception))
        self.assertIn("`retry`", str(refused.exception))

    def test_a_defect_the_base_already_carried_is_not_blamed_on_the_write(self):
        broken = DEMO.replace("| R7 | S1 | `a.py::t` |", "| R77 | S1 | `a.py::t` |")
        driver = self.driver([scenario("S1")], {"S1": ["a.py::t"]})
        text = driver.spec_text(broken, {"S1": "R8"})
        self.assertEqual(
            [defect for defect in spec_defects(text) if defect not in spec_defects(broken)], []
        )


class Defects(unittest.TestCase):
    def test_a_clean_spec_has_none(self):
        self.assertEqual(spec_defects(DEMO), [])
        self.assertEqual(spec_defects(""), [])

    def test_a_conflict_marker_is_named_with_its_line(self):
        for marker in (
            "<<<<<<< HEAD",
            "=======",
            ">>>>>>> origin/main",
            "||||||| parent of 313d1f7",
        ):
            with self.subTest(marker=marker):
                defects = spec_defects(DEMO + marker + "\n")
                self.assertEqual(len(defects), 1)
                self.assertIn("conflict marker", defects[0])
                self.assertIn("line %d" % (len(DEMO.splitlines()) + 1), defects[0])

    def test_a_requirement_numbered_twice_is_named_with_both_lines(self):
        text = DEMO.replace("(from demo/S1)", "(from demo/S2)") + "\n- **R8** WHEN c, THEN d\n"
        defects = spec_defects(text)
        self.assertEqual(len(defects), 1)
        self.assertIn("line 23: R8 is already numbered at line 15", defects[0])

    def test_a_second_section_for_one_slug_is_named(self):
        text = (
            DEMO
            + "\n## Coverage: demo\n\n| Requirement | Scenario | Test nodes |\n|---|---|---|\n| R8 | S1 | `a.py::t` |\n"
        )
        self.assertEqual(len(spec_defects(text)), 1)
        self.assertIn("a second Coverage section for demo", spec_defects(text)[0])

    def test_a_row_for_a_requirement_the_file_lacks_is_named(self):
        text = DEMO.replace("| R7 | S1 | `a.py::t` |", "| R99 | S1 | `a.py::t` |")
        self.assertEqual(len(spec_defects(text)), 1)
        self.assertIn("a Coverage row for R99", spec_defects(text)[0])

    def test_a_row_outside_a_coverage_section_is_not_read_as_one(self):
        self.assertEqual(spec_defects(DEMO.replace("## Coverage: other", "## Other")), [])


class TheRepositorysSpecs(unittest.TestCase):
    def test_every_spec_is_clean(self):
        """The class the guard exists for: a hand-resolved conflict, a second append or a reused number lands red here
        instead of in the living contract."""
        self.assertTrue(SPECS.is_dir(), SPECS)
        broken = {
            path.name: defects
            for path in sorted(SPECS.glob("*.md"))
            if (defects := spec_defects(path.read_text(encoding="utf-8")))
        }
        self.assertEqual(broken, {})
