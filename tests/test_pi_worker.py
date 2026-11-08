"""Pi as a worker: the pi family spawns, a Pi worker reports inside its own team.

The Pi worker is a fake `pi` process under the fake hc: no Pi is started for real and no model is
called. The spawn argv, the provider environment and the host policy are the seams.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_mcp_server import FakeCli, Session
from test_tools import ToolCase, raw, unwrap

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
import pytest
from agent_runtime import agent_runtime as lib  # noqa: E402
from agent_runtime import (
    host_policy,  # noqa: E402
    profiles,  # noqa: E402
)

pytestmark = pytest.mark.integration



RULES = {"mutations": True, "backends": ["claude", "codex", "pi"], "paths": ["/tmp"]}
# The credentials a Pi worker needs and a Claude or Codex worker must never inherit.
PI_VARS = ["MINIMAX_API_KEY", "PI_API_KEY", "PI_PROVIDER", "PI_MODEL"]


class PiSpawnTests(ToolCase):
    """T1: the pi family is a family: it spawns with its own argv."""

    ARGS = {"kind": "worker", "name": "w1", "task": "do x", "cwd": "/tmp/c"}

    def spawn_argv(self, env=None, **extra):
        session = self.start({"get:w1": {"out": raw("w1", "t0")}}, env=env)
        result, text = self.call(session, "spawn", dict(self.ARGS, **extra))
        self.assertFalse(result["isError"], text)
        return unwrap(self.runs("run")[-1][10:])

    def test_S1_a_pi_spawn_builds_the_pi_argv_with_the_task_last(self):
        """pi-worker/S1: the pi family launches Pi itself, not a Claude or Codex shim."""
        argv = self.spawn_argv(cli="pi", model="minimax/MiniMax-M3.1-Flash")
        self.assertEqual(argv[:3], ["pi", "--model", "minimax/MiniMax-M3.1-Flash"])
        self.assertEqual(len(argv), 4)
        self.assertIn("do x", argv[-1])
        self.assertNotIn("claude", argv)
        self.assertNotIn("codex", argv)

    def test_S1_a_pi_spawn_without_a_model_is_interactive(self):
        """pi-worker/S1: Pi runs interactively with the task as its only argument."""
        argv = self.spawn_argv(cli="pi")
        self.assertEqual(argv[0], "pi")
        self.assertEqual(len(argv), 2)
        self.assertIn("do x", argv[-1])

    def test_S1_the_pi_task_carries_the_role_and_the_report_protocol(self):
        """pi-worker/S1: Pi has no role file, so the role and the protocol travel in the task."""
        task = self.spawn_argv(cli="pi", agent="implementer")[-1]
        self.assertIn("Role: implementer", task)
        self.assertIn("Worker protocol:", task)
        self.assertIn("mcp__agent-runtime__report", task)
        self.assertIn("do x", task)

    def test_S1_a_pi_spawn_with_no_role_states_the_protocol_without_a_role_line(self):
        """pi-worker/S1: agent=none means no role, but the protocol still reaches the worker."""
        task = self.spawn_argv(cli="pi")[-1]
        self.assertEqual(task, self.spawn_argv(cli="pi", agent="none")[-1])
        self.assertNotIn("Role:", task)
        self.assertIn("Worker protocol:", task)

    def test_S6_the_effort_maps_onto_pi_thinking(self):
        """pi-worker/S6: the runtime's five efforts are Pi's five shared levels."""
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                argv = self.spawn_argv(cli="pi", effort=effort)
                self.assertEqual(argv[:3], ["pi", "--thinking", effort])
                self.assertEqual(len(argv), 4)

    def test_S6_an_effort_pi_does_not_know_is_refused_before_anything_starts(self):
        """pi-worker/S6: the runtime never sends a level the CLI does not accept."""
        for effort in ("off", "minimal", "extreme", ""):
            with self.subTest(effort=effort):
                session = self.start({"get:w1": {"out": raw("w1", "t0")}})
                result, text = self.call(session, "spawn", dict(self.ARGS, cli="pi", effort=effort))
                self.assertTrue(result["isError"], text)
                self.assertEqual(self.runs("run"), [])

    def test_S1_the_pi_family_is_offered_in_the_spawn_schema(self):
        """pi-worker/S1: a Pi lead names the pi backend like any other."""
        session = self.start()
        tools = session.send("tools/list")["result"]["tools"]
        self.assertIn("pi", tools[0]["inputSchema"]["properties"]["cli"]["enum"])


class PiProviderTests(ToolCase):
    """T2: the provider environment follows the family."""

    ARGS = {"kind": "worker", "name": "w1", "task": "profile task", "cwd": "/tmp"}
    WATCHED = PI_VARS

    def env_printer(self, name):
        program = self.dir / "bin" / name
        program.write_text("#!%s\nimport json, os\nprint(json.dumps({k: os.environ[k] for k in %r if k in os.environ}))\n"
                           % (sys.executable, self.WATCHED))
        program.chmod(0o755)

    def overlays(self):
        return [json.loads(line) for line in (self.dir / "overlay").read_text().splitlines()]

    def test_S2_a_pi_spawn_keeps_the_minimax_and_pi_provider_variables(self):
        """pi-worker/S2: Pi authenticates with MINIMAX_API_KEY, so its worker keeps it."""
        self.env_printer("pi")
        inherited = {key: "inherited" for key in self.WATCHED}
        session = self.start({"execute_profile": True, "login_env": inherited,
                              "get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="pi"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.overlays(), [inherited])

    def test_S2_a_claude_or_codex_spawn_loses_them(self):
        """pi-worker/S2: a worker of another family never inherits a credential it cannot use."""
        for family in ("claude", "codex"):
            with self.subTest(family=family):
                self.env_printer(family)
                inherited = {key: "inherited" for key in self.WATCHED}
                session = self.start({"execute_profile": True, "login_env": inherited,
                                      "get:w1": {"out": raw("w1", "t0")}})
                result, text = self.call(session, "spawn", dict(self.ARGS, cli=family))
                self.assertFalse(result["isError"], text)
                self.assertEqual(self.overlays()[-1], {})

    def test_S2_the_scrub_list_covers_the_pi_provider_variables(self):
        """pi-worker/S2: the provider list names every variable a family may hold."""
        for key in PI_VARS:
            self.assertIn(key, profiles.PROVIDER_VARIABLES)


class PiWorkerPolicyTests(unittest.TestCase):
    """T3: the server, not the client, decides what a Pi worker may do."""

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

    def authorize(self, env, role, tool, arguments=None):
        with mock.patch.object(lib, "parent_cli", return_value="pi"):
            host = host_policy.host_of(env)
            self.assertEqual(host, "pi")
            host_policy.authorize(env, host, role, tool, arguments or {}, None)

    def test_S3_a_pi_worker_may_report(self):
        """pi-worker/S3: reporting is the one thing a Pi worker must be able to do."""
        env = self.env()
        self.write(env, {"hosts": {"pi": RULES}})
        self.authorize(env, "worker", "report", {"text": "done", "kind": "complete", "assignment_id": "a1"})

    def test_S3_a_pi_worker_may_read_its_own_team(self):
        """pi-worker/S3: the team-scoped reads and the peer message stay open."""
        env = self.env()
        self.write(env, {"hosts": {"pi": RULES}})
        for tool in ("message", "wait", "status", "collect", "list"):
            with self.subTest(tool=tool):
                self.authorize(env, "worker", tool, {"to": "w1", "name": "w1", "target": "all"})

    def test_S4_a_pi_worker_may_not_spawn_stop_or_resume(self):
        """pi-worker/S4: a worker starts no worker and ends no worker."""
        env = self.env()
        self.write(env, {"hosts": {"pi": RULES}})
        for tool in ("spawn", "stop", "resume"):
            with self.subTest(tool=tool):
                with self.assertRaises(host_policy.Refused) as error:
                    self.authorize(env, "worker", tool, {"kind": "worker", "name": "w1", "task": "t",
                                                        "cwd": "/tmp", "cli": "claude"})
                self.assertIn("worker", str(error.exception))

    def test_S4_a_pi_worker_may_not_create_a_team_task(self):
        """pi-worker/S4: only the lead changes the team's tasks."""
        env = self.env()
        self.write(env, {"hosts": {"pi": RULES}})
        with self.assertRaises(host_policy.Refused):
            self.authorize(env, "worker", "task", {"action": "create", "subject": "s"})
        self.authorize(env, "worker", "task", {"action": "list"})

    def test_S4_a_pi_worker_without_a_policy_may_not_report(self):
        """pi-worker/S4: the operator's policy still governs the host."""
        env = self.env()
        with self.assertRaises(host_policy.Refused):
            self.authorize(env, "worker", "report", {"text": "done", "kind": "complete", "assignment_id": "a1"})


