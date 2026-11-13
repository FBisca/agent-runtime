"""The fixture the plan-layout tests build on: a one-ticket YAML plan with an oracle, a temp repo with an origin, and
the fake runtime and gh answering in process (`YamlPlanFixture`, on `GateFixture`).

Its lifecycle and rejection cases are kept by `test_acceptance.py`, `test_archive.py`, `test_runner.py`,
`test_plan.py` and `test_status_json.py` (plan-contract R22, R23)."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from fixtures import load_script, report_wait, spawn_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration

LIFECYCLE = ["APPROVED", "RUNNING", "DELIVERED", "VERIFYING", "ACCEPTED", "ARCHIVED"]
PROBE = "probe\n"


def plan_yaml(check):
    return yaml.safe_dump(
        {
            "schema": "plan/1",
            "slug": "demo",
            "goal": "The feature file says done.",
            "spec": "docs/specs/demo.md",
            "outcome": ["S1"],
            "scenarios": [
                {
                    "id": "S1",
                    "when": "the feature is delivered",
                    "then": "src/a/feature.txt reads done",
                    "evidence": {
                        "check": check,
                        "oracle": ["probe.txt.oracle"],
                        "place": "checks/",
                        "baseline": "fail",
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
        },
        sort_keys=False,
    )


GOOD_CHECK = "grep -qx done src/a/feature.txt && test -f checks/probe.txt"


class YamlPlanFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.plan.unlink()
        self.plan = self.repo / "docs" / "plans" / "2026-11-10-demo.plan.yaml"
        self.oracle_dir = self.repo / "docs" / "plans" / "2026-11-10-demo.oracle"
        self.oracle_dir.mkdir()
        (self.oracle_dir / "probe.txt.oracle").write_text(PROBE, encoding="utf-8")
        self.archive_tree = self.root / "repo.worktrees" / "demo-archive"

    def write_plan(self, check=GOOD_CHECK):
        self.plan.write_text(plan_yaml(check), encoding="utf-8")

    def approve(self, expect_ok=True):
        result = self.cli("plan", "approve", str(self.plan), "--repo", str(self.repo))
        if expect_ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def status(self):
        result = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def verify(self):
        return self.cli("verify", str(self.plan), "--state-dir", str(self.state_dir))

    def approval(self):
        return yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]

    def receipts(self, run_dir, phase=None):
        found = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((run_dir / "receipts").glob("*.json"))
        ]
        return [r for r in found if phase in (None, r.get("phase"))]

    def origin_tree(self):
        return subprocess.run(
            ["git", "-C", str(self.origin), "rev-parse", "main^{tree}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def steps(self, run_dir, content="done", archive=True):
        """The worker delivers src/a/feature.txt with a covering test; the reviewer passes; the work reaches main."""
        worktree = self.root / "repo.worktrees" / "demo-t1"
        report = json.loads(self.report("t1"))
        report.update(contract=3, covers={"S1": ["src/a/test_feature.py::test_feature"]})
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
        steps = [worker, reviewer]
        if archive:
            gaps = report_wait("demo-gaps", "complete")
            gaps["write"] = [[str(run_dir / "gaps.md"), "# Gaps\n- none\n"]]
            steps += [gaps]
        return steps

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
