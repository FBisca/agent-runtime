"""The structure budget: five ceilings over every Python file, one base revision, a ratchet on growth.

The script is the seam: these cases drive its decisions in process, while the program's own contract
(its exit codes and the lines it prints) is the acceptance file, driven over a real checkout. A
ceiling here is the spec's number, written out rather than imported, so a changed ceiling fails these
cases.

A file may exceed a ceiling only if it already did at the base revision, and only by getting no
worse, so `git` holds the base and the committed budget holds the ceilings alone.
"""

import json
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_impacted.file_map import map_changed_files, matches_any_glob, parse_file_map

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "structure_budget.py"
budget = runpy.run_path(str(SCRIPT))
BASELINE = REPO / "acceptance" / "structure" / "structure_budget.json"

CEILINGS = {
    "module-lines": 1000,
    "class-methods": 25,
    "function-lines": 50,
    "function-branches": 12,
    "parameters": 7,
}

# The ruff rule nearest each of the three ceilings the script counts itself, and the setting its number
# would land in. They are named to be measured, not to be adopted: the tests below are what keeps the
# ceilings the script's own numbers.
RUFF_RULES = {
    "class-methods": ("PLR0904", "lint.pylint.max-public-methods"),
    "function-branches": ("PLR0912", "lint.pylint.max-branches"),
    "parameters": ("PLR0913", "lint.pylint.max-args"),
}

# The number a ruff violation prints: "Too many branches (13 > 12)".
RUFF_NUMBER = re.compile(r"\((\d+) > \d+\)")

UNDER = dict.fromkeys(CEILINGS, 1)


def measured(**numbers):
    """A measured row: the metrics named, the rest of the file's five under every ceiling."""
    return {**UNDER, **numbers}


def long_module(lines):
    """A module of exactly `lines` source lines: short functions, so only its size trips a ceiling."""
    body = []
    name = 0
    while len(body) + 3 <= lines:
        body.extend(["def f%d():" % name, "    return 0", ""])
        name += 1
    body.extend("# padding %d" % index for index in range(lines - len(body)))
    return "\n".join(body) + "\n"


def ruff_numbers(root, names):
    """{path: {metric: number}} as ruff's own three rules measure them, every number asked for.

    A ceiling of zero makes each rule report the worst case it found in a file, so what comes back is a
    number to compare with the script's rather than a verdict. The names are the ones the script
    resolved, so both measurements cover the same files. Ruff is a dependency of the workspace and so
    is on `PATH` under `uv run`; without it there is nothing to compare and the test says so.
    """
    found = shutil.which("ruff")
    assert found, "ruff is what these tests measure against: run them through `uv run`"
    rules = sorted(RUFF_RULES.items())
    command = [
        found,
        "check",
        "--preview",  # PLR0904 is preview-only
        "--isolated",
        "--no-cache",
        "--ignore-noqa",
        "--output-format",
        "json",
        "--select",
        ",".join(code for _, (code, _) in rules),
        *(argument for _, (_, option) in rules for argument in ("--config", "%s=0" % option)),
        *(str(Path(root) / name) for name in names),
    ]
    done = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert done.returncode in (0, 1), done.stderr  # 1 is "violations found", which is the point
    codes = {code: metric for metric, (code, _) in rules}
    numbers = {}
    for diagnostic in json.loads(done.stdout or "[]"):
        metric = codes.get(diagnostic.get("code"))
        if metric is None:
            continue
        number = RUFF_NUMBER.search(diagnostic.get("message") or "")
        assert number, "ruff's %s message carries no number: %r" % (metric, diagnostic)
        name = Path(diagnostic["filename"]).resolve().relative_to(Path(root).resolve()).as_posix()
        per_file = numbers.setdefault(name, {})
        per_file[metric] = max(per_file.get(metric, 0), int(number.group(1)))
    return numbers


