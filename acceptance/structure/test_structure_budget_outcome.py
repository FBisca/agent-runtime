"""The structure budget's outcome: a ceiling no new file may pass, and a base no offender may worsen.

Every case drives `scripts/structure_budget.py` as a program over a git repository written in a
temporary directory, so no case imports it and the file collects before the script exists.

The rule under test is one sentence: **a file may exceed a ceiling only if it already did at
`--since`, and only by getting no worse.** Nothing per file is stored; `git` holds the base.

The offender line is the contract, one shape per kind:

    <path>: <metric> <value> > ceiling <ceiling>
    <path>: <metric> <value> > recorded <base> (ceiling <ceiling>)
    <path>: <metric> <value> > paid (ceiling <ceiling>)

Exit 0 is a clean tree, 1 is an offender, 2 is a refusal: an unreadable baseline, a `--since` that is
not a revision, or a `--paid` path that measures nothing.
"""

import ast
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "structure_budget.py"

CEILINGS = {
    "module-lines": 1000,
    "class-methods": 25,
    "function-lines": 50,
    "function-branches": 12,
    "parameters": 7,
}


def module(names=()):
    """A Python module whose source is `names` function definitions, one line each."""
    return "".join("def %s():\n    return 0\n\n" % name for name in names)


def long_module(lines):
    """A module of exactly `lines` source lines: short functions, so only its size trips a ceiling."""
    body = []
    name = 0
    while len(body) + 3 <= lines:
        body.extend(["def f%d():" % name, "    return 0", ""])
        name += 1
    body.extend("# padding %d" % index for index in range(lines - len(body)))
    return "\n".join(body) + "\n"


def wide_class(methods):
    """A module whose one class carries `methods` public methods, one line each."""
    body = ["class Wide:"]
    for index in range(methods):
        body.append("    def m%d(self):" % index)
        body.append("        return 0")
    return "\n".join(body) + "\n"


class Tree:
    """A git repository and its baseline: committed at `base`, then edited into the tree under test."""

    def __init__(self, base, head=None, ceilings=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.baseline = Path(self.tmp.name) / "budget.json"
        self.root.mkdir()
        for name, source in base.items():
            self.write(name, source)
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        for name, source in (head or {}).items():
            self.write(name, source)
        self.write_ceiling_file(ceilings)

    def write(self, name, source):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        return path

    def write_ceiling_file(self, ceilings=None):
        self.baseline.write_text(
            json.dumps(
                {
                    "measured_at": "2026-11-15T00:00:00+00:00",
                    "ceilings": dict(ceilings or CEILINGS),
                },
                indent=1,
            ),
            encoding="utf-8",
        )

    def baseline_bytes(self):
        return json.loads(self.baseline.read_text(encoding="utf-8"))

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=120
        ).stdout

    def run(self, *args):
        if not SCRIPT.exists():
            # The script is what the ticket adds: until it exists every case fails on an assertion
            # rather than on a traceback, so the baseline at `base` is red, not an error.
            return subprocess.CompletedProcess(args=args, returncode=127, stdout="", stderr="")
        # `--since` defaults to the base commit; a case that names its own must not depend on
        # argparse's last-wins, which is the bug this file exists to refuse.
        since = [] if "--since" in args else ["--since", self.base]
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.root),
                "--baseline",
                str(self.baseline),
                *since,
                *args,
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO),
            timeout=120,
        )


def lines(done):
    """The offender lines of a run: its non-blank stdout lines, in the order it printed them."""
    return [line for line in done.stdout.splitlines() if line.strip()]


