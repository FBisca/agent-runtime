"""The evidence runner (S12-S15): a check runs in a detached worktree and leaves a receipt, a result is PASS, FAIL or
UNKNOWN by rule, a negative patch must break the check, and `plan approve` refuses an uncalibrated baseline.

Every test starts git and a shell, so all are integration tests."""

import fcntl
import hashlib
import io
import json
import os
import signal
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
import yaml
from agent_orchestrator import runner
from fixtures import load_script

pytestmark = pytest.mark.integration

SECRET = "s3cr3t-value-never-logged"


def sha256_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(repo, *args):
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=30
    )
    return done.stdout.strip()


def junit(cases="", tests=0, failures=0, errors=0, skipped=0):
    body = (
        '<?xml version="1.0" encoding="utf-8"?><testsuites>'
        f'<testsuite name="s" tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}">'
        f"{cases}</testsuite></testsuites>"
    )
    return f"cat > \"$EEC_JUNIT\" <<'XML'\n{body}\nXML\n"


PASSING = '<testcase classname="t" name="ok"/>'
FAILING = '<testcase classname="t" name="bad"><failure message="assert 1 == 2"/></testcase>'
ERRORING = '<testcase classname="" name="tests.test_x"><error message="collection"/></testcase>'
SKIPPED = '<testcase classname="t" name="skip"><skipped message="why"/></testcase>'
# A case that dies on a missing interface is a failure, but not an assertion: it says nothing about the oracle.
MISSING = (
    '<testcase classname="t" name="behaviour"><failure message="ModuleNotFoundError: '
    "No module named 'not_yet_written'\"/></testcase>"
)
UNITTEST_ASSERTION = (
    '<testcase classname="t" name="eq"><failure type="AssertionError" message="1 != 2"/></testcase>'
)
XFAILED = '<testcase classname="t" name="xf"><skipped type="pytest.xfail" message="x"/></testcase>'


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "lead@example.com")
        git(self.repo, "config", "user.name", "Lead")
        (self.repo / "a.txt").write_text("good\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "base")
        self.commit = git(self.repo, "rev-parse", "HEAD")
        self.receipts = self.root / "receipts"

    def run_check(self, check, **kwargs):
        kwargs.setdefault("scenario", "S1")
        kwargs.setdefault("phase", "integration")
        kwargs.setdefault("digest", "d" * 64)
        return runner.run_evidence(
            self.repo, kwargs.pop("commit", self.commit), check=check, **kwargs
        )


class EvidenceRunner(RepoCase):
    def test_S12_runs_in_a_detached_worktree_at_the_commit_with_the_declared_environment(self):
        venv = self.root / "leadvenv"
        (venv / "bin").mkdir(parents=True)
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        real_cache = self.root / "cache"
        base_env = {
            "PATH": f"{venv / 'bin'}:{os.environ['PATH']}",
            "HOME": str(self.root / "realhome"),
            "VIRTUAL_ENV": str(venv),
            "XDG_STATE_HOME": str(self.root / "realstate"),
            "XDG_CACHE_HOME": str(real_cache),
            "UV_CACHE_DIR": str(real_cache / "uv"),
            "LANG": "C.UTF-8",
            "DECLARED": SECRET,
            "UNDECLARED": "nope",
        }
        check = (
            "echo HOME=$HOME; echo XDG_STATE_HOME=$XDG_STATE_HOME; echo VIRTUAL_ENV=${VIRTUAL_ENV-unset};"
            " echo PATH=$PATH; echo UNDECLARED=${UNDECLARED-unset}; echo XDG_CACHE_HOME=$XDG_CACHE_HOME;"
            " echo UV_CACHE_DIR=$UV_CACHE_DIR; echo LANG=$LANG; echo JUNIT=$EEC_JUNIT; echo PWD=$(pwd);"
            " echo HEAD=$(git rev-parse HEAD); echo PROBE=$(cat checks/probe.txt);"
            ' echo DECLARED_SEEN=$([ "$DECLARED" = %s ] && echo yes || echo no);'
            " set -o | grep -E 'pipefail|errexit'" % SECRET
        )
        run = self.run_check(
            check,
            oracles={"probe.txt.oracle": b"probe\n"},
            place="checks/",
            env_names=["DECLARED"],
            base_env=base_env,
            receipts_dir=self.receipts,
        )
        seen = dict(line.split("=", 1) for line in run.log.splitlines() if "=" in line)
        self.assertNotIn(str(self.root / "realhome"), seen["HOME"])
        self.assertNotEqual(seen["XDG_STATE_HOME"], base_env["XDG_STATE_HOME"])
        self.assertEqual(seen["VIRTUAL_ENV"], "unset")
        self.assertNotIn(str(venv / "bin"), seen["PATH"])
        self.assertEqual(seen["UNDECLARED"], "unset")
        self.assertEqual(seen["XDG_CACHE_HOME"], str(real_cache))
        self.assertEqual(seen["UV_CACHE_DIR"], str(real_cache / "uv"))
        self.assertEqual(seen["LANG"], "C.UTF-8")
        self.assertTrue(seen["JUNIT"].endswith(".xml"))
        self.assertNotEqual(Path(seen["PWD"]).resolve(), self.repo)
        self.assertEqual(seen["HEAD"], self.commit)
        self.assertEqual(seen["PROBE"], "probe")
        self.assertEqual(seen["DECLARED_SEEN"], "yes")
        self.assertIn("pipefail", run.log)
        self.assertEqual(run.receipt["result"], "PASS", run.log)
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 0, "the worktree stayed")
        self.assertFalse(Path(seen["PWD"]).exists())
        self.assertNotIn(SECRET, run.log)

    def test_S12_a_failing_pipeline_stops_the_check_under_errexit_and_pipefail(self):
        run = self.run_check("false | true; echo unreachable")
        self.assertNotIn("unreachable", run.log)
        self.assertEqual(run.receipt["exit"], 1)

    def test_S12_the_receipt_names_what_ran_and_never_an_env_value(self):
        base_env = {"PATH": os.environ["PATH"], "HOME": str(self.root), "DECLARED": SECRET}
        run = self.run_check(
            junit(PASSING, tests=1) + "echo $DECLARED",
            oracles={"probe.txt.oracle": b"probe\n"},
            place="checks/",
            env_names=["DECLARED"],
            base_env=base_env,
            receipts_dir=self.receipts,
        )
        receipt = run.receipt
        self.assertEqual(
            (receipt["scenario"], receipt["phase"], receipt["digest"]),
            ("S1", "integration", "d" * 64),
        )
        self.assertEqual(receipt["commit"], self.commit)
        self.assertEqual(receipt["tree"], git(self.repo, "rev-parse", self.commit + "^{tree}"))
        self.assertEqual(receipt["check"], junit(PASSING, tests=1) + "echo $DECLARED")
        self.assertEqual(
            receipt["oracles"], {"probe.txt.oracle": hashlib.sha256(b"probe\n").hexdigest()}
        )
        self.assertEqual(receipt["env"], ["DECLARED"])
        for tool in ("bash", "git"):
            self.assertTrue(receipt["tools"][tool], receipt["tools"])
        self.assertEqual(receipt["exit"], 0)
        self.assertEqual(
            receipt["junit"],
            {
                "tests": 1,
                "executed": 1,
                "passed": 1,
                "failures": 0,
                "assertions": 0,
                "errors": 0,
                "skipped": 0,
            },
        )
        self.assertEqual(receipt["result"], "PASS")
        self.assertFalse(receipt["exit_code_only"])
        self.assertGreaterEqual(receipt["seconds"], 0)
        self.assertEqual(run.path, self.receipts / "S1.integration.1.json")
        text = run.path.read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), run.sha256)
        self.assertEqual(json.loads(text), receipt)
        self.assertNotIn(SECRET, text)
        self.assertNotIn(SECRET, (self.receipts / "S1.integration.1.log").read_text())

    def test_S12_a_second_run_of_the_same_item_is_a_new_receipt_not_an_overwrite(self):
        first = self.run_check("true", receipts_dir=self.receipts)
        second = self.run_check("true", receipts_dir=self.receipts)
        self.assertEqual(first.path.name, "S1.integration.1.json")
        self.assertEqual(second.path.name, "S1.integration.2.json")
        self.assertTrue(first.path.is_file())

    def test_S12_the_ledger_entry_holds_the_receipt_sha256(self):
        run = self.run_check("true", receipts_dir=self.receipts)
        data = {}
        runner.record_in_ledger(data, run)
        self.assertEqual(data["receipts"]["S1.integration.1"], run.sha256)


