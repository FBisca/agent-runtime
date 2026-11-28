"""`reroute <plan> --route|--step ... --reason TEXT`: a person changes a run's routes while it runs.

The command rewrites the ledger's route record and logs the change; a live driver adopts it at its next round for
every agent it spawns after that, an agent already running keeps its route, and a restart resumes on the rerouted
record. A resume argument that differs from the record is still refused, and the refusal names `reroute`.
"""

import json
import unittest

import pytest
from fixtures import DRIVER, RunFixture, assignment, wait_result
from test_decider import DeciderFixture
from test_gates import GateFixture
from test_routes_resume import recorded

pytestmark = pytest.mark.integration

KIND = "agent-orchestrator/ledger"
REASON = "the reviewer moves to codex for every later review"


def spawns(calls):
    """(name, backend, model, effort) of every spawn, in order."""
    return [
        (c["args"]["name"], c["args"]["cli"], c["args"]["model"], c["args"].get("effort"))
        for c in calls
    ]


class RerouteFixture(RunFixture):
    def reroute_args(self, *routes, reason=REASON):
        """The command line of a reroute, without the program: `--route`/`--step` pairs and the reason."""
        args = ["reroute", str(self.plan), "--state-dir", str(self.state_dir)]
        for flag, value in routes:
            args += [flag, value]
        if reason is not None:
            args += ["--reason", reason]
        return args

    def reroute(self, *routes, reason=REASON, expect=0):
        self.alias_state(self.plan)
        result = self.cli(*self.reroute_args(*routes, reason=reason))
        self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def reroute_step(self, *routes, working=("demo-t1",)):
        """A wait step during which the Lead runs `reroute`; the named agents are still working."""
        return wait_result(
            [assignment(name) for name in working],
            run=[[*DRIVER, *self.reroute_args(*routes)]],
        )


