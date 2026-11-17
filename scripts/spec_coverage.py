#!/usr/bin/env python3
"""Every `### Requirement: <title>` in docs/specs/*.md has a tagged test; every tag names a title.

A tag is the pytest mark `spec("<title>")`, a Vitest `describe("spec: <title>")` or a `// spec: <title>`
(or `#`) comment, in a .py, .ts, .js or .rs file under a `tests` or `test` directory."""

import argparse
import os
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^### Requirement: (.+?)\s*$", re.M)
TAGS = (
    re.compile(r"\bspec\(\s*\"([^\"]+)\"\s*\)"),
    re.compile(r"\bdescribe\(\s*\"spec: ([^\"]+)\""),
    re.compile(r"(?://|#)\s*spec: (.+?)\s*$", re.M),
)
SUFFIXES = {".py", ".ts", ".js", ".rs"}
SKIP = {".git", "node_modules", ".venv"}


def test_files(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP]
        if {"tests", "test"} & set(Path(folder).relative_to(root).parts):
            yield from (Path(folder, f) for f in files if Path(f).suffix in SUFFIXES)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path.cwd())
    root = parser.parse_args().root.resolve()
    titles = {}
    for spec in sorted((root / "docs" / "specs").glob("*.md")):
        for title in HEADING.findall(spec.read_text(encoding="utf-8")):
            titles.setdefault(title, spec.relative_to(root))
    tags = {}
    for path in test_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in TAGS:
            for title in pattern.findall(text):
                tags.setdefault(title, path.relative_to(root))
    problems = [
        f'{spec}: requirement "{t}" has no tagged test — tag a test that proves it with spec("{t}")'
        for t, spec in titles.items()
        if t not in tags
    ] + [
        f'{path}: tag "{t}" names no requirement in docs/specs — fix the tag to an existing title, '
        "or add the requirement"
        for t, path in sorted(tags.items())
        if t not in titles
    ]
    print("\n".join(problems) or f"{len(titles)} requirements, each with a tagged test")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
