"""The driver and the route config: a configured route is chosen and logged; an invalid one stops the run.

S2 — a configured route is chosen, validated and logged.
S3 — a route that names an unsupported backend or a class the run does not have fails before any worker is spawned.
S4 — the plan schema and the gate/review/PR state machine stay stable under a new provider and role route.
"""

import json
import os
import unittest
from unittest import mock

import pytest
from fixtures import RunFixture, plan_text, report_wait, spawn_id  # noqa: E402

pytestmark = pytest.mark.integration


def config(*entries, version=1):
    return {"version": version, "routes": [dict(entry) for entry in entries]}


def route(klass="small", backend="claude", model="haiku", **extra):
    entry = {"class": klass, "backend": backend, "model": model}
    entry.update(extra)
    return entry


class RouteSelectionRunTests(RunFixture, unittest.TestCase):
    """The driver: a configured route is chosen and logged, and an invalid one stops the run before any worker."""

    def routes_file(self, data, name="routes.json"):
        path = self.root / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def workers(self):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}

    def test_S2_the_configured_backend_model_and_effort_are_chosen(self):
        """orchestrator-engine/R2: P4/S2 — the config names the combination the worker runs on, and the spawn carries it."""
        path = self.routes_file(
            config(
                route("small", "codex", "gpt-s", effort="high"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path)
        worker = self.workers()["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "gpt-s", "high")
        )

    def test_S2_the_selected_route_is_logged_in_the_route_ledger(self):
        """orchestrator-engine/R2: P4/S2 — the ledger keeps the resolved route, so the run says what it spent and where."""
        path = self.routes_file(
            config(
                route("small", "codex", "gpt-s", effort="high"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path)
        record = self.ledger()["routes"]
        self.assertEqual(record["source"], "explicit")
        self.assertEqual(record["path"], path)
        self.assertEqual(
            record["classes"]["small"],
            {"class": "small", "backend": "codex", "model": "gpt-s", "effort": "high"},
        )

    def test_S2_the_selected_route_is_logged_in_the_event_log(self):
        """orchestrator-engine/R2: P4/S2 — the run says which route it dispatched on."""
        path = self.routes_file(
            config(
                route("small", "codex", "gpt-s", effort="high"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path)
        logged = [row for row in self.events("route") if row.get("unit") == "t1"]
        self.assertTrue(logged, "the dispatch logged no route")
        self.assertEqual(
            (logged[0]["backend"], logged[0]["model"], logged[0]["effort"]),
            ("codex", "gpt-s", "high"),
        )

    def test_S2_the_repository_policy_config_answers_when_no_file_is_named(self):
        """orchestrator-engine/R2: P4/S2 — the repository's own policy answers when the run names no config."""
        policy = self.repo / ".orchestrate" / "routes.json"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(
            json.dumps(
                config(
                    route("small", "codex", "gpt-s"),
                    route("medium", "claude", "sonnet"),
                    route("large", "claude", "opus"),
                )
            ),
            encoding="utf-8",
        )
        self.run_driver()
        worker = self.workers()["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("codex", "gpt-s"))
        self.assertEqual(self.ledger()["routes"]["source"], "repo")

    def test_S2_the_user_default_config_answers_when_no_other_config_is_named(self):
        """orchestrator-engine/R2: P4/S2 — the user's own default answers when neither the run nor the repository names a config."""
        home = self.root / "home"
        (home / ".config" / "orchestrate").mkdir(parents=True)
        (home / ".config" / "orchestrate" / "routes.json").write_text(
            json.dumps(
                config(
                    route("small", "codex", "gpt-s"),
                    route("medium", "claude", "sonnet"),
                    route("large", "claude", "opus"),
                )
            ),
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            self.run_driver()
        worker = self.workers()["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("codex", "gpt-s"))
        self.assertEqual(self.ledger()["routes"]["source"], "user")

    def test_S2_the_named_config_wins_over_the_repository_policy(self):
        """orchestrator-engine/R2: P4/S2 — the order is explicit, then the repository's policy, then the user's default."""
        policy = self.repo / ".orchestrate" / "routes.json"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(
            json.dumps(
                config(
                    route("small", "claude", "haiku"),
                    route("medium", "claude", "sonnet"),
                    route("large", "claude", "opus"),
                )
            ),
            encoding="utf-8",
        )
        path = self.routes_file(
            config(
                route("small", "codex", "gpt-s"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path)
        worker = self.workers()["demo-t1"]
        self.assertEqual((worker["cli"], worker["model"]), ("codex", "gpt-s"))
        self.assertEqual(self.ledger()["routes"]["source"], "explicit")

    def test_S2_an_explicit_override_wins_over_the_config(self):
        """orchestrator-engine/R2: P4/S2 — the override on the command line beats the config file."""
        path = self.routes_file(
            config(
                route("small", "claude", "haiku"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path, "--route", "small=codex/gpt-s@high")
        worker = self.workers()["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "gpt-s", "high")
        )
        self.assertEqual(self.ledger()["routes"]["classes"]["small"]["backend"], "codex")

    def test_S2_a_legacy_run_still_answers_with_the_model_classes_table(self):
        """orchestrator-engine/R2: P4/S2 — with no config at all the legacy table answers, as before."""
        self.run_driver()
        worker = self.workers()["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("claude", "haiku", "low")
        )
        self.assertEqual(self.ledger()["routes"]["source"], "legacy")

    def test_S1_a_route_naming_pi_and_the_top_effort_reaches_the_worker_spawn(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S1 — pi and max are expressible, and the spawn carries both."""
        path = self.routes_file(
            config(
                route("small", "pi", "minimax/MiniMax-M3.1-Flash", effort="max"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path)
        worker = self.workers()["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "max"),
        )

    def test_S4_the_matrix_answers_the_effort_when_no_route_names_one(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S4 — a route that names no effort leaves the size/difficulty matrix in charge."""
        path = self.routes_file(
            config(
                route("small", "codex", "gpt-s"),
                route("medium", "claude", "sonnet"),
                route("large", "claude", "opus"),
            )
        )
        self.run_driver("--routes-file", path)
        worker = self.workers()["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]), ("codex", "gpt-s", "low")
        )

    def test_S1_an_override_naming_pi_dispatches_a_pi_worker(self):
        """orchestrator-engine/R2: orchestrator-routing-effort-role/S1 — an override may name pi, and the run spawns it."""
        self.run_driver("--route", "small=pi/minimax/MiniMax-M3.1-Flash@xhigh")
        worker = self.workers()["demo-t1"]
        self.assertEqual(
            (worker["cli"], worker["model"], worker["effort"]),
            ("pi", "minimax/MiniMax-M3.1-Flash", "xhigh"),
        )

    def test_S3_an_override_for_an_unknown_class_fails_before_any_worker_is_spawned(self):
        """orchestrator-engine/R2: P4/S3 — an override for a class the driver does not know stops the run."""
        result = self.run_driver("--route", "tiny=codex/gpt-s", expect=2)
        self.assertIn("route override", result.stderr)
        self.assertIn("tiny", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    def test_S3_a_config_that_leaves_a_class_out_fails_before_any_worker_is_spawned(self):
        """orchestrator-engine/R2: P4/S3 — a class with no route must not fall back to another billable model."""
        path = self.routes_file(
            config(route("small", "claude", "haiku"), route("medium", "claude", "sonnet"))
        )
        result = self.run_driver("--routes-file", path, expect=2)
        self.assertIn("large", result.stderr)
        self.assertEqual(self.calls("spawn"), [])

    # ---- S4: the state machine does not move when the provider and the role change ---------------------------

    def lifecycle(self, slug, extra=(), pr=7):
        """One unit's whole life: worker, reviewer, CI gate, merge. What the run did, in order."""
        plan = self.root / ("%s.plan.md" % slug)
        text = plan_text(rows=("T1",)).replace("Slug: `demo`", "Slug: `%s`" % slug)
        plan.write_text(text, encoding="utf-8")
        state = self.root / ("state-" + slug)
        run_dir = state / slug
        worker, reviewer = "%s-t1" % slug, "%s-t1-review" % slug
        report = json.dumps(
            {
                "contract": 1,
                "task_id": "t1",
                "status": "COMPLETE",
                "summary": "done",
                "acceptance_met": True,
                "files": [],
                "decisions": [],
                "interfaces": [],
                "remaining": [],
                "checks": [],
                "review_rounds": 0,
                "usage": "unknown",
                "head": "abc1234",
                "pr": "https://github.com/o/r/pull/%d" % pr,
            }
        )
        review = json.dumps(
            {"contract": 2, "verdict": "pass", "items": [], "checks_run": [], "commit": "@HEAD"}
        )
        done = report_wait(worker, "complete", id=spawn_id(worker))
        done["write"] = [
            [str(run_dir / "t1" / "report.json"), report],
            [
                str(self.root / "repo.worktrees" / worker / "src" / "a" / "test_a.py"),
                'def test_a():\n    """S1"""\n',
            ],
        ]
        seen = report_wait(reviewer, "complete")
        seen["write"] = [[str(run_dir / "t1" / "review.json"), review]]
        self.waits([done, seen])
        self.prs(["MERGED"])
        self.checks(["green"])
        checkout = self.root / "repo.worktrees" / worker
        base_env = self.env

        def env():
            environment = base_env()
            environment["FAKE_CHECKOUT"] = str(checkout)
            return environment

        self.env = env
        try:
            self.run_driver(
                "--state-dir",
                str(state),
                "--repo",
                str(self.repo),
                "--wait-seconds",
                "1",
                "--idle-interval",
                "0",
                *extra,
                plan=plan,
            )
        finally:
            self.env = base_env
        log = state / slug / "events.jsonl"
        rows = (
            [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            if log.exists()
            else []
        )
        gates = [row["gate"][1] for row in rows if row["kind"] == "state"]
        return {"gates": gates, "plan": plan.read_text(encoding="utf-8"), "state": state}

    def test_S4_the_gate_review_and_pr_state_machine_stays_stable_under_a_new_provider_route(self):
        """orchestrator-engine/R2: P4/S4 — routing the worker to another provider and the reviewer to another role moves nothing else."""
        legacy = self.lifecycle("demo", pr=7)
        path = self.routes_file(
            config(
                route("small", "claude", "haiku"),
                route("medium", "codex", "gpt-m"),
                route("large", "codex", "gpt-l"),
            ),
            name="routes.json",
        )
        routed = self.lifecycle(
            "demo2", extra=("--routes-file", path, "--brain-cli", "codex"), pr=8
        )
        self.assertEqual(legacy["gates"], routed["gates"])
        self.assertIn("| COMPLETE ", legacy["plan"])
        self.assertIn("| COMPLETE ", routed["plan"])
        self.assertIn("merged https://github.com/o/r/pull/7", legacy["plan"])
        self.assertIn("merged https://github.com/o/r/pull/8", routed["plan"])

    def test_S4_the_routed_run_really_dispatched_on_the_new_provider(self):
        """orchestrator-engine/R2: P4/S4 — the comparison above is not vacuous: the routed run used the other provider."""
        path = self.routes_file(
            config(
                route("small", "claude", "haiku"),
                route("medium", "codex", "gpt-m"),
                route("large", "codex", "gpt-l"),
            ),
            name="routes.json",
        )
        self.lifecycle("demo2", extra=("--routes-file", path, "--brain-cli", "codex"), pr=8)
        spawns = {call["args"]["name"]: call["args"] for call in self.calls("spawn")}
        self.assertEqual(
            (spawns["demo2-t1"]["cli"], spawns["demo2-t1"]["model"]), ("claude", "haiku")
        )
        self.assertEqual(
            (spawns["demo2-t1-review"]["cli"], spawns["demo2-t1-review"]["model"]),
            ("codex", "gpt-m"),
        )


if __name__ == "__main__":
    unittest.main()
