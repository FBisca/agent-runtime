"""The dispatch loop against a fake agent-runtime, a temp git repo with an origin, and a temp plan."""

import json
import unittest
from pathlib import Path

import pytest
from fixtures import ROWS, RunFixture, assignment, load_script, plan_text, wait_result

pytestmark = pytest.mark.integration


class RunTests(RunFixture, unittest.TestCase):
    def test_named_agent_reaches_every_spawn(self):
        """orchestrator-engine/R2 — The role the run names reaches every worker it spawns."""
        self.run_driver("--agent", "none")
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for spawn in spawns:
            self.assertEqual(spawn["args"]["agent"], "none")

    def test_default_run_names_implementer_only_for_workers(self):
        """orchestrator-engine/R2 — Implementation workers select their role; review gates stay roleless."""
        self.run_driver()
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        workers = [
            s["args"] for s in spawns if not s["args"]["name"].endswith(("-review", "-gaps"))
        ]
        reviewers = [s["args"] for s in spawns if s["args"]["name"].endswith(("-review", "-gaps"))]
        self.assertTrue(workers)
        for spawn in workers:
            self.assertEqual(spawn.get("agent"), "implementer")
        for spawn in reviewers:
            self.assertNotIn("agent", spawn)

    def test_named_profile_reaches_every_spawn(self):
        """orchestrator-engine/R2 — The profile the run names reaches every worker it spawns."""
        self.run_driver("--profile", "minimax")
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for spawn in spawns:
            self.assertEqual(spawn["args"]["cli"], "minimax")

    def test_default_run_names_the_cli_family(self):
        """orchestrator-engine/R2 — Without a named profile, the spawn names the CLI family it runs."""
        self.run_driver()
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for spawn in spawns:
            self.assertEqual(spawn["args"]["cli"], "claude")

    def test_S1_the_unit_brief_names_the_task_id_the_report_must_carry(self):
        """orchestrator-engine/R15: orchestrator-report-task-id/S1"""
        self.run_driver()
        brief = (self.state_dir / "demo" / "t1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("task_id `t1`", brief)

    def test_S1_dispatches_ready_agent_tickets_up_to_the_cap(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S1"""
        self.run_driver("--max-workers", "2")
        spawns = self.calls("spawn")
        self.assertEqual([c["args"]["name"] for c in spawns], ["demo-t1", "demo-t3"])
        first = spawns[0]["args"]
        self.assertEqual(
            (first["kind"], first["cli"], first["model"], first["effort"]),
            ("worker", "claude", "haiku", "low"),
        )
        second = spawns[1]["args"]
        self.assertEqual((second["model"], second["effort"]), ("sonnet", "medium"))
        run_dir = self.state_dir / "demo"
        self.assertEqual(first["task"], "Read %s and follow it." % (run_dir / "t1" / "task.md"))
        worktree = Path(first["cwd"])
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), self.base)
        self.assertEqual(self.git(worktree, "branch", "--show-current"), "feat/demo-t1")
        task_md = (run_dir / "t1" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "contract.md",
            str(run_dir / "t1" / "report.json"),
            "S1",
            "WHEN a THEN b.",
            "red-first",
            "src/a/",
            str(worktree),
            "feat/demo-t1",
        ):
            self.assertIn(needle, task_md)
        tasks = self.ledger()["tasks"]
        self.assertEqual(set(tasks), {"t1", "t3"})
        self.assertEqual(
            (tasks["t1"]["status"], tasks["t1"]["runtime_id"]), ("ACTIVE", "term-demo-t1")
        )
        self.assertEqual(
            (tasks["t1"]["packet"]["class"], tasks["t1"]["packet"]["unit"]), ("small", "t1")
        )
        self.assertEqual(tasks["t1"]["packet"]["owns"], ["src/a/"])

    def test_S1_worktree_branches_from_the_fetched_origin_main(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S1"""
        other = self.root / "other"
        self.git(self.root, "clone", "-q", str(self.origin), str(other))
        (other / "new.txt").write_text("remote\n", encoding="utf-8")
        self.git(other, "add", ".")
        self.git(
            other,
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "-m",
            "remote",
        )
        self.git(other, "push", "-q", "origin", "main")
        remote_head = self.git(other, "rev-parse", "HEAD")
        (self.repo / "local.txt").write_text("local\n", encoding="utf-8")
        self.git(self.repo, "add", ".")
        self.git(
            self.repo,
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "-m",
            "local",
        )
        self.assertNotEqual(self.git(self.repo, "rev-parse", "origin/main"), remote_head)
        self.plan.write_text(plan_text(rows=("T1",)), encoding="utf-8")
        self.run_driver()
        worktree = Path(self.calls("spawn")[0]["args"]["cwd"])
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), remote_head)
        self.assertTrue((worktree / "new.txt").exists())
        self.assertFalse((worktree / "local.txt").exists())

    def test_S1_long_slug_gets_a_valid_worker_name_ending_in_the_ticket(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S1"""
        slug = "parallel-wait-and-cli-profiles"
        self.assertEqual(len(slug), 30)
        self.plan.write_text(
            plan_text(rows=("T1", "T3")).replace("`demo`", "`%s`" % slug), encoding="utf-8"
        )
        self.run_driver()
        names = [c["args"]["name"] for c in self.calls("spawn")]
        self.assertEqual(len(names), 2)
        for name, ticket in zip(names, ("t1", "t3")):
            self.assertRegex(name, r"^[a-z0-9][a-z0-9_-]{0,31}$")
            self.assertTrue(name.endswith("-" + ticket), name)
            self.assertTrue(name.startswith("parallel-wait-and-cli-profil"), name)
        self.assertEqual(load_script().worker_name("a" * 27 + "-bcd", 12), "a" * 27 + "-t12")

    def test_S1_default_cap_is_three(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S1"""
        self.run_driver()
        self.assertEqual(
            [c["args"]["name"] for c in self.calls("spawn")], ["demo-t1", "demo-t3", "demo-t5"]
        )

    def test_S1_codex_backend_picks_the_codex_model(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S1"""
        self.run_driver("--cli", "codex")
        first = self.calls("spawn")[0]["args"]
        self.assertEqual((first["cli"], first["model"]), ("codex", "gpt-s"))

    def test_S2_unapproved_plan_exits_2_and_spawns_nothing(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S2"""
        self.plan.write_text(plan_text(status="DRAFT"), encoding="utf-8")
        result = self.run_driver(expect=2)
        self.assertIn("APPROVED", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state_dir / "demo.json").exists())

    def test_S2_missing_field_names_the_ticket_and_field(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S2"""
        broken = plan_text().replace("| src/a/ | small |", "| src/a/ | |")
        self.plan.write_text(broken, encoding="utf-8")
        result = self.run_driver(expect=2)
        self.assertIn("T1", result.stderr)
        self.assertIn("Size", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_S2_unknown_scenario_and_missing_table_exit_2(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S2"""
        self.plan.write_text(
            plan_text().replace("| S1 | none |", "| S9 | none |"), encoding="utf-8"
        )
        self.assertIn("S9", self.run_driver(expect=2).stderr)
        self.plan.write_text("# Plan\n\nSlug: `demo`. Status: APPROVED.\n", encoding="utf-8")
        self.assertIn("Tickets", self.run_driver(expect=2).stderr)
        self.assertEqual(self.calls(), [])

    def test_S3_dispatch_changes_only_the_status_cell_of_dispatched_and_person_rows(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S3"""
        before = self.plan.read_text(encoding="utf-8").splitlines()
        self.run_driver("--max-workers", "2")
        after = self.plan.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(before), len(after))
        changed = {b: a for b, a in zip(before, after) if a != b}
        self.assertEqual(
            changed,
            {
                ROWS["T1"]: ROWS["T1"].replace("TO-DO", "IN PROGRESS"),
                ROWS["T3"]: ROWS["T3"].replace("TO-DO", "IN PROGRESS"),
                ROWS["T4"]: ROWS["T4"].replace("TO-DO", "needs you: sign-off"),
            },
        )

    def test_S3_pause_and_complete_write_one_row_too(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S3"""
        spec = load_script()
        for status in ("IN PROGRESS · needs you: pick a name", "COMPLETE 2026-11-07"):
            before = self.plan.read_text(encoding="utf-8").splitlines()
            spec.set_status(self.plan, "T2", status)
            after = self.plan.read_text(encoding="utf-8").splitlines()
            diff = [(b, a) for b, a in zip(before, after) if a != b]
            self.assertEqual(len(before), len(after))
            self.assertEqual(len(diff), 1)
            self.assertTrue(
                diff[0][1].startswith(
                    "| T2 | second | S2 | T1 | src/b/ | regular | complex | agent | " + status
                )
            )

    def test_S4_next_iteration_acts_on_the_edited_plan(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S4"""
        self.plan.write_text(plan_text(rows=("T1", "T2")), encoding="utf-8")
        new_row = ROWS["T3"].replace("| T3 | third", "| T6 | sixth")
        self.waits(
            [
                wait_result(
                    [assignment("demo-t1")],
                    replace=[
                        [
                            str(self.plan),
                            "| T1 | first | S1 | none | src/a/ | small | simple | agent | IN PROGRESS |",
                            "| T1 | first | S1 | none | src/a/ | small | simple | agent | COMPLETE 2026-11-07 |\n"
                            + new_row,
                        ]
                    ],
                ),
                wait_result(),
            ]
        )
        self.run_driver()
        self.assertEqual(
            sorted(c["args"]["name"] for c in self.calls("spawn")),
            ["demo-t1", "demo-t2", "demo-t6"],
        )
        self.assertEqual(len(self.calls("wait")), 2)

    def test_S14_restart_reuses_the_lead_and_spawns_nothing_bound(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S14"""
        self.run_driver()
        first_calls = len(self.calls("spawn"))
        self.assertEqual(self.ledger()["lead"], "orchestrate:demo")
        self.assertTrue(all(c["lead"] == "orchestrate:demo" for c in self.calls()))
        data = self.ledger()
        data["lead"] = "orchestrate:stored"
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
        self.plan.write_text(
            self.plan.read_text(encoding="utf-8").replace("IN PROGRESS", "TO-DO"), encoding="utf-8"
        )
        self.calls_reset = len(self.calls())
        self.run_driver()
        self.assertEqual(len(self.calls("spawn")), first_calls)
        later = self.calls()[self.calls_reset :]
        self.assertTrue(later and all(c["lead"] == "orchestrate:stored" for c in later))
        self.assertIn("wait", [c["tool"] for c in later])

    def test_S14_restart_with_status_reset_to_todo_does_not_respawn_a_bound_ticket(self):
        """orchestrator-engine/R2: code-driven-orchestrator/S14"""
        self.run_driver("--max-workers", "1")
        self.plan.write_text(
            self.plan.read_text(encoding="utf-8").replace("IN PROGRESS", "TO-DO"), encoding="utf-8"
        )
        self.run_driver("--max-workers", "1")
        names = [c["args"]["name"] for c in self.calls("spawn")]
        self.assertEqual(names.count("demo-t1"), 1)

    def test_a_refused_spawn_keeps_the_reservation_and_exits_1(self):
        """orchestrator-engine/R5: code-driven-orchestrator/S14"""
        self.refuse(["demo-t1"])
        self.plan.write_text(plan_text(rows=("T1",)), encoding="utf-8")
        result = self.run_driver(expect=1)
        self.assertIn("T1 not dispatched", result.stderr)
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "RESERVED")
        self.assertIn("TO-DO", self.plan.read_text(encoding="utf-8"))
        self.refuse([])
        self.run_driver()
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()