class RerouteCommandTests(RerouteFixture, unittest.TestCase):
    """The command on a run's ledger, with no driver live."""

    def seed(self, record=None):
        """The ledger and run directory a run that already started leaves."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.alias_state(self.plan)
        data = {"version": 1, "kind": KIND, "tasks": {}, "lead": "orchestrate:demo"}
        data["routes"] = record or recorded(steps={"reviewer": ("claude", "opus", "xhigh")})
        self.ledger_file = self.state_dir / "demo.json"
        self.ledger_file.write_text(json.dumps(data), encoding="utf-8")
        self.run_dir_of().mkdir(parents=True, exist_ok=True)

    def refused(self, *routes, reason=REASON, mention=()):
        """The command exits 2 with one JSON refusal on stderr, and the ledger and the log are as they were."""
        before = self.ledger_file.read_bytes() if self.ledger_file.exists() else None
        result = self.reroute(*routes, reason=reason, expect=2)
        refusal = json.loads(result.stderr)
        self.assertEqual(sorted(refusal), ["error", "reason", "way_out"])
        self.assertTrue(refusal["way_out"])
        after = self.ledger_file.read_bytes() if self.ledger_file.exists() else None
        self.assertEqual(after, before)
        self.assertEqual(self.run_events("reroute"), [])
        for word in mention:
            self.assertIn(word, result.stderr)
        return refusal

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_reroute_rewrites_the_recorded_routes_and_logs_the_change(self):
        """— the reviewer moves from claude/opus@xhigh to codex/gpt-6.2-sol@xhigh; the rest of the record stays."""
        self.seed()
        before = json.loads(self.ledger_file.read_text(encoding="utf-8"))["routes"]
        result = self.reroute(("--step", "reviewer=codex/gpt-6.2-sol@xhigh"))
        record = self.ledger()["routes"]
        self.assertEqual(
            record["steps"]["reviewer"],
            {"step": "reviewer", "backend": "codex", "model": "gpt-6.2-sol", "effort": "xhigh"},
        )
        self.assertEqual(record["classes"], before["classes"])
        self.assertEqual((record["source"], record["path"]), (before["source"], before["path"]))
        self.assertEqual(record["revision"], 1)
        (event,) = self.run_events("reroute")
        self.assertEqual(
            (event["step"], event["old"], event["new"], event["reason"], event["revision"]),
            ("reviewer", "claude/opus@xhigh", "codex/gpt-6.2-sol@xhigh", REASON, 1),
        )
        answer = json.loads(result.stdout)
        self.assertEqual(answer["revision"], 1)
        self.assertEqual(
            answer["changes"],
            [{"step": "reviewer", "old": "claude/opus@xhigh", "new": "codex/gpt-6.2-sol@xhigh"}],
        )

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_second_reroute_moves_the_revision_on_and_a_class_route_is_rerouted_too(self):
        """— each reroute is its own revision; a class route is rewritten like a step route."""
        self.seed()
        self.reroute(("--step", "reviewer=codex/gpt-6.2-sol@xhigh"))
        self.reroute(("--route", "large=codex/gpt-l@max"))
        record = self.ledger()["routes"]
        self.assertEqual(record["revision"], 2)
        self.assertEqual(
            (record["classes"]["large"]["backend"], record["classes"]["large"]["model"]),
            ("codex", "gpt-l"),
        )
        self.assertEqual(record["steps"]["reviewer"]["model"], "gpt-6.2-sol")
        self.assertEqual([e["revision"] for e in self.run_events("reroute")], [1, 2])

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_an_invalid_backend_is_refused_and_the_ledger_is_unchanged(self):
        """— a backend the driver cannot run is named, and nothing is written."""
        self.seed()
        refusal = self.refused(("--step", "reviewer=gemini/x@high"), mention=("gemini",))
        self.assertIn("reroute", refusal["way_out"])

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_reroute_without_a_reason_or_a_route_is_refused(self):
        """— a reroute says why and what; either missing is refused."""
        self.seed()
        self.refused(("--step", "reviewer=codex/gpt-l@xhigh"), reason=None, mention=("--reason",))
        self.refused(mention=("--route", "--step"))

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_plan_with_no_run_is_refused(self):
        """— there is no record to reroute before the run starts; `run` takes the routes then."""
        self.ledger_file = self.state_dir / "demo.json"
        refusal = self.refused(("--step", "reviewer=codex/gpt-l@xhigh"))
        self.assertIn("run", refusal["way_out"])

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_reroute_that_leaves_a_brain_run_without_a_reviewer_route_is_refused(self):
        """— a brain run reviews on its medium class; moving that class off the brain needs a reviewer route."""
        record = recorded(cli="claude", medium=("codex", "gpt-m", None))
        record["brain_cli"] = "codex"
        self.seed(record)
        self.refused(("--route", "medium=claude/sonnet"), mention=("--step reviewer",))


class RerouteLiveTests(RerouteFixture, GateFixture, unittest.TestCase):
    """A live driver adopts a reroute at its next round."""

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_live_driver_spawns_its_next_reviewer_on_the_new_route_and_leaves_the_running_worker(
        self,
    ):
        """— the worker started on claude/haiku keeps working on it; the reviewer spawned after the reroute runs on
        the rerouted step, and the pane says the run adopted it."""
        self.waits(
            [
                self.reroute_step(("--step", "reviewer=codex/gpt-6.2-sol@xhigh")),
                self.worker_done(tests=True),
                self.reviewer_done(),
            ]
        )
        result = self.run_driver()
        self.assertEqual(
            spawns(self.calls("spawn")),
            [
                ("demo-t1", "claude", "haiku", "low"),
                ("demo-t1-review", "codex", "gpt-6.2-sol", "xhigh"),
            ],
        )
        # The running worker is neither stopped nor resumed for the reroute: it is stopped only at the cleanup,
        # once it had handed in on the route it started on.
        self.assertEqual(self.calls("resume"), [])
        for stop in self.calls("stop"):
            logged = [json.loads(line)["kind"] for line in stop["events"]]
            self.assertIn("report", logged)
            self.assertLess(logged.index("reroute-adopted"), logged.index("report"))
        (adopted,) = self.run_events("reroute-adopted")
        self.assertEqual(adopted["revision"], 1)
        self.assertIn("rerouted the reviewer to codex/gpt-6.2-sol@xhigh", result.stderr)


class RerouteFreshWorkerTests(RerouteFixture, DeciderFixture, unittest.TestCase):
    """A fresh worker and an escalation, spawned after a reroute, run on the new route."""

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_a_fresh_worker_after_a_reroute_runs_on_the_new_class_route(self):
        """— the unit's first worker ran on claude/haiku; its fresh worker runs where small now routes."""
        self.waits(
            [
                self.reroute_step(("--route", "small=codex/gpt-6.2-luna@medium")),
                self.blocked(),
                self.decided("fresh worker"),
            ]
        )
        self.run_driver()
        first, fresh = spawns(self.calls("spawn"))
        self.assertEqual(first[1:3], ("claude", "haiku"))
        self.assertEqual(fresh, ("demo-t1", "codex", "gpt-6.2-luna", "medium"))
        packet = self.task("t1d1")["packet"]
        self.assertEqual(
            (packet["backend"], packet["model"], packet["effort"]),
            ("codex", "gpt-6.2-luna", "medium"),
        )
        self.assertEqual(self.task("t1")["packet"]["model"], "haiku")

    @pytest.mark.spec("A run is rerouted while it runs")
    def test_an_escalation_after_a_reroute_runs_on_the_rerouted_next_class(self):
        """— one class up lands on medium as it is routed now: its backend and its model."""
        self.waits(
            [
                self.reroute_step(("--route", "medium=codex/gpt-6.2-terra@high")),
                self.blocked(),
                self.decided("retry one class up"),
            ]
        )
        self.run_driver()
        _, fresh = spawns(self.calls("spawn"))
        self.assertEqual(fresh[:3], ("demo-t1", "codex", "gpt-6.2-terra"))
        self.assertEqual(self.task("t1d1")["packet"]["class"], "medium")


