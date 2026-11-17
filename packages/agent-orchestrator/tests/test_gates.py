"""The gates after a worker reports: converge, a fresh reviewer, fix rounds, a fresh worker for a dead pane."""

import io
import json
import os
import sys
import unittest
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
import yaml
from agent_orchestrator import contracts
from agent_orchestrator import plan as yaml_plan
from fixtures import (
    RunFixture,
    added,
    keep_scenarios,
    load_script,
    red_of,
    report_wait,
    resume_id,
    scenarios_of,
    spawn_id,
    wait_result,
)

pytestmark = pytest.mark.integration


MAJOR = {
    "id": "M1",
    "file": "src/a/x.py",
    "line": 7,
    "problem": "drops the last row",
    "fix": "include the final row",
}
BLOCKING = {
    "kind": "blocking",
    "summary": "drops the last row",
    "file": "src/a/x.py",
    "line": 7,
}


def held(data):
    """`data` with a person-owned sign-off ticket that waits on every agent ticket, unless it already has a person's
    ticket. A plan is ACCEPTED once its agent tickets are COMPLETE, and the archive starts only once every ticket is:
    the held ticket keeps a gate test's run from going on to build an archive it is not about."""
    tickets = data["tickets"]
    if any(str(t.get("owner", "agent")).strip().lower() != "agent" for t in tickets):
        return data
    tickets.append(
        dict(
            id="T9",
            delivers="sign-off",
            satisfies=[scenarios_of(data)[0]["id"]],
            paths=["docs/"],
            owner="Felippe",
            blocked_by=[t["id"] for t in tickets],
        )
    )
    return data


