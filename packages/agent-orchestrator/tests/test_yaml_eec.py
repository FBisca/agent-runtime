"""The fixture the plan-layout tests build on: a one-ticket YAML plan, a temp repo with an origin, and the fake runtime
and gh answering in process (`YamlPlanFixture`, on `GateFixture`).

Its lifecycle and rejection cases are kept by `test_acceptance.py`, `test_archive.py`, `test_plan.py` and
`test_status_json.py` (plan-contract R22, R23)."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from fixtures import load_script, red_of, report_wait, spawn_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration

LIFECYCLE = ["APPROVED", "RUNNING", "ACCEPTED", "ARCHIVED"]


def plan_yaml():
    return yaml.safe_dump(
        {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "The feature file says done.",
            "spec": "docs/specs/demo.md",
            "scenarios": [
                {
                    "id": "S1",
                    "when": "the feature is delivered",
                    "then": "src/a/feature.txt reads done",
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
        },
        sort_keys=False,
    )


class YamlPlanFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.plan.unlink()
        self.plan = self.repo / "docs" / "plans" / "2026-11-10-demo.plan.yaml"
        self.archive_tree = self.root / "repo.worktrees" / "demo-archive"

    def write_plan(self):
        self.plan.write_text(plan_yaml(), encoding="utf-8")

    def approve(self, expect_ok=True):
        result = self.cli("plan", "approve", str(self.plan), "--repo", str(self.repo))
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def status(self):
        result = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def approval(self):
        return yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]

    def origin_tree(self):
        return subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", "main^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def steps(self, run_dir, content="done"):
        """The worker delivers src/a/feature.txt with a covering test; the reviewer passes; the work reaches main."""
        worktree = self.root / "repo.worktrees" / "demo-t1"
        report = json.loads(self.report("t1"))
        report.update(contract=3, covers={"S1": ["src/a/test_feature.py::test_feature"]})
        report["test_cost"] = self.measured(report["covers"])
        report["red"] = red_of(report["covers"])
        worker = report_wait("demo-t1", "complete", id=spawn_id("demo-t1"))
        worker["write"] = [
            [str(run_dir / "t1" / "report.json"), json.dumps(report)],
            [str(worktree / "src" / "a" / "feature.txt"), content + "\n"],
            [
                str(worktree / "src" / "a" / "test_feature.py"),
                "def test_feature():\n    assert open('src/a/feature.txt').read().strip() == 'done'\n",
            ],
        ]
        reviewer = report_wait("demo-t1-review", "complete")
        reviewer["write"] = [[str(run_dir / "t1" / "review.json"), self.review2()]]
        identity = ["-c", "user.name=T", "-c", "user.email=t@example.invalid"]
        reviewer["run"] = [
            ["git", "-C", str(worktree), "add", "-A"],
            ["git", "-C", str(worktree), *identity, "commit", "-qm", "feature"],
            ["git", "-C", str(worktree), "push", "-q", "origin", "HEAD:main"],
        ]
        return [worker, reviewer]

    def run_yaml(self, steps):
        self.waits(steps)
        return self.in_process(
            load_script(),
            self.driver_args(),
        )

    def delivered(self):
        """Approve, run to the end, and return (approved bytes, status, run_dir)."""
        self.write_plan()
        self.approve()
        approved = self.plan.read_bytes()
        run_dir = Path(self.status()["run_dir"])
        result = self.run_yaml(self.steps(run_dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        return approved, self.status(), run_dir
