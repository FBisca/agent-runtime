"""Pi as a lead: identity and generation, the guarded MCP delegation path and its failure paths.

The Pi host is the process that starts the MCP server, so the tests drive a server under a fake `pi`
process (the same trick the Claude/Codex tests use) and against the real dispatch store with the fake
terminal transport. No Pi is started for real and no model is called.
"""

import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from test_mcp_server import FakeCli, Session
from test_tools import ToolCase, raw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
import pytest
from agent_runtime import agent_runtime as lib  # noqa: E402
from agent_runtime import (
    hc_tools,  # noqa: E402
    host_policy,  # noqa: E402
)
from agent_runtime.dispatch_store import Store  # noqa: E402

pytestmark = pytest.mark.integration


GENERATION = re.compile(r"^\d+:")
RULES = {"mutations": True, "backends": ["claude", "codex"], "paths": ["/tmp"]}


class PiIdentityTests(unittest.TestCase):
    """T1: Pi is a harness of its own, never a silent Claude."""

    def test_pi_argv_is_a_harness_and_other_programs_are_not(self):
        """agent-runtime/P2-S1"""
        self.assertEqual(lib.cli_of_args("pi "), "pi")
        self.assertEqual(lib.cli_of_args("pi --version"), "pi")
        self.assertEqual(lib.cli_of_args("node /x/node_modules/.bin/pi --version"), "pi")
        self.assertEqual(
            lib.cli_of_args("node /x/@earendil-works/pi-coding-agent/dist/index.js"), "pi"
        )
        for other in ("pip install", "/usr/bin/pip3", "pixel --version", "node /x/pi.js"):
            self.assertIsNone(lib.cli_of_args(other), other)

    def test_pi_marker_never_resolves_to_claude(self):
        """agent-runtime/P2-S1: the env marker alone decides, so no real process ancestry is read (a test run
        from inside a Claude session would otherwise find that session above it)"""
        with mock.patch.object(lib, "ancestor_cli", return_value=None):
            self.assertEqual(lib.detect_cli({"PI_CODING_AGENT": "true"}), "pi")
            info = lib.resolve(
                {"PI_CODING_AGENT": "true", "HC_TERMINAL_ID": "t0"},
                None,
                lambda t: {"parent": None},
            )
        self.assertEqual((info["cli"], info["role"]), ("pi", "lead"))

    def test_pi_generation_is_the_pi_process_pid_and_start_time(self):
        """agent-runtime/P2-S1"""
        procs = {100: (90, "/bin/zsh -l"), 90: (80, "pi "), 80: (1, "launchd")}
        with (
            mock.patch.object(lib, "ps_entry", side_effect=procs.get),
            mock.patch.object(lib, "lstart", side_effect={90: "Fri Nov  7 09:00:00 2026"}.get),
            mock.patch.object(lib.os, "getppid", return_value=100),
        ):
            self.assertEqual(lib.cli_generation(), "90:Fri Nov  7 09:00:00 2026")
            self.assertIsNone(lib.parent_cli())  # the direct parent is a shell, not the harness

    def test_claude_and_codex_keep_their_own_identity(self):
        """agent-runtime/P2-S5"""
        procs = {100: (90, "/bin/zsh"), 90: (80, "codex exec"), 80: (1, "launchd")}
        with (
            mock.patch.object(lib, "ps_entry", side_effect=procs.get),
            mock.patch.object(lib, "lstart", side_effect={90: "Fri Nov  7 09:00:00 2026"}.get),
            mock.patch.object(lib.os, "getppid", return_value=100),
        ):
            self.assertEqual(lib.cli_generation(), "90:Fri Nov  7 09:00:00 2026")
        with mock.patch.object(lib, "ps_entry", return_value=None):
            self.assertEqual(lib.detect_cli({"CODEX_THREAD_ID": "x"}), "codex")
            self.assertEqual(lib.detect_cli({"CLAUDECODE": "1"}), "claude")
            self.assertEqual(lib.detect_cli({"PI_CODING_AGENT": "true"}), "pi")
            self.assertIsNone(lib.detect_cli({}))

    def test_a_pi_session_without_a_pi_process_is_refused(self):
        """agent-runtime/P2-S4: an environment variable alone is not a session."""
        with mock.patch.object(lib, "cli_generation", return_value=None):
            with self.assertRaises(hc_tools.ToolError) as error:
                hc_tools.dispatch_identity({"PI_CODING_AGENT": "true", "HC_SCOPE": "pi"})
        self.assertIn("cannot establish a Pi lead session", str(error.exception))

    def test_an_ambiguous_pi_session_is_refused(self):
        """agent-runtime/P2-S4: two hosts claiming one session are ambiguous, not Claude."""
        with mock.patch.object(lib, "cli_generation", return_value=None):
            with self.assertRaises(hc_tools.ToolError) as error:
                hc_tools.dispatch_identity(
                    {"PI_CODING_AGENT": "true", "CLAUDECODE": "1", "HC_SCOPE": "pi"}
                )
        self.assertIn("ambiguous", str(error.exception))

    def test_claude_and_codex_keep_their_session_id_fallback(self):
        """agent-runtime/P2-S5: the Claude/Codex identity behaviour is unchanged.

        PR #45 renames the Claude session variable; the fallback stays a session id either way."""
        with mock.patch.object(lib, "cli_generation", return_value=None):
            self.assertEqual(
                hc_tools.dispatch_identity({"CODEX_THREAD_ID": "thread", "HC_SCOPE": "s"}),
                ("s", "thread"),
            )
            found = []
            for name in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID"):
                try:
                    found.append(hc_tools.dispatch_identity({name: "session", "HC_SCOPE": "s"}))
                except hc_tools.ToolError:
                    pass
            self.assertTrue(found, "the Claude session id fallback is gone")
            self.assertEqual(found[0], ("s", "session"))


