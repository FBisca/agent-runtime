"""Integration on a pinned default-branch SHA, the acceptance states and `verify <plan>`: a YAML plan whose every
ticket merged is accepted only when its integration ran for the exact tree and contract and the whole suite, when the
repo declares one, did not fail there."""

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
from agent_orchestrator import verify
from fixtures import WORLDS, RunFixture, load_script, report_wait, spawn_id
from test_gates import GateFixture

pytestmark = pytest.mark.integration

STATES = ["APPROVED", "RUNNING", "DELIVERED", "VERIFYING"]
SNAPSHOTS: dict[
    type, dict[str, Path]
] = {}  # test class -> the worlds its tests start from, by what built them


# A whole suite that holds only when the feature file says done: a plan whose merge lands anything else is rejected.
FEATURE_DONE = 'test "$(cat src/a/feature.txt)" = done'


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
        "schema": "plan/1",
        "slug": "demo",
        "goal": "The feature file says done.",
        "spec": "docs/specs/demo.md",
        "scenarios": scenarios,
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

    def declare_full(self, run, verify=None):
        """The repo's `checks.full`: the whole suite integration runs once on the pinned commit."""
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        lines = ["checks:", "  full:", "    run: '%s'" % run.replace("'", "''")]
        if verify is not None:
            lines.append("    verify: %s" % ("true" if verify else "false"))
        config.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def declared(self):
        config = self.repo / ".orchestrate" / "config.yaml"
        return config.read_text(encoding="utf-8") if config.exists() else None

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
            report["test_cost"] = self.measured(report["covers"])
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
        """Every ticket merged and the plan driven to the end. A merge that lands anything but `done` is rejected
        by the whole suite the plan's repo declares, which holds only when the feature file says done."""

        def build():
            if lands != "done":
                self.declare_full(FEATURE_DONE)
            self.write_plan(scenarios, tickets)
            if lands:
                self.merged_on_main(lands)
            self.drive([self.worker(gate), self.reviewer()])

        key = self.snapshot_key("delivered", gate, lands, scenarios, tickets, self.declared())
        self.from_snapshot(key, build)

    def status(self):
        result = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def verify(self, *extra):
        return self.cli("verify", str(self.plan), "--state-dir", str(self.state_dir), *extra)

    def origin_tree(self):
        return self.git(self.origin, "rev-parse", "main^{tree}")

    def history(self):
        return [h["state"] for h in self.status()["history"]]


class DeliveredSnapshotTests(unittest.TestCase):
    def test_S12_a_delivered_plan_is_driven_once_per_class_and_a_test_that_changes_it_takes_a_copy(
        self,
    ):
        """orchestrator-engine/R2: test-cost/S12 — the tests that only read a delivered plan share one drive."""
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


