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

    def test_S1_checks_node_reads_each_optional_key(self):
        self.write(
            "checks:\n  fast: pytest -q\n  affected: scripts/affected.sh {base} {paths}\n"
            "  full:\n    run: scripts/full.sh\n"
        )
        checks = config.load(self.root).checks
        self.assertEqual(checks.fast, "pytest -q")
        self.assertEqual(checks.affected, "scripts/affected.sh {base} {paths}")
        self.assertEqual(checks.full_run, "scripts/full.sh")
        self.assertTrue(checks.full_verify)

    def test_S1_checks_full_verify_false_is_read(self):
        self.write("checks:\n  full:\n    run: make all\n    verify: false\n")
        self.assertFalse(config.load(self.root).checks.full_verify)

    def test_S1_checks_node_is_absent_by_default_and_each_key_is_optional(self):
        self.assertIsNone(config.load(self.root).checks)
        self.write("checks: {}\n")
        checks = config.load(self.root).checks
        self.assertIsNone(checks.fast)
        self.assertIsNone(checks.affected)
        self.assertIsNone(checks.full_run)
        self.assertTrue(checks.full_verify)

    def test_S1_unknown_checks_key_is_refused_naming_its_dotted_path(self):
        for text, dotted in (
            ("checks:\n  slow: x\n", "checks.slow"),
            ("checks:\n  full:\n    cmd: x\n", "checks.full.cmd"),
        ):
            self.write(text)
            with self.assertRaises(config.ConfigError) as caught:
                config.load(self.root)
            self.assertIn(dotted, str(caught.exception))
            self.assertIn("remove", str(caught.exception))

    def test_S1_affected_placeholder_other_than_base_or_paths_is_refused(self):
        self.write("checks:\n  affected: run {paths} {ticket}\n")
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.root)
        self.assertIn("checks.affected", str(caught.exception))
        self.assertIn("{ticket}", str(caught.exception))
        self.assertIn("{base}", str(caught.exception))
        self.assertIn("{paths}", str(caught.exception))

    def test_S1_checks_values_of_the_wrong_type_are_refused_by_path(self):
        for text, dotted in (
            ("checks:\n  fast: [pytest]\n", "checks.fast"),
            ("checks:\n  full: make\n", "checks.full"),
            ("checks:\n  full:\n    run: 3\n", "checks.full.run"),
            ("checks:\n  full:\n    run: make\n    verify: maybe\n", "checks.full.verify"),
        ):
            self.write(text)
            with self.assertRaises(config.ConfigError) as caught:
                config.load(self.root)
            self.assertIn(dotted, str(caught.exception))


class TestPatternTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def write(self, text):
        path = self.root / ".orchestrate" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_S2_default_patterns_decide_a_test_file_by_name_or_tests_directory(self):
        from agent_orchestrator import plan

        patterns = config.load(self.root).test_patterns
        for path in (
            "a/test_x.py",
            "a/x_test.go",
            "a/x.test.ts",
            "a/x.spec.ts",
            "pkg/tests/helper.py",
            "tests/data/fixture.json",
        ):
            with self.subTest(path=path):
                self.assertTrue(plan.is_test_path(path, patterns))
        for path in ("src/feature.py", "pyproject.toml", ".orchestrate/config.yaml", "src/attests.py"):
            with self.subTest(path=path):
                self.assertFalse(plan.is_test_path(path, patterns))

    def test_S2_config_replaces_the_patterns(self):
        from agent_orchestrator import plan

        self.write("tests:\n  patterns: ['check_*.sh']\n")
        patterns = config.load(self.root).test_patterns
        self.assertTrue(plan.is_test_path("ci/check_it.sh", patterns))
        self.assertFalse(plan.is_test_path("a/test_x.py", patterns))
        # a directory named tests still holds tests whatever the patterns say
        self.assertTrue(plan.is_test_path("tests/x.sh", patterns))

    def test_S2_a_non_list_or_empty_value_is_refused_naming_its_dotted_path(self):
        for body in ("patterns: x", "patterns: []", "patterns: [1]", "patterns: ['']"):
            with self.subTest(body=body):
                self.write("tests:\n  " + body + "\n")
                with self.assertRaises(config.ConfigError) as caught:
                    config.load(self.root)
                self.assertIn("tests.patterns", str(caught.exception))