class BaseDecisionTests(unittest.TestCase):
    """S2, S3, S15, S16, S17: a ceiling is held only by a file that held it at the base."""

    def test_S15_a_file_that_crossed_a_ceiling_since_the_base_is_named_with_the_ceiling(self):
        """structure-budget/S15"""
        tree = {"pkg/big.py": measured(**{"module-lines": 1100})}
        at_base = {"pkg/big.py": measured(**{"module-lines": 900})}
        self.assertEqual(
            budget["growth_lines"](tree, at_base),
            ["pkg/big.py: module-lines 1100 > ceiling 1000"],
        )

    def test_S3_a_file_with_no_measurement_at_the_base_is_named_with_the_ceiling(self):
        """structure-budget/S3"""
        tree = {"pkg/wide.py": measured(**{"class-methods": 26})}
        self.assertEqual(
            budget["growth_lines"](tree, {}),
            ["pkg/wide.py: class-methods 26 > ceiling 25"],
        )

    def test_S2_a_file_that_grows_past_what_it_measured_at_the_base_is_named_alone(self):
        """structure-budget/S2"""
        tree = {
            "pkg/big.py": measured(**{"module-lines": 1250}),
            "pkg/fine.py": measured(**{"module-lines": 120}),
        }
        at_base = {
            "pkg/big.py": measured(**{"module-lines": 1150}),
            "pkg/fine.py": measured(**{"module-lines": 120}),
        }
        lines = budget["growth_lines"](tree, at_base)
        self.assertEqual(lines, ["pkg/big.py: module-lines 1250 > recorded 1150 (ceiling 1000)"])
        self.assertNotIn("fine.py", " ".join(lines), "a file that did not worsen is not named")

    def test_S16_an_offender_that_grows_names_its_number_at_the_base_and_may_only_shrink(self):
        """structure-budget/S16"""
        at_base = {"pkg/big.py": measured(**{"module-lines": 1100})}
        self.assertEqual(
            budget["growth_lines"]({"pkg/big.py": measured(**{"module-lines": 1150})}, at_base),
            ["pkg/big.py: module-lines 1150 > recorded 1100 (ceiling 1000)"],
        )
        self.assertEqual(
            budget["growth_lines"]({"pkg/big.py": measured(**{"module-lines": 1050})}, at_base),
            [],
            "an offender that shrinks holds its grandfather, even over the ceiling",
        )

    def test_S17_growth_under_a_ceiling_is_free_and_no_file_is_stored(self):
        """structure-budget/S17"""
        tree = {"pkg/mod.py": measured(**{"module-lines": 400})}
        at_base = {"pkg/mod.py": measured(**{"module-lines": 120})}
        self.assertEqual(budget["growth_lines"](tree, at_base), [])
        document = budget["baseline_document"]()
        self.assertEqual(sorted(document), ["ceilings", "measured_at"])
        self.assertEqual(document["ceilings"], CEILINGS)

    def test_S4_write_stamps_today_and_stores_no_per_file_row(self):
        """structure-budget/S4"""
        document = budget["baseline_document"]()
        stamp = datetime.fromisoformat(document["measured_at"])
        age = abs((datetime.now(timezone.utc) - stamp).total_seconds())
        self.assertLess(age, 120, "the baseline records the day it was measured")
        self.assertNotIn("files", document, "a deliberate change is recorded as the day, not a row")

    def test_S5_the_committed_baseline_names_the_ceilings_and_no_file(self):
        """structure-budget/S5"""
        document = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(sorted(document), ["ceilings", "measured_at"])
        self.assertEqual(document["ceilings"], CEILINGS)
        self.assertEqual(
            budget["growth_lines"]({"pkg/small.py": measured()}, {}),
            [],
            "a file under every ceiling needs no row",
        )

    def test_S1_a_tree_at_its_base_passes_and_the_line_names_the_ceilings_it_read(self):
        """structure-budget/S1"""
        tree = {"pkg/mod.py": measured(**{"module-lines": 120})}
        at_base = {"pkg/mod.py": measured(**{"module-lines": 120})}
        self.assertEqual(budget["growth_lines"](tree, at_base), [])
        line = budget["summary"](tree, {"ceilings": CEILINGS}, "origin/main")
        for metric, ceiling in CEILINGS.items():
            self.assertIn(metric, line)
            self.assertIn(str(ceiling), line)
        self.assertIn("origin/main", line, "a reader sees the revision the base was read at")


