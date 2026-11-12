"""Evidence at the ticket gate, integration on a pinned default-branch SHA, the acceptance states, `accept` and
`verify <plan>`: a YAML plan is accepted only on receipts the driver wrote for the exact tree and contract."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
import yaml
from agent_orchestrator import plan as yaml_plan
from agent_orchestrator import verify
from fixtures import load_script, report_wait, spawn_id
from test_archive import plan_copies
from test_gates import GateFixture

pytestmark = pytest.mark.integration

PROBE = "probe\n"
GOOD = "grep -qx done src/a/feature.txt && test -f checks/probe.txt"
STATES = ["APPROVED", "RUNNING", "DELIVERED", "VERIFYING"]


def scenario(sid, evidence=None):
    return dict(
        id=sid, when="a %s" % sid, then="b %s" % sid, **({"evidence": evidence} if evidence else {})
    )


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


def check_item(check=GOOD):
    return {"check": check, "oracle": ["probe.txt.oracle"], "place": "checks/", "baseline": "fail"}


def plan_data(scenarios, tickets, outcome=("S1",)):
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "The feature file says done.",
        "spec": "docs/specs/demo.md",
        "outcome": list(outcome),
        "scenarios": scenarios,
        "tickets": tickets,
    }


def model(scenarios, tickets, outcome=("S1",)):
    return yaml_plan.Plan.model_validate(plan_data(scenarios, tickets, outcome))


class RoutingTests(unittest.TestCase):
    """Which scenarios' evidence runs at a ticket's gate (unit: no process, no repository)."""

    def routed(self, plan, ticket_id, done=()):
        return verify.gate_scenarios(plan, ticket_id, set(done))

    def test_S16_a_risk_tagged_ticket_runs_the_evidence_of_the_scenarios_it_satisfies(self):
        plan = model(
            [scenario("S1", check_item()), scenario("S2", check_item("true")), scenario("S3")],
            [
                ticket("T1", ("S2", "S3"), risk=["state"]),
                ticket("T2", ("S1",)),
            ],
        )
        self.assertEqual(self.routed(plan, "T1"), ["S2"])

    def test_S16_a_ticket_without_risk_that_does_not_complete_an_outcome_runs_no_evidence(self):
        plan = model(
            [scenario("S1", check_item()), scenario("S2", check_item("true"))],
            [ticket("T1", ("S2",)), ticket("T2", ("S1",))],
        )
        self.assertEqual(self.routed(plan, "T1"), [])

    def test_S16_the_ticket_that_completes_an_outcomes_satisfies_set_runs_it_whatever_its_risk(
        self,
    ):
        plan = model(
            [scenario("S1", check_item())],
            [ticket("T1", ("S1",)), ticket("T2", ("S1",), blocked_by=["T1"])],
        )
        self.assertEqual(self.routed(plan, "T1"), [], "T2 still has to deliver its part")
        self.assertEqual(self.routed(plan, "T2", done=["T1"]), ["S1"])
        self.assertEqual(self.routed(plan, "T1", done=["T2"]), ["S1"])

    def test_S16_human_evidence_never_runs_at_a_gate(self):
        plan = model(
            [scenario("S1", {"human": "confirm"})], [ticket("T1", ("S1",), risk=["state"])]
        )
        self.assertEqual(self.routed(plan, "T1"), [])


class AcceptanceFixture(GateFixture):
    def setUp(self):
        super().setUp()
        self.plan.unlink()
        self.plan = self.repo / "docs" / "plans" / "2026-11-10-demo.plan.yaml"
        self.oracle_dir = self.repo / "docs" / "plans" / "2026-11-10-demo.oracle"
        self.oracle_dir.mkdir()
        (self.oracle_dir / "probe.txt.oracle").write_text(PROBE, encoding="utf-8")
        self.COVERS = {"S1": ["src/a/test_feature.py::test_feature"]}

    def write_plan(self, scenarios=None, tickets=None):
        scenarios = scenarios or [scenario("S1", check_item())]
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

    def default_mutator(self, attempt=1, patch=None):
        """A wait step: the fresh mutation reviewer of an uncalibrated oracle writes a behavioural patch."""
        step = report_wait("demo-mut-s1-%d" % attempt, "complete")
        step["write"] = [
            [
                str(self.run_dir / "mutation" / ("S1.%d.patch" % attempt)),
                patch
                or "--- a/src/a/feature.txt\n+++ b/src/a/feature.txt\n@@ -1 +1 @@\n-done\n+nope\n",
            ]
        ]
        return step

    def archive_steps(self):
        """The archive worker and the gaps reviewer of a plan that was accepted."""
        archiver = report_wait("demo-archive", "complete", id=spawn_id("demo-archive"))
        archiver["write"] = plan_copies(
            self.plan, self.root / "repo.worktrees" / "demo-archive"
        ) + [
            [
                str(self.run_dir / "archive" / "report.json"),
                self.report("archive", pr="https://github.com/o/r/pull/9"),
            ],
            [
                str(self.root / "repo.worktrees" / "demo-archive" / "docs" / "specs" / "demo.md"),
                "# Demo\nPaths: src/a/\n",
            ],
        ]
        gaps = report_wait("demo-gaps", "complete")
        gaps["write"] = [[str(self.run_dir / "gaps.md"), "# Gaps\n- none\n"]]
        return [archiver, gaps]

    def drive(self, steps, archive=False):
        self.waits(steps + (self.archive_steps() if archive else []))
        self.run_driver("--repo", str(self.repo))

    def delivered(self, gate="done", lands="done", scenarios=None, tickets=None, archive=False):
        self.write_plan(scenarios, tickets)
        if lands:
            self.merged_on_main(lands)
        self.drive([self.worker(gate), self.reviewer(), self.default_mutator()], archive)

    def receipts(self, phase=None):
        found = [
            json.loads(p.read_text(encoding="utf-8"))
            for p in sorted((self.run_dir / "receipts").glob("*.json"))
        ]
        return [r for r in found if phase in (None, r["phase"])]

    def status(self):
        result = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def verify(self, *extra):
        return self.cli("verify", str(self.plan), "--state-dir", str(self.state_dir), *extra)

    def accept(self, sid, note="looked at it", *extra):
        return self.cli(
            "accept",
            str(self.plan),
            sid,
            "--note",
            note,
            "--state-dir",
            str(self.state_dir),
            *extra,
        )

    def origin_tree(self):
        return self.git(self.origin, "rev-parse", "main^{tree}")

    def history(self):
        return [h["state"] for h in self.status()["history"]]


class TicketGateTests(AcceptanceFixture, unittest.TestCase):
    def test_S16_the_completing_ticket_runs_its_evidence_at_its_head_after_the_invariants(self):
        """The worker's work is not committed yet: the evidence runs on the tree the ticket holds."""
        self.delivered(archive=True)
        (receipt,) = self.receipts("ticket")
        self.assertEqual((receipt["scenario"], receipt["result"]), ("S1", "PASS"))
        self.assertEqual(
            receipt["digest"],
            yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]["digest"],
        )
        self.assertIn(
            "demo-t1-review", self.spawned(), "a passing ticket evidence goes on to the reviewer"
        )
        stem = "S1.ticket.1"
        recorded = self.ledger()["receipts"][stem]
        raw = (self.run_dir / "receipts" / (stem + ".json")).read_bytes()
        self.assertEqual(
            recorded, hashlib.sha256(raw).hexdigest(), "the ledger holds the receipt's sha256"
        )

    def test_S16_a_failing_evidence_is_a_fix_round_carrying_the_command_and_the_log_tail(self):
        check = "echo the-feature-says-$(cat src/a/feature.txt) >&2; grep -qx done src/a/feature.txt && test -f checks/probe.txt"
        self.write_plan([scenario("S1", check_item(check))])
        self.drive([self.worker("almost")])
        (receipt,) = self.receipts("ticket")
        self.assertEqual(receipt["result"], "FAIL")
        items = self.first_findings()["items"]
        self.assertEqual(len(items), 1, items)
        self.assertEqual(items[0]["command"], check)
        self.assertIn("the-feature-says-almost", items[0]["excerpt"])
        self.assertEqual(self.first_findings()["source"], "verify")
        self.assertTrue(Path(items[0]["log_path"]).is_file())
        self.assertNotIn("demo-t1-review", self.spawned(), "a failing evidence is not reviewed")

    def test_S16_an_unknown_evidence_is_a_fix_round_too(self):
        check = "if [ -f src/a/feature.txt ]; then sleep 30; else exit 1; fi"
        item = dict(check_item(check), timeout=1)
        self.write_plan([scenario("S1", item)])
        self.drive([self.worker("done")])
        (receipt,) = self.receipts("ticket")
        self.assertEqual((receipt["result"], receipt["reason"]), ("UNKNOWN", "timeout"))
        (finding,) = self.first_findings()["items"]
        self.assertIn("UNKNOWN", finding["excerpt"])

    def test_S16_a_ticket_that_routes_no_evidence_runs_none(self):
        self.write_plan(
            [
                scenario("S1", check_item()),
                scenario("S2", {"check": "test -f src/a/feature.txt", "baseline": "fail"}),
            ],
            [ticket("T1", ("S2",)), ticket("T2", ("S1",), blocked_by=["T1"])],
        )
        self.COVERS = {"S2": ["src/a/test_feature.py::test_feature"]}
        self.waits([self.worker("done"), self.reviewer(), self.default_mutator()])
        self.run_driver("--repo", str(self.repo))
        self.assertEqual(self.receipts("ticket"), [])