class Results(RepoCase):
    def result(self, run):
        return run.receipt["label"]

    def test_S13_a_clean_exit_with_executed_cases_and_no_skips_is_PASS(self):
        run = self.run_check(junit(PASSING + PASSING, tests=2))
        self.assertEqual((run.receipt["result"], self.result(run)), ("PASS", "PASS"))

    def test_S13_an_exit_with_no_cases_executed_is_UNKNOWN(self):
        run = self.run_check(junit("", tests=0))
        self.assertEqual(run.receipt["result"], "UNKNOWN")
        self.assertEqual(run.receipt["reason"], "no cases executed")

    def test_S13_a_skipped_or_xfailed_case_is_UNKNOWN(self):
        for label, cases in (("skipped", PASSING + SKIPPED), ("xfailed", PASSING + XFAILED)):
            with self.subTest(label):
                run = self.run_check(junit(cases, tests=2, skipped=1))
                self.assertEqual(run.receipt["result"], "UNKNOWN")
                self.assertEqual(run.receipt["reason"], "cases skipped")

    def test_S13_an_assertion_failure_is_FAIL(self):
        run = self.run_check(junit(PASSING + FAILING, tests=2, failures=1) + "exit 1")
        self.assertEqual((run.receipt["result"], self.result(run)), ("FAIL", "FAIL"))

    def test_S13_only_an_assertion_failure_counts_as_one_in_the_junit_counts(self):
        for cases, assertions in (
            (PASSING + FAILING, 1),
            (PASSING + UNITTEST_ASSERTION, 1),
            (PASSING + MISSING, 0),
            (FAILING + MISSING, 1),
        ):
            with self.subTest(cases=cases):
                run = self.run_check(junit(cases, tests=2, failures=2) + "exit 1")
                self.assertEqual(run.receipt["result"], "FAIL")
                self.assertEqual(run.receipt["junit"]["assertions"], assertions)

    def test_S13_a_check_that_dies_on_an_unbound_variable_never_ran_and_is_UNKNOWN_env(self):
        run = self.run_check('test -n "$EEC_UNDECLARED_VARIABLE"')
        self.assertEqual(self.result(run), "UNKNOWN (env)")
        self.assertIn("EEC_UNDECLARED_VARIABLE", run.receipt["detail"])

    def test_S13_an_unbound_variable_in_a_script_the_check_runs_is_not_the_checks_own(self):
        script = (
            'printf \'set -u\\necho $EEC_NOPE\\n\' > "$TMPDIR/inner.sh"; bash "$TMPDIR/inner.sh"'
        )
        run = self.run_check(script, base_env={**os.environ, "TMPDIR": str(self.root)})
        self.assertIn("unbound variable", run.log)
        self.assertEqual(run.receipt["label"], "FAIL")

    def test_S13_an_unbound_variable_beside_junit_is_not_taken_for_a_missing_env(self):
        run = self.run_check(junit(PASSING + FAILING, tests=2, failures=1) + "exit 1")
        self.assertEqual(run.receipt["label"], "FAIL")

    def test_S13_a_collection_or_setup_error_is_FAIL_error(self):
        run = self.run_check(junit(ERRORING, tests=1, errors=1) + "exit 2")
        self.assertEqual(self.result(run), "FAIL (error)")
        self.assertEqual(run.receipt["result"], "FAIL")

    def test_S13_a_timeout_is_UNKNOWN_and_the_process_group_is_killed(self):
        marker = self.root / "child.pid"
        start = time.monotonic()
        run = self.run_check(f"sleep 300 & echo $! > {marker}; wait", timeout=1)
        self.assertLess(time.monotonic() - start, 30)
        self.assertEqual(self.result(run), "UNKNOWN (timeout)")
        pid = int(marker.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
            self.fail("the check's child survived the timeout")

    def test_S13_exit_127_is_UNKNOWN_missing_tool(self):
        run = self.run_check("no-such-tool-xyz --version")
        self.assertEqual(self.result(run), "UNKNOWN (missing tool)")

    def test_S13_an_unset_env_name_is_UNKNOWN_env_and_the_check_never_runs(self):
        marker = self.root / "ran"
        run = self.run_check(
            f"touch {marker}",
            env_names=["EEC_NOT_SET_ANYWHERE"],
            base_env={"PATH": os.environ["PATH"]},
        )
        self.assertEqual(self.result(run), "UNKNOWN (env)")
        self.assertIn("EEC_NOT_SET_ANYWHERE", run.receipt["detail"])
        self.assertFalse(marker.exists())
        self.assertIsNone(run.receipt["exit"])

    def test_S13_a_modified_tracked_file_is_UNKNOWN_dirty_tree(self):
        run = self.run_check("echo changed >> a.txt")
        self.assertEqual(self.result(run), "UNKNOWN (dirty tree)")
        self.assertEqual((self.repo / "a.txt").read_text(), "good\n")

    def test_S13_a_check_without_junit_is_labelled_exit_code_only(self):
        passed, failed = self.run_check("true"), self.run_check("false")
        self.assertEqual(
            (passed.receipt["result"], passed.receipt["exit_code_only"]), ("PASS", True)
        )
        self.assertEqual(
            (failed.receipt["result"], failed.receipt["exit_code_only"]), ("FAIL", True)
        )
        self.assertIsNone(passed.receipt["junit"])

    def test_S13_nothing_is_rerun_automatically(self):
        counter = self.root / "count"
        run = self.run_check(f"echo x >> {counter}; exit 1")
        self.assertEqual(run.receipt["result"], "FAIL")
        self.assertEqual(counter.read_text().count("x"), 1)


class NegativeControl(RepoCase):
    def patch(self):
        (self.repo / "a.txt").write_text("broken\n")
        text = git(self.repo, "diff") + "\n"
        git(self.repo, "checkout", "--", "a.txt")
        return text.encode()

    def test_S15_the_check_must_fail_on_the_patched_scratch_copy(self):
        run = self.run_check(
            "grep -qx good a.txt",
            oracles={},
            negative=("break.patch.oracle", self.patch()),
            receipts_dir=self.receipts,
        )
        self.assertEqual(run.receipt["result"], "PASS", run.log)
        self.assertEqual(run.receipt["negative"]["result"], "FAIL")
        self.assertEqual((self.repo / "a.txt").read_text(), "good\n")

    def test_S15_a_check_that_still_passes_on_the_patched_copy_is_FAIL_vacuous(self):
        run = self.run_check("true", negative=("break.patch.oracle", self.patch()))
        self.assertEqual((run.receipt["result"], run.receipt["label"]), ("FAIL", "FAIL (vacuous)"))
        self.assertEqual(run.receipt["negative"]["result"], "PASS")

    def test_S15_a_negative_that_fails_on_something_other_than_an_assertion_is_UNKNOWN(self):
        check = (
            "if grep -qx broken a.txt; then\n"
            + junit(PASSING + MISSING, tests=2, failures=1)
            + "exit 1\nelse\n"
            + junit(PASSING, tests=1)
            + "fi\n"
        )
        run = self.run_check(check, negative=("break.patch.oracle", self.patch()))
        self.assertEqual(run.receipt["result"], "UNKNOWN")
        self.assertEqual(run.receipt["reason"], "negative control")

    def test_S15_a_patch_that_does_not_apply_is_UNKNOWN(self):
        run = self.run_check("true", negative=("bad.patch.oracle", b"not a patch\n"))
        self.assertEqual(run.receipt["result"], "UNKNOWN")
        self.assertIn("negative", run.receipt["reason"])


class Baseline(RepoCase):
    """S14: `plan approve` runs the baseline at `base` and refuses what is not a calibrated red."""

    def setUp(self):
        super().setUp()
        self.state = self.root / "state"
        self.plans = self.repo / "docs" / "plans"
        self.plans.mkdir(parents=True)
        self.plan = self.plans / "demo.plan.yaml"
        self.oracles = self.plans / "demo.oracle"
        self.oracles.mkdir()
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "plans dir")

    def write(self, evidence, oracle=True):
        if oracle:
            (self.oracles / "probe.txt.oracle").write_text("probe\n")
            evidence = {**evidence, "oracle": ["probe.txt.oracle"], "place": "checks/"}
        self.plan.write_text(
            yaml.safe_dump(
                {
                    "schema": "plan/1",
                    "slug": "demo",
                    "goal": "g",
                    "spec": "docs/specs/demo.md",
                    "outcome": ["S1"],
                    "scenarios": [
                        {"id": "S1", "when": "a", "then": "b", "evidence": evidence},
                        {"id": "S2", "when": "c", "then": "d"},
                    ],
                    "tickets": [
                        {
                            "id": "T1",
                            "delivers": "x",
                            "satisfies": ["S1", "S2"],
                            "paths": ["src/a/"],
                            "size": "small",
                            "difficulty": "simple",
                        }
                    ],
                },
                sort_keys=False,
            )
        )

    def approve(self):
        out = io.StringIO()
        spec = load_script()
        with (
            mock.patch.object(spec, "resolve_runtime", return_value={"role": "lead"}),
            mock.patch.dict(os.environ, {"HC_TERMINAL_ID": "lead"}),
            redirect_stdout(out),
            redirect_stderr(out),
        ):
            code = spec.run_cli(
                [
                    "plan",
                    "approve",
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state),
                    "--base",
                    "main",
                ]
            )
        return code, out.getvalue()

    def refused(self, evidence, *expected, oracle=True):
        self.write(evidence, oracle)
        before = self.plan.read_bytes()
        code, message = self.approve()
        self.assertNotEqual(code, 0, message)
        for text in ("S1", *expected):
            self.assertIn(text, message)
        self.assertEqual(self.plan.read_bytes(), before, "approve changed the plan")
        self.assertFalse(self.state.exists() and any(self.state.rglob("*")), "approve wrote state")
        self.assertEqual(git(self.repo, "worktree", "list").count("\n"), 0)
        return message

    def approved(self, evidence, oracle=True):
        self.write(evidence, oracle)
        code, message = self.approve()
        self.assertEqual(code, 0, message)
        approval = yaml.safe_load(self.plan.read_text())["approval"]
        receipts = list(self.state.rglob("receipts/S1.baseline.1.json"))
        self.assertEqual(len(receipts), 1, list(self.state.rglob("*")))
        return approval, json.loads(receipts[0].read_text())

    def test_S14_a_calibrated_red_is_approved_and_the_receipt_says_so(self):
        check = junit(PASSING + FAILING, tests=2, failures=1) + "exit 1"
        approval, receipt = self.approved({"check": check, "baseline": "fail"})
        self.assertTrue(receipt["calibrated"])
        self.assertEqual(
            (receipt["phase"], receipt["digest"], receipt["result"]),
            ("baseline", approval["digest"], "FAIL"),
        )
        self.assertEqual(receipt["commit"], approval["base"])

    def ledger_path(self):
        from agent_orchestrator import driver

        return self.state / driver.repo_key(self.repo) / "demo.json"

    def test_S12_approve_records_each_baseline_receipts_sha256_in_the_plans_ledger(self):
        check = junit(PASSING + FAILING, tests=2, failures=1) + "exit 1"
        _, receipt = self.approved({"check": check, "baseline": "fail"})
        path = next(self.state.rglob("receipts/S1.baseline.1.json"))
        data = json.loads(self.ledger_path().read_text())
        self.assertEqual(data["receipts"], {"S1.baseline.1": sha256_of(path)})
        self.assertEqual((data["version"], data["tasks"]), (1, {}))

    def test_S12_approve_keeps_what_the_ledger_already_holds_and_a_second_approval_adds_to_it(self):
        ledger = self.ledger_path()
        ledger.parent.mkdir(parents=True)
        ledger.write_text(
            json.dumps({"version": 1, "tasks": {"t1": {"status": "COMPLETE"}}, "lead": "L"})
        )
        check = junit(PASSING + FAILING, tests=2, failures=1) + "exit 1"
        self.approved({"check": check, "baseline": "fail"})
        self.approve()
        data = json.loads(ledger.read_text())
        self.assertEqual((data["tasks"], data["lead"]), ({"t1": {"status": "COMPLETE"}}, "L"))
        self.assertEqual(sorted(data["receipts"]), ["S1.baseline.1", "S1.baseline.2"])
        for name, digest in data["receipts"].items():
            self.assertEqual(digest, sha256_of(next(self.state.rglob("receipts/%s.json" % name))))

    def test_S12_a_ledger_under_the_old_key_is_imported_before_the_baseline_is_recorded(self):
        old = self.state / "demo.json"
        old.parent.mkdir(parents=True)
        task = {"packet": {"plan": str(self.plan), "checkout": str(self.repo)}}
        old.write_text(json.dumps({"version": 1, "tasks": {"t1": task}}))
        check = junit(PASSING + FAILING, tests=2, failures=1) + "exit 1"
        self.approved({"check": check, "baseline": "fail"})
        data = json.loads(self.ledger_path().read_text())
        self.assertEqual(data["tasks"], {"t1": task})
        self.assertEqual(list(data["receipts"]), ["S1.baseline.1"])
        self.assertFalse(old.exists(), "the old ledger was left behind")

    def hold_run_lock(self, text="pid 4242 since 2026-11-10T09:00:00Z\n"):
        common = Path(git(self.repo, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        (common / "agent-orchestrator").mkdir(exist_ok=True)
        lock = open(common / "agent-orchestrator" / "demo.lock", "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.write(text)
        lock.flush()

    def test_S12_approve_refuses_while_a_driver_holds_the_plans_run_lock_and_writes_nothing(self):
        old = self.state / "demo.json"
        old.parent.mkdir(parents=True)
        task = {"packet": {"plan": str(self.plan), "checkout": str(self.repo)}}
        old.write_text(json.dumps({"version": 1, "tasks": {"t1": task}}))
        (self.state / "demo").mkdir()
        (self.state / "demo" / "events.jsonl").write_text("{}\n")
        self.write(
            {"check": junit(PASSING + FAILING, tests=2, failures=1) + "exit 1", "baseline": "fail"}
        )
        before = {p: p.read_bytes() for p in (old, self.plan, self.state / "demo" / "events.jsonl")}
        self.hold_run_lock()
        code, message = self.approve()
        self.assertEqual(code, 2, message)
        self.assertIn("pid 4242", message)
        self.assertIn("plan demo is being driven by", message)
        self.assertIn("orchestrate-demo", message)
        self.assertIn("plan approve", message)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(sorted(p.name for p in self.state.iterdir()), ["demo", "demo.json"])

    def test_S12_a_ledger_that_stays_busy_refuses_the_approval_instead_of_a_traceback(self):
        ledger = self.ledger_path()
        ledger.parent.mkdir(parents=True)
        lock = open(ledger.with_name(ledger.name + ".lock"), "w")
        self.addCleanup(lock.close)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.write(
            {"check": junit(PASSING + FAILING, tests=2, failures=1) + "exit 1", "baseline": "fail"}
        )
        before = self.plan.read_bytes()
        with mock.patch.object(runner, "LEDGER_WAIT", 0.2):
            code, message = self.approve()
        self.assertEqual(code, 2, message)
        self.assertIn("busy", message)
        self.assertNotIn("Traceback", message)
        self.assertEqual(self.plan.read_bytes(), before, "the plan was approved")
        self.assertFalse(list(self.state.rglob("approvals")), "a snapshot was written")

    def test_S12_a_refused_approval_creates_no_ledger(self):
        self.refused({"check": "true", "baseline": "fail"}, "baseline", oracle=False)
        self.assertFalse(self.ledger_path().exists())

    def test_S12_a_plan_with_no_check_evidence_creates_no_ledger(self):
        self.write({"human": "confirm"}, oracle=False)
        self.assertEqual(self.approve()[0], 0)
        self.assertFalse(self.ledger_path().exists())

    def test_S14_a_red_that_cannot_be_calibrated_is_approved_but_marked_so(self):
        _, receipt = self.approved({"check": "grep -q nothing a.txt", "baseline": "fail"})
        self.assertFalse(receipt["calibrated"])
        self.assertTrue(receipt["exit_code_only"])

    def test_S14_a_check_that_passes_at_base_is_refused_as_vacuous(self):
        message = self.refused(
            {"check": "true", "baseline": "fail"}, "baseline", "fail", "PASS", oracle=False
        )
        self.assertIn("vacuous", message)

    def test_S14_a_red_from_a_collection_error_is_refused(self):
        check = junit(ERRORING, tests=1, errors=1) + "exit 2"
        self.refused({"check": check, "baseline": "fail"}, "baseline", "error")

    def test_S14_a_new_oracle_whose_behaviour_dies_on_a_missing_interface_is_uncalibrated(self):
        check = junit(PASSING + MISSING, tests=2, failures=1) + "exit 1"
        _, receipt = self.approved({"check": check, "baseline": "fail"})
        self.assertFalse(receipt["calibrated"])
        self.assertEqual(receipt["junit"]["assertions"], 0)

    def test_S14_a_missing_interface_with_no_passing_control_is_refused(self):
        check = junit(MISSING, tests=1, failures=1) + "exit 1"
        self.refused({"check": check, "baseline": "fail"}, "baseline", "control")

    def test_S14_an_existing_test_that_dies_on_a_missing_interface_is_refused(self):
        check = junit(PASSING + MISSING, tests=2, failures=1) + "exit 1"
        self.refused(
            {"check": check, "baseline": "fail"}, "baseline", "on an assertion", oracle=False
        )

    def test_S14_a_check_that_dies_on_an_unbound_variable_is_refused_naming_it(self):
        for expected in ("fail", "pass"):
            with self.subTest(expected):
                self.refused(
                    {"check": 'test -n "$EEC_UNDECLARED_VARIABLE"', "baseline": expected},
                    "baseline",
                    "UNKNOWN (env)",
                    "EEC_UNDECLARED_VARIABLE",
                    oracle=False,
                )

    def test_S14_a_new_oracle_with_no_passing_control_case_is_refused(self):
        check = junit(FAILING, tests=1, failures=1) + "exit 1"
        self.refused({"check": check, "baseline": "fail"}, "baseline", "control")

    def test_S14_an_existing_test_needs_no_control_case(self):
        check = junit(FAILING, tests=1, failures=1) + "exit 1"
        _, receipt = self.approved({"check": check, "baseline": "fail"}, oracle=False)
        self.assertTrue(receipt["calibrated"])

    def test_S14_a_baseline_pass_item_must_pass(self):
        self.refused(
            {"check": "false", "baseline": "pass"}, "baseline", "pass", "FAIL", oracle=False
        )
        _, receipt = self.approved({"check": "true", "baseline": "pass"}, oracle=False)
        self.assertEqual(receipt["result"], "PASS")

    def test_S14_an_unknown_baseline_is_refused_with_its_reason(self):
        self.refused(
            {"check": "no-such-tool-xyz", "baseline": "fail"},
            "baseline",
            "missing tool",
            oracle=False,
        )

    def test_S14_the_baseline_runs_the_snapshot_oracles_at_base(self):
        (self.repo / "checks").mkdir()
        _, receipt = self.approved(
            {"check": "test -f checks/probe.txt && exit 1; exit 1", "baseline": "fail"}
        )
        self.assertEqual(list(receipt["oracles"]), ["probe.txt.oracle"])
        self.assertFalse((self.repo / "checks" / "probe.txt").exists())