class PiWorkerTests(ToolCase):
    """T4: the whole path: a Pi worker starts, reports, and the lead's team sees it once."""

    def setUp(self):
        super().setUp()
        self.pi = FakeCli("pi")
        self.addCleanup(self.pi.close)
        self.config = self.dir / "config"
        (self.config / "agent-runtime").mkdir(parents=True)

    def policy(self, **rules):
        (self.config / "agent-runtime" / "hosts.json").write_text(json.dumps({"hosts": {"pi": rules}}),
                                                                   encoding="utf-8")

    def lead_env(self, **extra):
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"),
               "HC_TERMINAL_ID": "t0", "HC_SCOPE": "pi", "AGENT_RUNTIME_DISPATCH": "1",
               "XDG_CONFIG_HOME": str(self.config), "PI_CODING_AGENT": "true"}
        env.update(extra)
        return env

    def lead_replies(self, workers=("w1",)):
        rows = [{"name": name, "address": "t%d" % (index + 1), "parent": "t0", "status": "working"}
                for index, name in enumerate(workers)]
        replies = {"get:t0": {"out": raw("lead", None)},
                   "ls": {"out": json.dumps({"terminals": rows})},
                   "live": []}
        for row in rows:
            for key in (row["name"], row["address"]):
                replies["get:" + key] = {"out": json.dumps(row)}
        return replies

    def pi_session(self, replies=None, env=None):
        session = self.start(replies, self.lead_env(**(env or {})), new_cli=self.pi)
        return session

    def worker_session(self, address="t1"):
        env = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"),
               "HC_TERMINAL_ID": address, "HC_SCOPE": "pi", "AGENT_RUNTIME_DISPATCH": "1",
               "XDG_CONFIG_HOME": str(self.config), "PI_CODING_AGENT": "true"}
        session = Session(env, new_cli=self.pi)
        self.addCleanup(session.close)
        session.handshake()
        return session

    def test_S8_a_pi_worker_reports_once_and_the_lead_team_receives_it(self):
        """pi-worker/S8: the report binds the assignment and lands in the lead's team, once."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(session, "spawn", {"kind": "worker", "cli": "pi", "name": "w1",
                                                     "task": "work", "cwd": "/tmp"})
        self.assertFalse(spawned["isError"], text)
        assignment = spawned["structuredContent"]["assignment_id"]
        self.assertIn("pi", self.runs("run")[-1])
        worker = self.worker_session()
        reported, text = self.call(worker, "report", {"text": "STATUS: COMPLETE", "kind": "complete",
                                                      "assignment_id": assignment})
        self.assertFalse(reported["isError"], text)
        waited, text = self.call(session, "wait", {"target": "all", "timeout": 5})
        self.assertFalse(waited["isError"], text)
        batch = waited["structuredContent"]
        self.assertEqual([m["kind"] for m in batch["messages"]], ["complete"])
        self.assertEqual(batch["messages"][0]["text"], "STATUS: COMPLETE")
        self.assertEqual(batch["messages"][0]["assignment_id"], assignment)
        self.assertEqual(batch["messages"][0]["recipient"], "t0")

    def test_S5_a_pi_worker_may_not_reach_a_worker_outside_its_team(self):
        """pi-worker/S5: the team scope holds for a Pi worker too."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(session, "spawn", {"kind": "worker", "cli": "pi", "name": "w1",
                                                     "task": "work", "cwd": "/tmp"})
        self.assertFalse(spawned["isError"], text)
        assignment = spawned["structuredContent"]["assignment_id"]
        worker = self.worker_session()
        reported, text = self.call(worker, "report", {"text": "half done", "kind": "progress",
                                                      "assignment_id": assignment})
        self.assertFalse(reported["isError"], text)
        denied, text = self.call(worker, "collect", {"name": "someone-else"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("not a worker of your team", text)

    def test_S4_a_pi_worker_may_not_spawn_another_worker(self):
        """pi-worker/S4: the host policy refuses a worker's spawn, whatever it names."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        spawned, text = self.call(session, "spawn", {"kind": "worker", "cli": "pi", "name": "w1",
                                                     "task": "work", "cwd": "/tmp"})
        self.assertFalse(spawned["isError"], text)
        worker = self.worker_session()
        denied, text = self.call(worker, "spawn", {"kind": "worker", "cli": "claude", "name": "w2",
                                                    "task": "more work", "cwd": "/tmp"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("worker", text)

    def test_S7_a_pi_spawn_outside_the_authorized_directory_is_refused(self):
        """pi-worker/S7: the operator's directories bound a Pi spawn like any other."""
        self.policy(**RULES)
        session = self.pi_session(self.lead_replies())
        denied, text = self.call(session, "spawn", {"kind": "worker", "cli": "pi", "name": "w1",
                                                    "task": "work", "cwd": "/etc"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("may not start a worker in", text)
        self.assertEqual(self.runs("run"), [])

    def test_S7_a_pi_spawn_needs_an_authorized_backend(self):
        """pi-worker/S7: the runtime never picks a provider for a Pi lead."""
        self.policy(mutations=True, backends=["claude"], paths=["/tmp"])
        session = self.pi_session(self.lead_replies())
        denied, text = self.call(session, "spawn", {"kind": "worker", "cli": "pi", "name": "w1",
                                                    "task": "work", "cwd": "/tmp"})
        self.assertTrue(denied["isError"], text)
        self.assertIn("not authorized", text)
        self.assertEqual(self.runs("run"), [])


if __name__ == "__main__":
    unittest.main()