class IntegrationTests(AcceptanceFixture, unittest.TestCase):
    def test_S17_every_ticket_complete_pins_the_fetched_default_branch_and_accepts_on_its_tree(
        self,
    ):
        self.delivered(archive=True)
        self.assertEqual(self.history(), STATES + ["ACCEPTED", "ARCHIVED"])
        (receipt,) = [r for r in self.receipts("integration") if r["scenario"] == "S1"]
        digest = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]["digest"]
        self.assertEqual(
            (receipt["result"], receipt["tree"], receipt["digest"]),
            ("PASS", self.origin_tree(), digest),
        )
        pin = self.ledger()["integration"]
        self.assertEqual(pin["sha"], self.git(self.origin, "rev-parse", "main"))
        self.assertEqual(pin["tree"], self.origin_tree())
        self.assertIn("demo-archive", self.spawned(), "an accepted plan may be archived")
        self.assertEqual(self.verify().returncode, 0)

    def test_S17_an_integration_that_fails_rejects_the_plan_and_asks_the_lead(self):
        self.delivered(lands="almost")
        self.assertEqual(self.history(), STATES + ["REJECTED"])
        self.assertEqual(self.status()["tickets"][0]["state"], "COMPLETE")
        self.assertEqual([r["result"] for r in self.receipts("integration")], ["FAIL"])
        self.assertNotIn("demo-archive", self.spawned(), "a rejected plan is not archived")
        (wake,) = [w for w in self.wakes() if w["kind"] == "plan-rejected"]
        for way_out in ("S1", "ticket", "run", "accept"):
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
        self.drive([self.worker("done"), self.reviewer(), self.default_mutator()])
        self.assertEqual(
            self.history(),
            ["APPROVED", "RUNNING"],
            "nothing is delivered until a commit holds the merge",
        )
        self.assertEqual(self.receipts("integration"), [])
        (wake,) = [w for w in self.wakes() if w["kind"] == "integration-waits"]
        self.assertIn("T1", wake["text"])

    def test_S17_a_merge_commit_the_repository_does_not_have_is_not_contained_either(self):
        self.write_plan()
        self.prs([{"state": "MERGED", "mergeCommit": {"oid": "f" * 40}}])
        self.drive(
            [self.worker("done"), self.reviewer(), self.default_mutator()]
        )  # gh answers a merge commit that exists nowhere
        self.assertEqual(self.history(), ["APPROVED", "RUNNING"])
        self.assertEqual(self.receipts("integration"), [])
        self.assertTrue([w for w in self.wakes() if w["kind"] == "integration-waits"])

    def test_S17_a_ticket_complete_without_a_known_merge_commit_makes_integration_wait(self):
        self.write_plan()
        self.merged_on_main("done")
        self.prs([{"state": "MERGED", "mergeCommit": None}])  # gh names no merge commit
        self.drive([self.worker("done"), self.reviewer(), self.default_mutator()])
        self.assertEqual(
            self.history(),
            ["APPROVED", "RUNNING"],
            "a ticket whose merge is unknown is not contained by any commit",
        )
        self.assertEqual(self.receipts("integration"), [])
        (wake,) = [w for w in self.wakes() if w["kind"] == "integration-waits"]
        self.assertIn("T1", wake["text"])

    def test_S17_a_new_run_verifies_a_rejected_plan_again(self):
        self.delivered(lands="almost")
        subprocess.run(self.land("done"), check=True, capture_output=True)
        self.drive([self.default_mutator()], archive=True)
        self.assertEqual(self.history()[-1], "ARCHIVED")
        self.assertEqual([r["result"] for r in self.receipts("integration")], ["FAIL", "PASS"])