class IntegrationTests(AcceptanceFixture, unittest.TestCase):
    def test_S17_every_ticket_complete_pins_the_fetched_default_branch_and_accepts_on_its_tree(
        self,
    ):
        self.delivered()
        self.assertEqual(self.history(), STATES + ["ACCEPTED", "ARCHIVED"])
        digest = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]["digest"]
        pin = self.ledger()["integration"]
        self.assertEqual(pin["sha"], self.git(self.origin, "rev-parse", "main"))
        self.assertEqual((pin["tree"], pin["digest"]), (self.origin_tree(), digest))
        self.assertEqual(len(self.gh_calls("pr", "create")), 1, "an accepted plan is archived")
        self.assertEqual(self.verify().returncode, 0)

    def test_S17_an_integration_that_fails_rejects_the_plan_and_asks_the_lead(self):
        self.delivered(lands="almost")
        self.assertEqual(self.history(), STATES + ["REJECTED"])
        self.assertEqual(self.status()["tickets"][0]["state"], "COMPLETE")
        self.assertEqual(self.gh_calls("pr", "create"), [], "a rejected plan is not archived")
        (wake,) = [w for w in self.wakes() if w["kind"] == "plan-rejected"]
        for way_out in ("full", "ticket", "run"):
            self.assertIn(way_out, wake["text"])
        self.assertNotEqual(self.verify().returncode, 0)

    def test_S17_integration_waits_while_the_default_branch_lacks_a_ticket_merge(self):
        self.write_plan()
        orphan = self.git(
            self.repo,
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit-tree",
            "-m",
            "orphan",
            "HEAD^{tree}",
        )
        self.prs([{"state": "MERGED", "mergeCommit": {"oid": orphan}}])
        self.drive([self.worker("done"), self.reviewer()])
        self.assertEqual(
            self.history(),
            ["APPROVED", "RUNNING"],
            "nothing is delivered until a commit holds the merge",
        )
        self.assertNotIn("integration", self.ledger())
        (wake,) = [w for w in self.wakes() if w["kind"] == "integration-waits"]
        self.assertIn("T1", wake["text"])

    def test_S17_a_merge_commit_the_repository_does_not_have_is_not_contained_either(self):
        self.write_plan()
        self.prs([{"state": "MERGED", "mergeCommit": {"oid": "f" * 40}}])
        self.drive(
            [self.worker("done"), self.reviewer()]
        )  # gh answers a merge commit that exists nowhere
        self.assertEqual(self.history(), ["APPROVED", "RUNNING"])
        self.assertNotIn("integration", self.ledger())
        self.assertTrue([w for w in self.wakes() if w["kind"] == "integration-waits"])

    def test_S17_a_ticket_complete_without_a_known_merge_commit_makes_integration_wait(self):
        self.write_plan()
        self.merged_on_main("done")
        self.prs([{"state": "MERGED", "mergeCommit": None}])  # gh names no merge commit
        self.drive([self.worker("done"), self.reviewer()])
        self.assertEqual(
            self.history(),
            ["APPROVED", "RUNNING"],
            "a ticket whose merge is unknown is not contained by any commit",
        )
        self.assertNotIn("integration", self.ledger())
        (wake,) = [w for w in self.wakes() if w["kind"] == "integration-waits"]
        self.assertIn("T1", wake["text"])

    def test_S17_a_new_run_verifies_a_rejected_plan_again(self):
        self.delivered(lands="almost")
        subprocess.run(self.land("done"), check=True, capture_output=True)
        self.drive([])
        self.assertEqual(self.history()[-1], "ARCHIVED")
        self.assertEqual(self.ledger()["integration"]["attempt"], 2)


class WholeSuiteTests(AcceptanceFixture, unittest.TestCase):
    """`checks.full.run` at verify: once per pass on the pinned commit, or left to CI (check-tiers S6, S7)."""

    def full_events(self):
        return self.run_events("full")

    def deliver(self, *extra):
        def build():
            self.write_plan()
            self.merged_on_main("done")
            self.waits([self.worker("done"), self.reviewer()])
            self.run_driver("--repo", str(self.repo), *extra)

        self.from_snapshot(self.snapshot_key("deliver", extra, self.declared()), build)

    def test_S6_a_failing_whole_suite_on_the_integrated_commit_rejects_the_plan(self):
        self.declare_full("test -f src/a/never.txt")
        self.deliver()
        self.assertEqual(self.history(), STATES + ["REJECTED"])
        (wake,) = [w for w in self.wakes() if w["kind"] == "plan-rejected"]
        self.assertIn("test -f src/a/never.txt", wake["text"])
        self.assertIn("--skip-full", wake["text"])
        log = self.run_dir / "full.log"
        self.assertTrue(log.is_file())
        self.assertIn(str(log), wake["text"])
        (event,) = self.full_events()
        self.assertEqual(event["state"], "FAIL")

    def test_S6_a_passing_whole_suite_runs_once_on_the_pinned_commit_and_the_plan_is_accepted(self):
        runs = self.root / "full-runs.txt"
        self.declare_full(
            'test "$(cat src/a/feature.txt)" = done && git rev-parse HEAD >> %s' % runs
        )
        self.deliver()
        main = self.git(self.origin, "rev-parse", "main")
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        self.assertEqual(
            runs.read_text(encoding="utf-8").split(), [main]
        )  # once, at the pinned commit
        (event,) = self.full_events()
        self.assertEqual((event["state"], event["sha"]), ("PASS", main))

    def test_S7_skip_full_leaves_the_whole_suite_to_ci(self):
        self.declare_full("test -f src/a/never.txt")
        self.deliver("--skip-full")
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        (event,) = self.full_events()
        self.assertEqual((event["state"], event["reason"]), ("skipped", "--skip-full"))

    def test_S7_a_repo_that_sets_full_verify_false_leaves_the_whole_suite_to_ci(self):
        self.declare_full("test -f src/a/never.txt", verify=False)
        self.deliver()
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        (event,) = self.full_events()
        self.assertEqual(
            (event["state"], event["reason"]), ("skipped", "checks.full.verify: false")
        )

    def test_S6_the_whole_suite_runs_in_a_scratch_checkout_not_the_repo_and_logs_to_full_log(self):
        where = self.root / "full-where.txt"
        self.declare_full("pwd -P >> %s; echo whole-suite-output" % where)
        self.deliver()
        (ran_in,) = where.read_text(encoding="utf-8").split()
        self.assertNotEqual(ran_in, str(self.repo.resolve()))
        self.assertFalse(Path(ran_in).exists(), "the scratch checkout is removed afterwards")
        self.assertIn("whole-suite-output", (self.run_dir / "full.log").read_text(encoding="utf-8"))

    def test_S7_a_run_without_a_declared_full_records_no_skip(self):
        self.deliver("--skip-full")
        self.assertEqual(self.full_events(), [])

    def test_control_a_delivered_plan_is_accepted_without_a_config(self):
        self.deliver()
        self.assertEqual(self.history()[: len(STATES) + 1], STATES + ["ACCEPTED"])
        self.assertEqual(self.full_events(), [])


