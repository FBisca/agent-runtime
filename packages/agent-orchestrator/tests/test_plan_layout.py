"""Outcome oracle for the plan-layout plan: a plan lives in its own folder, a repo config names where plans live and
how they archive, and archive either promotes the acceptance tests or discards the plan.

Frozen at approval; placed into packages/agent-orchestrator/tests/ by the runner; never edited by a ticket.
It drives the real driver through the suite's existing seam (GateFixture: a temp repo with an origin, the fake
runtime and the fake gh answering in process) and the engine's CLI contract only:

  <repo>/.orchestrate/config.yaml        plans.dir, acceptance.dir, archive.mode (promote | discard)
  <plans.dir>/<date>-<slug>/plan.yaml    the contract; acceptance files in <folder>/acceptance/
  evidence: acceptance: [files], package: <dir | .>; top-level domain: <slug>, archive: <mode>
  destination                            <package>/<acceptance.dir>/<domain>/<file>
  plans list --json --repo <repo>        rows {path, format, slug}
  plan approve / status --json / verify / run, each with --repo
  ledger                                 <state>/<repo-key>/<slug>.json, its plan.state

Domain, package and acceptance dir all differ from the slug and the defaults, so a destination built from anything
but them misses. The control cases exercise the legacy layout (X.plan.yaml beside X.oracle/), with and without a
config: they pass before and after the implementation.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

import pytest
import yaml
from fixtures import load_script
from test_yaml_eec import GOOD_CHECK, LIFECYCLE, PROBE, YamlPlanFixture, plan_yaml

pytestmark = pytest.mark.integration

FOLDER = "2026-11-12-demo"
PLACED = "pkg/accept/flows/probe.txt"
CHECK = "grep -qx done src/a/feature.txt && test -f " + PLACED


def config(plans, mode="promote"):
    return yaml.safe_dump(
        {"plans": {"dir": plans}, "acceptance": {"dir": "accept"}, "archive": {"mode": mode}},
        sort_keys=False,
    )


def folder_plan(spec=True):
    data = {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "The feature file says done.",
        "domain": "flows",
        "outcome": ["S1"],
        "scenarios": [
            {
                "id": "S1",
                "when": "the feature is delivered",
                "then": "src/a/feature.txt reads done",
                "evidence": {
                    "check": CHECK,
                    "baseline": "fail",
                    "acceptance": ["probe.txt"],
                    "package": "pkg",
                },
            }
        ],
        "tickets": [
            {
                "id": "T1",
                "delivers": "the feature file",
                "satisfies": ["S1"],
                "paths": ["src/a/"],
                "size": "small",
                "difficulty": "simple",
            }
        ],
    }
    if spec:
        data["spec"] = "docs/specs/demo.md"
    return yaml.safe_dump(data, sort_keys=False)


def listing(root):
    """Every path under `root`, .git excluded: what a command may not add to."""
    if not root.exists():
        return set()
    return {
        str(p.relative_to(root)) for p in root.rglob("*") if ".git" not in p.relative_to(root).parts
    }


class FolderPlanFixture(YamlPlanFixture):
    """A folder plan under the plans dir the repo config names; every command names the repository. The repo
    carries a `pkg/` package on origin main; the legacy plan the parent fixture prepares is not written."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.oracle_dir)
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "README.md").write_text("pkg\n", encoding="utf-8")
        self.publish("pkg")
        self.home_dir = self.root / "plans-home"

    def write_config(self, rel, mode="promote"):
        (self.repo / ".orchestrate").mkdir(exist_ok=True)
        (self.repo / ".orchestrate" / "config.yaml").write_text(config(rel, mode), encoding="utf-8")

    def write_folder(self, plans_dir, spec=True):
        self.folder = plans_dir / FOLDER
        (self.folder / "acceptance").mkdir(parents=True)
        (self.folder / "acceptance" / "probe.txt").write_text(PROBE, encoding="utf-8")
        self.plan = self.folder / "plan.yaml"
        self.plan.write_text(folder_plan(spec), encoding="utf-8")

    def write_legacy(self):
        """The parent fixture's legacy plan, tracked: X.plan.yaml beside X.oracle/."""
        self.oracle_dir.mkdir()
        (self.oracle_dir / "probe.txt.oracle").write_text(PROBE, encoding="utf-8")
        legacy = self.oracle_dir.with_name("2026-11-10-demo.plan.yaml")
        legacy.write_text(
            yaml.safe_dump(
                dict(yaml.safe_load(plan_yaml(GOOD_CHECK)), slug="legacy-demo"), sort_keys=False
            ),
            encoding="utf-8",
        )
        self.git(self.repo, "add", str(legacy), str(self.oracle_dir))
        return legacy

    def report(self, task_id, **over):
        """The ticket and archive reports of a YAML plan: contract 3 with covers, whatever the plan file is called."""
        data = json.loads(super().report(task_id, **over))
        data.setdefault("covers", self.COVERS)
        data["contract"] = 3
        return json.dumps(data)

    def approve(self, expect_ok=True):
        result = self.cli("plan", "approve", str(self.plan), "--repo", str(self.repo))
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def status(self):
        result = self.cli(
            "status",
            str(self.plan),
            "--repo",
            str(self.repo),
            "--state-dir",
            str(self.state_dir),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def verify(self):
        return self.cli(
            "verify", str(self.plan), "--repo", str(self.repo), "--state-dir", str(self.state_dir)
        )

    def listed(self):
        result = self.cli("plans", "list", "--json", "--repo", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return {str(Path(row["path"]).resolve()): row["slug"] for row in json.loads(result.stdout)}

    def run_folder(self, steps):
        self.waits(steps)
        return self.in_process(
            load_script(),
            [
                "run",
                str(self.plan),
                "--repo",
                str(self.repo),
                "--state-dir",
                str(self.state_dir),
                "--models-file",
                str(self.models),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
            ],
        )

    def ledger_state(self, run_dir):
        ledger = run_dir.parent / (run_dir.name + ".json")
        return (json.loads(ledger.read_text(encoding="utf-8")).get("plan") or {}).get("state")

    def origin_tree(self):
        """origin main's entries outside the ticket's paths: mode, type, blob and path."""
        lines = subprocess.run(
            ["git", "-C", str(self.origin), "ls-tree", "-r", "main"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        return [line for line in lines if not line.split("\t", 1)[1].startswith("src/a/")]

    def publish(self, *paths):
        """Commit and push the authored files to origin main, as a planning PR would."""
        self.git(self.repo, "add", *paths)
        self.git(self.repo, "commit", "-qm", "plan")
        self.git(self.repo, "push", "-q", "origin", "HEAD:main")


class Controls(FolderPlanFixture, unittest.TestCase):
    """The legacy layout keeps working, with and without a repo config: these pass before and after."""

    def test_control_a_legacy_plan_still_approves_and_is_listed(self):
        self.plan = self.write_legacy()
        self.approve()
        self.assertIn("digest", yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"])
        self.assertIn("legacy-demo", self.listed().values())

    def test_control_a_legacy_plan_beside_a_config_still_approves_and_is_listed(self):
        self.write_config("docs/plans")
        self.plan = self.write_legacy()
        self.approve()
        self.assertIn("legacy-demo", self.listed().values())


class ConfiguredDiscovery(FolderPlanFixture, unittest.TestCase):
    def test_plans_list_reads_the_configured_dir_outside_the_repository_and_creates_nothing(self):
        self.write_config(str(self.home_dir))
        self.write_folder(self.home_dir)
        before = (listing(self.repo), listing(self.home_dir))
        listed = self.listed()
        self.assertEqual(listed.get(str(self.plan.resolve())), "demo", listed)
        self.assertEqual(
            (listing(self.repo), listing(self.home_dir)), before, "discovery created a file"
        )

    def test_an_untracked_folder_plan_in_the_repo_is_listed_beside_the_legacy_plans(self):
        self.write_config("docs/plans")
        legacy = self.write_legacy()
        self.write_folder(self.repo / "docs" / "plans")
        listed = self.listed()
        self.assertEqual(listed.get(str(self.plan.resolve())), "demo", listed)
        self.assertEqual(listed.get(str(legacy.resolve())), "legacy-demo", listed)

    def test_an_unknown_config_key_is_refused_naming_it(self):
        (self.repo / ".orchestrate").mkdir()
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            "plans:\n  dir: docs/plans\n  extra: 1\n", encoding="utf-8"
        )
        result = self.cli("plans", "list", "--json", "--repo", str(self.repo))
        self.assertNotEqual(result.returncode, 0, "an unknown config key was accepted")
        self.assertIn("plans.extra", result.stdout + result.stderr)


class PromoteArchive(FolderPlanFixture, unittest.TestCase):
    def test_promote_moves_the_acceptance_into_its_domain_and_deletes_the_plan_folder(self):
        self.write_config("docs/plans")
        self.write_folder(self.repo / "docs" / "plans")
        self.publish(".orchestrate", "docs/plans")
        self.approve()
        run_dir = Path(self.status()["run_dir"])
        result = self.run_folder(self.steps(run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()["plan"]["history"], LIFECYCLE)
        self.assertEqual(self.git(self.origin, "show", "feat/demo-archive:" + PLACED) + "\n", PROBE)
        self.assertEqual(
            self.git(
                self.origin,
                "ls-tree",
                "-r",
                "--name-only",
                "feat/demo-archive",
                "docs/plans/" + FOLDER,
            ),
            "",
        )
        self.assertTrue(
            [
                r
                for r in self.receipts(run_dir, "archive")
                if r["scenario"] == "S1" and r["result"] == "PASS"
            ],
            "the outcome was not re-verified on the archive head",
        )
        self.assertEqual(self.verify().returncode, 0)


class DiscardArchive(FolderPlanFixture, unittest.TestCase):
    def test_discard_archives_without_a_spec_or_a_pr_and_deletes_the_external_plan(self):
        self.write_config(str(self.home_dir), mode="discard")
        self.write_folder(self.home_dir, spec=False)
        before = self.origin_tree()
        self.approve()
        run_dir = Path(self.status()["run_dir"])
        worker, reviewer, gaps = self.steps(run_dir)
        result = self.run_folder([worker, reviewer, gaps])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ledger_state(run_dir), "ARCHIVED")
        self.assertLessEqual(
            set(self.spawned()),
            {"demo-t1", "demo-t1-review", "demo-gaps"},
            "discard spawned an archive worker",
        )
        self.assertFalse(self.folder.exists(), "discard kept the plan folder")
        self.assertEqual(self.origin_tree(), before, "discard wrote to the repository")


if __name__ == "__main__":
    unittest.main()