class PaidTests(unittest.TestCase):
    """S6: a paid path takes no grandfather from the base, and only for the metrics it names."""

    TREE = {"pkg/big.py": measured(**{"module-lines": 1050})}
    AT_BASE = {"pkg/big.py": measured(**{"module-lines": 1100})}

    def test_S6_a_paid_metric_may_not_be_grandfathered_and_a_metric_not_paid_is_left_alone(self):
        """structure-budget/S6"""
        self.assertEqual(
            budget["growth_lines"](self.TREE, self.AT_BASE),
            [],
            "grandfathered and shrinking: unpaid, the base is what it is held to",
        )
        self.assertEqual(
            budget["growth_lines"](
                self.TREE, self.AT_BASE, paid_paths=["pkg"], paid_metrics=["module-lines"]
            ),
            ["pkg/big.py: module-lines 1050 > paid (ceiling 1000)"],
        )
        self.assertEqual(
            budget["growth_lines"](
                self.TREE, self.AT_BASE, paid_paths=["pkg"], paid_metrics=["class-methods"]
            ),
            [],
            "a row for a metric that was not paid is left alone",
        )

    def test_S6_a_paid_metric_may_be_repeated_and_every_named_metric_is_paid(self):
        """structure-budget/S6"""
        tree = {"pkg/big.py": measured(**{"module-lines": 1050, "class-methods": 26})}
        paid = budget["growth_lines"](
            tree,
            {},
            paid_paths=["pkg/"],
            paid_metrics=["module-lines", "class-methods"],
        )
        self.assertEqual(
            paid,
            [
                "pkg/big.py: module-lines 1050 > paid (ceiling 1000)",
                "pkg/big.py: class-methods 26 > paid (ceiling 25)",
            ],
        )

    def test_S6_a_paid_path_that_measures_no_file_is_refused(self):
        """structure-budget/S6"""
        with self.assertRaises(budget["Refused"]) as caught:
            budget["paid_paths"](self.TREE, ["pkq/"])
        self.assertIn("pkq/", str(caught.exception))


class MeasureTests(unittest.TestCase):
    """S8 and the five definitions: a test module is measured exactly as source is."""

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
            "    def test_%d(self):\n        return 0\n" % index for index in range(27)
        )
        numbers = budget["metrics_of"]("import unittest\n\n\n" + suite)
        self.assertEqual(numbers["class-methods"], 27)
        at_base = {"tests/test_wide.py": measured(**{"class-methods": 26})}
        self.assertEqual(
            budget["growth_lines"]({"tests/test_wide.py": numbers}, at_base),
            ["tests/test_wide.py: class-methods 27 > recorded 26 (ceiling 25)"],
        )

    @pytest.mark.integration
    def test_S8_the_checkouts_own_test_tree_is_measured_as_source_is(self):
        """structure-budget/S8: what git lists decides, and the test tree is part of what it lists."""
        resolved = budget["resolve"](str(REPO))
        self.assertIn("tests/test_structure_budget.py", resolved, "the test tree is measured too")
        self.assertIn("scripts/structure_budget.py", resolved)


