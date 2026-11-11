"""`launch` validates the plan and starts the driver in a named Herdr terminal through a fake `hc`."""

import json
import os
import subprocess
import unittest

import pytest
import yaml
from fixtures import DRIVER, RunFixture, plan_data

pytestmark = pytest.mark.integration

FAKE_HC = """#!/usr/bin/env python3
import json, os, sys
home = os.environ["FAKE_HC_HOME"]
with open(os.path.join(home, "hc.log"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1] == "ls":
    print(open(os.path.join(home, "live.json")).read())
elif sys.argv[1] == "run":
    if "--parent" in sys.argv and os.environ.get("FAKE_HC_BAD_PARENT"):
        print("unknown parent", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"name": sys.argv[sys.argv.index("--name") + 1], "id": "t9"}))
"""


class LaunchTests(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        hc = self.home / "hc"
        hc.write_text(FAKE_HC, encoding="utf-8")
        hc.chmod(0o755)
        self.live([])

    def live(self, names, state="live"):
        rows = [
            {
                "address": "t1",
                "agent": "claude-code",
                "cwd": "/x",
                "parent": "t0",
                "state": "live",
                "status": "working",
                "turn": 0,
                "where": "w",
            }
        ]  # a record without a name
        rows += [{"address": "t2", "name": n, "state": state, "status": "working"} for n in names]
        (self.home / "live.json").write_text(
            json.dumps({"terminals": rows, "hidden_finished": 3}), encoding="utf-8"
        )

    def hc_calls(self):
        path = self.home / "hc.log"
        return (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )

    def launch(self, plan=None, expect=0, parent=None, bad_parent=False):
        env = dict(
            os.environ,
            PATH=str(self.home) + os.pathsep + os.environ["PATH"],
            FAKE_HC_HOME=str(self.home),
        )
        env.pop("HC_TERMINAL_ID", None)
        if parent is not None:
            env["HC_TERMINAL_ID"] = parent
        if bad_parent:
            env["FAKE_HC_BAD_PARENT"] = "1"
        result = subprocess.run(
            [*DRIVER, "launch", str(plan or self.plan), "--state-dir", str(self.state_dir)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def test_S49_starts_under_launcher_terminal(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S49"""
        self.launch(parent="t175")
        argv = [c for c in self.hc_calls() if c[0] == "run"][0]
        self.assertEqual(argv[argv.index("--parent") + 1], "t175")

    def test_S7_the_run_is_told_the_launching_terminal_to_alert(self):
        """orchestrator-engine/R14: orchestrator-lead-alerts/S7"""
        self.launch(parent="t175")
        argv = [c for c in self.hc_calls() if c[0] == "run"][0]
        tail = argv[argv.index("--") + 1 :]
        self.assertEqual(tail[tail.index("--notify-terminal") + 1], "t175")

    def test_S7_a_run_launched_outside_a_terminal_alerts_no_one(self):
        """orchestrator-engine/R14: orchestrator-lead-alerts/S7"""
        self.launch()
        argv = [c for c in self.hc_calls() if c[0] == "run"][0]
        self.assertNotIn("--notify-terminal", argv)

    def test_S50_starts_top_level_outside_terminal(self):
        """orchestrator-engine/R2: orchestrate-lean-loop/S50"""
        self.launch()
        argv = [c for c in self.hc_calls() if c[0] == "run"][0]
        self.assertNotIn("--parent", argv)

    def test_bad_parent_falls_back_to_top_level(self):
        """orchestrator-engine/R2"""
        out = json.loads(self.launch(parent="stale", bad_parent=True).stdout)
        starts = [c for c in self.hc_calls() if c[0] == "run"]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0][starts[0].index("--parent") + 1], "stale")
        self.assertNotIn("--parent", starts[1])
        self.assertTrue(out["started"])
        self.assertFalse(out["nested"])
        self.assertIn("unknown parent", out["nesting_error"])

    def test_S17_starts_run_in_a_named_pane(self):
        """orchestrator-engine/R6: code-driven-orchestrator/S17"""
        result = self.launch()
        self.assertEqual(json.loads(result.stdout)["pane"], "orchestrate-demo")
        starts = [c for c in self.hc_calls() if c[0] == "run"]
        self.assertEqual(len(starts), 1)
        argv = starts[0]
        self.assertEqual(argv[:2], ["run", "--bg"])
        self.assertEqual(argv[argv.index("--name") + 1], "orchestrate-demo")
        self.assertEqual(argv[argv.index("--cwd") + 1], str(self.repo))
        tail = argv[argv.index("--") + 1 :]
        # The member is a package: the pane runs `python -m agent_orchestrator run <plan>`.
        self.assertEqual(tail[1:4], ["-m", "agent_orchestrator", "run"])
        self.assertEqual(tail[4], str(self.plan))

    def test_S17_invalid_plan_exits_2_and_starts_nothing(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S17"""
        self.plan.write_text(yaml.safe_dump(plan_data(), sort_keys=False), encoding="utf-8")
        result = self.launch(expect=2)
        self.assertIn("plan approve", result.stderr)
        self.assertEqual([c for c in self.hc_calls() if c[0] == "run"], [])

    def test_S17_live_driver_is_not_started_twice(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S17"""
        self.live(["orchestrate-demo"])
        result = self.launch()
        out = json.loads(result.stdout)
        self.assertEqual((out["pane"], out["started"]), ("orchestrate-demo", False))
        self.assertEqual([c for c in self.hc_calls() if c[0] == "run"], [])

    def yaml_plan(self, approve=True):
        """An approved (or unapproved) one-ticket YAML plan in the temp repo; approval state under self.state_dir."""
        self.plan.unlink()
        self.plan = self.plan.parent / "2026-11-10-demo.plan.yaml"
        self.plan.write_text(
            yaml.safe_dump(
                {
                    "schema": "plan/1",
                    "slug": "demo",
                    "goal": "deliver",
                    "spec": "docs/specs/demo.md",
                    "outcome": ["S1"],
                    "scenarios": [
                        {"id": "S1", "when": "a", "then": "b", "evidence": {"human": "confirm"}}
                    ],
                    "tickets": [
                        {
                            "id": "T1",
                            "delivers": "x",
                            "satisfies": ["S1"],
                            "paths": ["src/a/"],
                            "size": "small",
                            "difficulty": "simple",
                        }
                    ],
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        if approve:
            result = subprocess.run(
                [
                    *DRIVER,
                    "plan",
                    "approve",
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                ],
                capture_output=True,
                text=True,
                env=dict(os.environ, PATH=str(self.home) + os.pathsep + os.environ["PATH"]),
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_an_approved_yaml_plan_starts_run_in_its_named_pane(self):
        """An approved YAML plan is launched in one pane running `run <plan.yaml>`."""
        self.yaml_plan()
        result = subprocess.run(
            [*DRIVER, "launch", str(self.plan), "--state-dir", str(self.state_dir)],
            capture_output=True,
            text=True,
            env=dict(
                {k: v for k, v in os.environ.items() if k != "HC_TERMINAL_ID"},
                PATH=str(self.home) + os.pathsep + os.environ["PATH"],
                FAKE_HC_HOME=str(self.home),
            ),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["pane"], "orchestrate-demo")
        starts = [c for c in self.hc_calls() if c[0] == "run"]
        self.assertEqual(len(starts), 1)
        tail = starts[0][starts[0].index("--") + 1 :]
        self.assertEqual(tail[2:5], ["agent_orchestrator", "run", str(self.plan)])
        self.assertEqual(tail[tail.index("--state-dir") + 1], str(self.state_dir))

    def test_an_unapproved_yaml_plan_exits_2_naming_plan_approve(self):
        """`launch` makes the check `run` makes: an unapproved contract starts no pane."""
        self.yaml_plan(approve=False)
        result = self.launch(expect=2)
        self.assertIn("plan approve", result.stderr)
        self.assertEqual([c for c in self.hc_calls() if c[0] == "run"], [])

    def test_lead_commands_find_a_yaml_plans_run_directory(self):
        """`decide` reads a YAML plan's slug, so it reaches the unit's requests instead of refusing the plan."""
        self.yaml_plan()
        result = subprocess.run(
            [
                *DRIVER,
                "decide",
                str(self.plan),
                "t1",
                "retry",
                "--reason",
                "r",
                "--state-dir",
                str(self.state_dir),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("t1 has no open request", result.stderr + result.stdout)

    def test_S17_exited_terminal_of_the_same_name_does_not_block(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S17"""
        self.live(["orchestrate-demo"], state="exited")
        out = json.loads(self.launch().stdout)
        self.assertTrue(out["started"])
        self.assertEqual(len([c for c in self.hc_calls() if c[0] == "run"]), 1)


if __name__ == "__main__":
    unittest.main()
