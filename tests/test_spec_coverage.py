"""scripts/spec_coverage.py holds every spec requirement title to a tagged test, and every tag to a title."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_coverage.py"
SPEC = "# Thing\n\n## Requirements\n\n### Requirement: Widgets spin\nIt SHALL spin.\n"
# The fixtures' tags are assembled, so this file's own source carries no tag the real run would read.
TAG = "spec"


def mark(title):
    return '@pytest.mark.%s("%s")\n' % (TAG, title)


def run(root):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.spec("Spec coverage")
class SpecCoverageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "docs" / "specs").mkdir(parents=True)
        (self.root / "docs" / "specs" / "thing.md").write_text(SPEC)
        (self.root / "tests").mkdir()

    def write_test(self, name, text):
        (self.root / "tests" / name).write_text(text)

    def test_a_title_no_test_tags_fails_naming_it_and_the_way_out(self):
        self.write_test("test_other.py", "def test_x():\n    pass\n")
        result = run(self.root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('"Widgets spin" has no tagged test', result.stdout)
        self.assertIn('with %s("Widgets spin")' % TAG, result.stdout)

    def test_a_tag_naming_no_title_fails_naming_it_and_the_way_out(self):
        self.write_test(
            "test_spin.py", mark("Widgets spin") + mark("Widgets fly") + "def test_x():\n    pass\n"
        )
        result = run(self.root)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('tag "Widgets fly" names no requirement', result.stdout)
        self.assertIn("add the requirement", result.stdout)
        self.assertNotIn("no tagged test", result.stdout)

    def test_matching_titles_and_tags_pass_in_every_tag_form(self):
        more = "\n### Requirement: Widgets fly\nIt SHALL fly.\n\n### Requirement: Widgets land\nIt SHALL land.\n"
        (self.root / "docs" / "specs" / "thing.md").write_text(SPEC + more)
        self.write_test("test_spin.py", mark("Widgets spin") + "def test_x():\n    pass\n")
        self.write_test("fly.test.ts", 'describe("%s: Widgets fly", () => {});\n' % TAG)
        self.write_test("land.rs", "// %s: Widgets land\nfn t() {}\n" % TAG)
        (self.root / "node_modules" / "tests").mkdir(parents=True)
        (self.root / "node_modules" / "tests" / "x.js").write_text("// %s: Nowhere\n" % TAG)
        result = run(self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
