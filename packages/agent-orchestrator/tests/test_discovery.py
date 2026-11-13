"""Plan discovery: `plans list --json`, and plans kept outside the repository via `--repo`."""

import argparse
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script
from test_plan import contract

pytestmark = pytest.mark.integration


class DiscoveryFixture(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.env_patch = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead-test"}
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        # The fixture's Markdown plan is removed: each test lays out its own plan directories.
        self.plan.unlink()
        self.plan.parent.rmdir()
        self.plan.parent.parent.rmdir()

    def track(self, relative, text="# plan\n"):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git(self.repo, "add", relative)
        return path

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def listing(self, *extra):
        code, out, err = self.run_cli("plans", "list", "--json", "--repo", str(self.repo), *extra)
        self.assertEqual(code, 0, err)
        return [entry["path"] for entry in json.loads(out)]


class DiscoveryTests(DiscoveryFixture):
    def test_S5_one_directory_lists_its_plans_and_the_explicit_plan(self):
        docs = self.track("docs/plans/2026-01-01-a.plan.yaml")
        self.track("docs/plans/2026-01-02-b.plan.md")
        self.track("docs/plans/2026-01-03-c.oracle/test_x.py.oracle")
        self.assertEqual(
            self.listing(str(docs)),
            [
                str(docs.resolve()),
                str((self.repo / "docs/plans/2026-01-02-b.plan.md").resolve()),
            ],
        )

    def test_S5_hidden_directory_is_discovered_without_an_explicit_path(self):
        self.track(".plans/2026-01-01-a.plan.yaml")
        self.assertEqual(
            self.listing(),
            [str((self.repo / ".plans/2026-01-01-a.plan.yaml").resolve())],
        )

    def test_S5_two_directories_refuse_naming_both_and_the_explicit_path_way_out(self):
        self.track("docs/plans/2026-01-01-a.plan.yaml")
        self.track(".plans/2026-01-02-b.plan.yaml")
        code, out, err = self.run_cli("plans", "list", "--json", "--repo", str(self.repo))
        self.assertEqual(code, 2)
        self.assertIn("docs/plans", err)
        self.assertIn(".plans", err)
        self.assertIn("plans list --json <plan", err)
        self.assertEqual(out, "")

    def test_S5_two_directories_with_an_explicit_path_list_that_directory(self):
        docs = self.track("docs/plans/2026-01-01-a.plan.yaml")
        self.track(".plans/2026-01-02-b.plan.yaml")
        self.assertEqual(self.listing(str(docs)), [str(docs.resolve())])

    def test_S5_two_directories_with_a_repo_config_naming_one_list_that_directory(self):
        docs = self.track("docs/plans/2026-01-01-a.plan.yaml")
        self.track("tests/data/reference.plan.yaml")
        self.track(".orchestrate/config.yaml", "plans:\n  dir: docs/plans\n")
        self.assertEqual(self.listing(), [str(docs.resolve())])

    def test_S5_two_directories_with_a_repo_config_naming_neither_still_refuse(self):
        self.track("docs/plans/2026-01-01-a.plan.yaml")
        self.track(".plans/2026-01-02-b.plan.yaml")
        self.track(".orchestrate/config.yaml", "plans:\n  dir: work\n")
        code, out, err = self.run_cli("plans", "list", "--json", "--repo", str(self.repo))
        self.assertEqual(code, 2)
        self.assertIn("docs/plans", err)
        self.assertIn(".plans", err)
        self.assertEqual(out, "")

    def test_S5_without_repo_the_current_directory_is_the_repository(self):
        plan = self.track("docs/plans/2026-01-01-a.plan.yaml")
        cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, cwd)
        code, out, err = self.run_cli("plans", "list", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([entry["path"] for entry in json.loads(out)], [str(plan.resolve())])

    def test_S5_no_plans_is_an_empty_list_and_creates_nothing(self):
        self.track("README.md", "x\n")
        self.assertEqual(self.listing(), [])
        self.assertFalse((self.repo / "docs" / "plans").exists())
        self.assertFalse((self.repo / ".plans").exists())
        self.assertFalse((self.repo / ".orchestrate").exists())


class ConfiguredDiscoveryTests(DiscoveryFixture):
    """S2: folder plans from the configured dir, beside the legacy listing."""

    def config(self, text):
        path = self.repo / ".orchestrate" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def folder(self, base, name, slug=None):
        plan = base / name / "plan.yaml"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(f"slug: {slug}\n" if slug else "goal: x\n", encoding="utf-8")
        return plan

    def entries(self, *extra):
        code, out, err = self.run_cli("plans", "list", "--json", "--repo", str(self.repo), *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_S2_a_plans_dir_inside_the_repository_untracked_lists_its_folders(self):
        self.config("plans:\n  dir: work\n")
        plan = self.folder(self.repo / "work", "2026-01-01-a", slug="a")
        legacy = self.track("docs/plans/2026-01-02-b.plan.yaml")
        found = {e["path"]: e for e in self.entries()}
        self.assertEqual(found[str(plan.resolve())]["slug"], "a")
        self.assertEqual(found[str(plan.resolve())]["format"], "yaml")
        self.assertIn(str(legacy.resolve()), found)

    def test_S2_a_plans_dir_outside_the_repository_lists_its_folders(self):
        outside = self.root / "elsewhere"
        self.config(f"plans:\n  dir: {outside}\n")
        plan = self.folder(outside, "2026-01-01-a")
        self.folder(outside, "2026-01-02-b", slug="bee")
        (outside / "loose.txt").write_text("x", encoding="utf-8")
        (outside / "no-plan").mkdir()
        found = {e["path"]: e["slug"] for e in self.entries()}
        self.assertEqual(
            found,
            {
                str(plan.resolve()): "2026-01-01-a",
                str((outside / "2026-01-02-b" / "plan.yaml").resolve()): "bee",
            },
        )

    def test_S2_an_unknown_config_key_fails_the_command_naming_it(self):
        self.config("plans:\n  dir: work\n  colour: red\n")
        code, out, err = self.run_cli("plans", "list", "--json", "--repo", str(self.repo))
        self.assertEqual(code, 2)
        self.assertIn("plans.colour", err)
        self.assertEqual(out, "")

    def test_S2_listing_creates_no_directory_or_file(self):
        self.config("plans:\n  dir: nowhere/plans\n")
        self.assertEqual(self.entries(), [])
        self.assertFalse((self.repo / "nowhere").exists())

    def test_S2_a_missing_config_lists_docs_plans_folders(self):
        plan = self.folder(self.repo / "docs" / "plans", "2026-01-01-a")
        self.assertEqual([e["path"] for e in self.entries()], [str(plan.resolve())])


class ConfigReadByEveryCommandTests(DiscoveryFixture):
    """S1: every command that names a plan reads the repo config first."""

    def bad(self, text):
        path = self.repo / ".orchestrate" / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_S1_each_plan_command_refuses_a_bad_config_naming_its_dotted_path(self):
        plan = self.repo / "x.plan.yaml"
        plan.write_text("slug: x\n", encoding="utf-8")  # never parsed: the config refuses first
        state = str(self.root / "state")
        for text, dotted in (
            ("plans:\n  colour: red\n", "plans.colour"),
            ("archive:\n  mode: keep\n", "archive.mode"),
        ):
            self.bad(text)
            for verb in (
                ("plan", "check"),
                ("plan", "approve"),
                ("status",),
                ("verify",),
                ("run",),
            ):
                with self.subTest(config=dotted, verb=verb):
                    argv = (
                        self.driver_args("--repo", str(self.repo), plan=plan, routed=False)
                        if verb == ("run",)
                        else [*verb, str(plan), "--repo", str(self.repo), "--state-dir", state]
                    )
                    code, out, err = self.run_cli(*argv)
                    self.assertEqual(code, 2, err)
                    self.assertIn(dotted, err)
                    self.assertEqual(out, "")

    def test_S1_plan_schema_needs_no_repo_config(self):
        self.bad("colour: red\n")
        code, _, err = self.run_cli("plan", "schema")
        self.assertEqual(code, 0, err)


class ExternalPlanTests(DiscoveryFixture):
    """S6: a plan outside the repository is checked, approved and run with `--repo`."""

    def setUp(self):
        super().setUp()
        self.outside = self.root / "contracts" / "demo.plan.yaml"
        self.outside.parent.mkdir()
        self.outside.write_text(
            "# kept outside\n" + yaml.safe_dump(contract(), sort_keys=False), encoding="utf-8"
        )
        self.oracles = self.outside.with_name("demo.oracle")
        self.oracles.mkdir()
        self.state = self.root / "state"

    def test_S6_check_and_approve_work_outside_the_repository(self):
        before = sorted(p.name for p in self.outside.parent.iterdir())
        code, _, err = self.run_cli(
            "plan",
            "check",
            str(self.outside),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state),
        )
        self.assertEqual(code, 0, err)
        original = self.outside.read_bytes()
        code, _, err = self.run_cli(
            "plan",
            "approve",
            str(self.outside),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state),
        )
        self.assertEqual(code, 0, err)
        written = self.outside.read_bytes()
        self.assertTrue(written.startswith(original))
        self.assertIn(b"approval:", written)
        self.assertEqual(sorted(p.name for p in self.outside.parent.iterdir()), before)
        self.assertFalse((self.repo / "docs" / "plans").exists())
        code, _, err = self.run_cli(
            "plan",
            "check",
            str(self.outside),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state),
        )
        self.assertEqual(code, 0, err)

    def test_S6_run_reads_the_approved_plan_outside_the_repository(self):
        code, _, err = self.run_cli(
            "plan",
            "approve",
            str(self.outside),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state),
        )
        self.assertEqual(code, 0, err)
        before = self.outside.read_bytes()
        args = argparse.Namespace(
            plan=str(self.outside),
            repo=str(self.repo),
            state_dir=str(self.state),
            base="origin/main",
        )
        driver = load_script().Driver(args)
        model = driver.read_plan()
        self.assertEqual(model["slug"], "demo")
        self.assertEqual(driver.repo, self.repo.resolve())
        self.assertEqual(self.outside.read_bytes(), before)
        self.assertFalse((self.repo / "docs" / "plans").exists())