@pytest.mark.integration
class RuffDivergenceTests(unittest.TestCase):
    """S20: the five ceilings are this script's numbers, and ruff's neighbouring rules are not.

    Handing a ceiling to ruff looks like removing a second implementation and is not: a ceiling is the
    thing a tool counts, and ruff counts a different thing, so the offender lines would move. Both
    measurements are taken once for the class over the whole tree, and each case reads the same pair.
    """

    @classmethod
    def setUpClass(cls):
        cls.gate = budget["measure_tree"](str(REPO))
        cls.ruff = ruff_numbers(REPO, budget["resolve"](str(REPO)))

    def delegated(self, metric):
        """The gate's numbers with one ceiling left to ruff's rule instead, as a swap would leave them."""
        return {
            name: {**numbers, metric: self.ruff.get(name, {}).get(metric, 0)}
            for name, numbers in self.gate.items()
        }

    @staticmethod
    def over(numbers, metric):
        """The files one set of numbers holds over one ceiling, which is the script's own number."""
        ceiling = budget["ceilings_of"]({"ceilings": {}})[metric]
        return {name for name, found in numbers.items() if found[metric] > ceiling}

    def test_S20_a_ruff_branch_ceiling_would_let_files_off_the_offender_list(self):
        """structure-budget/S20: PLR0912 counts no comprehension filter, conditional or assert."""
        dropped = sorted(
            self.over(self.gate, "function-branches")
            - self.over(self.delegated("function-branches"), "function-branches")
        )
        self.assertTrue(
            dropped,
            "ruff's PLR0912 counts neither a comprehension filter nor a conditional expression nor an "
            "assert, so a branch ceiling handed to it would leave the files this script counts over "
            "the ceiling off the list; it left none off here, which means the two rules now agree and "
            "the delegation costs nothing: measure the two over this tree before believing that, then "
            "delegate it and delete this test",
        )

    def test_S20_a_ruff_method_ceiling_would_name_classes_the_script_does_not(self):
        """structure-budget/S20: PLR0904 counts a dunder method as public, and this script does not."""
        added = sorted(
            self.over(self.delegated("class-methods"), "class-methods")
            - self.over(self.gate, "class-methods")
        )
        self.assertTrue(
            added,
            "ruff's PLR0904 counts a dunder method as public and this script counts public methods "
            "alone, so a method ceiling handed to it would name classes that hold no more public "
            "method than the ceiling allows; it named none here, which means the two rules now agree "
            "and the delegation costs nothing: measure the two over this tree before believing that, "
            "then delegate it and delete this test",
        )

    def test_S20_a_ruff_parameter_ceiling_would_let_an_offender_off_the_list(self):
        """structure-budget/S20: PLR0913 counts no `self`, and this script counts every parameter."""
        dropped = sorted(
            self.over(self.gate, "parameters")
            - self.over(self.delegated("parameters"), "parameters")
        )
        self.assertTrue(
            dropped,
            "ruff's PLR0913 counts no `self` and this script counts every parameter a function "
            "declares, so a parameter ceiling handed to it would leave methods this script counts "
            "over the ceiling off the list; it left none off here, which means the two rules now "
            "agree and the delegation costs nothing: measure the two over this tree before believing "
            "that, then delegate it and delete this test",
        )


