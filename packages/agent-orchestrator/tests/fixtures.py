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
import yaml

MEMBER = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
DRIVER = [sys.executable, "-B", "-m", "agent_orchestrator"]
TESTS = Path(__file__).resolve().parent
UV_CACHE = str(Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv")
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


SCENARIOS = ("S1", "S2", "S3")
ROWS = {
    "T1": dict(
        delivers="first", satisfies=["S1"], paths=["src/a/"], size="small", difficulty="simple"
    ),
    "T2": dict(
        delivers="second",
        satisfies=["S2"],
        paths=["src/b/"],
        size="regular",
        difficulty="complex",
        blocked_by=["T1"],
    ),
    "T3": dict(
        delivers="third", satisfies=["S3"], paths=["src/c/"], size="regular", difficulty="simple"
    ),
    "T4": dict(delivers="sign-off", satisfies=["S1"], paths=["docs/"], owner="Felippe"),
    "T5": dict(
        delivers="fifth", satisfies=["S1"], paths=["src/e/"], size="small", difficulty="complex"
    ),
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


def plan_data(rows=("T1", "T2", "T3", "T4", "T5")):
    """The fixture plan as a plan/1 mapping: the rows' tickets and three scenarios (human evidence only)."""
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "outcome": ["S1"],
        "scenarios": [
            dict(id=sid, when="a", then="b", evidence={"human": "confirm"}) for sid in SCENARIOS
        ],
        "tickets": [dict(id=name, **ROWS[name]) for name in rows],
    }


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
        self.set_plan()

    def set_plan(self, rows=("T1", "T2", "T3", "T4", "T5"), **kwargs):
        """The plan this test runs: the fixture rows, approved. Called again, it replaces the plan and its approval."""
        self.plan = self.approved_yaml_plan(rows, **kwargs)
        return self.plan

    def git(self, cwd, *args):
        # A CI runner has no git identity: every commit a test makes names its own.
        result = subprocess.run(
            [
                "git",
                "-C",
                str(cwd),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.invalid",
                *args,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def refuse(self, names):
        (self.home / "refuse.json").write_text(json.dumps(names), encoding="utf-8")

    GREEN = [
        {"name": "unit", "bucket": "pass", "link": "https://github.com/o/r/actions/runs/55/job/8"}
    ]

    def checks(self, answers, pr=None, ready=False):
        """What `gh pr checks` answers on its next calls (the last repeats): "green", "pending", "none" (no checks
        reported yet), "fail" or ("fail", <check name>). With `pr` (a number) only that PR is answered this way; with
        `ready`, only a PR that is ready (not a draft)."""
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
        name = (
            "checks-ready.json" if ready else "checks.json" if pr is None else "checks-%s.json" % pr
        )
        (self.home / name).write_text(json.dumps(rows), encoding="utf-8")

    def prs(self, states):
        """The PR's states once `gh pr ready` has run: names or objects (mergeStateStatus ...); the last repeats."""
        (self.home / "prs.json").write_text(json.dumps(states), encoding="utf-8")

    def drafts(self, states):
        """The PR's states while it is a draft (OPEN when unscripted); the last repeats."""
        (self.home / "view-draft.json").write_text(json.dumps(states), encoding="utf-8")

    def undos(self, answers):
        """What `gh pr ready --undo` answers: [exit code, stderr] per call (the last repeats)."""
        (self.home / "undo.json").write_text(json.dumps(answers), encoding="utf-8")

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
        """The environment of a subprocess the test starts: a home of its own, so a command that falls back to the
        default state dir (`~/.local/state/orchestrate`) never writes into the real one (yaml-eec D27); the real uv
        cache, so a placed oracle's `uv run` does not rebuild from nothing."""
        return dict(
            os.environ,
            HOME=str(self.root / "home"),
            UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR") or UV_CACHE,
            PATH=str(self.home) + os.pathsep + os.environ["PATH"],
            PYTHONPATH=str(TESTS) + os.pathsep + os.environ.get("PYTHONPATH", ""),
            FAKE_RUNTIME_HOME=str(self.home),
            FAKE_PLAN=str(self.state_dir / "demo.json"),
            FAKE_CHECKOUT=str(self.root / "repo.worktrees" / "demo-t1"),
            FAKE_EVENTS=str(self.run_dir_of() / "events.jsonl"),
        )

    def runtime_process(self, argv, timeout=None):
        """The fake runtime's answer, in process: a driver run starts no process per call."""
        return fake_runtime.process(
            argv[1:], timeout=timeout
        )  # the fake reads the argv without the program name

    def gh_process(self, argv, timeout=None, cwd=None):
        """The fake `gh`'s answer, in process."""
        return fake_gh.process(argv, timeout=timeout)  # the fake has no working directory

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
                SystemExit
            ) as exit:  # argparse's refusal: the exit code a process would have returned
                codes.append(
                    exit.code if isinstance(exit.code, int) else int(exit.code is not None)
                )
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
        """A subcommand of the script, as the Lead would run it, in this process; the caller checks the exit code.

        A test of the process boundary itself starts `DRIVER` on its own."""
        return self.in_process(load_script(), list(args), timeout=timeout)

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
        path = self.run_dir_of() / "wakes.jsonl"
        rows = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        return rows

    def wake_lines(self, kind=None):
        return [w["text"] for w in self.wakes() if kind in (None, w["kind"])]

    # A YAML plan's ledger and run directory sit under `<state>/<repo-key>/`. Tests written before the key look for
    # `<state>/demo` and `<state>/demo.json`, so the key directory is by default an alias of the state directory
    # itself: both spellings name the same files. A test of the layout turns the alias off.
    alias_repo_key = True

    def alias_state(self, plan):
        if not self.alias_repo_key or not str(plan).endswith(".plan.yaml"):
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        alias = self.state_dir / load_script().repo_key(self.repo)
        if not alias.is_symlink():
            alias.symlink_to(".")

    def approved_yaml_plan(
        self, rows=("T1",), repo=None, name="demo.plan.yaml", mutate=None, state_dir=None
    ):
        """The fixture rows as an approved plan/1 YAML file in `repo`; its path. `mutate` edits the mapping first."""
        repo = repo or self.repo
        path = repo / "docs" / "plans" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = plan_data(rows)
        if mutate:
            mutate(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        out = io.StringIO()
        spec = load_script()
        with (
            mock.patch.object(spec, "resolve_runtime", return_value={"role": "lead"}),
            mock.patch.dict(
                os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead"}
            ),
            redirect_stdout(out),
            redirect_stderr(out),
        ):
            code = spec.run_cli(
                [
                    "plan",
                    "approve",
                    str(path),
                    "--repo",
                    str(repo),
                    "--state-dir",
                    str(state_dir or self.state_dir),
                ]
            )
        self.assertEqual(code, 0, out.getvalue())
        return path

    def run_driver(self, *extra, plan=None, expect=0):
        """The driver run in this process: the fakes answer in process, so the run starts no process of its own."""
        spec = load_script()
        self.alias_state(plan or self.plan)
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

    def run_dir_of(self, slug="demo"):
        """The run directory the driver names: under the repository's key in the state directory."""
        return self.state_dir / load_script().repo_key(self.repo) / slug

    def seed_status(self, **statuses):
        """A ledger that already says `T1="IN PROGRESS"`, as an earlier run left it."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "demo.json").write_text(
            json.dumps({"version": 1, "tasks": {}, "ticket_status": statuses}), encoding="utf-8"
        )

    def reset_status(self, old, new):
        """What a person does by hand: every ticket Status that starts with `old` becomes `new`."""
        for ticket, status in dict(self.ledger().get("ticket_status") or {}).items():
            if status.startswith(old):
                self.set_ticket_status(ticket, new)

    def lead_sets(self, **statuses):
        """A wait step during which the Lead writes ticket Statuses into the ledger: `T1="TO-DO"`."""
        script = (
            "import json,sys\n"
            "p=sys.argv[1]; d=json.load(open(p)); d.setdefault('ticket_status',{}).update(json.loads(sys.argv[2]))\n"
            "open(p,'w').write(json.dumps(d))"
        )
        return wait_result(
            run=[
                [
                    sys.executable,
                    "-c",
                    script,
                    str(self.state_dir / "demo.json"),
                    json.dumps(statuses),
                ]
            ],
            write=[[str(self.home / "refuse.json"), "[]"]],
        )

    def ticket_status(self, ticket="T1"):
        """A ticket's Status as the ledger holds it: the ledger owns status, the plan file never carries it."""
        return (self.ledger().get("ticket_status") or {}).get(ticket, "TO-DO")

    def set_ticket_status(self, ticket, status):
        """What a person does by hand between two runs: write a ticket's Status into the ledger."""
        path = self.state_dir / "demo.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("ticket_status", {})[ticket] = status
        path.write_text(json.dumps(data), encoding="utf-8")

    def events(self, *kinds):
        """The run's events.jsonl as dicts, optionally only the given kinds."""
        path = self.run_dir_of() / "events.jsonl"
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
