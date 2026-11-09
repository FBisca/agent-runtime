"""Shared fixtures for the driver tests: a fake agent-runtime, a temp git repo with an origin, a temp plan.

The fakes are modules (`fake_runtime`, `fake_gh`): the driver asks the runtime and `gh` through seams, so a test run answers them in process and starts no process per call. The fixture also puts thin
wrappers for the same modules on PATH, for a driver run outside the tests.
"""

import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import mock

import fake_gh
import fake_runtime

MEMBER = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
DRIVER = [sys.executable, "-B", "-m", "agent_orchestrator"]
TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))  # the fakes are imported by name


def wrapper(module):
    """The executable the fixture puts on PATH: a fake module run as a program."""
    return (
        "#!/usr/bin/env python3\n"
        '"""The fake %s: the module the tests answer in process, as a program."""\n'
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import %s\n"
        "sys.exit(%s.main(sys.argv[1:]))\n" % (module, str(TESTS), module, module)
    )


HEADER = """# Plan: demo

Slug: `demo`. Living spec: `docs/specs/demo.md`. Status: {status} 2026-11-06.

## Decisions

| # | Choice |
|---|---|
| D1 | keep it small |

## Scenarios

- **S1** WHEN a THEN b.
- **S2** WHEN c THEN d.
- **S3** WHEN e THEN f.

## Seams

- the CLI.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
"""

ROWS = {
    "T1": "| T1 | first | S1 | none | src/a/ | small | simple | agent | TO-DO |",
    "T2": "| T2 | second | S2 | T1 | src/b/ | regular | complex | agent | TO-DO |",
    "T3": "| T3 | third | S3 | none | src/c/ | regular | simple | agent | TO-DO |",
    "T4": "| T4 | sign-off | none | none | | | | Felippe | TO-DO |",
    "T5": "| T5 | fifth | S1 | none | src/e/ | small | complex | agent | TO-DO |",
}


# ---- the real shapes of `wait` (agent-runtime server/dispatch_wait.py, dispatch_store.py) ---------------------------
# A result is {reason, messages, assignments, unfinished: [assignment_id], receipt, more}. An assignment entry is
# {assignment_id, worker_id, state: working|complete|blocked|decision|failed, name}, plus `runtime_state`
# (idle|done|unreported|error|...) when `wait` observed the terminal. A message envelope has no name: {message_id,
# team_id, worker_id, assignment_id, sender, recipient, kind: progress|complete|blocked|decision|failed, text}.

MESSAGES = iter(range(1, 10**6))
TURN_ENDED = ("idle", "done")
NEEDS_PERSON = ("unreported", "error")


def spawn_id(name, nth=1):
    """The assignment id the fake runtime gives the nth spawn of `name`."""
    return "a-" + name + ("" if nth == 1 else "-%d" % nth)


def resume_id(name, nth=1):
    """The assignment id the fake runtime gives the nth resume of `name`."""
    return "r%d-%s" % (nth, name)


def assignment(name, state="working", runtime_state=None, id=None):
    entry = {
        "assignment_id": id or spawn_id(name),
        "worker_id": "w-" + name,
        "state": state,
        "name": name,
    }
    if runtime_state:
        entry["runtime_state"] = runtime_state
    return entry


def envelope(assignment_entry, kind, text=None):
    return {
        "message_id": "m%d" % next(MESSAGES),
        "team_id": "team",
        "worker_id": assignment_entry["worker_id"],
        "assignment_id": assignment_entry["assignment_id"],
        "sender": assignment_entry["worker_id"],
        "recipient": "lead",
        "kind": kind,
        "text": text or "%s - see run directory" % kind.upper(),
    }


def wait_result(assignments=(), messages=(), write=(), replace=(), run=(), reason=None):
    """What `wait(all)` answers, with `reason` derived the way the runtime does unless given."""
    assignments = [dict(a) for a in assignments]
    unfinished = [a["assignment_id"] for a in assignments if a["state"] != "complete"]
    states = {a["state"] for a in assignments}
    ended = any(
        a["state"] == "working" and a.get("runtime_state") in TURN_ENDED + NEEDS_PERSON
        for a in assignments
    )
    if reason is None:
        if not assignments:
            reason = "messages" if messages else "empty"
        elif "failed" in states:
            reason = "failed"
        elif ended or states & {"blocked", "decision"}:
            reason = "attention"
        elif not unfinished:
            reason = "completed"
        else:
            reason = "messages" if messages else "timeout"
    step = {
        "reason": reason,
        "messages": list(messages),
        "assignments": assignments,
        "unfinished": unfinished,
        "receipt": "r%d" % next(MESSAGES),
        "more": False,
    }
    if write:
        step["write"] = [list(w) for w in write]
    if replace:
        step["replace"] = [list(r) for r in replace]
    if run:
        step["run"] = [list(r) for r in run]
    return step