class SlugTests(DiscoveryFixture):
    """The listing names a YAML plan by the `slug:` field, which is the key its ledger lives under."""

    def slugs(self, *extra):
        code, out, err = self.run_cli("plans", "list", "--json", "--repo", str(self.repo), *extra)
        self.assertEqual(code, 0, err)
        return {entry["path"].rsplit("/", 1)[1]: entry["slug"] for entry in json.loads(out)}

    def test_S1_a_yaml_plan_is_listed_under_its_slug_field_not_its_dated_file_name(self):
        data = contract()
        data["slug"] = "agent-workbench"
        self.track("docs/plans/2026-11-11-agent-workbench-plan.plan.yaml", yaml.safe_dump(data))
        self.track("docs/plans/2026-11-09-legacy-name.plan.md")
        self.assertEqual(
            self.slugs(),
            {
                "2026-11-11-agent-workbench-plan.plan.yaml": "agent-workbench",
                "2026-11-09-legacy-name.plan.md": "2026-11-09-legacy-name",
            },
        )

    def test_S1_a_yaml_plan_that_does_not_parse_keeps_its_file_derived_slug(self):
        self.track("docs/plans/2026-11-11-broken.plan.yaml", "slug: [unclosed\n")
        self.assertEqual(self.slugs(), {"2026-11-11-broken.plan.yaml": "2026-11-11-broken"})
