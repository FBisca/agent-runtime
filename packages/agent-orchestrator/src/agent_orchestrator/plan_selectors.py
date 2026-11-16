"""What a check's `-k` selector selects, read statically from an acceptance file.

No import and no execution: the tests pytest would match are read from the syntax tree, so the lint
can refuse a selector that runs another scenario's tests as well as its own.
"""

import ast
import re
import shlex
from pathlib import Path


def control_test(function):
    """Whether a test is a control: `control` is one of its words, so both `test_control_x` and the per-scenario
    `test_S1_control_x` a plan writes to guard S1's fixture are controls, and neither carries a scenario's proof."""
    return "control" in function.split("_")


def pytest_selector(check):
    """The `-k` expression of a check that runs pytest, or None when the check runs no pytest, carries no `-k`, or
    cannot be split as a shell line: those checks are not linted. The last `-k` wins, as pytest's own parser does."""
    try:
        words = shlex.split(check)
    except ValueError:
        return None
    if not any(Path(word).name in ("pytest", "py.test") for word in words):
        return None
    selector = None
    for i, word in enumerate(words):
        if word == "-k" and i + 1 < len(words):
            selector = words[i + 1]
        elif word.startswith("-k") and len(word) > 2:
            selector = word[2:]
    return selector


def selector_terms(expression):
    """The terms of a `-k` expression made of words joined by `or`, or None for any other expression.

    pytest matches each term as a case-insensitive substring of a test's names. Not modelled, so an expression that
    uses them is not linted: `and`, `not`, parentheses, matching against directory names, markers or the ids of
    parametrized tests, and the files the check names outside its acceptance files."""
    words = expression.split()
    if not words or len(words) % 2 == 0 or any(op != "or" for op in words[1::2]):
        return None
    terms = words[0::2]
    if any(term in ("and", "not", "or") or "(" in term or ")" in term for term in terms):
        return None
    return terms


def named_scenarios(term):
    """The scenario ids a selector term spells out as a word: `S1`, `test_S1_` and `s1` all name S1."""
    found = set()
    for word in re.split(r"[^A-Za-z0-9]+", term):
        if re.fullmatch(r"[Ss]\d+[A-Za-z]?", word):
            found.add("S" + word[1:].lower())
    return found


def collected_tests(source, module):
    """[(names pytest matches `-k` against, function name)] for the tests of a module, read statically: no import,
    no execution. A test is a `test_*` function at module level, or a `test_*` method of a class named `Test*` or of
    a class with a base (a unittest.TestCase subclass, which pytest collects whatever its name)."""
    tests = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test_"
        ):
            tests.append(((module, node.name), node.name))
        elif isinstance(node, ast.ClassDef) and (node.name.startswith("Test") or node.bases):
            for item in node.body:
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and item.name.startswith("test_"):
                    tests.append(((module, node.name, item.name), item.name))
    return tests