def report_wait(name, kind="complete", text=None, id=None, **extra):
    """An agent reports `kind`: its assignment takes the matching state and the envelope names it by id."""
    entry = assignment(
        name, kind if kind in ("complete", "blocked", "decision", "failed") else "working", id=id
    )
    return wait_result([entry], [envelope(entry, kind, text)], **extra)


def plan_text(rows=("T1", "T2", "T3", "T4", "T5"), status="APPROVED"):
    return HEADER.format(status=status) + "\n".join(ROWS[r] for r in rows) + "\n"


TEMPLATE = None


def template_repo():
    """A bare origin and a repo with one commit, built once for the whole suite.

    Every test copies it instead of running git seven times: the setup was a quarter of a test's time."""
    global TEMPLATE
    if TEMPLATE is None:
        TEMPLATE = tempfile.TemporaryDirectory()
        root = Path(TEMPLATE.name)
        origin = root / "origin.git"
        repo = root / "repo"
        subprocess.run(
            ["git", "init", "-q", "--bare", "-b", "main", str(origin)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", str(origin)],
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "-q",
                "-m",
                "fixture",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "push", "-q", "origin", "main"],
            check=True,
            capture_output=True,
        )
    return Path(TEMPLATE.name)


if (
    TYPE_CHECKING
):  # the mixin is mixed into a `unittest.TestCase`; the base is for the type checker only
    _Case = unittest.TestCase
else:
    _Case = object


