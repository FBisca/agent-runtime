"""The dispatch loop against a fake agent-runtime, a temp git repo with an origin, and a temp plan."""

import json
import unittest
from pathlib import Path

import pytest
import yaml
from fixtures import (
    RunFixture,
    assignment,
    load_script,
    plan_data,
    report_wait,
    spawn_id,
    wait_result,
)
from test_gates import GateFixture

pytestmark = pytest.mark.integration


class RunTests(RunFixture, unittest.TestCase):
    def test_named_agent_reaches_every_spawn(self):
        """— The role the run names reaches every worker it spawns."""
        self.run_driver("--agent", "none")
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for spawn in spawns:
            self.assertEqual(spawn["args"]["agent"], "none")

    def test_default_run_names_implementer_only_for_workers(self):
        """— Implementation workers select their role; review gates stay roleless."""
        self.run_driver()
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        workers = [s["args"] for s in spawns if not s["args"]["name"].endswith("-review")]
        reviewers = [s["args"] for s in spawns if s["args"]["name"].endswith("-review")]
        self.assertTrue(workers)
        for spawn in workers:
            self.assertEqual(spawn.get("agent"), "implementer")
        for spawn in reviewers:
            self.assertNotIn("agent", spawn)

    def test_named_profile_reaches_every_spawn(self):
        """— The profile the run names reaches every worker it spawns."""
        self.run_driver("--profile", "minimax")
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for spawn in spawns:
            self.assertEqual(spawn["args"]["cli"], "minimax")

    def test_default_run_names_the_cli_family(self):
        """— Without a named profile, the spawn names the CLI family it runs."""
        self.run_driver()
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for spawn in spawns:
            self.assertEqual(spawn["args"]["cli"], "claude")

    @pytest.mark.spec("The report contract matches Report")
    def test_S1_the_unit_brief_names_the_task_id_the_report_must_carry(self):
        """orchestrator-report-task-id/S1"""
        self.run_driver()
        brief = (self.run_dir_of("demo") / "t1" / "task.md").read_text(encoding="utf-8")
        self.assertIn("task_id `t1`", brief)

    def test_S1_dispatches_ready_agent_tickets_up_to_the_cap(self):
        """code-driven-orchestrator/S1"""
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
        run_dir = self.run_dir_of("demo")
        self.assertEqual(first["task"], "Read %s and follow it." % (run_dir / "t1" / "task.md"))
        worktree = Path(first["cwd"])
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), self.base)
        self.assertEqual(self.git(worktree, "branch", "--show-current"), "feat/demo-t1")
        task_md = (run_dir / "t1" / "task.md").read_text(encoding="utf-8")
        for needle in (
            "contract.md",
            "S1",
            "WHEN a, THEN b",
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
        """code-driven-orchestrator/S1"""
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
        self.set_plan(("T1",))
        self.run_driver()
        worktree = Path(self.calls("spawn")[0]["args"]["cwd"])
        self.assertEqual(self.git(worktree, "rev-parse", "HEAD"), remote_head)
        self.assertTrue((worktree / "new.txt").exists())
        self.assertFalse((worktree / "local.txt").exists())

    def test_S1_long_slug_gets_a_valid_worker_name_ending_in_the_ticket(self):
        """code-driven-orchestrator/S1"""
        slug = "parallel-wait-and-cli-profiles"
        self.assertEqual(len(slug), 30)
        self.set_plan(("T1", "T3"), mutate=lambda data: data.update(slug=slug))
        self.run_driver()
        names = [c["args"]["name"] for c in self.calls("spawn")]
        self.assertEqual(len(names), 2)
        for name, ticket in zip(names, ("t1", "t3")):
            self.assertRegex(name, r"^[a-z0-9][a-z0-9_-]{0,31}$")
            self.assertTrue(name.endswith("-" + ticket), name)
            self.assertTrue(name.startswith("parallel-wait-and-cli-profil"), name)
        self.assertEqual(load_script().worker_name("a" * 27 + "-bcd", 12), "a" * 27 + "-t12")

    def test_S1_default_cap_is_three(self):
        """code-driven-orchestrator/S1"""
        self.run_driver()
        self.assertEqual(
            [c["args"]["name"] for c in self.calls("spawn")], ["demo-t1", "demo-t3", "demo-t5"]
        )

    def test_S1_codex_backend_picks_the_codex_model(self):
        """code-driven-orchestrator/S1"""
        self.run_driver("--cli", "codex")
        first = self.calls("spawn")[0]["args"]
        self.assertEqual((first["cli"], first["model"]), ("codex", "gpt-s"))

    def test_S2_unapproved_plan_exits_2_and_spawns_nothing(self):
        """code-driven-orchestrator/S2"""
        data = plan_data(("T1",))
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        result = self.run_driver(expect=2)
        self.assertIn("plan approve", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.state_dir / "demo.json").exists())

    def test_S3_dispatch_writes_the_ledger_and_leaves_the_plan_untouched(self):
        """code-driven-orchestrator/S3"""
        before = self.plan.read_bytes()
        self.run_driver("--max-workers", "2")
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(
            {t: self.ticket_status(t) for t in ("T1", "T3", "T4")},
            {"T1": "IN PROGRESS", "T3": "IN PROGRESS", "T4": "needs you: sign-off"},
        )

    def test_S14_restart_reuses_the_lead_and_spawns_nothing_bound(self):
        """code-driven-orchestrator/S14"""
        self.run_driver()
        first_calls = len(self.calls("spawn"))
        self.assertEqual(self.ledger()["lead"], "orchestrate:demo")
        self.assertTrue(all(c["lead"] == "orchestrate:demo" for c in self.calls()))
        data = self.ledger()
        data["lead"] = "orchestrate:stored"
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
        self.reset_status("IN PROGRESS", "TO-DO")
        self.calls_reset = len(self.calls())
        self.run_driver(
            expect=1
        )  # the worker the first drive left idle with no report is stranded, not finished
        self.assertEqual(len(self.calls("spawn")), first_calls)
        later = self.calls()[self.calls_reset :]
        self.assertTrue(later and all(c["lead"] == "orchestrate:stored" for c in later))
        self.assertIn("wait", [c["tool"] for c in later])

    def test_S14_restart_with_status_reset_to_todo_does_not_respawn_a_bound_ticket(self):
        """code-driven-orchestrator/S14"""
        self.run_driver("--max-workers", "1")
        self.reset_status("IN PROGRESS", "TO-DO")
        self.run_driver("--max-workers", "1", expect=1)  # stranded: see above
        names = [c["args"]["name"] for c in self.calls("spawn")]
        self.assertEqual(names.count("demo-t1"), 1)

    @pytest.mark.spec("Runtime refusals are reported")
    def test_a_refused_spawn_keeps_the_reservation_and_exits_1(self):
        """code-driven-orchestrator/S14"""
        self.refuse(["demo-t1"])
        self.set_plan(("T1",))
        result = self.run_driver(expect=1)
        self.assertRegex(result.stderr, r"T1\s+build\s+✗ not dispatched")
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "RESERVED")
        self.assertIn("not dispatched", self.ticket_status("T1"))
        self.refuse([])
        self.run_driver()
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "ACTIVE")


