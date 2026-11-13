"""The default selection in tests/conftest.py: the fast suite unless the command line asks for more."""

import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

HERE = Path(__file__).resolve().parent
modify_items = runpy.run_path(str(HERE / "conftest.py"))["pytest_collection_modifyitems"]


class Item:
    def __init__(self, name, marker):
        self.name = name
        self.marker = marker

    def get_closest_marker(self, name):
        return self.marker if self.marker == name else None


def select(args, file_or_dir=(), markexpr="", numprocesses=None, durations=None):
    deselected = []
    options = {"markexpr": markexpr, "numprocesses": numprocesses, "durations": durations}
    config = SimpleNamespace(
        getoption=lambda name, default=None: options.get(name, default),
        invocation_params=SimpleNamespace(args=tuple(args)),
        option=SimpleNamespace(file_or_dir=list(file_or_dir)),
        hook=SimpleNamespace(pytest_deselected=lambda items: deselected.extend(items)),
    )
    items = [Item("fast", "unit"), Item("slow", "integration")]
    modify_items(config, items)
    return [item.name for item in items]


class DefaultSelectionTests(unittest.TestCase):
    def test_R100_the_bare_run_is_the_unit_suite(self):
        """R100: `uv run pytest` alone keeps the integration tests out."""
        self.assertEqual(select(["-q"]), ["fast"])

    def test_R100_a_plugin_option_value_is_not_a_path(self):
        """R100: `-p no:warnings` and `-p no:cacheprovider` still run only the unit suite."""
        self.assertEqual(select(["-q", "-p", "no:warnings"]), ["fast"])
        self.assertEqual(select(["-p", "no:cacheprovider", "--impacted"]), ["fast"])

    def test_R100_a_separate_option_value_is_not_a_path(self):
        """R100: an option value given as its own argument does not widen the run."""
        self.assertEqual(select(["--impacted", "--impacted-base-branch", "origin/main"]), ["fast"])

    def test_R100_a_named_path_runs_what_it_names(self):
        """R100: a path or node id on the command line keeps every test it collects."""
        self.assertEqual(
            select(["tests/test_status.py"], file_or_dir=["tests/test_status.py"]),
            ["fast", "slow"],
        )

    def test_R100_parallelism_and_a_marker_expression_keep_their_meaning(self):
        """R100: `-n` runs the whole suite; an explicit `-m` wins."""
        self.assertEqual(select(["-n", "4"], numprocesses=4), ["fast", "slow"])
        self.assertEqual(select(["-m", "integration"], markexpr="integration"), ["fast", "slow"])