class GateFixture(RunFixture):
    # A gate test is about one unit, not the archive: its plan holds a person's ticket open. A fixture about the
    # archive or the plan's acceptance sets this False.
    HOLD = True
    """One ticket, scripted worker and reviewer steps; shared by the gate and merge tests."""

    def approved_yaml_plan(
        self, rows=("T1",), repo=None, name="demo.plan.yaml", mutate=None, state_dir=None
    ):
        """The fixture plan with only the scenarios its rows satisfy: each scenario needs a ticket whose tests prove
        it, and a scenario no ticket satisfies would hold a delivered plan's archive on a missing test node."""

        def satisfied_only(data):
            if mutate:
                mutate(data)
            wanted = {sid for ticket in data["tickets"] for sid in ticket["satisfies"]}
            keep_scenarios(data, wanted)
            if self.HOLD:
                held(data)

        return super().approved_yaml_plan(
            rows, repo=repo, name=name, mutate=satisfied_only, state_dir=state_dir
        )

    def setUp(self):
        super().setUp()
        self.expose_package_to_children()
        self.set_plan(("T1",))
        self.run_dir = self.state_dir / load_script().repo_key(self.repo) / "demo"
        self.worktree = self.root / "repo.worktrees" / "demo-t1"

    def expose_package_to_children(self):
        """Name the package's source on `PYTHONPATH` for the test's length, so a child that starts the CLI can import
        it.

        A test that reaches the process boundary starts `DRIVER` (`python -m agent_orchestrator`), and the evidence
        runner builds its tree with a plain `uv sync`, whose venv holds no workspace member: this process finds the
        package only because the suite's test path put it on `sys.path`, which a child does not inherit. The
        environment it does inherit is named here and taken back when the test ends."""
        source = str(Path(__file__).resolve().parents[1] / "src")
        before = os.environ.get("PYTHONPATH")
        entries = [entry for entry in (before or "").split(os.pathsep) if entry]
        if source in entries:
            return
        os.environ["PYTHONPATH"] = os.pathsep.join([source, *entries])

        def restore():
            if before is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = before

        self.addCleanup(restore)

    extra_files = None  # worktree path -> text the worker also writes
    COVERS = {"S1": ["src/a/test_a.py::test_a"]}

    def report(self, task_id, **over):
        yaml_plan = self.plan.name.endswith(".plan.yaml")
        data = dict(
            {
                # A YAML plan's ticket reports contract 3 and maps its scenarios to test nodes.
                "contract": 3 if yaml_plan else 1,
                **({"covers": self.COVERS} if yaml_plan else {}),
                "task_id": task_id,
                "status": "COMPLETE",
                "summary": "done",
                "acceptance_met": True,
                "files": [],
                "decisions": [],
                "interfaces": [],
                "checks": [],
                "review_rounds": 0,
                "usage": "unknown",
                "head": "@HEAD",
                "pr": "https://github.com/o/r/pull/7",
            },
            **over,
        )
        if yaml_plan and "red" not in over:
            data["red"] = red_of(data.get("covers"))
        return json.dumps(data)

    def worker_done(self, task_id="t1", tests=False, report=None, name="demo-t1"):
        """A wait step: the worker hands in its report through the runtime (and, with tests, writes a tagged test)."""
        fix_round = int(task_id.split("r")[1]) if "r" in task_id else 0
        step = report_wait(
            name,
            "complete",
            text=None if report is False else report or self.report(task_id),
            id=resume_id(name, fix_round) if fix_round else spawn_id(name),
        )
        step["write"] = []
        if tests:
            step["write"].append(
                [str(self.worktree / "src" / "a" / "test_a.py"), 'def test_a():\n    """S1"""\n']
            )
        for name_, text in (self.extra_files or {}).items():
            step["write"].append([str(self.worktree / name_), text])
        return step

    def use_yaml_plan(self, paths=("src/a/",), lint=True, unproven=False):
        """Replace the plan with an approved one-ticket plan. With `lint=False` the plan is approved as one approved
        before the lint existed. With `unproven` the ticket also satisfies S2, which the default covers leave
        unmapped."""
        data = {
            "schema": "plan/2",
            "slug": "demo",
            "goal": "deliver",
            "spec": "docs/specs/demo.md",
            "requirements": added(
                [dict(id="S1", when="a", then="b")]
                + ([dict(id="S2", when="c", then="d")] if unproven else [])
            ),
            "tickets": [
                dict(
                    id="T1",
                    delivers="first",
                    satisfies=["S1", "S2"] if unproven else ["S1"],
                    paths=list(paths),
                    size="small",
                    difficulty="simple",
                    max_fix_rounds=3,
                )
            ],
        }
        if self.HOLD:
            held(data)
        self.plan.unlink()
        self.plan = self.plan.with_suffix(".yaml")
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False))
        # The run uses the repo key's alias: it must exist before the run's directory does.
        self.alias_state(self.plan)
        out = io.StringIO()
        with (
            mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"}),
            mock.patch.dict(
                os.environ,
                {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "test-lead"},
            ),
            redirect_stdout(out),
            redirect_stderr(out),
            mock.patch.object(yaml_plan, "lint_problems", return_value=[])
            if not lint
            else nullcontext(),
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

    def first_findings(self):
        path = self.run_dir / "t1r1" / "findings.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def review(self, **over):
        """The verdict a reviewer hands in: a pass, or a block naming one finding per blocking item or major."""
        found = [
            {
                "file": item["file"],
                "line": item["line"],
                "quote": "x = 1",
                "claim": item.get("summary") or item["problem"],
            }
            for item in [*over.get("items", []), *over.get("major", [])]
            if item.get("kind", "blocking") == "blocking"
        ]
        return json.dumps({"verdict": "block" if found else "pass", "findings": found})

    review2 = review

    def reviewer_done(self, review=None, write=None):
        """A wait step in which the reviewer reports complete with its verdict as the text."""
        return report_wait("demo-t1-review", "complete", text=review or self.review())

    def task(self, task_id="t1"):
        return self.ledger()["tasks"][task_id]

    def request(self, unit="t1"):
        """The unit's first request.json: what the Lead was asked."""
        return json.loads((self.run_dir / unit / "request.json").read_text(encoding="utf-8"))

    def skipped(self):
        """A wait step in which the Lead drops the unit, so a run that ends in a request can finish."""
        return self.decide_step("t1", "skip")

    def spawned(self):
        return [c["args"]["name"] for c in self.calls("spawn")]


class GateTests(GateFixture, unittest.TestCase):
    def test_S5_failing_verify_resumes_the_worker_in_a_new_task_directory(self):
        """code-driven-orchestrator/S5"""
        self.waits([self.worker_done()])
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual(len(resumes), 1)
        self.assertEqual(resumes[0]["args"]["name"], "demo-t1")
        self.assertEqual(
            resumes[0]["args"]["task"],
            "Read %s and follow it." % (self.run_dir / "t1r1" / "task.md"),
        )
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(
            (findings["contract"], findings["source"], findings["attempt"]), (2, "verify", 1)
        )
        self.assertEqual(
            [(i["source"], i["check"]) for i in findings["items"]],
            [("verify", "covers")],
        )
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "S1: test node src/a/test_a.py::test_a does not exist",
            "t1r1",
            "findings.json",
        ):
            self.assertIn(needle, task_md)
        self.assertEqual(self.spawned(), ["demo-t1"])
        tasks = self.ledger()["tasks"]
        self.assertEqual(
            (tasks["t1"]["status"], tasks["t1"]["report"]["review_rounds"]), ("RELEASED", 1)
        )
        self.assertEqual(
            (tasks["t1r1"]["status"], tasks["t1r1"]["runtime_id"]), ("ACTIVE", "term-demo-t1")
        )
        self.assertEqual(tasks["t1r1"]["packet"]["unit"], "t1")
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 1)

    def ledger_unit(self, unit):
        from fixtures import load_script

        data = self.ledger()
        return load_script().unit_summary(data, unit)

    def test_S5_a_fix_round_passing_converge_goes_to_the_reviewer(self):
        """code-driven-orchestrator/S5 — (a fix round is reviewed since orchestrate-lean-loop/S8)"""
        self.waits([self.worker_done(), self.worker_done("t1r1", tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 1)
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.task("t1r1")["gate"], "done")

    def test_S5_a_third_failure_goes_to_the_lead_not_a_third_fix_round(self):
        """code-driven-orchestrator/S5"""
        self.waits(
            [self.worker_done(), self.worker_done("t1r1"), self.worker_done("t1r2"), self.skipped()]
        )
        self.run_driver()
        self.assertEqual(len(self.calls("resume")), 2)
        self.assertEqual(self.request()["task"], "t1r2")
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.ledger_unit("t1")["failed_reviews"], 3)

    @pytest.mark.spec("Request files are repaired")
    def test_an_unreadable_request_file_is_written_again_and_the_lead_can_decide(self):
        """— (a crash left a 0-byte request.json behind)"""
        path = self.run_dir / "t1" / "request.json"
        emptied = wait_result(run=[[sys.executable, "-c", "open(%r, 'w').close()" % str(path)]])
        self.waits(
            [
                self.worker_done(),
                self.worker_done("t1r1"),
                self.worker_done("t1r2"),
                emptied,
                self.skipped(),
            ]
        )
        self.run_driver()
        request = self.request()
        self.assertEqual((request["task"], request["status"]), ("t1r2", "resolved"))
        self.assertEqual(self.task("t1r2")["gate"], "skipped")

    def test_S6_passing_converge_spawns_a_fresh_large_reviewer_with_paths_only(self):
        """code-driven-orchestrator/S6"""
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review"])
        args = self.calls("spawn")[1]["args"]
        self.assertEqual(
            (args["kind"], args["model"], args["effort"], args["cwd"]),
            ("worker", "opus", "high", str(self.worktree)),
        )
        self.assertEqual(
            args["task"],
            "Read %s and follow it. Hand in the verdict as the text of your `report` call."
            % (self.run_dir / "t1-review" / "task.md"),
        )
        text = (self.run_dir / "t1-review" / "task.md").read_text(encoding="utf-8")
        for needle in (
            str(self.run_dir / "t1" / "task.md"),
            str(self.plan),
            "origin/main...",
            str(self.worktree),
            "Name what a user or caller would hit",
        ):
            self.assertIn(needle, text)
        task = self.task()
        self.assertEqual(
            (task["status"], task["gate"], task["report"]["head"]),
            ("PARTIAL", "reviewing", self.git(self.worktree, "rev-parse", "HEAD")),
        )
        self.assertEqual(self.calls("resume"), [])

    def test_S6_a_blocking_review_sends_its_findings_as_a_fix_round(self):
        """code-driven-orchestrator/S6"""
        review = self.review(major=[MAJOR])
        self.waits([self.worker_done(tests=True), self.reviewer_done(review)])
        self.run_driver()
        resumes = self.calls("resume")
        self.assertEqual([r["args"]["name"] for r in resumes], ["demo-t1"])
        findings = json.loads((self.run_dir / "t1r1" / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(findings["source"], "review")
        (item,) = findings["items"]
        self.assertEqual((item["id"], item["file"], item["line"]), ("R1", "src/a/x.py", 7))
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("drops the last row", task_md)
        self.assertIn("src/a/x.py", task_md)
        self.assertEqual(self.task()["report"]["review_rounds"], 1)
        self.assertEqual(self.task("t1r1")["status"], "ACTIVE")

    def test_S6_a_passing_review_hands_the_ticket_to_the_ci_gate(self):
        """code-driven-orchestrator/S6"""
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        task = self.task()
        self.assertEqual((task["status"], task["gate"]), ("COMPLETE", "done"))
        self.assertEqual(task["report"]["task_id"], "t1")
        self.assertTrue(self.ticket_status("T1").startswith("COMPLETE "))

    def test_S7_a_fix_round_for_a_dead_pane_spawns_a_fresh_worker_with_the_original_task(self):
        """code-driven-orchestrator/S7"""
        self.states({"demo-t1": "exited"})
        self.waits([self.worker_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])
        fresh = self.calls("spawn")[1]["args"]
        for needle in (
            str(self.run_dir / "t1" / "task.md"),
            str(self.run_dir / "t1r1" / "findings.json"),
            str(self.run_dir / "t1r1" / "task.md"),
        ):
            self.assertIn(needle, fresh["task"])
        first = self.calls("spawn")[0]["args"]
        self.assertEqual(
            (fresh["cwd"], fresh["model"], fresh["effort"]),
            (first["cwd"], first["model"], first["effort"]),
        )
        self.assertEqual(self.calls("status")[0]["args"], {"name": "demo-t1"})
        tasks = self.ledger()["tasks"]
        self.assertEqual((tasks["t1"]["status"], tasks["t1r1"]["status"]), ("RELEASED", "ACTIVE"))
        self.assertEqual(tasks["t1r1"]["runtime_id"], "term-demo-t1")

    def test_S7_an_unreadable_status_counts_as_a_gone_pane(self):
        """code-driven-orchestrator/S7"""
        self.states({"demo-t1": "error"})
        self.waits([self.worker_done()])
        self.run_driver()
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])


