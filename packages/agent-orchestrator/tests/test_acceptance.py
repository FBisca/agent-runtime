"""A plan's acceptance: a YAML plan is ACCEPTED once every agent ticket is COMPLETE, merged with its gate passed and CI
green on its head, and the archive starts from there; a contract edited after approval starts no worker."""

import io
import json
import os
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import pytest
import yaml
from fixtures import WORLDS, RunFixture, added, load_script, red_of, report_wait, spawn_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration

STATES = ["APPROVED", "RUNNING"]
SNAPSHOTS: dict[
    type, dict[str, Path]
] = {}  # test class -> the worlds its tests start from, by what built them


def scenario(sid):
    return dict(id=sid, when="a %s" % sid, then="b %s" % sid)


def ticket(tid="T1", satisfies=("S1",), **over):
    return dict(
        dict(
            id=tid,
            delivers="the feature file",
            satisfies=list(satisfies),
            paths=["src/a/"],
            size="small",
            difficulty="simple",
        ),
        **over,
    )


def plan_data(scenarios, tickets):
    return {
        "schema": "plan/2",
        "slug": "demo",
        "goal": "The feature file says done.",
        "spec": "docs/specs/demo.md",
        "requirements": added(scenarios),
        "tickets": tickets,
    }


if (
    TYPE_CHECKING
):  # the mixin is mixed into a `RunFixture` test case; the base is for the type checker only
    _Base = RunFixture
else:
    _Base = object


class SnapshotFixture(_Base):
    """Mixin for a `RunFixture` class: the tests of a class that only read a run's result share one run of it."""

    def from_snapshot(self, key, build):
        """Leave the world as `build` leaves it: the first test of the class to name `key` builds it, every later one
        starts from a copy of that. A run's approval and ledger name the world's path, so the copy goes back to the same
        path, and a test that changes what it finds changes its own copy."""
        cls = type(self)
        if cls not in SNAPSHOTS:
            SNAPSHOTS[cls] = {}
            self.addClassCleanup(SNAPSHOTS.pop, cls, None)
        held = SNAPSHOTS[cls]
        if key in held:
            shutil.rmtree(self.root)
            shutil.copytree(held[key], self.root, symlinks=True)
            return
        build()
        kept = Path(WORLDS[cls].scratch.name) / ("snapshot-%d" % len(held))
        shutil.copytree(self.root, kept, symlinks=True)
        held[key] = kept

    def snapshot_key(self, *parts):
        """What decides the world a build leaves: its arguments and what the worker is scripted to report."""
        scripted = [getattr(self, name, None) for name in ("COVERS", "extra_files")]
        return json.dumps([parts, scripted], sort_keys=True, default=str)


