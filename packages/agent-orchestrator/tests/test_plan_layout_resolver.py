"""One resolver answers for both plan layouts: the kind, the folder or oracle dir, the contract files, a path's place
relative to the repository, whether a path belongs to the plan and whether the folder may be deleted."""

import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest
from agent_orchestrator import plan as yaml_plan
from agent_orchestrator import verify
from agent_orchestrator.plan import PlanLayout

pytestmark = pytest.mark.unit


class ResolverFixture(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.folder_plan = self.repo / "docs" / "plans" / "2026-11-13-demo" / "plan.yaml"
        self.legacy_plan = self.repo / "docs" / "plans" / "2026-11-13-demo.plan.yaml"

    def folder(self, path=None):
        return PlanLayout(path or self.folder_plan, self.repo)

    def legacy(self, path=None):
        return PlanLayout(path or self.legacy_plan, self.repo)


class ResolverTests(ResolverFixture):
    def test_S1_the_kind_comes_from_the_plan_path_alone(self):
        self.assertEqual(self.folder().kind, "folder")
        self.assertEqual(self.legacy().kind, "legacy")
        self.assertTrue(self.folder().is_folder)
        self.assertFalse(self.legacy().is_folder)

    def test_S1_a_folder_plan_has_a_folder_and_no_oracle_dir(self):
        layout = self.folder()
        self.assertEqual(layout.folder, self.folder_plan.parent)
        self.assertIsNone(layout.oracle_dir)
        self.assertEqual(layout.contract_files, [self.folder_plan])

    def test_S1_a_legacy_plan_has_an_oracle_dir_beside_it_and_no_folder(self):
        layout = self.legacy()
        self.assertIsNone(layout.folder)
        self.assertEqual(layout.oracle_dir, self.repo / "docs/plans/2026-11-13-demo.oracle")
        self.assertEqual(
            layout.contract_files,
            [
                self.legacy_plan,
                self.repo / "docs/plans/2026-11-13-demo.plan.md",
                self.repo / "docs/plans/2026-11-13-demo.oracle",
            ],
        )

    def test_S1_a_paths_place_is_relative_to_the_repository_or_none_outside_it(self):
        self.assertEqual(
            self.folder().place(self.folder_plan), "docs/plans/2026-11-13-demo/plan.yaml"
        )
        outside = self.root / "elsewhere" / "plan.yaml"
        self.assertIsNone(self.folder(outside).place(outside))

    def test_S1_a_legacy_plan_outside_the_repository_is_placed_under_docs_plans(self):
        outside = self.root / "elsewhere" / "2026-11-13-demo.plan.yaml"
        self.assertEqual(self.legacy(outside).plan_place, "docs/plans/2026-11-13-demo.plan.yaml")
        self.assertEqual(self.legacy().plan_place, "docs/plans/2026-11-13-demo.plan.yaml")

    def test_S1_a_folder_plan_owns_every_file_in_its_folder_and_nothing_beside_it(self):
        layout = self.folder()
        for name in ("plan.yaml", "plan.md", "acceptance/test_a.py", "negatives/x.patch"):
            self.assertTrue(layout.owns("docs/plans/2026-11-13-demo/" + name), name)
        self.assertFalse(layout.owns("docs/plans/2026-11-13-demo-two/plan.yaml"))
        self.assertFalse(layout.owns("docs/plans/other.py"))

    def test_S1_a_legacy_plan_owns_its_file_and_its_oracle_dir_only(self):
        layout = self.legacy()
        self.assertTrue(layout.owns("docs/plans/2026-11-13-demo.plan.yaml"))
        self.assertTrue(layout.owns("docs/plans/2026-11-13-demo.oracle/probe.txt.oracle"))
        self.assertFalse(layout.owns("docs/plans/2026-11-13-demo.plan.md"))
        self.assertFalse(layout.owns("docs/plans/2026-11-13-demo.oracle-two/x"))

    def test_S1_a_folder_plan_outside_the_repository_owns_no_repository_path(self):
        outside = self.root / "elsewhere" / "plan.yaml"
        self.assertFalse(self.folder(outside).owns("docs/plans/plan.yaml"))

    def test_S1_the_folder_may_be_deleted_unless_it_is_or_holds_the_repository(self):
        self.assertTrue(self.folder().may_delete_folder())
        self.assertFalse(self.legacy().may_delete_folder())
        self.assertFalse(PlanLayout(self.repo / "plan.yaml", self.repo).may_delete_folder())
        self.assertFalse(PlanLayout(self.root / "plan.yaml", self.repo).may_delete_folder())

    def test_S1_the_deletable_folder_is_named_by_its_place_in_the_repository(self):
        self.assertEqual(self.folder().folder_place(), "docs/plans/2026-11-13-demo")
        self.assertIsNone(self.legacy().folder_place())
        outside = self.root / "elsewhere" / "plan.yaml"
        self.assertIsNone(self.folder(outside).folder_place())
        with self.assertRaisesRegex(ValueError, "is or holds the repository"):
            PlanLayout(self.root / "plan.yaml", self.repo).folder_place()

    def test_S1_the_stem_and_the_format_come_from_the_plan_path(self):
        self.assertEqual(self.folder().stem, "2026-11-13-demo")
        self.assertEqual(self.legacy().stem, "2026-11-13-demo")
        self.assertEqual(PlanLayout.of(Path("x/a.plan.md")).format, "markdown")
        self.assertEqual(self.legacy().format, "yaml")
        self.assertTrue(PlanLayout.is_plan_file(Path("x/a.plan.yaml")))
        self.assertTrue(PlanLayout.is_plan_file(Path("x/plan.yaml")))
        self.assertFalse(PlanLayout.is_plan_file(Path("x/a.plan.md")))


class NoRepositoryTests(ResolverFixture):
    def test_S1_a_question_about_the_repository_without_one_is_refused_naming_the_way_out(self):
        layout = PlanLayout(self.folder_plan)
        self.assertEqual(layout.kind, "folder")
        for ask in (
            lambda: layout.place(self.folder_plan),
            lambda: layout.owns("docs/plans/x/plan.yaml"),
            lambda: layout.may_delete_folder(),
        ):
            with self.assertRaisesRegex(ValueError, "needs the repository"):
                ask()


class PackageCopyTests(unittest.TestCase):
    """S23: what belongs to the package is what git reports for it, not what a walk of the disk finds."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.folder = self.repo / "docs" / "plans" / "2026-11-15-demo"
        (self.folder / "acceptance").mkdir(parents=True)
        (self.folder / "acceptance" / "probe.txt").write_text("probe\n")
        (self.repo / "pkg").mkdir()
        (self.repo / ".gitignore").write_text(".worktrees/\n")
        self.git("init", "-q", "-b", "main")
        self.commit("the plan, its acceptance file and the package")

    def git(self, *args):
        done = subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True
        )
        return done.stdout

    def commit(self, message):
        self.git("add", "-A")
        self.git("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", message)

    def problems(self):
        """The plan check's problems for a promote plan whose acceptance file lands in the package."""
        data = {
            "domain": "flows",
            "archive": "promote",
            "spec": "docs/specs/demo.md",
            "scenarios": [
                {
                    "id": "S1",
                    "when": "it runs",
                    "then": "it passes",
                    "evidence": {
                        "check": "true",
                        "baseline": "pass",
                        "acceptance": ["probe.txt"],
                        "package": "pkg",
                    },
                }
            ],
            "tickets": [],
        }
        return yaml_plan.folder_problems(
            data, self.folder / "plan.yaml", self.repo, data["scenarios"]
        )

    @pytest.mark.integration
    def test_S23_a_copy_git_ignores_is_no_module_of_the_package_and_a_committed_one_is_named(self):
        """structure-budget/S23: a nested checkout's copy neither clashes nor is named the module."""
        copy = self.repo / "pkg" / ".worktrees" / "wt" / "acceptance" / "probe.txt"
        copy.parent.mkdir(parents=True)
        copy.write_text("probe\n")
        self.assertEqual(self.problems(), [], "a copy in an ignored tree is no module of the package")
        tracked = self.repo / "pkg" / "tests" / "probe.txt"
        tracked.parent.mkdir()
        tracked.write_text("already here")
        self.commit("a module of that basename")
        message = "\n".join(self.problems())
        self.assertIn("pkg/tests/probe.txt", message, "the refusal names the copy, not only a basename")


class AskedByTests(ResolverFixture):
    def test_S1_verifys_integrity_rule_asks_the_resolver(self):
        folder = verify.integrity(None, self.folder(), "docs/plans/2026-11-13-demo/plan.md")
        self.assertIn("is in the plan folder", folder)
        legacy = verify.integrity(None, self.legacy(), "docs/plans/2026-11-13-demo.plan.yaml")
        self.assertIn("is the plan", legacy)
        oracle = verify.integrity(None, self.legacy(), "docs/plans/2026-11-13-demo.oracle/a")
        self.assertIn("is an oracle file", oracle)

    def test_S1_no_module_asks_a_file_name_for_the_layout(self):
        source = Path(yaml_plan.__file__).parent
        files = [source / name for name in ("plan.py", "verify.py", "workbench.py")]
        # The driver is a package: read every module of it, so a split cannot hide the layout.
        files += sorted((source / "driver").glob("*.py"))
        for path in files:
            text = path.read_text(encoding="utf-8")
            for banned in ("is_folder_plan", 'removesuffix(".plan.yaml")', '.plan.yaml")'):
                if path.name == "plan.py" and banned != "is_folder_plan":
                    continue  # the resolver itself reads the suffix
                self.assertNotIn(
                    banned, text, "%s still derives the layout: %s" % (path.name, banned)
                )
