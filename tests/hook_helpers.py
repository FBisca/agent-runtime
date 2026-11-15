"""What the hook tests share: the payload helpers and the store fixture the hook classes inherit.

The helpers and the fixture are used by more than one of the hook modules, so they live here; the
modules themselves carry the behaviour each one tests.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
from agent_runtime import (  # noqa: E402
    agent_runtime as lib,
)
from agent_runtime import (  # noqa: E402
    dispatch_store,
)
from hook_harness import (  # noqa: E402
    CONTEXT,
    run_context_raw,
)


def bash(command, **extra):
    return {"tool_name": "Bash", "tool_input": {"command": command}, **extra}


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


class HookStoreCase(unittest.TestCase):
    """Hooks run under a fake `codex` process (so a CLI ancestor exists) against a store seeded in a temp
    XDG_STATE_HOME; the seed callback receives the generation the hook will compute."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.state),
            "HC_SCOPE": "scope",
            "HC_TERMINAL_ID": "t0",
        }

    def hook(self, payload, seed=None, env=None, script=CONTEXT, raw=None):
        proc = subprocess.Popen(
            ["codex", "-c", '"$@"; exit $?', "codex", sys.executable, "-B", str(script)],
            executable="/bin/bash",
            env=env or self.env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if seed:
            seed("%d:%s" % (proc.pid, lib.lstart(proc.pid)))
        out, err = proc.communicate(raw if raw is not None else json.dumps(payload), timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        return json.loads(out) if out.strip() else None

    def in_process(self, payload, seed=None, env=None, raw=None):
        """The context hook's `main` in process under its own pinned generation: what it printed, or None.

        A table of payload shapes would otherwise pay a fake CLI process, a python start and a
        process-table read per row; the tests that prove that stdin/stdout/exit boundary still start one.
        Each call takes a fresh generation, as a fresh fake CLI process would, so a row's seeding lands in
        a team of its own."""
        self.hook_runs = getattr(self, "hook_runs", 0) + 1
        generation = "in-process:%d" % self.hook_runs
        if seed:
            seed(generation)
        out, _ = run_context_raw(payload, env or self.env, generation, raw=raw)
        return out

    def team(self, generation, lead="t0", scope="scope"):
        store = dispatch_store.Store(self.env)
        return store, store.team(scope, lead, generation)

    def stray_team(self, generation, lead="t0", scope="scope"):
        """A newer team row for the terminal, written with SQL.

        The store refuses a generation it does not own, so a fixture that needs the state that
        refusal is about writes the row itself."""
        store = dispatch_store.Store(self.env)
        team = "stray-%s" % generation[:12]
        with store.transaction() as db:
            db.execute("INSERT INTO teams VALUES (?,?,?,?)", (team, scope, lead, generation))
        return store, team

    def other_live_generation(self):
        """A generation of another process that runs right now: the test runner's own."""
        return "%d:%s" % (os.getpid(), lib.lstart(os.getpid()))

    def sql(self, store, statement, *params):
        with store.transaction() as db:
            db.execute(statement, params)

    def worker(self, store, team, name="w1", state="working"):
        ident = store.register(team, name)
        self.sql(
            store,
            "UPDATE assignments SET state=?,current=1 WHERE id=?",
            state,
            ident["assignment_id"],
        )
        return ident

    def message(self, store, team, lead="t0", assignment=None, message_id="m1"):
        self.sql(
            store,
            "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
            message_id,
            team,
            lead,
            assignment,
            "{}",
        )

    def last_wait(self, store, team, reason, attention):
        with store.transaction() as db:
            db.execute(
                "INSERT INTO last_wait(team,reason,attention,fingerprint) VALUES (?,?,?,?)",
                (team, reason, attention, dispatch_store.wait_fingerprint(db, team)),
            )

    @staticmethod
    def line(out):
        return out["hookSpecificOutput"]["additionalContext"]

    PROMPT = {"hook_event_name": "UserPromptSubmit", "prompt": "hi"}