class RerouteRestartTests(RerouteFixture, unittest.TestCase):
    """A restart after a reroute: the rerouted record is the pinned record."""

    def setUp(self):
        super().setUp()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.alias_state(self.plan)
        data = {"version": 1, "kind": KIND, "tasks": {}, "lead": "orchestrate:demo"}
        data["routes"] = recorded()
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
        self.run_dir_of().mkdir(parents=True, exist_ok=True)

    def worker(self):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}["demo-t1"]

    @pytest.mark.spec("A resumed run keeps its routes")
    def test_a_restart_after_a_reroute_resumes_on_the_rerouted_record(self):
        """— small was codex/gpt-s@high; the reroute moved it, and the restart spawns there."""
        self.reroute(("--route", "small=pi/minimax/MiniMax-M3.1-Flash@max"))
        self.run_driver(routed=False)
        worker = self.worker()
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "max"),
        )

    @pytest.mark.spec("A resumed run keeps its routes")
    def test_a_resume_argument_that_matches_the_rerouted_record_is_accepted(self):
        """— the rerouted route said again on the command line is not a conflict."""
        self.reroute(("--step", "reviewer=codex/gpt-6.2-sol@xhigh"))
        self.run_driver("--step", "reviewer=codex/gpt-6.2-sol@xhigh", routed=False)
        self.assertEqual(self.worker()["model"], "gpt-s")

    @pytest.mark.spec("A resumed run keeps its routes")
    def test_a_conflicting_resume_step_is_still_refused_and_names_reroute(self):
        """— the reroute command is the one way to change a run's routes."""
        before = (self.state_dir / "demo.json").read_bytes()
        result = self.run_driver("--step", "reviewer=codex/gpt-l@xhigh", routed=False, expect=2)
        self.assertEqual(self.calls("spawn"), [])
        self.assertEqual((self.state_dir / "demo.json").read_bytes(), before)
        self.assertIn("--step", result.stderr)
        self.assertIn("agent-orchestrator reroute", result.stderr)


if __name__ == "__main__":
    unittest.main()
