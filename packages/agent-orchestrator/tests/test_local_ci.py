"""`run --local-ci`: the repository's full check runs on this machine for each pull request head, beside the loop,
and its result takes the place of `gh pr checks` without the gates, the fix round or readiness knowing."""

import itertools
import json
import subprocess
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from agent_orchestrator import driver as engine
from agent_orchestrator.driver import local_ci
from agent_orchestrator.driver.pull import CI_STEP_LINES, Pull
from agent_orchestrator.plan import PlanError

pytestmark = pytest.mark.integration

URL = "https://github.com/o/r/pull/7"
HEAD = "a" * 40
NEWER = "b" * 40
BASE = "c" * 40


class Completed:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


class FakeProcess:
    """A started local run: it is going until the test writes its exit file, as the run's own script would."""

    pids = itertools.count(4000)

    def __init__(self, argv, **options):
        self.argv, self.options, self.pid, self.returncode = argv, options, next(self.pids), None

    def poll(self):
        return self.returncode


class FakeDriver:
    """What the lanes ask of the Driver: the record, the log, the plan's gate steps and the `gh` seam."""

    def __init__(self, root, **args):
        self.repo = Path(root) / "repo"
        self.repo.mkdir()
        self.run_dir = Path(root) / "run"
        self.run_dir.mkdir()
        self.args = SimpleNamespace(
            local_ci=True, local_ci_jobs=1, gate=["make check"], base="origin/main", **args
        )
        self.main_failed = None
        self.run_logs = {}
        self.tasks = {}
        self.events, self.rounds, self.requests, self.data = [], [], [], {}

    def gh(self, argv, timeout=60, cwd=None):
        result = engine.gh_process(argv, timeout=timeout)
        return result.returncode, result.stdout, result.stderr

    def task(self, task_id):
        return self.tasks.setdefault(task_id, {"report": {"head": HEAD}})

    def mark(self, task_id, **fields):
        self.task(task_id).update(fields)

    def unit_of(self, task_id):
        return task_id

    def log(self, kind, **fields):
        self.events.append((kind, fields))

    @contextmanager
    def book(self):
        yield self.data

    def fail_gate(self, task_id, report, source, items, **options):
        self.rounds.append((source, items))
        return True

    def request(self, task_id, kind, reason):
        self.requests.append((kind, reason))
        return False


class LocalCiFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.driver = FakeDriver(self.tmp.name)
        self.lane = Pull(self.driver)
        self.gh_calls, self.started, self.killed, self.git = [], [], [], []
        self.gh_answer = lambda argv: Completed(0, "[]")
        for patcher in (
            mock.patch.object(engine, "gh_process", self.gh_process),
            mock.patch.object(engine, "ci_process", self.ci_process),
            mock.patch.object(engine, "run", self.run_git),
            mock.patch.object(local_ci.os, "killpg", lambda pid, sig: self.killed.append(pid)),
            mock.patch.object(local_ci.os, "kill", mock.Mock(side_effect=ProcessLookupError)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def gh_process(self, argv, timeout=None, cwd=None):
        self.gh_calls.append(argv)
        return self.gh_answer(argv)

    def ci_process(self, argv, **options):
        process = FakeProcess(argv, **options)
        self.started.append(process)
        return process

    def run_git(self, argv, timeout=15):
        self.git.append(argv)
        return BASE if "rev-parse" in argv else ""

    def poll(self, task_id="t1", head=HEAD):
        return self.lane.poll_checks(task_id, URL, head)

    def finish(self, process, code):
        """The run's script ends: its exit code is written, its process is gone."""
        directory = Path(process.options["cwd"])
        (directory / "exit").write_text("%d\n" % code, encoding="utf-8")
        process.returncode = code

    def gh_matching(self, *prefix):
        return [c for c in self.gh_calls if c[: len(prefix)] == list(prefix)]


class RunPerHeadTests(LocalCiFixture):
    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_a_head_gets_one_run_started_without_waiting_and_github_is_not_asked_for_checks(self):
        self.assertEqual(self.poll(), "pending")
        self.assertEqual(self.poll(), "pending")
        (process,) = self.started
        env = process.options["env"]
        self.assertEqual((env["LOCAL_CI_SHA"], env["LOCAL_CI_COMMAND"]), (HEAD, "make check"))
        self.assertTrue(process.options["start_new_session"], "its own process group")
        directory = Path(process.options["cwd"])
        self.assertEqual(directory, self.driver.run_dir / "t1" / ("local-ci-" + HEAD[:12]))
        self.assertTrue(env["LOCAL_CI_TREE"].startswith(str(directory)))
        self.assertEqual(self.gh_matching("pr", "checks"), [])
        self.assertEqual(self.gh_matching("run"), [])

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_the_full_check_of_the_config_wins_over_the_gates_and_setup_is_passed_on(self):
        config = self.driver.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir()
        config.write_text(
            "checks:\n  full:\n    run: make full\nworktree:\n  setup: ./link.sh\n",
            encoding="utf-8",
        )
        self.poll()
        env = self.started[0].options["env"]
        self.assertEqual(
            (env["LOCAL_CI_COMMAND"], env["LOCAL_CI_SETUP"]), ("make full", "./link.sh")
        )

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_the_gates_are_joined_when_the_config_names_no_full_check(self):
        self.driver.args.gate = ["make lint", "make test"]
        self.poll()
        self.assertEqual(
            self.started[0].options["env"]["LOCAL_CI_COMMAND"], "make lint && make test"
        )

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_the_states_are_pending_green_and_failed_with_the_rows_gh_would_print(self):
        self.assertEqual(self.poll(), "pending")
        (pending,) = self.driver.task("t1")["ci_rows"]
        self.assertEqual(
            (pending["name"], pending["bucket"], pending["command"]),
            ("local-ci", "pending", "make check"),
        )
        process = self.started[0]
        log = Path(process.options["cwd"]) / "ci.log"
        log.write_text("\n".join("line %d" % n for n in range(1, 80)), encoding="utf-8")
        self.finish(process, 1)
        self.assertEqual(self.lane.poll_checks("t1", URL, HEAD), "failed")
        (row,) = self.driver.task("t1")["ci_rows"]
        self.assertEqual(
            (row["name"], row["bucket"], row["log_path"], row["command"]),
            ("local-ci", "fail", str(log), "make check"),
        )
        self.assertTrue(row["description"].endswith("line 79"))
        self.assertEqual(len(row["description"].splitlines()), CI_STEP_LINES)
        self.assertEqual(self.driver.task("t1")["ci_head"], HEAD)

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_exit_zero_is_green(self):
        self.poll()
        self.finish(self.started[0], 0)
        self.assertEqual(self.lane.poll_checks("t1", URL, HEAD), "green")
        self.assertEqual(self.driver.task("t1")["ci_rows"][0]["bucket"], "pass")

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_a_restarted_driver_reads_a_finished_run_back_and_starts_none(self):
        self.poll()
        self.finish(self.started[0], 0)
        self.driver.__dict__.pop(
            "_local_ci"
        )  # the driver's memory is gone; the run directory is not
        self.assertEqual(self.lane.poll_checks("t1", URL, HEAD), "green")
        self.assertEqual(len(self.started), 1)

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_a_head_the_poll_does_not_name_is_read_from_the_pull_request(self):
        self.gh_answer = lambda argv: Completed(0, json.dumps({"headRefOid": HEAD}))
        self.assertEqual(self.lane.poll_checks("t1", URL), "pending")
        self.assertEqual(self.started[0].options["env"]["LOCAL_CI_SHA"], HEAD)
        self.assertEqual(self.gh_matching("pr", "checks"), [])

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_no_command_refuses_the_run_naming_the_fix(self):
        args = SimpleNamespace(local_ci=True, gate=[])
        with self.assertRaises(PlanError) as refused:
            local_ci.require_command(args, self.driver.repo)
        self.assertIn("checks.full.run", str(refused.exception))
        self.assertIn("--gate", str(refused.exception))
        local_ci.require_command(SimpleNamespace(local_ci=True, gate=["make"]), self.driver.repo)
        local_ci.require_command(SimpleNamespace(local_ci=False, gate=[]), self.driver.repo)

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_without_the_flag_gh_answers_and_no_process_starts(self):
        self.driver.args.local_ci = False
        self.gh_answer = lambda argv: Completed(0, json.dumps([{"name": "t", "bucket": "pass"}]))
        self.assertEqual(self.poll(), "green")
        self.assertEqual(self.gh_matching("pr", "checks")[0][:2], ["pr", "checks"])
        self.assertEqual(self.started, [])
        self.assertEqual(self.gh_matching("api"), [])


class BoundedTests(LocalCiFixture):
    @pytest.mark.spec("Local CI runs are bounded and replaced")
    def test_the_cap_queues_the_rest_until_a_slot_frees(self):
        self.assertEqual(self.poll("t1"), "pending")
        self.assertEqual(self.poll("t2", NEWER), "pending")
        self.assertEqual(len(self.started), 1, "the second head waits for a slot")
        self.finish(self.started[0], 0)
        self.assertEqual(self.poll("t2", NEWER), "pending")
        self.assertEqual(len(self.started), 2)
        self.assertEqual(self.started[1].options["env"]["LOCAL_CI_SHA"], NEWER)

    @pytest.mark.spec("Local CI runs are bounded and replaced")
    def test_jobs_raises_the_cap(self):
        self.driver.args.local_ci_jobs = 2
        self.poll("t1")
        self.poll("t2", NEWER)
        self.assertEqual(len(self.started), 2)

    @pytest.mark.spec("Local CI runs are bounded and replaced")
    def test_a_newer_head_kills_the_older_run_and_removes_its_worktree(self):
        self.poll("t1", HEAD)
        older = self.started[0]
        tree = Path(older.options["env"]["LOCAL_CI_TREE"])
        tree.mkdir(parents=True)
        self.assertEqual(self.poll("t1", NEWER), "pending")
        self.assertEqual(self.killed, [older.pid])
        self.assertIn(
            ["git", "-C", str(self.driver.repo), "worktree", "remove", "--force", str(tree)],
            self.git,
        )
        self.assertEqual(self.started[1].options["env"]["LOCAL_CI_SHA"], NEWER)


class EvidenceTests(LocalCiFixture):
    @pytest.mark.spec("A local CI failure is evidence for the fix round")
    def test_the_finding_carries_the_local_command_log_and_last_lines(self):
        self.poll()
        process = self.started[0]
        log = Path(process.options["cwd"]) / "ci.log"
        log.write_text("\n".join("line %d" % n for n in range(1, 120)), encoding="utf-8")
        self.finish(process, 2)
        self.lane.poll_checks("t1", URL, HEAD)
        self.driver.main_failed = set()  # the base branch passes
        task = self.driver.task("t1")
        self.lane.ci_failed("t1", URL, task)
        (_, (item,)) = self.driver.rounds[0]
        self.assertEqual((item["check"], item["source"]), ("local-ci", "ci"))
        self.assertEqual((item["command"], item["log_path"]), ("make check", str(log)))
        self.assertTrue(item["excerpt"].endswith("line 119"))
        self.assertEqual(len(item["excerpt"].splitlines()), CI_STEP_LINES)
        self.assertNotIn("gh run view", item["command"])


class BaseBranchTests(LocalCiFixture):
    def failed_ticket(self):
        self.poll()
        self.finish(self.started[0], 1)
        self.lane.poll_checks("t1", URL, HEAD)
        return self.driver.task("t1")

    @pytest.mark.spec("Local CI asks the base branch once")
    def test_the_base_runs_lazily_and_the_ticket_waits_without_reading_github_runs(self):
        task = self.failed_ticket()
        self.assertEqual(len(self.started), 1)
        self.assertFalse(self.lane.ci_failed("t1", URL, task))
        self.assertFalse(self.lane.ci_failed("t1", URL, task))
        self.assertEqual(len(self.started), 2, "the base is run once")
        self.assertEqual(self.started[1].options["env"]["LOCAL_CI_SHA"], BASE)
        self.assertEqual((self.driver.rounds, self.driver.requests), ([], []))
        self.assertEqual(self.gh_matching("run"), [])

    @pytest.mark.spec("Local CI asks the base branch once")
    def test_a_base_that_fails_too_is_main_red_naming_local_ci(self):
        task = self.failed_ticket()
        self.lane.ci_failed("t1", URL, task)
        self.finish(self.started[1], 1)
        self.lane.ci_failed("t1", URL, task)
        ((kind, reason),) = self.driver.requests
        self.assertEqual(kind, "ci")
        self.assertIn("main is red: local-ci", reason)
        self.assertEqual(self.driver.rounds, [])
        self.assertEqual(self.gh_matching("run"), [])

    @pytest.mark.spec("Local CI asks the base branch once")
    def test_a_base_that_passes_makes_the_failure_a_fix_round(self):
        task = self.failed_ticket()
        self.lane.ci_failed("t1", URL, task)
        self.finish(self.started[1], 0)
        self.lane.ci_failed("t1", URL, task)
        self.assertEqual([source for source, _ in self.driver.rounds], ["ci"])
        self.assertEqual(self.driver.requests, [])
        self.assertEqual(self.driver.main_failed, set())


class StatusTests(LocalCiFixture):
    @pytest.mark.spec("Local CI posts a commit status")
    def test_the_state_is_posted_when_first_read_and_when_it_changes(self):
        self.poll()
        self.poll()
        self.finish(self.started[0], 0)
        self.poll()
        posts = self.gh_matching("api")
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][1], "repos/o/r/statuses/" + HEAD)
        self.assertIn("state=pending", posts[0])
        self.assertIn("state=success", posts[1])
        self.assertIn("context=local-ci", posts[1])
        self.assertTrue(any(a.startswith("description=") for a in posts[1]))

    @pytest.mark.spec("Local CI posts a commit status")
    def test_a_refused_post_is_logged_and_changes_no_verdict(self):
        self.gh_answer = lambda argv: Completed(1, "", "forbidden")
        self.assertEqual(self.poll(), "pending")
        self.finish(self.started[0], 1)
        self.assertEqual(self.poll(), "failed")
        self.assertIn("local-ci-status", [kind for kind, _ in self.driver.events])
        self.assertEqual(self.driver.task("t1")["ci_state"], "failed")