class HumanTests(AcceptanceFixture, unittest.TestCase):
    SCENARIOS = None

    def two(self):
        return [
            scenario("S1", check_item()),
            scenario("S2", {"human": "a person confirms the panel"}),
        ]

    def delivered_with_human(self, lands="done"):
        self.COVERS = {
            "S1": ["src/a/test_feature.py::test_feature"],
            "S2": ["src/a/test_feature.py::test_feature"],
        }
        self.delivered(lands=lands, scenarios=self.two())

    def test_S18_a_human_item_waits_for_a_person_and_the_archive_waits_for_it(self):
        self.delivered_with_human()
        self.assertEqual(self.history(), STATES)
        self.assertNotIn("demo-archive", self.spawned())
        self.assertTrue([w for w in self.wakes() if w["kind"] == "plan-needs-accept"])
        self.assertNotEqual(self.verify().returncode, 0)

    def test_S18_accept_records_the_person_time_scope_and_tree_and_the_plan_is_accepted_with_human(
        self,
    ):
        self.delivered_with_human()
        done = self.accept("S2", "checked the panel")
        self.assertEqual(done.returncode, 0, done.stderr)
        record = self.ledger()["acceptance"]["S2"]
        self.assertEqual(record["state"], "HUMAN_ACCEPTED")
        self.assertEqual(record["tree"], self.origin_tree())
        for key in ("by", "at", "scope", "note", "digest"):
            self.assertTrue(record[key], key)
        self.assertIn("S2", record["scope"])
        self.assertEqual(self.history()[-1], "ACCEPTED_WITH_HUMAN")
        self.assertNotIn("ACCEPTED", self.history())
        verified = self.verify()
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["state"], "ACCEPTED_WITH_HUMAN")

    def test_S18_a_run_after_accept_archives_the_plan(self):
        self.delivered_with_human()
        self.assertEqual(self.accept("S2").returncode, 0)
        self.drive([], archive=True)
        self.assertIn("demo-archive", self.spawned())

    def test_S18_the_lead_human_accepts_a_rejected_item(self):
        self.delivered(lands="almost")
        done = self.accept("S1", "the oracle is wrong about the file; verified by hand")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.history()[-2:], ["REJECTED", "ACCEPTED_WITH_HUMAN"])
        self.assertEqual(self.ledger()["acceptance"]["S1"]["previous"]["result"], "FAIL")

    def test_S25_a_plan_accepted_with_human_over_a_failing_outcome_archives_without_a_fix_round(
        self,
    ):
        self.delivered(lands="almost")
        self.assertEqual(self.accept("S1").returncode, 0)
        self.drive([], archive=True)
        self.assertEqual(self.history()[-2:], ["ACCEPTED_WITH_HUMAN", "ARCHIVED"])
        self.assertEqual(self.spawned().count("demo-archive"), 1)
        self.assertEqual(self.receipts("archive"), [], "an item a person accepted is not re-run")

    def test_S18_accept_refuses_an_item_that_passes_and_a_spawned_worker(self):
        self.delivered_with_human()
        refused = self.accept("S1")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("passes", refused.stderr)
        self.assertNotIn("acceptance", self.ledger())
        out = io.StringIO()
        with (
            mock.patch.object(
                load_script(), "resolve_runtime", return_value={"role": "worker", "parent": "p"}
            ),
            redirect_stdout(out),
            redirect_stderr(out),
        ):
            code = load_script().run_cli(
                ["accept", str(self.plan), "S2", "--note", "x", "--state-dir", str(self.state_dir)]
            )
        self.assertEqual(code, 2, out.getvalue())
        self.assertIn("Lead", out.getvalue())
        self.assertNotIn("acceptance", self.ledger())

    def test_S18_accept_needs_a_note(self):
        self.delivered_with_human()
        result = self.cli("accept", str(self.plan), "S2", "--state-dir", str(self.state_dir))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--note", result.stderr)


