"""YAML tickets enter the existing Markdown dispatch loop unchanged."""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import load_script, plan_text, spawn_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class DispatchFixture(GateFixture, unittest.TestCase):
    def exercise(self, use_yaml):
        self.setUp()
        self.plan.write_text(plan_text(rows=("T1", "T2", "T3", "T5")))
        if use_yaml:
            parsed = load_script().parse_plan(self.plan.read_text())
            self.plan = self.plan.with_suffix(".yaml")
            data = {
                "schema": "plan/1",
                "slug": "demo",
                "goal": "deliver",
                "spec": "docs/specs/demo.md",
                "outcome": ["S1"],
                "scenarios": [
                    dict(id=s, when="a", then="b", evidence={"human": "confirm"})
                    for s in parsed["scenarios"]
                ],
                "tickets": [
                    dict(
                        id=t["#"],
                        delivers=t["What it delivers"],
                        satisfies=t["Satisfies"].split(","),
                        paths=t["Paths"].split(","),
                        size=t["Size"],
                        difficulty=t["Difficulty"],
                        blocked_by=[] if t["Blocked by"] == "none" else [t["Blocked by"]],
                        max_fix_rounds=3,
                    )
                    for t in parsed["tickets"]
                ],
            }
            self.plan.write_text(yaml.safe_dump(data, sort_keys=False))
            out = io.StringIO()
            with (
                mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"}),
                mock.patch.dict(
                    os.environ,
                    {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "test-lead"},
                ),
                redirect_stdout(out),
                redirect_stderr(out),
            ):
                code = load_script().run_cli(
                    [
                        "plan",
                        "approve",
                        str(self.plan),
                        "--repo",
                        str(self.repo),
                        "--state-dir",
                        str(self.state_dir),
                    ]
                )
            self.assertEqual(code, 0, out.getvalue())
        before = self.plan.read_bytes()
        first = self.worker_done(tests=True)
        self.assertEqual(first["messages"][0]["assignment_id"], spawn_id("demo-t1"))
        self.waits([first, self.reviewer_done()])
        result = self.run_driver("--cli", "codex", "--repo", str(self.repo), "--max-workers", "4")
        spawns = [c["args"] for c in self.calls("spawn")]
        names = [c["name"] for c in spawns]
        self.assertEqual(names, ["demo-t1", "demo-t3", "demo-t5", "demo-t1-review", "demo-t2"])
        self.assertEqual(self.task()["status"], "COMPLETE")
        t2 = next(c for c in self.calls("spawn") if c["args"]["name"] == "demo-t2")
        self.assertTrue(
            any(
                json.loads(e)["kind"] == "state"
                and json.loads(e).get("status", [None, None])[1] == "COMPLETE"
                for e in t2["events"]
            )
        )
        if use_yaml:
            self.assertEqual(self.plan.read_bytes(), before)
            self.assertTrue(any(e["kind"] == "converge-skipped" for e in self.events()))
            self.assertIn("YAML", result.stderr)
        return (
            [(c["name"], c["cli"], c["model"], c["effort"]) for c in spawns],
            {
                k: (v["packet"]["class"], v["packet"]["effort"], v["packet"]["model"])
                for k, v in self.ledger()["tasks"].items()
            },
        )


class PlanDispatchTests(unittest.TestCase):
    def test_S4_same_names_routes_and_blocking_order_and_immutable_yaml(self):
        observations = []
        for use_yaml in (False, True):
            fixture = DispatchFixture()
            try:
                observations.append(fixture.exercise(use_yaml))
            finally:
                fixture.doCleanups()
        self.assertEqual(observations[0], observations[1])
