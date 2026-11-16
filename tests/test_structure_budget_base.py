"""The baseline is where a change grew from, not where the base branch happens to point.

`--since origin/main` names the base branch's head, and a head moves with every merge into it. A
branch that was merely *behind* was therefore measured against a tree it never grew from: the gate
reported growth the branch did not make, named the pull request, and read exactly like a real
regression. It cost four rebases in one night -- three agents and one of the centurion's own branches.

The revision a change grew from is the merge base, and git already knows it. These cases drive a real
checkout, because the defect lives in the shape of the history rather than in the measuring.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "structure_budget.py"


def module_of(lines, name="padding"):
    """A module of exactly `lines` source lines: short functions, so only its size trips a ceiling."""
    body = []
    number = 0
    while len(body) + 3 <= lines:
        body.extend(["def f%d():" % number, "    return 0", ""])
        number += 1
    body.extend("# %s %d" % (name, index) for index in range(lines - len(body)))
    return "\n".join(body) + "\n"


class BehindBranchCase(unittest.TestCase):
    """A branch cut from an old base, on a main that has since paid some of the same file down."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.repo = Path(self._dir.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.repo / "acceptance").mkdir()
        (self.repo / "acceptance" / "budget.json").write_text(
            json.dumps({"ceilings": {"module-lines": 1000}}), encoding="utf-8"
        )
        self.write("big.py", module_of(1500))
        self.write("other.py", module_of(10))
        self.commit("the base a branch cuts from")
        self.cut = self.sha()

        # main moves on and pays 300 lines of the same file down, which is what any split does.
        self.write("big.py", module_of(1200))
        self.commit("main pays big.py down")
        self.tip = self.sha()

    def tearDown(self):
        self._dir.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True
        )

    def sha(self, rev="HEAD"):
        return self.git("rev-parse", rev).stdout.strip()

    def write(self, name, text):
        (self.repo / name).write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def run_gate(self, since):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--root",
                str(self.repo),
                "--baseline",
                str(self.repo / "acceptance" / "budget.json"),
                "--since",
                since,
            ],
            capture_output=True,
            text=True,
            cwd=self.repo,
        )

    def branch_behind_main(self):
        """A branch cut from the old base that changes something else and leaves big.py alone."""
        self.git("checkout", "-q", "-b", "feat", self.cut)
        self.write("other.py", module_of(12))
        self.commit("a change that does not touch the big module")


class MergeBaseTests(BehindBranchCase):
    def test_a_branch_behind_main_is_not_blamed_for_what_main_paid_down(self):
        self.branch_behind_main()
        done = self.run_gate("main")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)

    def test_the_base_it_reports_is_where_the_branch_grew_from(self):
        self.branch_behind_main()
        done = self.run_gate("main")
        base = (done.stdout + done.stderr).split("base ", 1)[1].split(" ", 1)[0]
        self.assertEqual(base, self.cut)
        self.assertNotEqual(base, self.tip, "the base branch's head is not the baseline")

    def test_a_branch_that_really_grows_the_module_still_fails(self):
        self.branch_behind_main()
        self.write("big.py", module_of(1501))
        self.commit("one line over the file it inherited")
        done = self.run_gate("main")
        self.assertEqual(done.returncode, 1, done.stderr + done.stdout)
        self.assertIn("module-lines 1501 > recorded 1500", done.stdout + done.stderr)

    def test_a_base_commit_that_is_already_an_ancestor_is_its_own_baseline(self):
        # What CI passes: a pull request's base commit, and the tip a push replaced.
        self.branch_behind_main()
        self.write("big.py", module_of(1501))
        self.commit("one line over")
        done = self.run_gate(self.cut)
        self.assertEqual(done.returncode, 1)
        self.assertIn("module-lines 1501 > recorded 1500", done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