class UnverifiedTests(AcceptanceFixture, unittest.TestCase):
    def test_S19_a_person_merges_a_ticket_whose_evidence_failed_and_the_plan_cannot_be_accepted(
        self,
    ):
        """The ticket evidence failed at its gate (a fix round was open) and the pull request was merged by hand."""
        check = "grep -qx done src/a/feature.txt && test -f checks/probe.txt"
        self.write_plan(
            [scenario("S1", check_item(check))], [ticket("T1", ("S1",), risk=["state"])]
        )
        sha = self.merged_on_main("almost")
        self.early([{"state": "MERGED", "mergeCommit": {"oid": sha}}])
        self.drive([self.worker("almost")])
        status = self.status()
        self.assertEqual(status["tickets"][0]["state"], "COMPLETE")
        self.assertTrue(status["tickets"][0]["unverified"])
        self.assertEqual(self.ledger()["unverified"]["T1"]["scenarios"], ["S1"])
        self.assertEqual(self.receipts("ticket")[0]["result"], "FAIL")
        self.assertEqual(self.history()[-1], "REJECTED", "the evidence still fails at integration")
        refused = self.accept("S1")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("unverified", refused.stderr)
        self.assertNotEqual(self.verify().returncode, 0)

    def test_S19_a_ticket_merged_after_its_evidence_passed_is_not_flagged(self):
        self.delivered(archive=True)
        self.assertNotIn("unverified", self.status()["tickets"][0])
        self.assertNotIn("unverified", self.ledger())