class SinceTests(unittest.TestCase):
    """S15 and S16 across the boundary: the base is the tree at the `--since` revision in git."""

    def checkout(self, base_lines=1150, head_lines=None, name="pkg/big.py"):
        """A checkout whose one module is committed at `base_lines` and optionally changed after."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "repo"
        (root / "pkg").mkdir(parents=True)
        self.write(root, long_module(base_lines), name)
        for command in (("init", "-q", "-b", "main"), ("add", "-A")):
            self.git(root, *command)
        self.git(root, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "base")
        if head_lines is not None:
            self.write(root, long_module(head_lines))
        return root

    @staticmethod
    def write(root, source, name="pkg/big.py"):
        (root / name).write_text(source, encoding="utf-8")

    @staticmethod
    def git(root, *args):
        done = subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, timeout=120
        )
        return done.stdout.decode().strip()

    def commit(self, root, message="change"):
        """Commit the tracked change, so the next run reads the move or the growth as the tree."""
        self.git(root, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qam", message)

    def stamp(self, root):
        """The budget committed beside a checkout, written once so a `--write` run is seen."""
        path = root / "budget.json"
        if not path.exists():
            path.write_text(json.dumps(budget["baseline_document"]()), encoding="utf-8")
        return path

    def drive(self, root, *args, since="HEAD"):
        """The script driven as a program over `root`, held to every ceiling at `since`."""
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(root),
                "--baseline",
                str(self.stamp(root)),
                "--since",
                since,
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

    # One plain name and one git quotes without `-z` (non-ASCII under the default core.quotePath):
    # both are legal paths, and a move of either may never fail the build.
    MOVES = (("pkg/big.py", "pkg/moved.py"), ("pkg/méta.py", "pkg/renommé.py"))

    @pytest.mark.integration
    def test_S19_a_moved_offender_keeps_the_number_it_had_at_the_base(self):
        """structure-budget/S19: a git mv carries an offender's number to the path it moved to."""
        for before, after in self.MOVES:
            with self.subTest(moved=before):
                self.keeps_its_number_through(before, after)

    def keeps_its_number_through(self, before, after):
        """The move at `--since` inherits its base number, and only shrinking holds it."""
        root = self.checkout(base_lines=1150, name=before)
        base = self.git(root, "rev-parse", "HEAD")
        self.git(root, "mv", before, after)
        self.commit(root, "move the module")
        moved = self.drive(root, since=base)
        self.assertEqual(moved.returncode, 0, moved.stdout + moved.stderr)
        self.assertIn(base, moved.stderr, "the run is held to the revision it named, not the move")
        self.write(root, long_module(1200), after)
        self.commit(root, "grow the moved module")
        grown = self.drive(root, since=base)
        self.assertEqual(grown.returncode, 1, grown.stdout + grown.stderr)
        self.assertIn("%s: module-lines 1200 > recorded 1150 (ceiling 1000)" % after, grown.stdout)

    @pytest.mark.integration
    def test_S16_the_base_is_the_tree_at_the_since_revision_in_git(self):
        """structure-budget/S16: the number an offender may not pass comes from git, not a file."""
        root = self.checkout(head_lines=1250)
        at_base = budget["measure_at"](str(root), "HEAD")
        self.assertEqual(at_base["pkg/big.py"]["module-lines"], 1150)
        self.assertEqual(
            budget["growth_lines"](budget["measure_tree"](str(root)), at_base),
            ["pkg/big.py: module-lines 1250 > recorded 1150 (ceiling 1000)"],
        )

    @pytest.mark.integration
    def test_S16_a_clean_run_that_keeps_a_grandfather_reports_on_stderr(self):
        """structure-budget/S16: stdout is what the gate refused, so a clean run keeps it empty."""
        root = self.checkout(head_lines=900)
        at_ceilings = self.drive(root)
        self.assertEqual(at_ceilings.returncode, 0, at_ceilings.stdout + at_ceilings.stderr)
        self.assertIn("module-lines 1000", at_ceilings.stdout, "a tree at its ceilings names them")
        # The same file, over the ceiling at the base and shrunk back: clean, but holding debt.
        self.write(root, long_module(1100))
        kept = self.drive(root)
        self.assertEqual(kept.returncode, 0, kept.stdout + kept.stderr)
        self.assertEqual(
            kept.stdout.strip(), "", "a kept grandfather is a diagnostic, not a refusal"
        )
        self.assertIn("module-lines 1000", kept.stderr)

    @pytest.mark.integration
    def test_S4_write_stamps_the_day_and_never_accepts_growth(self):
        """structure-budget/S4: the base is git, so writing the budget cannot bless a crossing."""
        root = self.checkout(base_lines=900, head_lines=1100)
        wrote = self.drive(root, "--write")
        self.assertEqual(wrote.returncode, 0, wrote.stdout + wrote.stderr)
        self.assertIn("never accepts growth", wrote.stdout)
        document = json.loads(self.stamp(root).read_text(encoding="utf-8"))
        self.assertEqual(sorted(document), ["ceilings", "measured_at"])
        gate = self.drive(root)
        self.assertEqual(gate.returncode, 1, gate.stdout + gate.stderr)
        self.assertIn("pkg/big.py: module-lines 1100 > ceiling 1000", gate.stdout)

    @pytest.mark.integration
    def test_S23_resolve_lists_the_tree_git_holds_and_a_tree_outside_one_is_measured_by_walking(
        self,
    ):
        """structure-budget/S23: a file this checkout carries is resolved; a copy another tree holds is not."""
        root = self.checkout()
        (root / "pkg" / "new.py").write_text(long_module(1200))
        self.assertEqual(
            budget["resolve"](str(root)),
            ["pkg/big.py", "pkg/new.py"],
            "a file the checkout carries counts whether or not it was committed yet",
        )
        grown = self.drive(root)
        self.assertEqual(grown.returncode, 1, grown.stdout + grown.stderr)
        self.assertIn("pkg/new.py: module-lines 1200 > ceiling 1000", grown.stdout)

        (root / "pkg" / "new.py").unlink()
        (root / ".gitignore").write_text("ignored/\n")
        (root / "ignored").mkdir()
        (root / "ignored" / "big.py").write_text(long_module(1200))
        (root / ".venv" / "lib").mkdir(parents=True)
        (root / ".venv" / "lib" / "big.py").write_text(long_module(1200))
        (root / "pkg" / "checkout").mkdir()
        self.git(root / "pkg" / "checkout", "init", "-q", "-b", "main")
        (root / "pkg" / "checkout" / "big.py").write_text(long_module(1200))
        self.assertEqual(
            budget["resolve"](str(root)),
            ["pkg/big.py"],
            "an ignored tree, a cache tree and another checkout are no source of this one",
        )
        clean = self.drive(root)
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

        outside = root.parent / "outside"
        (outside / "pkg").mkdir(parents=True)
        (outside / "pkg" / "big.py").write_text(long_module(1200))
        self.assertEqual(
            budget["resolve"](str(outside)),
            ["pkg/big.py"],
            "a tree outside a repository has no index to ask, so the walk answers",
        )
        self.assertEqual(
            budget["measure_tree"](str(outside))["pkg/big.py"]["module-lines"],
            1200,
            "the tree it is given is still measured by walking",
        )

    @pytest.mark.integration
    def test_S15_a_since_that_is_not_a_revision_is_refused_naming_it(self):
        """structure-budget/S15"""
        root = self.checkout()
        with self.assertRaises(budget["Refused"]) as caught:
            budget["measure_at"](str(root), "no-such-rev")
        self.assertIn("no-such-rev", str(caught.exception))