class VerifyTests(AcceptanceFixture, unittest.TestCase):
    def reasons(self, result):
        return [e for e in json.loads(result.stdout)["errors"]]

    def test_S20_verify_accepts_a_delivered_plan_and_names_each_item(self):
        self.delivered()
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["state"], "ACCEPTED")
        self.assertEqual(body["items"], [])
        self.assertEqual(body["integration"], self.git(self.origin, "rev-parse", "main"))

    def test_S20_an_integration_run_for_another_digest_or_tree_does_not_count(self):
        self.delivered()
        original = self.ledger()
        for field, why in (("digest", "another approval digest"), ("tree", "holds tree")):
            forged = json.loads(json.dumps(original))
            forged["integration"][field] = "0" * 40
            (self.state_dir / "demo.json").write_text(json.dumps(forged), encoding="utf-8")
            result = self.verify()
            self.assertNotEqual(result.returncode, 0, field)
            (reason,) = self.reasons(result)
            self.assertIn("integration", reason)
            self.assertIn(why, reason)
            self.assertEqual(json.loads(result.stdout)["items"][0]["state"], "UNKNOWN")
        (self.state_dir / "demo.json").write_text(json.dumps(original), encoding="utf-8")

    def test_S20_a_failing_whole_suite_names_its_command_and_log(self):
        self.delivered(lands="almost")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        (reason,) = self.reasons(result)
        self.assertIn("full FAIL", reason)
        self.assertIn(FEATURE_DONE, reason)

    def test_S20_a_plan_that_never_reached_integration_is_not_accepted(self):
        self.write_plan()
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned default-branch commit", self.reasons(result)[0])

    def test_S20_acceptance_is_computed_the_same_from_the_ledger_data(self):
        """The pure function `verify` and the driver share: the pin's tree must be the commit's."""
        data = {"integration": {"sha": "a" * 40, "tree": "t", "digest": "d", "attempt": 1}}
        self.assertEqual(verify.acceptance(data, digest="d")["state"], "ACCEPTED")
        got = verify.acceptance(data, digest="d", tree_of=lambda sha: "other")
        self.assertEqual((got["state"], got["items"][0]["state"]), ("REJECTED", "UNKNOWN"))
        failed = dict(data, full={"state": "FAIL", "attempt": 1, "command": "x", "sha": "a" * 40})
        got = verify.acceptance(failed, digest="d")
        self.assertEqual((got["state"], got["items"][0]["scenario"]), ("REJECTED", "full"))


class RejectionTests(AcceptanceFixture, unittest.TestCase):
    def test_S22_a_plan_whose_tickets_completed_but_whose_outcome_fails_is_never_accepted_or_archived(
        self,
    ):
        self.delivered(lands="almost")
        self.assertEqual(self.history()[-1], "REJECTED")
        self.assertNotIn("ACCEPTED", self.history())
        self.assertEqual(self.gh_calls("pr", "create"), [])
        self.assertNotEqual(self.verify().returncode, 0)

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
        self.assertNotEqual(self.verify().returncode, 0)

    def run_driver_result(self):
        return self.in_process(
            load_script(),
            self.driver_args("--repo", str(self.repo)),
        )


if __name__ == "__main__":
    unittest.main()