class VerifyTests(AcceptanceFixture, unittest.TestCase):
    def receipt_path(self, phase="integration"):
        return next(
            p
            for p in sorted((self.run_dir / "receipts").glob("*.json"))
            if json.loads(p.read_text(encoding="utf-8"))["phase"] == phase
        )

    def reasons(self, result):
        return [e for e in json.loads(result.stdout)["errors"]]

    def test_S20_verify_accepts_a_delivered_plan_and_names_each_item(self):
        self.delivered(archive=True)
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stderr)
        body = json.loads(result.stdout)
        self.assertEqual(body["state"], "ACCEPTED")
        self.assertEqual([(i["scenario"], i["state"]) for i in body["items"]], [("S1", "PASS")])

    def test_S20_a_missing_receipt_does_not_count(self):
        self.delivered(archive=True)
        path = self.receipt_path()
        shutil.move(str(path), str(path) + ".gone")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        (reason,) = self.reasons(result)
        self.assertIn("S1", reason)
        self.assertIn("missing", reason)
        self.assertEqual(json.loads(result.stdout)["items"][0]["state"], "UNKNOWN")

    def test_S20_a_receipt_for_another_tree_or_digest_does_not_count(self):
        self.delivered(archive=True)
        path = self.receipt_path()
        original = path.read_text(encoding="utf-8")
        for field, why in (("tree", "another tree"), ("digest", "another approval digest")):
            forged = (
                json.dumps(
                    dict(json.loads(original), **{field: "0" * 40}), indent=2, sort_keys=True
                )
                + "\n"
            )
            path.write_text(forged, encoding="utf-8")
            data = self.ledger()
            data["receipts"][path.stem] = hashlib.sha256(
                forged.encode()
            ).hexdigest()  # the ledger agrees: still stale
            (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
            result = self.verify()
            self.assertNotEqual(result.returncode, 0, field)
            (reason,) = self.reasons(result)
            self.assertIn("S1", reason)
            self.assertIn(why, reason)
        path.write_text(original, encoding="utf-8")

    def test_S20_a_receipt_that_no_longer_matches_the_ledger_hash_does_not_count(self):
        self.delivered(archive=True)
        path = self.receipt_path()
        path.write_text(
            path.read_text(encoding="utf-8").replace('"PASS"', '"PASS" '), encoding="utf-8"
        )
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        (reason,) = self.reasons(result)
        self.assertIn("S1", reason)
        self.assertIn("sha256", reason)

    def test_S3_a_receipt_rewritten_in_place_keeping_digest_tree_and_result_does_not_count(self):
        """A Pi worker has no hooks: it can rewrite a receipt, keeping its digest, tree and result, and the ledger must still refuse it."""
        self.delivered(archive=True)
        path = self.receipt_path()
        original = json.loads(path.read_text(encoding="utf-8"))
        rewritten = json.dumps(original, indent=4, sort_keys=False) + "\n\n"
        self.assertNotEqual(rewritten, path.read_text(encoding="utf-8"))
        path.write_text(rewritten, encoding="utf-8")
        for field in ("digest", "tree", "result"):
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[field], original[field])
        result = self.verify()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        (reason,) = self.reasons(result)
        self.assertIn("S1", reason)
        self.assertIn("sha256", reason)
        self.assertEqual(json.loads(result.stdout)["items"][0]["state"], "UNKNOWN")

    def test_S20_a_plan_that_never_reached_integration_is_not_accepted(self):
        self.write_plan()
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("S1", self.reasons(result)[0])

    def test_S20_acceptance_is_computed_the_same_from_the_ledger_data(self):
        """The pure function `verify` and the driver share: a human acceptance never turns a stale receipt into PASS."""
        plan = model([scenario("S1", check_item())], [ticket("T1")])
        data = {"integration": {"sha": "a" * 40, "tree": "t", "digest": "d"}, "receipts": {}}
        got = verify.acceptance(plan, data, Path("/nonexistent"), digest="d")
        self.assertEqual((got["state"], got["items"][0]["state"]), ("REJECTED", "UNKNOWN"))


class RejectionTests(AcceptanceFixture, unittest.TestCase):
    def test_S22_a_plan_whose_tickets_completed_but_whose_outcome_fails_is_never_accepted_or_archived(
        self,
    ):
        self.delivered(lands="almost")
        self.assertEqual(self.history()[-1], "REJECTED")
        self.assertNotIn("ACCEPTED", self.history())
        self.assertNotIn("demo-archive", self.spawned())
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
            [
                "run",
                str(self.plan),
                "--state-dir",
                str(self.state_dir),
                "--models-file",
                str(self.models),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
                "--repo",
                str(self.repo),
            ],
        )


if __name__ == "__main__":
    unittest.main()