class PiPolicyTests(unittest.TestCase):
    """T2: the server, not the client, decides what a Pi lead may do."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def env(self, **overrides):
        env = {"HOME": self.temp.name, "XDG_CONFIG_HOME": str(Path(self.temp.name) / ".config")}
        env.update(overrides)
        return env

    def write(self, env, data):
        path = Path(env["XDG_CONFIG_HOME"]) / "agent-runtime" / "hosts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_mutations_are_denied_without_a_policy(self):
        """agent-runtime/P2-S4"""
        env = self.env()
        with mock.patch.object(lib, "parent_cli", return_value="pi"):
            host = host_policy.host_of(env)
            self.assertEqual(host, "pi")
            with self.assertRaises(host_policy.Refused):
                host_policy.authorize(
                    env,
                    host,
                    "lead",
                    "spawn",
                    {"kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp", "cli": "claude"},
                    None,
                )

    def test_policy_allows_the_named_backend_and_nothing_else(self):
        """agent-runtime/P2-S4"""
        env = self.env()
        self.write(env, {"hosts": {"pi": RULES}})
        with mock.patch.object(lib, "parent_cli", return_value="pi"):
            host = host_policy.host_of(env)
            arguments = {"kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp"}
            with self.assertRaises(host_policy.Refused):
                host_policy.authorize(env, host, "lead", "spawn", dict(arguments), None)
            with self.assertRaises(host_policy.Refused):
                host_policy.authorize(
                    env, host, "lead", "spawn", dict(arguments, cli="other-cli"), None
                )
            host_policy.authorize(env, host, "lead", "spawn", dict(arguments, cli="claude"), None)

    def test_policy_limits_the_directory_a_worker_may_run_in(self):
        """agent-runtime/P2-S4"""
        env = self.env()
        self.write(env, {"hosts": {"pi": dict(RULES, backends=["claude", "codex", "pi"])}})
        with mock.patch.object(lib, "parent_cli", return_value="pi"):
            host = host_policy.host_of(env)
            for cli in ("claude", "pi"):  # the guarantee holds for a Pi spawn too (pi-worker/S7)
                arguments = {"kind": "worker", "name": "w1", "task": "t", "cli": cli}
                with (
                    mock.patch.object(lib, "run_command") as ran,
                    self.assertRaisesRegex(host_policy.Refused, "may not start a worker in"),
                ):
                    host_policy.authorize(
                        env, host, "lead", "spawn", dict(arguments, cwd="/etc"), None
                    )
                ran.assert_not_called()
                host_policy.authorize(env, host, "lead", "spawn", dict(arguments, cwd="/tmp"), None)

    def test_a_malformed_policy_refuses_instead_of_trusting(self):
        """agent-runtime/P2-S4"""
        env = self.env()
        self.write(env, {"hosts": {"pi": {"mutations": "yes"}}})
        with mock.patch.object(lib, "parent_cli", return_value="pi"):
            host = host_policy.host_of(env)
            with self.assertRaises(host_policy.Refused):
                host_policy.authorize(env, host, "lead", "spawn", {"cli": "claude"}, None)

    def test_pi_is_a_lead_only(self):
        """agent-runtime/P2-S1"""
        env = self.env()
        self.write(env, {"hosts": {"pi": RULES}})
        with mock.patch.object(lib, "parent_cli", return_value="pi"):
            host = host_policy.host_of(env)
            with self.assertRaises(host_policy.Refused):
                host_policy.authorize(
                    env,
                    host,
                    "worker",
                    "spawn",
                    {"kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp", "cli": "claude"},
                    None,
                )

    def test_claude_and_codex_hosts_keep_their_unrestricted_behaviour(self):
        """agent-runtime/P2-S5"""
        for host in ("claude", "codex"):
            with mock.patch.object(lib, "parent_cli", return_value=host):
                self.assertEqual(host_policy.host_of(self.env()), host)
                host_policy.authorize(
                    self.env(),
                    host,
                    "lead",
                    "spawn",
                    {"kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp"},
                    None,
                )


class PiLeadTests(ToolCase):
    """T2: a Pi lead delegates to Claude and Codex workers through the existing dispatch store."""

    def setUp(self):
        super().setUp()
        self.pi = FakeCli("pi")
        self.addCleanup(self.pi.close)
        self.config = self.dir / "config"
        (self.config / "agent-runtime").mkdir(parents=True)

    def policy(self, **rules):
        (self.config / "agent-runtime" / "hosts.json").write_text(
            json.dumps({"hosts": {"pi": rules}}), encoding="utf-8"
        )

    def pi_env(self, **extra):
        env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "HC_SCOPE": "pi",
            "AGENT_RUNTIME_DISPATCH": "1",
            "XDG_CONFIG_HOME": str(self.config),
            "PI_CODING_AGENT": "true",
            "CODEX_THREAD_ID": "",
        }
        env.update(extra)
        return env

    def pi_session(self, replies=None, env=None, cli=None):
        return self.start(replies, self.pi_env(**(env or {})), new_cli=cli or self.pi)

    def teams(self):
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            return [tuple(row) for row in db.execute("SELECT scope, lead, generation FROM teams")]

    def worker_session(self, address="t1"):
        env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": address,
            "CODEX_THREAD_ID": "worker-" + address,
            "HC_SCOPE": "pi",
            "AGENT_RUNTIME_DISPATCH": "1",
        }
        session = Session(env, new_cli=True)
        self.addCleanup(session.close)
        session.handshake()
        return session

    def lead_replies(self, workers=("w1",)):
        rows = [
            {"name": name, "address": "t%d" % (index + 1), "parent": "t0", "status": "working"}
            for index, name in enumerate(workers)
        ]
        replies = {
            "get:t0": {"out": raw("lead", None)},
            "ls": {"out": json.dumps({"terminals": rows})},
            "live": [],
        }
        for row in rows:
            for key in (row["name"], row["address"]):
                replies["get:" + key] = {"out": json.dumps(row)}
        return replies

    # --- S1: identity and generation -------------------------------------------------------

    def test_S1_pi_lead_is_identified_as_pi_with_a_stable_generation(self):
        """agent-runtime/P2-S1: the lead is Pi, not Claude, and a reconnect keeps the same team."""
        session = self.pi_session(self.lead_replies())
        result, text = self.call(session, "list", {})
        self.assertFalse(result["isError"], text)
        teams = self.teams()
        self.assertEqual(len(teams), 1)
        scope, lead, generation = teams[0]
        self.assertEqual((scope, lead), ("pi", "t0"))
        self.assertRegex(generation, GENERATION)  # the Pi process, not a session id
        again = self.pi_session(self.lead_replies())  # the MCP server restarted, Pi did not
        result, text = self.call(again, "list", {})
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.teams(), teams)

    def test_S1_a_new_pi_process_is_a_new_generation_and_the_old_one_goes_stale(self):
        """agent-runtime/P2-S3: a restart never inherits another generation's assignments."""
        first = self.pi_session(self.lead_replies())
        self.call(first, "list", {})
        old = self.teams()[0]
        other = FakeCli("pi")
        self.addCleanup(other.close)
        second = self.pi_session(self.lead_replies(), cli=other)
        result, text = self.call(second, "list", {})
        self.assertFalse(result["isError"], text)
        teams = self.teams()
        self.assertEqual(len(teams), 2)
        self.assertNotEqual(teams[1][2], old[2])
        stale, text = self.call(first, "list", {})
        self.assertTrue(stale["isError"], text)
        self.assertIn("stale", text)

    # --- S2: delegation ----------------------------------------------------------------------

    def test_S2_pi_lead_delegates_to_a_claude_worker_and_collects_one_result(self):
        """agent-runtime/P2-S2: spawn, message, wait and collect over the durable store."""
        self.policy(**RULES)
        replies = self.lead_replies()
        replies["read:w1"] = {"out": "the worker's answer"}
        session = self.pi_session(replies)
        spawned, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "review this", "cwd": "/tmp"},
        )
        self.assertFalse(spawned["isError"], text)
        assignment = spawned["structuredContent"]["assignment_id"]
        self.assertEqual(spawned["structuredContent"]["name"], "w1")
        self.assertIn("claude", self.runs("run")[-1])
        worker = self.worker_session()
        sent, text = self.call(session, "message", {"to": "w1", "text": "also check the tests"})
        self.assertFalse(sent["isError"], text)
        reported, text = self.call(
            worker,
            "report",
            {"text": "STATUS: COMPLETE", "kind": "complete", "assignment_id": assignment},
        )
        self.assertFalse(reported["isError"], text)
        waited, text = self.call(session, "wait", {"target": "all", "timeout": 5})
        self.assertFalse(waited["isError"], text)
        batch = waited["structuredContent"]
        self.assertEqual([m["kind"] for m in batch["messages"]], ["complete"])
        self.assertEqual(batch["messages"][0]["text"], "STATUS: COMPLETE")
        self.assertEqual(batch["messages"][0]["assignment_id"], assignment)
        self.assertEqual(batch["messages"][0]["recipient"], "t0")
        collected, text = self.call(session, "collect", {"name": "w1"})
        self.assertFalse(collected["isError"], text)
        self.assertIn("the worker's answer", collected["content"][0]["text"])

    def test_S2_pi_lead_delegates_to_a_codex_worker(self):
        """agent-runtime/P2-S2: the same path with the other backend."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "codex", "name": "w1", "task": "review this", "cwd": "/tmp"},
        )
        self.assertFalse(spawned["isError"], text)
        self.assertIn("codex", self.runs("run")[-1])

    def test_S2_a_repeated_report_is_delivered_once(self):
        """agent-runtime/P2-S2: at-least-once delivery, one report."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, _ = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        assignment = spawned["structuredContent"]["assignment_id"]
        worker = self.worker_session()
        for _ in range(2):
            reported, text = self.call(
                worker,
                "report",
                {
                    "text": "done",
                    "kind": "complete",
                    "assignment_id": assignment,
                    "message_id": "retry-1",
                },
            )
            self.assertFalse(reported["isError"], text)
        waited, text = self.call(session, "wait", {"target": "all", "timeout": 5})
        self.assertFalse(waited["isError"], text)
        self.assertEqual(len(waited["structuredContent"]["messages"]), 1)

    def test_S2_a_duplicate_spawn_does_not_start_a_second_worker(self):
        """agent-runtime/P2-S2: one task, one worker, one report."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(spawned["isError"], text)
        again, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work again", "cwd": "/tmp"},
        )
        self.assertTrue(again["isError"], text)
        self.assertIn("name_taken", text)  # the live name was refused, nothing was started

    def test_S2_a_report_wakes_the_wait_while_another_worker_still_works(self):
        """agent-runtime/P2-S2 (#31): wait(all) returns the first undelivered report."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies(("w1", "w2")))
        fast, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "fast", "cwd": "/tmp"},
        )
        self.assertFalse(fast["isError"], text)
        slow, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w2", "task": "slow", "cwd": "/tmp"},
        )
        self.assertFalse(slow["isError"], text)
        worker = self.worker_session("t1")
        reported, text = self.call(
            worker,
            "report",
            {
                "text": "fast done",
                "kind": "complete",
                "assignment_id": fast["structuredContent"]["assignment_id"],
            },
        )
        self.assertFalse(reported["isError"], text)
        waited, text = self.call(session, "wait", {"target": "all", "timeout": 5})
        self.assertFalse(waited["isError"], text)
        batch = waited["structuredContent"]
        self.assertEqual([m["text"] for m in batch["messages"]], ["fast done"])
        self.assertEqual(batch["unfinished"], [slow["structuredContent"]["assignment_id"]])

    def test_S2_a_pi_lead_uses_the_one_team_ledger(self):
        """agent-runtime/P2-S2 (#34): the Pi lead shares the store's team, so its workers are counted
        once and the team limit is the same one every host sees."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(spawned["isError"], text)
        store = Store({"HOME": str(self.dir / "home")})
        with store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM teams").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM workers").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM assignments").fetchone()[0], 1)

    # --- S3: recovery and isolation ---------------------------------------------------------

    def test_S3_a_disconnected_pi_lead_reconnects_to_its_own_team(self):
        """agent-runtime/P2-S3: the MCP connection dropped after the spawn; the work is not repeated."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(spawned["isError"], text)
        assignment = spawned["structuredContent"]["assignment_id"]
        session.close()  # the MCP connection dropped
        worker = self.worker_session()
        reported, text = self.call(
            worker, "report", {"text": "done", "kind": "complete", "assignment_id": assignment}
        )
        self.assertFalse(reported["isError"], text)
        again = self.pi_session(self.lead_replies())
        waited, text = self.call(again, "wait", {"target": "all", "timeout": 5})
        self.assertFalse(waited["isError"], text)
        batch = waited["structuredContent"]
        self.assertEqual([m["assignment_id"] for m in batch["messages"]], [assignment])
        self.assertEqual(len(self.runs("run")), 1)  # the task was not dispatched twice

    def test_S3_a_pi_lead_cannot_reach_another_leads_worker(self):
        """agent-runtime/P2-S3: no cross-lead read, mutation or result collection."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        denied, text = self.call(session, "collect", {"name": "someone-else"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("not a worker of your team", text)
        denied, text = self.call(session, "stop", {"name": "someone-else"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("not a worker of your team", text)

    # --- S4: the mutation guard ---------------------------------------------------------------

    def test_S4_a_pi_lead_may_not_mutate_without_a_policy(self):
        """agent-runtime/P2-S4: mutations are denied by default."""
        session = self.pi_session(self.lead_replies())
        denied, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertTrue(denied["isError"], text)
        self.assertIn("not authorized to mutate", text)
        self.assertEqual(self.runs("run"), [])  # nothing was started

    def test_S4_a_pi_lead_may_not_choose_a_backend_the_operator_did_not_authorize(self):
        """agent-runtime/P2-S4: an explicit, authorized backend; never a silent provider choice."""
        self.policy(mutations=True, backends=["codex"], paths=["/tmp"])
        session = self.pi_session(self.lead_replies())
        denied, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertTrue(denied["isError"], text)
        self.assertIn("not authorized", text)
        self.assertEqual(self.runs("run"), [])

    def test_S4_a_forged_pi_session_is_refused(self):
        """agent-runtime/P2-S4: an environment variable alone is not a session (unit seam)."""
        with mock.patch.object(lib, "cli_generation", return_value=None):
            with self.assertRaises(hc_tools.ToolError) as error:
                hc_tools.dispatch_identity({"PI_CODING_AGENT": "true", "HC_SCOPE": "pi"})
        self.assertIn("cannot establish a Pi lead session", str(error.exception))

    def test_S4_an_ambiguous_pi_session_is_refused(self):
        """agent-runtime/P2-S4: two hosts claiming one session are ambiguous, not Claude."""
        with mock.patch.object(lib, "cli_generation", return_value=None):
            with self.assertRaises(hc_tools.ToolError) as error:
                hc_tools.dispatch_identity(
                    {"PI_CODING_AGENT": "true", "CLAUDECODE": "1", "HC_SCOPE": "pi"}
                )
        self.assertIn("ambiguous", str(error.exception))

    def test_S4_a_pi_lead_needs_the_durable_store(self):
        """agent-runtime/P2-S2: without dispatch there is no identity, attribution or scope."""
        env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
            "HC_SCOPE": "pi",
            "PI_CODING_AGENT": "true",
        }
        session = Session(env, new_cli=self.pi)
        self.addCleanup(session.close)
        session.handshake()
        denied, text = self.call(session, "status", {"name": "w1"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("AGENT_RUNTIME_DISPATCH=1", text)

    # --- cancellation --------------------------------------------------------------------------

    def test_a_cancelled_wait_leaves_the_worker_running(self):
        """agent-runtime/P2-S2: an interrupted wait never stops or duplicates the worker."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "cli": "claude", "name": "w1", "task": "work", "cwd": "/tmp"},
        )
        self.assertFalse(spawned["isError"], text)
        write = lambda m: (
            session.proc.stdin.write(json.dumps(m) + "\n"),
            session.proc.stdin.flush(),
        )
        write(
            {
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {"name": "wait", "arguments": {"target": "all", "timeout": 30}},
            }
        )
        write({"jsonrpc": "2.0", "id": 51, "method": "ping"})
        self.assertEqual(json.loads(session.proc.stdout.readline())["id"], 51)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 50}})
        started = time.monotonic()
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=10), 0)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(self.runs("kill"), [])  # the worker was not stopped
        self.assertEqual(len(self.runs("run")), 1)


if __name__ == "__main__":
    unittest.main()