class RecoveryTests(GateFixture, unittest.TestCase):  # noqa: PLR0904
    """A restarted drive recovers an attempt whose agent the runtime no longer has, or says why it cannot."""

    def crashed(self, state="exited"):
        """A first drive spawned t1 and ended; its worker is gone when the next one starts."""
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task()["status"], "ACTIVE")
        self.gone("demo-t1", state)

    def wakes_of(self, kind):
        return [w for w in self.wakes() if w["kind"] == kind]

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S2_a_definite_answer_from_the_runtime_orphans_the_attempt(self):
        for state, answer in (("exited", "exited"), ("not_found", "unknown: not_found")):
            with self.subTest(state=state):
                self.setUp()
                self.crashed(state)
                self.run_driver()
                (orphaned,) = self.run_events("orphaned")
                self.assertEqual(
                    (orphaned["unit"], orphaned["task"], orphaned["answer"]), ("t1", "t1", answer)
                )
                self.assertEqual(self.task()["release_reason"], "orphaned: " + answer)

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S2_a_live_terminal_is_not_released_or_spawned_over(self):
        self.crashed("working")
        self.run_driver(expect=1)
        self.assertEqual(self.run_events("orphaned"), [])
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task()["status"], "ACTIVE")

    @pytest.mark.spec("A run does not finish over open work")
    def test_S2a_a_terminal_at_work_that_wait_does_not_list_is_stranded_not_finished(self):
        ""","""
        self.crashed("working")
        self.run_driver(expect=1)
        self.assertEqual(
            len(self.run_events("finish")), 1, "only the first, healthy drive finished"
        )
        (stranded,) = self.wakes_of("stranded")
        self.assertIn("t1", stranded["text"])
        self.assertIn("working", stranded["text"])

    @pytest.mark.spec("A run does not finish over open work")
    def test_S2a_a_finished_turn_with_no_landed_report_is_stranded_not_finished(self):
        for state in ("idle", "done"):
            with self.subTest(state=state):
                self.setUp()
                self.crashed(state)
                self.run_driver(expect=1)
                self.assertEqual(len(self.run_events("finish")), 1, "only the first drive finished")
                (stranded,) = self.wakes_of("stranded")
                self.assertIn("t1", stranded["text"])
                self.assertIn("no report", stranded["text"])
                self.assertEqual(self.task()["status"], "ACTIVE")

    @pytest.mark.spec("A run does not finish over open work")
    def test_S2a_a_second_restart_over_the_same_stranded_task_exits_non_zero_too(self):
        self.crashed("unreadable")
        self.run_driver(expect=1)
        self.run_driver(expect=1)
        self.assertEqual(
            len(self.run_events("finish")), 1, "only the first, healthy drive finished"
        )
        self.assertEqual(len(self.wakes_of("stranded")), 1, "the Lead is told once")

    @pytest.mark.spec("A run does not finish over open work")
    def test_S2a_a_loss_the_loop_meets_before_it_would_finish_is_reconciled_first(self):
        self.waits(
            [
                wait_result([assignment("demo-t1")], write=self.goes("demo-t1")),
                wait_result(),
            ]
        )
        self.run_driver()
        self.assertEqual(len(self.run_events("orphaned")), 1)
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])
        self.assertEqual(self.task("t1o1")["status"], "ACTIVE")
        self.assertEqual(self.run_events("finish")[-1]["kind"], "finish")

    @pytest.mark.spec("A run does not finish over open work")
    def test_S2a_a_task_it_cannot_settle_raises_stranded_and_the_run_does_not_finish(self):
        self.waits(
            [
                wait_result([assignment("demo-t1")], write=self.goes("demo-t1", "unreadable")),
                wait_result(),
            ]
        )
        self.run_driver(expect=1)
        self.assertEqual(self.run_events("finish"), [])
        (stranded,) = self.wakes_of("stranded")
        self.assertIn("t1", stranded["text"])
        self.assertEqual(self.spawned(), ["demo-t1"])
        self.assertEqual(self.task()["status"], "ACTIVE")

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S2b_a_status_that_cannot_be_read_releases_and_spawns_nothing(self):
        for state, error in (
            ("unreadable", "hc get timed out"),
            ("error", "the runtime does not know this agent"),
        ):
            with self.subTest(state=state):
                self.setUp()
                self.crashed(state)
                self.run_driver(expect=1)
                self.assertEqual(self.spawned(), ["demo-t1"])
                self.assertEqual(self.task()["status"], "ACTIVE")
                self.assertEqual(self.run_events("orphaned"), [])
                (stranded,) = self.wakes_of("stranded")
                self.assertIn("t1", stranded["text"])
                self.assertIn(error, stranded["text"])

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S2c_an_orphaned_reviewer_takes_the_path_of_a_reviewer_that_exited(self):
        self.waits([self.worker_done(tests=True)])
        self.run_driver()
        self.assertEqual(self.task()["gate"], "reviewing")
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1-review"])
        self.gone("demo-t1-review")
        self.waits(
            [
                wait_result(),
                report_wait("demo-t1-review", "failed", "exited", id=spawn_id("demo-t1-review", 2)),
                self.skipped(),
            ]
        )
        self.run_driver()
        self.assertEqual(self.run_events("orphaned")[0]["task"], "t1")
        self.assertEqual(len(self.run_events("review-ended")), 2)
        self.assertEqual(self.request()["event"], "review")
        self.assertNotIn("t1o1", self.ledger()["tasks"])

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S3_an_orphaned_attempt_without_a_report_is_dispatched_again_as_a_new_attempt(self):
        self.crashed()
        self.run_driver()
        tasks = self.ledger()["tasks"]
        self.assertEqual(
            (tasks["t1"]["status"], tasks["t1"]["release_reason"]), ("RELEASED", "orphaned: exited")
        )
        self.assertEqual(tasks["t1o1"]["status"], "ACTIVE")
        self.assertEqual(tasks["t1o1"]["packet"]["unit"], "t1")
        self.assertEqual(tasks["t1o1"]["packet"]["checkout"], tasks["t1"]["packet"]["checkout"])
        self.assertEqual(tasks["t1o1"]["packet"]["worker"], "demo-t1")
        first, second = [c["args"] for c in self.calls("spawn")]
        self.assertEqual((second["name"], second["cwd"]), (first["name"], first["cwd"]))
        self.assertEqual(self.git(second["cwd"], "branch", "--show-current"), "feat/demo-t1")
        self.assertEqual(self.calls("resume"), [])
        self.assertEqual(self.ticket_status("T1"), "IN PROGRESS")

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S3_the_attempt_dispatched_again_is_waited_on_before_the_run_may_finish(self):
        """drive-restart-recovery/S3

        The wait that came before the re-dispatch cannot list the new attempt. Judging the run idle on it sent the
        loop to its finishing check, which asked `status` of a terminal the runtime had not settled yet: the real
        runtime answers `unknown` there, so the unit was stranded and the new worker died with the drive."""
        self.crashed()
        self.run_driver()
        calls = self.calls()
        respawn = [i for i, c in enumerate(calls) if c["tool"] == "spawn"][-1]
        after = [c["tool"] for c in calls[respawn + 1 :] if c["tool"] in ("wait", "status")]
        self.assertTrue(after, "the run ended without asking the runtime about the new attempt")
        self.assertEqual(after[0], "wait", "the new attempt was judged by status before any wait")

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S3_a_second_loss_is_dispatched_again_as_the_next_attempt(self):
        self.crashed()
        self.run_driver()
        self.gone("demo-t1")
        self.run_driver()
        tasks = self.ledger()["tasks"]
        self.assertEqual((tasks["t1o1"]["status"], tasks["t1o2"]["status"]), ("RELEASED", "ACTIVE"))

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S3a_dispatch_refuses_a_closed_task_id_before_it_spawns_or_resumes(self):
        from unittest import mock

        spec = load_script()
        self.run_driver()
        for closed in ("COMPLETE", "RELEASED"):
            with self.subTest(status=closed):
                data = self.ledger()
                data["tasks"]["t1"]["status"] = closed
                (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")
                before = len(self.calls())

                def again(driver):
                    ticket = driver.last_plan["tickets"][0]
                    driver.dispatch(driver.last_plan, ticket)

                with mock.patch.object(spec.Driver, "loop", again):
                    result = self.run_driver(expect=1)
                self.assertIn(closed, result.stderr)
                self.assertIn("t1", result.stderr)
                self.assertEqual(
                    [c for c in self.calls()[before:] if c["tool"] in ("spawn", "resume")], []
                )

    def release(self, task_id="t1"):
        data = self.ledger()
        data["tasks"][task_id]["status"] = "RELEASED"
        (self.state_dir / "demo.json").write_text(json.dumps(data), encoding="utf-8")

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S4_a_released_unit_whose_worker_is_gone_is_dispatchable_from_in_progress(self):
        self.crashed()
        self.release()
        self.assertEqual(self.ticket_status("T1"), "IN PROGRESS")
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])
        tasks = self.ledger()["tasks"]
        self.assertEqual((tasks["t1"]["status"], tasks["t1r1"]["status"]), ("RELEASED", "ACTIVE"))
        self.assertEqual(tasks["t1r1"]["packet"]["unit"], "t1")

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S4_a_released_unit_whose_worker_is_still_live_is_not_dispatched(self):
        self.crashed("working")
        self.release()
        self.run_driver()
        self.assertEqual(self.spawned(), ["demo-t1"])

    @pytest.mark.spec("Orphaned tasks are recovered")
    def test_S4_a_cell_with_no_attempt_in_the_ledger_stays_a_lock(self):
        self.seed_status(**{t: "IN PROGRESS" for t in ("T1", "T2", "T3", "T4", "T5")})
        self.run_driver()
        self.assertEqual(self.spawned(), [])

    def lead_answers(self):
        """A wait step during which the cause is fixed and the Lead sets the ticket's cell back to TO-DO."""
        return self.lead_sets(T1="TO-DO")

    @pytest.mark.spec("Not-dispatched tickets")
    def test_S5_a_failed_dispatch_raises_one_wake_and_the_lead_s_todo_retries_it_in_the_same_run(
        self,
    ):
        self.refuse(["demo-t1"])
        self.waits([self.lead_answers()])
        self.run_driver()
        (wake,) = self.wakes_of("not-dispatched")
        self.assertEqual(wake["unit"], "t1")
        self.assertIn("T1", wake["text"])
        self.assertIn("TO-DO", wake["text"])
        self.assertIn("name_taken", wake["text"])
        self.assertEqual(self.spawned(), ["demo-t1", "demo-t1"])  # the refused one, then the retry
        self.assertEqual(self.task()["status"], "ACTIVE")
        self.assertEqual(len(self.run_events("start")), 1)

    def declare_setup(self, command):
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(exist_ok=True)
        config.write_text(yaml.safe_dump({"worktree": {"setup": command}}), encoding="utf-8")

    @pytest.mark.spec("Ticket dispatch")
    def test_a_declared_worktree_setup_runs_once_in_the_new_worktree_with_the_main_root(self):
        self.declare_setup('echo "$ORCHESTRATE_MAIN_ROOT" > .setup-ran; pwd -P >> .setup-ran')
        self.waits([self.worker_done()])
        self.run_driver()
        tree = self.root / "repo.worktrees" / "demo-t1"
        ran = (tree / ".setup-ran").read_text().split()
        self.assertEqual(ran, [str(self.repo), str(tree.resolve())])
        self.assertEqual(self.spawned(), ["demo-t1"])

    @pytest.mark.spec("Ticket dispatch")
    def test_a_failing_worktree_setup_fails_the_dispatch_and_leaves_no_broken_tree(self):
        self.declare_setup("echo env files are missing >&2; exit 3")
        self.waits([wait_result()])
        self.run_driver(expect=1)
        self.assertEqual(self.spawned(), [], "no worker may start on a tree the setup left broken")
        (wake,) = self.wakes_of("not-dispatched")
        self.assertIn("worktree setup", wake["text"])
        self.assertIn("env files are missing", wake["text"])
        self.assertIn("worktree setup", self.ticket_status("T1"))
        self.assertFalse((self.root / "repo.worktrees" / "demo-t1").exists())

    @pytest.mark.spec("Not-dispatched tickets")
    def test_S5_without_the_lead_s_todo_the_same_run_does_not_try_again(self):
        self.refuse(["demo-t1"])
        self.waits([wait_result(write=[[str(self.home / "refuse.json"), "[]"]])])
        self.run_driver(expect=1)
        self.assertEqual(self.spawned(), ["demo-t1"])  # the refused attempt only
        self.assertEqual(len(self.wakes_of("not-dispatched")), 1)
        self.assertIn("not dispatched", self.ticket_status("T1"))
        self.assertEqual(len(self.wakes_of("not-dispatched")), 1)  # once per ticket and reason

    @pytest.mark.spec("Not-dispatched tickets")
    def test_S12_a_not_dispatched_ticket_whose_cell_is_not_exactly_TO_DO_is_not_tried_again_in_the_run(
        self,
    ):
        ""","""
        self.refuse(["demo-t1"])
        self.waits([self.lead_sets(T1="TO-DO · retry")])
        self.run_driver(expect=1)
        self.assertEqual(self.spawned(), ["demo-t1"])  # the refused attempt only
        self.assertEqual(len(self.wakes_of("not-dispatched")), 1)
        self.assertEqual(self.ledger()["tasks"]["t1"]["status"], "RESERVED")

    @pytest.mark.spec("Not-dispatched tickets")
    def test_S12a_the_not_dispatched_wake_names_the_ticket_the_reason_and_the_exact_TO_DO_way_out(
        self,
    ):
        ""","""
        self.refuse(["demo-t1"])
        self.waits([wait_result(write=[[str(self.home / "refuse.json"), "[]"]])])
        self.run_driver(expect=1)
        (wake,) = self.wakes_of("not-dispatched")
        self.assertEqual(wake["unit"], "t1")
        self.assertIn("T1", wake["text"])
        self.assertIn("name_taken", wake["text"])
        self.assertIn("set the ticket's Status cell back to exactly TO-DO", wake["text"])

    @pytest.mark.spec("Not-dispatched tickets")
    def test_S5_a_refused_restart_attempt_is_dispatched_again_after_the_lead_s_todo(self):
        self.crashed()
        self.release()
        self.refuse(["demo-t1"])
        self.run_driver(expect=1)
        (wake,) = self.wakes_of("not-dispatched")
        self.assertEqual(self.ledger()["tasks"]["t1r1"]["status"], "RESERVED")
        self.assertEqual(wake["unit"], "t1")
        self.waits([self.lead_answers()])
        self.run_driver()
        tasks = self.ledger()["tasks"]
        self.assertEqual(tasks["t1r1"]["status"], "ACTIVE")
        self.assertEqual(
            self.spawned(), ["demo-t1"] * 4
        )  # the first, the refused, its restart-time retry, the Lead's retry
        self.assertNotIn("t1r2", tasks)

    @pytest.mark.spec("Not-dispatched tickets")
    def test_S5_a_refused_orphan_redispatch_raises_not_dispatched_and_retries_after_the_lead_s_todo(
        self,
    ):
        self.crashed()
        self.refuse(["demo-t1"])
        self.run_driver(expect=1)
        (wake,) = self.wakes_of("not-dispatched")
        self.assertIn("t1o1", wake["text"])
        self.assertEqual(self.wakes_of("stranded"), [])
        self.assertEqual(self.ledger()["tasks"]["t1o1"]["status"], "RESERVED")
        self.refuse([])
        self.set_plan(("T1",))
        self.run_driver()
        tasks = self.ledger()["tasks"]
        self.assertEqual(tasks["t1o1"]["status"], "ACTIVE")
        self.assertNotIn("t1o2", tasks)

    @pytest.mark.spec("A run does not finish over open work")
    def test_S4_a_worker_that_status_cannot_read_strands_the_unit_and_spawns_nothing(self):
        self.crashed("unreadable")
        self.release()
        self.run_driver(expect=1)
        self.assertEqual(self.spawned(), ["demo-t1"])
        (wake,) = self.wakes_of("stranded")
        self.assertIn("t1", wake["text"])


if __name__ == "__main__":
    unittest.main()
