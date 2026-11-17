"""The current UTC timestamp has one home, and every module that reads one keeps its type.

Each module of the package used to carry its own copy, three returning an ISO string and one a
datetime. The copy is the defect: two copies drift, and a call site's type is part of its contract.
"""

import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pytest
from agent_orchestrator import driver, events, timeutil, workbench

pytestmark = pytest.mark.unit

PACKAGE = Path(timeutil.__file__).resolve().parent


def modules_defining_now():
    """Every module of the package that defines its own `now`, by file name."""
    names = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "now":
                names.append(path.name)
    return names


class TimestampDefinitionTests(unittest.TestCase):
    def test_s12_the_utc_timestamp_is_defined_once_and_each_call_site_keeps_its_type(self):
        """structure-budget/S12"""
        moment = timeutil.now()
        self.assertIsInstance(moment, datetime)
        self.assertEqual(moment.tzinfo, timezone.utc)
        self.assertIsInstance(timeutil.iso(), str)
        self.assertTrue(timeutil.iso().endswith("+00:00"))

        # The modules that returned an ISO string still return one from the same definition...
        self.assertIs(driver.now, timeutil.iso)
        self.assertIs(events.now, timeutil.iso)
        # ...and the module that returned a datetime still returns one.
        self.assertIs(workbench.now, timeutil.now)

        # The definition itself exists once.
        self.assertEqual(modules_defining_now(), ["timeutil.py"])