class RealRunTests(unittest.TestCase):
    """The script itself, with no fake: a real repository, a real shell, a real exit file."""

    def git(self, *argv, cwd):
        subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)

    @pytest.mark.spec("Local CI judges a head on this machine")
    def test_a_real_run_sets_up_runs_the_check_logs_and_removes_its_worktree(self):
        with tempfile.TemporaryDirectory() as root:
            driver = FakeDriver(root, **{})
            driver.args.gate = ["cat marker.txt && test -f built && echo checked"]
            repo = driver.repo
            self.git("init", "-q", cwd=repo)
            (repo / "marker.txt").write_text("hello\n", encoding="utf-8")
            (repo / ".orchestrate").mkdir()
            (repo / ".orchestrate" / "config.yaml").write_text(
                "worktree:\n  setup: touch built\n", encoding="utf-8"
            )
            self.git("add", ".", cwd=repo)
            self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=repo)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            lane = Pull(driver)
            state = "pending"
            deadline = time.monotonic() + 30
            while state == "pending" and time.monotonic() < deadline:
                state, _rows = lane.local.checks("t1", URL, head)
                time.sleep(0.1)
            directory = driver.run_dir / "t1" / ("local-ci-" + head[:12])
            self.assertEqual(state, "green", (directory / "ci.log").read_text(encoding="utf-8"))
            log = (directory / "ci.log").read_text(encoding="utf-8")
            self.assertIn("hello", log)
            self.assertIn("checked", log)
            self.assertFalse((directory / "tree").exists(), "the worktree is removed")
            self.assertEqual((directory / "exit").read_text(encoding="utf-8").strip(), "0")