class RunFixture(_Case):
    """Mixin for TestCase classes: temp repo with an origin, a plan, a fake agent-runtime on PATH."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.origin = self.root / "origin.git"
        self.repo = self.root / "repo"
        template = template_repo()
        shutil.copytree(template / "origin.git", self.origin)
        shutil.copytree(template / "repo", self.repo)
        self.git(self.repo, "remote", "set-url", "origin", str(self.origin))
        self.base = self.git(self.repo, "rev-parse", "HEAD")
        self.plan = self.repo / "docs" / "plans" / "demo.plan.md"
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text(plan_text(), encoding="utf-8")
        self.state_dir = self.root / "state"
        self.home = self.root / "fake"
        self.home.mkdir()
        fake = self.home / "agent-runtime"
        fake.write_text(wrapper("fake_runtime"), encoding="utf-8")
        fake.chmod(0o755)
        for name, module in (("gh", "fake_gh"),):
            tool = self.home / name
            tool.write_text(wrapper(module), encoding="utf-8")
            tool.chmod(0o755)
        self.checks(["green"])
        self.prs(["MERGED"])
        self.refuse([])
        self.waits([])
        self.states({})
        models = self.root / "CLAUDE.md"
        models.write_text(
            "## Model classes\n\n| Class | Claude | Codex |\n|---|---|---|\n"
            '| small | `model: "haiku"` | `gpt-s` |\n| medium | `model: "sonnet"` | `gpt-m` |\n'
            '| large | `model: "opus"` | `gpt-l` |\n',
            encoding="utf-8",
        )
        self.models = models

    def git(self, cwd, *args):
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def refuse(self, names):
        (self.home / "refuse.json").write_text(json.dumps(names), encoding="utf-8")

    GREEN = [
        {"name": "unit", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/55/job/8"}
    ]

    def checks(self, answers, pr=None):
        """What `gh pr checks` answers on its next calls (the last repeats): "green", "pending", "none" (no checks
        reported yet), "fail" or ("fail", <check name>). With `pr` (a number) only that PR is answered this way."""
        rows = []
        for answer in answers:
            kind, *names = answer if isinstance(answer, tuple) else (answer, "unit tests")
            fails = [
                {
                    "name": name,
                    "bucket": "fail",
                    "link": "https://github.com/o/r/actions/runs/77/job/9",
                    "description": "boom",
                }
                for name in names
            ]
            rows.append(
                {
                    "green": [0, json.dumps(self.GREEN), ""],
                    "pending": [8, json.dumps([dict(self.GREEN[0], bucket="pending")]), ""],
                    "none": [1, "", "no checks reported on the 'feat/demo-t1' branch"],
                    "fail": [1, json.dumps(fails + [self.GREEN[0]]), ""],
                }[kind]
            )
        (self.home / ("checks.json" if pr is None else "checks-%s.json" % pr)).write_text(
            json.dumps(rows), encoding="utf-8"
        )

    def prs(self, states):
        """The PR's states once `gh pr ready` has run: names or objects (mergeStateStatus ...); the last repeats."""
        (self.home / "prs.json").write_text(json.dumps(states), encoding="utf-8")

    def early(self, states):
        """PR states `gh pr view` answers before and regardless of `gh pr ready` (a merge by hand)."""
        (self.home / "view-early.json").write_text(json.dumps(states), encoding="utf-8")

    def updates(self, answers):
        """What `gh pr update-branch` answers: [exit code, stderr] per call (the last repeats)."""
        (self.home / "update.json").write_text(json.dumps(answers), encoding="utf-8")

    def born_ready(self, number=7):
        """The PR is not a draft when the worker opens it."""
        (self.home / ("ready-%s.flag" % number)).write_text("1", encoding="utf-8")

    def gh_log(self):
        path = self.home / "gh.log"
        return (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )

    def gh_calls(self, *prefix):
        """The argv of every logged gh call that starts with `prefix`."""
        return [c["argv"] for c in self.gh_log() if c["argv"][: len(prefix)] == list(prefix)]

    def checks_log(self):
        """The pull requests whose checks were polled, in order."""
        return [argv[2] for argv in self.gh_calls("pr", "checks")]

    def states(self, mapping):
        (self.home / "states.json").write_text(json.dumps(mapping), encoding="utf-8")

    def gone(self, name, state="exited"):
        """`name` has no live terminal when the run starts, and is live again once the driver spawns it."""
        self.states({name: state})
        (self.home / "stopped.json").write_text(json.dumps([name]), encoding="utf-8")

    def goes(self, name, state="exited"):
        """The `write` entries of a wait step during which `name` is lost (see `gone`)."""
        return [
            [str(self.home / "states.json"), json.dumps({name: state})],
            [str(self.home / "stopped.json"), json.dumps([name])],
        ]

    def waits(self, steps):
        (self.home / "waits.json").write_text(json.dumps(steps), encoding="utf-8")

    def calls(self, tool=None):
        path = self.home / "calls.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return [r for r in rows if tool in (None, r["tool"])]

    def env(self):
        return dict(
            os.environ,
            PATH=str(self.home) + os.pathsep + os.environ["PATH"],
            PYTHONPATH=str(TESTS) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            FAKE_RUNTIME_HOME=str(self.home),
            FAKE_PLAN=str(self.plan),
            FAKE_CHECKOUT=str(self.root / "repo.worktrees" / "demo-t1"),
            FAKE_EVENTS=str(self.state_dir / "demo" / "events.jsonl"),
        )

    def runtime_process(self, argv, timeout=None):
        """The fake runtime's answer, in process: a driver run starts no process per call."""
        return fake_runtime.process(
            argv[1:], timeout=timeout
        )  # the fake reads the argv without the program name

    def gh_process(self, argv, timeout=None):
        """The fake `gh`'s answer, in process."""
        return fake_gh.process(argv, timeout=timeout)

    payload_grace = 0.05  # the payload wait, shortened so a test does not pay the real grace

    def repeated(self, step, times=3):
        """The same agent reporting again, `times` times, each under the assignment the previous warning resumed.

        R16 asks the sender before the Lead, so a bad payload is read twice; the agent it warns comes back as a new
        assignment, and a report under the old one would land on no gate at all.
        """
        steps = []
        for nth in range(1, times + 1):
            copy_of = copy.deepcopy(step)
            if nth > 1:
                for entry in copy_of.get("assignments") or []:
                    name = entry.get("worker_id", "")[2:]
                    entry["assignment_id"] = resume_id(name, nth - 1)
                for message in (
                    copy_of.get("messages") or []
                ):  # the envelope names the assignment too
                    name = message.get("worker_id", "")[2:]
                    message["assignment_id"] = resume_id(name, nth - 1)
            steps.append(copy_of)
        return steps

    def in_process(self, spec, argv, timeout=60):
        """The script run in this process, with the fakes answering in process.

        The run happens in a thread so a driver that never ends fails the test instead of hanging the suite."""
        out, err = io.StringIO(), io.StringIO()
        codes, errors = [], []

        def work():
            try:
                with (
                    mock.patch.dict(os.environ, self.env()),
                    redirect_stdout(out),
                    redirect_stderr(err),
                    mock.patch.object(spec, "runtime_process", self.runtime_process),
                    mock.patch.object(spec, "PAYLOAD_GRACE", self.payload_grace),
                    mock.patch.object(spec, "gh_process", self.gh_process),
                ):
                    codes.append(spec.run_cli(argv))
            except (
                BaseException
            ) as error:  # the run's own failure belongs to the test that asked for it
                errors.append(error)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        thread.join(timeout)
        if errors:
            raise errors[0]
        if thread.is_alive():
            raise AssertionError(
                "the driver did not finish in %ss: a fake that never answers" % timeout
            )
        return subprocess.CompletedProcess(
            argv, codes[0] if codes else 1, out.getvalue(), err.getvalue()
        )

    def cli(self, *args, timeout=60):
        """A subcommand of the script run as the Lead would run it; the caller checks the exit code."""
        return subprocess.run(
            [*DRIVER, *args], capture_output=True, text=True, env=self.env(), timeout=timeout
        )

    def decide_argv(self, unit, action, reason="because", answer=None):
        return [
            *DRIVER,
            "decide",
            str(self.plan),
            unit,
            action,
            "--reason",
            reason,
            "--state-dir",
            str(self.state_dir),
            *(["--answer", answer] if answer else []),
        ]

    def decide_step(self, unit, action, reason="because", answer=None, **extra):
        """A wait step during which the Lead runs `decide`: the fake runtime runs it when the driver calls wait."""
        return wait_result(run=[self.decide_argv(unit, action, reason, answer)], **extra)

    def wakes(self):
        path = self.state_dir / "demo" / "wakes.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return rows

    def wake_lines(self, kind=None):
        return [w["text"] for w in self.wakes() if kind in (None, w["kind"])]

    def run_driver(self, *extra, plan=None, expect=0):
        """The driver run in this process: the fakes answer in process, so the run starts no process of its own."""
        spec = load_script()
        argv = [
            "run",
            str(plan or self.plan),
            "--state-dir",
            str(self.state_dir),
            "--models-file",
            str(self.models),
            "--wait-seconds",
            "1",
            "--idle-interval",
            "0",
            *extra,
        ]
        result = self.in_process(spec, argv)
        self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def ledger(self):
        return json.loads((self.state_dir / "demo.json").read_text(encoding="utf-8"))

    def events(self, *kinds):
        """The run's events.jsonl as dicts, optionally only the given kinds."""
        path = self.state_dir / "demo" / "events.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return [r for r in rows if not kinds or r["kind"] in kinds]

    def assert_intents_precede_spawns(self):
        """D11: every spawn call went through ensure_agent, so its intent was already in events.jsonl."""
        spawns = self.calls("spawn")
        self.assertTrue(spawns)
        for call in spawns:
            logged = [json.loads(line) for line in call["events"]]
            self.assertIn(
                call["args"]["name"],
                [e.get("name") for e in logged if e["kind"] == "intent"],
                "no intent event before the spawn of %s" % call["args"]["name"],
            )


def load_script():
    """The engine's driver module, as the Lead's process sees it."""
    from agent_orchestrator import driver

    return driver


@contextmanager
def recorded_idles(spec):
    """Replace `Driver.idle` with a recorder of its interval. Patching `time.sleep` itself would also hit
    `subprocess` (its timeout loop sleeps ~1 ms), which makes the count and the interval flaky."""
    intervals = []
    with mock.patch.object(
        spec.Driver, "idle", lambda driver: intervals.append(driver.args.idle_interval)
    ):
        yield intervals
