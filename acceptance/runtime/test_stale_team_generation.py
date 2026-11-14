"""Outcome oracle for the stale-team-generation plan (issue #22).

A lead session owns an hc terminal. Any other CLI session that inherits that terminal's id - the `claude -p`
a lead runs from its own shell, a script, a test - is a new generation, and it must never take the terminal
away from the live lead. When such a session's process is gone and left nothing behind, the live lead gets its
team back.

The seams are the real ones: the MCP server over stdio under two fake CLI processes (the lead's and the
other session's), the dispatch store the runtime writes, and the lead-liveness hooks as subprocesses. No real
CLI, terminal or provider is reached. The `control` tests hold today and must keep holding: they prove each
fixture is real, so a failing case fails because of the defect and not because of the harness.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import pytest
from test_mcp_server import RUNTIME_SRC, FakeCli
from test_tools import ToolCase, raw

sys.path.insert(0, str(RUNTIME_SRC))
from agent_runtime import agent_runtime as lib  # noqa: E402
from agent_runtime import dispatch_store as dispatch  # noqa: E402

pytestmark = pytest.mark.integration

PLUGIN = Path(RUNTIME_SRC).parents[2]
CONTEXT_HOOK = PLUGIN / "hooks" / "lead_context.py"
STOP_HOOK = PLUGIN / "hooks" / "stop_guard.py"

SCOPE = "stale-team"
LEAD = "t0"
PROMPT = {"hook_event_name": "UserPromptSubmit", "prompt": "hi"}
STOP = {"hook_event_name": "Stop", "stop_hook_active": False}
# The terminal of the lead's worker, as the fake hc reports it.
WORKER = {"address": "t9", "name": "w1", "parent": LEAD, "status": "idle"}
SECOND = {"address": "t10", "name": "w2", "parent": LEAD, "status": "idle"}


class OwnershipOutcome(ToolCase):
    """The lead's terminal, the other session that inherits it, and the team each one owns."""

    def setUp(self):
        super().setUp()
        self.clis = []

    def replies(self):
        return {
            "get:" + LEAD: {"out": raw("lead", None)},
            "get:w1": {"out": json.dumps(WORKER)},
            "get:t9": {"out": json.dumps(WORKER)},
            "get:w2": {"out": json.dumps(SECOND)},
            "get:t10": {"out": json.dumps(SECOND)},
        }

    def env(self):
        return {
            "HC_TERMINAL_ID": LEAD,
            "HC_SCOPE": SCOPE,
            "AGENT_RUNTIME_DISPATCH": "1",
        }

    def cli(self):
        """A fake CLI process: a session of its own, with a generation of its own."""
        fake = FakeCli()
        self.addCleanup(fake.close)
        self.clis.append(fake)
        return fake

    def lead(self):
        """The lead's server, under a fake CLI process of its own."""
        self.lead_cli = self.cli()
        return self.start(self.replies(), self.env(), new_cli=self.lead_cli)

    def other(self):
        """A second session in the same terminal, under a fake CLI process of its own."""
        return self.start(self.replies(), self.env(), new_cli=self.cli())

    def store(self, home=None):
        return dispatch.Store({"HOME": str(home or (self.dir / "home"))})

    def teams(self):
        with self.store().transaction() as db:
            return [
                dict(row)
                for row in db.execute("SELECT id,scope,lead,generation FROM teams ORDER BY rowid")
            ]

    def owner(self):
        """The team the lead owns: the first one registered for its terminal."""
        teams = self.teams()
        self.assertTrue(teams, "the lead registered no team")
        return teams[0]

    def stray(self, generation, worker=False, home=None):
        """A newer team row for the lead's terminal, inserted as the store would hold it.

        The row is written with SQL, not through the store: a store that refuses a generation it does not own
        is the behaviour under test, and the fixture must be able to hold the state that behaviour reacts to.
        """
        team = str(uuid.uuid4())
        with self.store(home).transaction() as db:
            db.execute("INSERT INTO teams VALUES (?,?,?,?)", (team, SCOPE, LEAD, generation))
            if worker:
                db.execute(
                    "INSERT INTO workers VALUES (?,?,?,?,?)",
                    (str(uuid.uuid4()), team, "w9", "t99", 1),
                )
        return team

    def messages(self):
        with self.store().transaction() as db:
            return [dict(row) for row in db.execute("SELECT id,team,recipient FROM messages")]

    def spawn(self, session, name="w1"):
        return self.call(
            session, "spawn", {"kind": "worker", "name": name, "task": "work", "cwd": "/tmp"}
        )

    def live_generation(self):
        """The generation of a process that is running: this test's own."""
        return "%d:%s" % (os.getpid(), lib.lstart(os.getpid()))

    def dead_generation(self):
        """The generation of a process that has died: its pid and the stamp it carried while it ran."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.05)"])
        stamp = lib.lstart(proc.pid)
        proc.wait()
        self.assertIsNotNone(stamp, "harness: the fixture's process had no start stamp")
        self.assertIsNone(lib.lstart(proc.pid), "harness: the fixture's process is still running")
        return "%d:%s" % (proc.pid, stamp)

    def test_S1_the_other_session_is_refused_and_registers_no_team(self):
        """stale-team-generation/S1: a session that inherits the terminal never owns it."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        owner = self.owner()["generation"]
        other = self.other()
        result, text = self.call(other, "wait", {"target": "all", "timeout": 3})
        self.assertTrue(result["isError"], "the other session's call was served: %s" % text)
        self.assertEqual(
            len(self.teams()), 1, "the other session registered a team: %s" % self.teams()
        )
        self.assertIn(LEAD, text, "the refusal does not name the terminal: %s" % text)
        self.assertIn(owner, text, "the refusal does not name the owning generation: %s" % text)
        self.assertRegex(
            text,
            r"(?i)hc terminal|own terminal|new session",
            "the refusal does not name the way out: %s" % text,
        )

    def test_S2_the_live_lead_keeps_its_team_after_the_other_session_called(self):
        """stale-team-generation/S2: the lead reaches its own workers after the other session called."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        other = self.other()
        self.call(other, "wait", {"target": "all", "timeout": 3})
        result, text = self.call(lead, "message", {"to": "w1", "text": "still mine"})
        self.assertFalse(result["isError"], "the live lead was cut off from its team: %s" % text)
        self.assertEqual(len(self.messages()), 1, "the lead's message never reached its worker")
        self.assertEqual(len(self.teams()), 1, "the terminal carries more than one team")

    def test_S3_a_dead_and_empty_newer_team_no_longer_fences_the_lead(self):
        """stale-team-generation/S3: the lead takes its terminal back from a session that is gone."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        self.stray(self.dead_generation())
        result, text = self.call(lead, "message", {"to": "w1", "text": "still mine"})
        self.assertFalse(result["isError"], "the lead was fenced by a dead, empty team: %s" % text)
        self.assertEqual(len(self.messages()), 1, "the lead's message never reached its worker")

    def test_S4_a_newer_team_that_is_still_running_fences_the_lead(self):
        """stale-team-generation/S4: a live session keeps the terminal until it is gone."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        owner = self.live_generation()
        self.stray(owner)
        result, text = self.call(lead, "message", {"to": "w1", "text": "mine"})
        self.assertTrue(result["isError"], "a live newer team did not fence the lead: %s" % text)
        self.assertIn("stale team generation", text)
        self.assertIn(owner, text, "the fence does not name the owning generation: %s" % text)
        self.assertIn(LEAD, text, "the fence does not name the terminal: %s" % text)
        self.assertEqual(len(self.messages()), 0, "the fenced lead reached its worker")

    def test_S5_a_newer_team_that_holds_work_fences_the_lead(self):
        """stale-team-generation/S5: a team that still holds work keeps the terminal."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        owner = self.dead_generation()
        self.stray(owner, worker=True)
        result, text = self.call(lead, "message", {"to": "w1", "text": "mine"})
        self.assertTrue(
            result["isError"], "an occupied newer team did not fence the lead: %s" % text
        )
        self.assertIn("stale team generation", text)
        self.assertIn(owner, text, "the fence does not name the owning generation: %s" % text)
        self.assertEqual(len(self.messages()), 0, "the fenced lead reached its worker")

    def test_S6_the_refusal_and_the_fence_name_the_owner_and_the_way_out(self):
        """stale-team-generation/S6: a refusal names the reason and the way out."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        owner = self.owner()["generation"]
        other = self.other()
        result, text = self.call(other, "wait", {"target": "all", "timeout": 3})
        self.assertTrue(result["isError"], "the other session's call was served: %s" % text)
        self.assertIn(LEAD, text, "the refusal does not name the terminal: %s" % text)
        self.assertIn(owner, text, "the refusal does not name the owning generation: %s" % text)
        self.assertRegex(
            text,
            r"(?i)hc terminal|own terminal|new session",
            "the refusal does not name the way out: %s" % text,
        )
        newer = self.live_generation()
        self.stray(newer)
        fenced, text = self.call(lead, "message", {"to": "w1", "text": "mine"})
        self.assertTrue(fenced["isError"], "a live newer team did not fence the lead: %s" % text)
        self.assertIn("stale team generation", text)
        self.assertIn(LEAD, text, "the fence does not name the terminal: %s" % text)
        self.assertIn(newer, text, "the fence does not name the owning generation: %s" % text)

    def test_S7_the_lead_liveness_hooks_find_the_lead_behind_a_dead_empty_team(self):
        """stale-team-generation/S7: the hooks read the same rule as the server."""
        line = self.context_line(stray=self.dead_generation())
        self.assertIsNotNone(line, "the hook printed nothing for a lead behind a dead, empty team")
        self.assertIn("workers need you", line)
        block = self.stop_block(stray=self.dead_generation()) or {}
        self.assertEqual(
            block.get("decision"),
            "block",
            "the Stop guard let the lead stop behind a dead, empty team",
        )
        occupied = self.live_generation()
        self.assertNotIn(
            "workers need you",
            self.context_line(stray=occupied, worker=True) or "",
            "the hook followed a newer team that holds work away from the lead",
        )

    def test_S8_a_spawn_binds_to_the_leads_own_team_behind_a_dead_empty_team(self):
        """stale-team-generation/S8: the bind and the authorization agree on who owns the terminal."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        self.stray(self.dead_generation())
        again, text = self.spawn(lead, "w2")
        self.assertFalse(
            again["isError"], "the spawn was refused behind a dead, empty team: %s" % text
        )
        with self.store().transaction() as db:
            teams = {row[0] for row in db.execute("SELECT DISTINCT team FROM workers")}
        self.assertEqual(len(teams), 1, "the worker was bound to another team: %s" % teams)

    def test_S9_a_refused_session_gets_the_diagnostic_view_with_a_delivery_error(self):
        """stale-team-generation/S9: a diagnostic call never fails the session that asks."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        owner = self.owner()["generation"]
        other = self.other()
        for verb, arguments in (
            ("status", {"name": "w1"}),
            ("collect", {"name": "w1"}),
            ("list", {}),
        ):
            result, text = self.call(other, verb, arguments)
            self.assertFalse(result["isError"], "the %s call failed: %s" % (verb, text))
            payload = result.get("structuredContent") or {}
            self.assertIn(
                "delivery_error", payload, "the %s view has no delivery_error: %s" % (verb, text)
            )
            self.assertIn(LEAD, payload["delivery_error"], verb)
            self.assertIn(owner, payload["delivery_error"], verb)
        self.assertEqual(len(self.messages()), 0, "the refused session reached the lead's worker")

    def test_control_the_lead_owns_its_terminal_and_reaches_its_worker(self):
        """The fixture is real: the lead spawns a worker and reaches it."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        result, text = self.call(lead, "message", {"to": "w1", "text": "hello"})
        self.assertFalse(result["isError"], "the lead could not message its worker: %s" % text)
        self.assertEqual(len(self.messages()), 1)
        self.assertEqual(len(self.teams()), 1, "the lead registered more than one team")

    def test_control_the_two_sessions_are_two_generations_of_one_terminal(self):
        """The fixture is real: each server takes its generation from its own CLI process."""
        lead = self.lead()
        spawned, text = self.spawn(lead)
        self.assertFalse(spawned["isError"], "setup: the lead could not spawn: %s" % text)
        other = self.other()
        self.assertNotEqual(self.lead_cli.proc.pid, self.clis[1].proc.pid)
        self.assertEqual(
            self.owner()["generation"],
            "%d:%s" % (self.lead_cli.proc.pid, lib.lstart(self.lead_cli.proc.pid)),
            "the lead's team does not carry its own CLI process's generation",
        )
        self.assertIsNotNone(other)

    # -- the lead-liveness hooks -----------------------------------------------------------------------------------

    def hook(self, script, payload, stray=None, worker=False, attention=None):
        """Run a lead-liveness hook under a fake CLI process, over a store that holds one lead.

        The hook runs as a subprocess, so its generation is that process's pid:lstart; the store it reads is
        its own, so the fixture holds exactly the lead and the row under test. `attention` seeds the last-wait
        row the reminder reads; without it the lead's work is unseen, which is what the Stop guard blocks on.
        """
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        proc = subprocess.Popen(
            [
                "codex",
                "-c",
                '"$@"; exit $?',
                "codex",
                sys.executable,
                "-B",
                str(script),
            ],
            executable="/bin/bash",
            env={
                "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
                "HOME": home.name,
                "HC_TERMINAL_ID": LEAD,
                "HC_SCOPE": SCOPE,
            },
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(proc.kill)
        generation = "%d:%s" % (proc.pid, lib.lstart(proc.pid))
        store = self.store(home.name)
        team = store.team(SCOPE, LEAD, generation)
        identity = store.register(team, "w1")
        with store.transaction() as db:
            db.execute(
                "UPDATE assignments SET state=?,current=1 WHERE id=?",
                ("working", identity["assignment_id"]),
            )
            if attention is not None:
                db.execute(
                    "INSERT INTO last_wait(team,reason,attention,fingerprint) VALUES (?,?,?,?)",
                    (team, "attention", attention, dispatch.wait_fingerprint(db, team)),
                )
        if stray:
            self.stray(stray, worker=worker, home=home.name)
        out, err = proc.communicate(json.dumps(payload), timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        return out

    def context_line(self, **kwargs):
        """The context line the lead's reminder hook prints, or None."""
        kwargs.setdefault("attention", 2)
        out = self.hook(CONTEXT_HOOK, PROMPT, **kwargs)
        if not out.strip():
            return None
        return json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def stop_block(self, **kwargs):
        """The Stop guard's verdict, or None when it lets the turn end."""
        out = self.hook(STOP_HOOK, STOP, **kwargs)
        if not out.strip():
            return None
        return json.loads(out)


if __name__ == "__main__":
    unittest.main()