class CheckoutTimeoutTests(GateFixture, unittest.TestCase):
    @pytest.mark.spec("Whole-tree git calls get a large timeout")
    def test_S3_worktree_add_gets_a_checkout_sized_timeout(self):
        """orchestrator-lead-alerts/S3"""
        from unittest import mock

        from fixtures import load_script

        driver = load_script()
        with mock.patch.object(driver, "run", wraps=driver.run) as spy:
            self.waits([self.worker_done()])
            self.run_driver()
        adds = [c for c in spy.call_args_list if c.args[0][3:5] == ["worktree", "add"]]
        self.assertTrue(adds, "no worktree was added")
        for call in adds:
            self.assertGreaterEqual(call.kwargs.get("timeout", 15), 300)


if __name__ == "__main__":
    unittest.main()


class VerifyGateTests(GateFixture, unittest.TestCase):
    """A YAML plan's ticket is gated by verify's invariants, not by converge."""

    def setUp(self):
        super().setUp()
        self.use_yaml_plan()

    def found(self):
        findings = self.first_findings()
        self.assertEqual((findings["contract"], findings["source"]), (2, "verify"))
        return [(i["check"], i["excerpt"]) for i in findings["items"]]

    def test_S9_a_report_that_maps_every_satisfied_scenario_to_changed_nodes_goes_to_review(self):
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])
        self.assertEqual(self.calls("resume"), [])

    def test_S9_a_node_that_does_not_exist_is_a_fix_round_naming_scenario_node_and_reason(self):
        covers = {"S1": ["src/a/test_gone.py::test_nope"]}
        report = self.report("t1", covers=covers, red={"S1": "FAILED test_nope"})
        self.waits([self.worker_done(tests=True, report=report)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [
                (
                    "covers",
                    "S1: test node src/a/test_gone.py::test_nope does not exist"
                    " (no file src/a/test_gone.py)",
                )
            ],
        )
        self.assertEqual([r["args"]["name"] for r in self.calls("resume")], ["demo-t1"])
        self.assertEqual(self.spawned(), ["demo-t1"])
        task_md = (self.run_dir / "t1r1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("test_nope", task_md)

    @pytest.mark.spec("Agent tickets hold a test path")
    @pytest.mark.spec("Re-approval under a live driver")
    def test_a_covers_failure_on_paths_that_hold_no_test_names_the_reapproval(self):
        self.use_yaml_plan(paths=("src/a/x.py",), lint=False, unproven=True)
        self.extra_files = {"src/a/x.py": "x = 1\n"}
        self.waits(
            [
                self.worker_done(report=self.report("t1", covers={})),
                self.worker_done("t1r1", report=self.report("t1r1", covers={})),
                self.worker_done("t1r2", report=self.report("t1r2", covers={})),
                self.worker_done("t1r3", report=self.report("t1r3", covers={})),
                self.skipped(),
            ]
        )
        self.run_driver()
        request = self.request()
        self.assertEqual(request["task"], "t1r3")
        self.assertIn("the ticket's Paths can hold no test", request["reason"])
        self.assertIn("agent-orchestrator plan approve ", request["reason"])
        self.assertIn(" --reason <why>", request["reason"])
        self.assertIn("decide `retry`", request["reason"])
        self.assertEqual([o["action"] for o in request["menu"]], list(contracts.ACTIONS))

    def test_S4_a_covers_failure_on_paths_that_hold_a_test_keeps_the_plain_reason_and_menu(self):
        self.use_yaml_plan(unproven=True)
        self.waits(
            [
                self.worker_done(tests=True, report=self.report("t1", covers={})),
                self.worker_done("t1r1", tests=True, report=self.report("t1r1", covers={})),
                self.worker_done("t1r2", tests=True, report=self.report("t1r2", covers={})),
                self.worker_done("t1r3", tests=True, report=self.report("t1r3", covers={})),
                self.skipped(),
            ]
        )
        self.run_driver()
        request = self.request()
        self.assertNotIn("Paths can hold no test", request["reason"])
        first = request["menu"][0]
        self.assertEqual(first["action"] if isinstance(first, dict) else first, "retry")

    def test_S9_a_satisfied_scenario_left_unmapped_is_a_fix_round_naming_it(self):
        """An implementation provides its evidence: nothing in the plan exempts a scenario from its node."""
        report = self.report("t1", covers={})
        self.waits([self.worker_done(tests=True, report=report)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [("covers", "S1: no test node in covers; map the scenario to the tests that prove it")],
        )
        self.assertNotIn("demo-t1-review", self.spawned())

    def test_S9_a_report_that_is_not_contract_3_is_refused_by_name(self):
        report = json.loads(self.report("t1", contract=2))
        del report["covers"], report["red"]
        self.waits([self.worker_done(tests=True, report=json.dumps(report))])
        self.run_driver()
        found = self.found()
        self.assertEqual([c for c, _ in found], ["covers"])
        self.assertIn("report is contract 2, not 3", found[0][1])

    @pytest.mark.spec("Ticket scope and plan integrity")
    def test_S10_a_file_outside_the_ticket_paths_that_no_ticket_owns_goes_to_review(self):
        self.extra_files = {"src/z/extra.py": "x = 1\n"}
        self.waits([self.worker_done(tests=True), self.reviewer_done()])
        self.run_driver()
        self.assertEqual(self.spawned()[:2], ["demo-t1", "demo-t1-review"])

    def test_S10_touching_the_plan_is_an_integrity_failure_not_a_scope_one(self):
        self.extra_files = {"docs/plans/demo.plan.yaml": "schema: plan/2\n"}
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(
            self.found(),
            [
                (
                    "integrity",
                    "docs/plans/demo.plan.yaml is in the plan; a ticket never edits the plan that gates it",
                )
            ],
        )


class IsolationTests(GateFixture, unittest.TestCase):
    def test_D27_a_cli_approve_without_a_state_dir_writes_under_the_test_home(self):
        """yaml-eec/D27"""
        plan = self.approved_yaml_plan()
        real = Path.home() / ".local" / "state" / "orchestrate" / "approvals"
        before = sorted(real.iterdir()) if real.is_dir() else []
        result = self.cli("plan", "approve", str(plan), "--repo", str(self.repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        isolated = self.root / "home" / ".local" / "state" / "orchestrate" / "approvals"
        self.assertTrue(isolated.is_dir() and any(isolated.iterdir()))
        self.assertEqual(sorted(real.iterdir()) if real.is_dir() else [], before)
