"""Every test a living spec cites still exists (test-cost/S2), and every test module carries its marker (S4)."""

import functools
import re
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
CODE = re.compile(r"`([^`]+)`")
NAME = re.compile(r"test_\w+")
DOTTED = re.compile(r"([A-Z]\w*)\.(test_\w+)")
MARK = re.compile(r"pytest\.mark\.(unit|integration)\b")
PYTESTMARK = re.compile(r"^\s*pytestmark\s*=\s*.*pytest\.mark\.(unit|integration)", re.M)


def suite_files():
    for top in ("tests", "acceptance", "packages"):
        yield from (path for path in (ROOT / top).rglob("test_*.py") if ".venv" not in path.parts)


CLASS = re.compile(r"^(\s*)class (\w+)\b")
DEF = re.compile(r"^(\s*)(?:async\s+)?def (\w+)\b")


@functools.cache
def scan():
    """(index, unmarked): one read of every test file answers both fast-suite checks.

    A line scan, not an AST: the index covers every test file in the repository and parsing them
    whole cost most of the fast suite. The names a spec cites are on `def`/`class` lines, and the
    level is the module's one `pytestmark` line."""
    found, unmarked = {}, []
    for path in suite_files():
        text = path.read_text(encoding="utf-8")
        names = set()
        classes = []  # (indent, name), innermost last: a nested class does not hide its outer methods
        for line in text.splitlines():
            declared = CLASS.match(line)
            if declared:
                indent, name = len(declared.group(1)), declared.group(2)
                classes = [entry for entry in classes if entry[0] < indent] + [(indent, name)]
                names.add(name)
                continue
            defined = DEF.match(line)
            if defined:
                indent, name = len(defined.group(1)), defined.group(2)
                names.add(name)
                owner = next((entry for entry in reversed(classes) if entry[0] < indent), None)
                if owner is not None:
                    names.add(f"{owner[1]}::{name}")
                continue
            if line.strip() and not line.startswith((" ", "\t")):
                classes = []  # a new top-level statement ends every class body
        key = path.relative_to(ROOT).as_posix()
        found[key] = names
        if not PYTESTMARK.search(text):
            unmarked.append(key)
    return found, unmarked


def index():
    return scan()[0]


def unmarked_modules():
    return scan()[1]


def defined_anywhere(name):
    """A test, a class or a test module's stem (a spec may name a whole module)."""
    return any(name in names or Path(path).stem == name for path, names in index().items())


def in_another_repo(span):
    """A path whose first directory is not in this checkout names a sibling repository (`agent-skills/...`)."""
    return "/" in span and not (ROOT / span.split("/", 1)[0]).exists()


def stale_citations():
    """Each citation, by spec and line, that names a file or a test that does not exist.

    A backticked `path.py` sets the file the bare names after it on the same line belong to; `path::Class::name`
    names its own file; a bare name with no file before it may live in any suite. Elided names (`test_S1_…`),
    templates (`<domain>`) and spans with spaces (commands) are skipped, and so are citations of another repository's tests.
    """
    stale = []
    for spec in sorted((ROOT / "docs" / "specs").glob("*.md")):
        for number, line in enumerate(spec.read_text(encoding="utf-8").splitlines(), 1):
            current = None
            for span in CODE.findall(line):
                if (
                    "…" in span
                    or "..." in span
                    or " " in span
                    or "<" in span
                    or in_another_repo(span)
                ):
                    continue
                where = f"{spec.name}:{number}"
                span = re.sub(r"\[.*\]$", "", span)
                if "::" in span:
                    path, _, rest = span.partition("::")
                    if not rest:  # `path::` alone is the form of a node id, naming no test
                        continue
                    if path.endswith(".py") and "/" in path:
                        if path not in index():
                            stale.append(f"{where} {path}")
                        elif rest not in index()[path]:
                            stale.append(f"{where} {span}")
                        continue
                    if not defined_anywhere(span):
                        stale.append(f"{where} {span}")
                    continue
                if span.endswith(".py") and "/" in span and Path(span).name.startswith("test_"):
                    current = span
                    if span not in index():
                        stale.append(f"{where} {span}")
                    continue
                dotted = DOTTED.fullmatch(span)
                name = (
                    f"{dotted[1]}::{dotted[2]}"
                    if dotted
                    else span
                    if NAME.fullmatch(span)
                    else None
                )
                if name is None:
                    continue
                known = index().get(current) if current else None
                if not (name in known if known is not None else defined_anywhere(name)):
                    stale.append(f"{where} {span}")
    return stale


class SpecCitationTests(unittest.TestCase):
    def test_control_the_suites_define_over_a_thousand_tests(self):
        self.assertGreater(
            sum(1 for names in index().values() for name in names if "::test_" in name), 1000
        )

    def test_S2_every_test_a_spec_cites_exists(self):
        self.assertEqual(
            stale_citations(),
            [],
            "spec rows cite tests that no longer exist; name the test that keeps each guarantee",
        )

    def test_S4_every_test_module_carries_a_marker(self):
        self.assertEqual(
            unmarked_modules(),
            [],
            "each test module sets pytestmark to pytest.mark.unit or pytest.mark.integration",
        )