class AffectedMapTests(unittest.TestCase):
    """S9: the affected tier runs the test that proves a change to the script."""

    @staticmethod
    def pairs():
        """The committed map as `(glob, path)` pairs, read the way the plugin reads it."""
        with open(REPO / "pyproject.toml", "rb") as handle:
            ini = tomllib.load(handle)["tool"]["pytest"]["ini_options"]
        return parse_file_map(ini["impacted_map"])

    def test_S9_the_map_text_routes_the_script_to_its_own_test_module(self):
        """structure-budget/S9"""
        pairs = self.pairs()
        self.assertIn(("scripts/structure_budget.py", "tests/test_structure_budget.py"), pairs)
        routed = [
            path for glob, path in pairs if matches_any_glob("scripts/structure_budget.py", [glob])
        ]
        installer = [path for path in routed if path.startswith("tests/installer")]
        self.assertEqual(installer, [], "the installer suite does not prove this change")

    @pytest.mark.integration
    def test_S9_a_change_to_the_script_selects_its_own_test_module(self):
        """structure-budget/S9: the plugin's own selection, over the real repository."""
        selected = map_changed_files(["scripts/structure_budget.py"], self.pairs(), str(REPO))
        self.assertIn("tests/test_structure_budget.py", selected)
        installer = [path for path in selected if path.startswith("tests/installer")]
        self.assertEqual(installer, [], "the installer suite does not prove this change")


if __name__ == "__main__":
    unittest.main()