class StructureBudgetTests(unittest.TestCase):
    def tree(self, base, head=None, ceilings=None):
        """A repository, its base commit and the tree under test, removed when the case ends."""
        tree = Tree(base, head, ceilings)
        self.addCleanup(tree.tmp.cleanup)
        return tree

    def test_control_every_fixture_is_a_module_the_interpreter_parses(self):
        """structure-budget/S1: the control that passes at base.

        A fixture the interpreter refuses measures nothing: the budget reads a syntax tree, so a
        builder that emits an indentation error makes every case that uses it vacuous. That is how
        this file first shipped, so the parse is the control and the counts only name the shape."""
        builders = {
            "plain": module(["one", "two"]),
            "function-lines": long_module(120),
            "module-lines": long_module(CEILINGS["module-lines"] + 50),
            "class-methods": wide_class(CEILINGS["class-methods"] + 1),
            "one-method": wide_class(1),
        }
        for name, source in builders.items():
            with self.subTest(builder=name):
                ast.parse(source)
        self.assertEqual(long_module(120).count("\n"), 120)
        self.assertEqual(wide_class(CEILINGS["class-methods"] + 1).count("def "), 26)

    def test_S1_a_tree_under_its_ceilings_exits_zero_and_names_them(self):
        """structure-budget/S1"""
        tree = self.tree({"pkg/mod.py": module(["one", "two"])})
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        for metric, ceiling in CEILINGS.items():
            self.assertIn(metric, done.stdout)
            self.assertIn(str(ceiling), done.stdout)

    def test_S3_a_new_file_over_a_ceiling_fails_naming_the_ceiling(self):
        """structure-budget/S3"""
        tree = self.tree({"pkg/mod.py": module(["one"])}, {"pkg/wide.py": wide_class(26)})
        done = tree.run()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(lines(done), ["pkg/wide.py: class-methods 26 > ceiling 25"])

    def test_S15_a_file_that_crossed_a_ceiling_since_the_base_fails(self):
        """structure-budget/S15"""
        tree = self.tree(
            {"pkg/big.py": long_module(900)},
            {"pkg/big.py": long_module(1100)},
        )
        done = tree.run()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertEqual(lines(done), ["pkg/big.py: module-lines 1100 > ceiling 1000"])

    def test_S2_an_offender_that_grows_past_its_recorded_number_fails_alone(self):
        """structure-budget/S2"""
        tree = self.tree(
            {"pkg/big.py": long_module(1150), "pkg/fine.py": long_module(120)},
            {"pkg/big.py": long_module(1250), "pkg/fine.py": long_module(120)},
        )
        done = tree.run()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        printed = lines(done)
        self.assertEqual(len(printed), 1, "one growth prints one line: %r" % printed)
        self.assertEqual(printed[0], "pkg/big.py: module-lines 1250 > recorded 1150 (ceiling 1000)")
        self.assertNotIn("fine.py", done.stdout, "a file that did not worsen is not named")

    def test_S16_an_offender_that_shrinks_passes_and_that_is_the_ratchet(self):
        """structure-budget/S16"""
        tree = self.tree(
            {"pkg/big.py": long_module(1250)},
            {"pkg/big.py": long_module(1150)},
        )
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(lines(done), [])

    def test_S17_growth_under_a_ceiling_is_free_and_stores_nothing(self):
        """structure-budget/S17"""
        tree = self.tree({"pkg/mod.py": long_module(120)}, {"pkg/mod.py": long_module(400)})
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(sorted(tree.baseline_bytes()), ["ceilings", "measured_at"])

    def test_S6_a_paid_path_may_not_carry_an_offender_at_all(self):
        """structure-budget/S6"""
        tree = self.tree(
            {"pkg/big.py": long_module(1150)},
            {"pkg/big.py": long_module(1150)},
        )
        self.assertEqual(tree.run().returncode, 0, "grandfathered at base and unchanged holds")
        paid = tree.run("--paid", "pkg/", "--metric", "module-lines")
        self.assertEqual(paid.returncode, 1, paid.stdout + paid.stderr)
        self.assertEqual(lines(paid), ["pkg/big.py: module-lines 1150 > paid (ceiling 1000)"])
        unpaid = tree.run("--paid", "pkg/", "--metric", "class-methods")
        self.assertEqual(unpaid.returncode, 0, "--metric leaves a metric it did not name alone")

    def test_S19_a_moved_file_keeps_the_ceiling_it_had_at_the_base(self):
        """structure-budget/S19"""
        tree = self.tree({"big.py": long_module(1150)})
        tree.git("mv", "big.py", "moved.py")
        self.assertIn("R100", tree.git("diff", "-M", "--name-status", "HEAD"))
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(lines(done), [])

    def test_S6_a_paid_path_that_measures_nothing_is_refused(self):
        """structure-budget/S6"""
        tree = self.tree({"pkg/big.py": long_module(1150)})
        done = tree.run("--paid", "pkq/", "--metric", "module-lines")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("pkq/", done.stderr)

    def test_S8_a_test_module_is_measured_like_source(self):
        """structure-budget/S8"""
        tree = self.tree(
            {"tests/test_small.py": module(["one"])},
            {"tests/test_big.py": long_module(1100)},
        )
        done = tree.run()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("tests/test_big.py", done.stdout)

    def test_the_walk_skips_a_virtualenv_and_a_cache(self):
        """structure-budget/S1"""
        tree = self.tree(
            {"pkg/mod.py": module(["one"])},
            {
                ".venv/lib/huge.py": long_module(1100),
                "node_modules/huge.py": long_module(1100),
                "__pycache__/huge.py": long_module(1100),
            },
        )
        done = tree.run()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_a_since_that_is_not_a_revision_is_refused(self):
        """structure-budget/S15"""
        tree = self.tree({"pkg/mod.py": module(["one"])})
        done = tree.run("--since", "no-such-rev")
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertIn("no-such-rev", done.stderr)
