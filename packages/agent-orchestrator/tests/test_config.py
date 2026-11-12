"""The repo config: `<repo>/.orchestrate/config.yaml` and its defaults."""

import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import config

pytestmark = pytest.mark.unit


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def write(self, text):
        path = self.root / ".orchestrate" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_S1_missing_file_means_the_defaults(self):
        loaded = config.load(self.root)
        self.assertEqual(loaded.plans_dir, self.root / "docs" / "plans")
        self.assertEqual(loaded.acceptance_dir, "acceptance")
        self.assertEqual(loaded.archive_mode, "promote")
        self.assertFalse((self.root / ".orchestrate").exists())

    def test_S1_relative_path_is_under_the_repo_root(self):
        self.write(
            "plans:\n  dir: work/plans\nacceptance:\n  dir: accept\narchive:\n  mode: discard\n"
        )
        loaded = config.load(self.root)
        self.assertEqual(loaded.plans_dir, self.root / "work" / "plans")
        self.assertEqual(loaded.acceptance_dir, "accept")
        self.assertEqual(loaded.archive_mode, "discard")

    def test_S1_tilde_and_absolute_paths_are_allowed(self):
        self.write("plans:\n  dir: ~/plans\n")
        self.assertEqual(config.load(self.root).plans_dir, Path("~/plans").expanduser())
        self.write("plans:\n  dir: /somewhere/else\n")
        self.assertEqual(config.load(self.root).plans_dir, Path("/somewhere/else"))

    def test_S1_unknown_key_is_refused_naming_its_dotted_path_and_the_fix(self):
        for text, dotted in (
            ("colour: red\n", "colour"),
            ("plans:\n  dir: p\n  mode: x\n", "plans.mode"),
            ("archive:\n  dir: x\n", "archive.dir"),
        ):
            self.write(text)
            with self.assertRaises(config.ConfigError) as caught:
                config.load(self.root)
            self.assertIn(dotted, str(caught.exception))
            self.assertIn("remove", str(caught.exception))

    def test_S1_bad_mode_is_refused_naming_its_path_and_the_choices(self):
        self.write("archive:\n  mode: keep\n")
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.root)
        self.assertIn("archive.mode", str(caught.exception))
        self.assertIn("promote", str(caught.exception))
        self.assertIn("discard", str(caught.exception))

    def test_S1_a_node_of_the_wrong_shape_is_refused(self):
        self.write("plans: docs\n")
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.root)
        self.assertIn("plans", str(caught.exception))