class AcceptanceFixture(SnapshotFixture, GateFixture):
    HOLD = False  # the plan's acceptance and archive are what these tests are about

    def setUp(self):
        super().setUp()
        self.plan.unlink()
        self.plan = self.repo / "docs" / "plans" / "2026-11-10-demo.plan.yaml"
        self.COVERS = {"S1": ["src/a/test_feature.py::test_feature"]}

    def write_plan(self, scenarios=None, tickets=None):
        scenarios = scenarios or [scenario("S1")]
        tickets = tickets or [ticket("T1", [s["id"] for s in scenarios])]
        self.plan.write_text(
            yaml.safe_dump(plan_data(scenarios, tickets), sort_keys=False), encoding="utf-8"
        )
        self.alias_state(self.plan)
        out = io.StringIO()
        with (
            mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"}),
            mock.patch.dict(
                os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead"}
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

    def land(self, content):
        """A person's merge: `content` reaches the default branch of origin, from a clone of their own."""
        script = (
            "set -e; d=$(mktemp -d); git clone -q %s $d/c; cd $d/c; mkdir -p src/a; "
            "printf '%s\\n' > src/a/feature.txt; git add -A; "
            "git -c user.name=T -c user.email=t@example.invalid commit -qm feature; git push -q origin HEAD:main"
            % (self.origin, content)
        )
        return ["sh", "-c", script]

    def merged_on_main(self, content):
        """The person's merge, before the run: `content` is on origin's default branch and gh names that commit as the
        pull request's merge commit. Returns the commit."""
        subprocess.run(self.land(content), check=True, capture_output=True)
        sha = self.git(self.origin, "rev-parse", "main")
        self.prs([{"state": "MERGED", "mergeCommit": {"oid": sha}}])
        return sha

    def worker(self, content="done", covers=None):
        report = json.loads(self.report("t1"))
        report["covers"] = covers or self.COVERS
        if report.get("contract") == 3:
            report["red"] = red_of(report["covers"])
        step = report_wait("demo-t1", "complete", id=spawn_id("demo-t1"))
        step["write"] = [
            [str(self.run_dir / "t1" / "report.json"), json.dumps(report)],
            [str(self.worktree / "src" / "a" / "feature.txt"), content + "\n"],
            [
                str(self.worktree / "src" / "a" / "test_feature.py"),
                "def test_feature():\n    assert open('src/a/feature.txt').read().strip() == 'done'\n",
            ],
        ]
        return step

    def reviewer(self):
        step = report_wait("demo-t1-review", "complete")
        step["write"] = [[str(self.run_dir / "t1" / "review.json"), self.review2()]]
        return step

    def drive(self, steps):
        self.waits(steps)
        return self.run_driver("--repo", str(self.repo))

    def delivered(self, gate="done", lands="done", scenarios=None, tickets=None):
        """Every ticket merged and the plan driven to the end."""

        def build():
            self.write_plan(scenarios, tickets)
            if lands:
                self.merged_on_main(lands)
            self.drive([self.worker(gate), self.reviewer()])

        key = self.snapshot_key("delivered", gate, lands, scenarios, tickets)
        self.from_snapshot(key, build)

    def status(self):
        result = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def origin_tree(self):
        return self.git(self.origin, "rev-parse", "main^{tree}")

    def history(self):
        return [h["state"] for h in self.status()["history"]]


class DeliveredSnapshotTests(unittest.TestCase):
    @pytest.mark.spec("Test fixtures are built once")
    def test_S12_a_delivered_plan_is_driven_once_per_class_and_a_test_that_changes_it_takes_a_copy(
        self,
    ):
        """test-cost/S12 — the tests that only read a delivered plan share one drive."""
        seen = []

        class Readers(AcceptanceFixture, unittest.TestCase):
            def changed(self):
                return (self.run_dir / "changed-by-a-test").exists()

            def test_first(self):
                self.delivered()
                seen.append(self.changed())
                # a test that changes the plan's world: the next one must not see it
                (self.run_dir / "changed-by-a-test").write_text("x", encoding="utf-8")

            def test_second(self):
                self.delivered()
                seen.append(self.changed())

            def test_third(self):
                self.delivered()
                seen.append(self.changed())

        real = AcceptanceFixture.drive
        with mock.patch.object(
            AcceptanceFixture, "drive", autospec=True, side_effect=real
        ) as drive:
            suite = unittest.defaultTestLoader.loadTestsFromTestCase(Readers)
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
        self.assertEqual(
            drive.call_count, 1, "the delivered plan was driven again for a later test"
        )
        self.assertEqual(seen, [False, False, False], "a test saw what an earlier test changed")


class TicketGateTests(AcceptanceFixture, unittest.TestCase):
    def test_the_gate_says_it_picked_up_a_report(self):
        """The invariants read the checkout before the reviewer is spawned: the pane says the report was taken."""
        self.write_plan()
        self.merged_on_main("done")
        result = self.drive([self.worker("done"), self.reviewer()])
        self.assertTrue(
            any("checking its report" in line for line in result.stderr.splitlines()),
            result.stderr,
        )
        self.assertIn("demo-t1-review", self.spawned(), "a ticket that passes verify is reviewed")


class AcceptedTests(AcceptanceFixture, unittest.TestCase):
    @pytest.mark.spec("Plan lifecycle")
    def test_S17_every_agent_ticket_complete_accepts_the_plan_and_the_archive_starts(self):
        self.delivered()
        self.assertEqual(self.history(), STATES + ["ACCEPTED", "ARCHIVED"])
        self.assertEqual(self.status()["tickets"][0]["state"], "COMPLETE")
        self.assertEqual(len(self.gh_calls("pr", "create")), 1, "an accepted plan is archived")
        self.assertNotIn(
            "integration", self.ledger(), "nothing is run again once the tickets are in"
        )


class RejectionTests(AcceptanceFixture, unittest.TestCase):
    def test_S22_a_contract_edited_after_approval_spawns_no_worker(self):
        self.write_plan()
        self.plan.write_text(
            self.plan.read_text(encoding="utf-8").replace("b S1", "b S1 edited"), encoding="utf-8"
        )
        self.waits([])
        result = self.run_driver_result()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("plan approve", result.stdout + result.stderr)
        self.assertEqual(self.spawned(), [])
        self.assertNotIn("ACCEPTED", self.history())

    def run_driver_result(self):
        return self.in_process(
            load_script(),
            self.driver_args("--repo", str(self.repo)),
        )


if __name__ == "__main__":
    unittest.main()
