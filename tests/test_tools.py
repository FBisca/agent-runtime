"""Tool behaviour over stdio with a fake hc on PATH that logs argv; no real hc mutation."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path

from test_mcp_server import SERVER, Session

sys.path.insert(0, str(SERVER.parent))

FAKE = '''#!%(python)s
import hashlib, json, os, secrets, subprocess, sys, time
argv = sys.argv[1:]
with open(%(log)r, "a") as log:
    log.write(json.dumps(argv) + "\\n")
replies = json.load(open(%(replies)r))
key = argv[0] + ":" + argv[1] if len(argv) > 1 and argv[0] + ":" + argv[1] in replies else argv[0]
reply = replies.get(key, {"rc": 0, "out": ""})
if replies.get("profile_ready_at") and argv[0] == "read":
    if time.monotonic() < replies["profile_ready_at"]:
        reply = {"out": ""}
# Execute only opt-in test programs, using a simulated login environment. Log no full environment.
if argv[0] == "run" and replies.get("execute_profile"):
    login = dict(os.environ)
    login.update(replies.get("login_env", {}))
    login["PROFILE_TEST_SECRET"] = secrets.token_hex(24)
    login["ANTHROPIC_API_KEY"] = login["PROFILE_TEST_SECRET"]
    login["PROFILE_TEST_DIGEST"] = hashlib.sha256(login["PROFILE_TEST_SECRET"].encode()).hexdigest()
    if replies.get("exec_failure_path"):
        login["PATH"] = replies["exec_failure_path"] + ":/" + login["PROFILE_TEST_SECRET"]
    child = subprocess.run(argv[10:], env=login, text=True, capture_output=True)
    with open(%(overlay)r, "a") as log:
        log.write(child.stdout)
    artifacts = {"argv": %(log)r, "overlay": %(overlay)r}
    if argv[10].endswith("agent-runtime-profile"):
        artifacts["config"] = argv[12]
    with open(%(overlay)r + ".audit", "w") as audit:
        json.dump({name: login["PROFILE_TEST_SECRET"].encode() in open(path, "rb").read()
                   for name, path in artifacts.items()}, audit)
    if child.returncode:
        if login["PROFILE_TEST_SECRET"] in child.stderr:
            sys.stderr.write("secret-leaked ")
            sys.exit(1)
        if not replies.get("profile_exit"):
            sys.stderr.write(child.stderr)
            sys.exit(child.returncode)
        replies["read:w1"] = {"out": child.stderr}
        replies["get:w1"] = {"out": json.dumps({"address": "t9", "parent": "t0", "state": "finished"})}
        if replies["profile_exit"] == "delayed":
            replies["profile_ready_at"] = time.monotonic() + 0.3
            replies["get:w1"] = {"out": json.dumps({"address": "t9", "parent": "t0", "state": "live"})}
        reply = {"out": json.dumps({"first_message": {"sent": replies["profile_exit"] != "unsent"}})}
        json.dump(replies, open(%(replies)r, "w"))
# Opt-in live-name model: replies["live"] lists terminal names; hc run refuses a live name like real hc.
if "live" in replies and argv[0] in ("run", "rm"):
    name = argv[argv.index("--name") + 1] if argv[0] == "run" else argv[1]
    if argv[0] == "run" and name in replies["live"]:
        sys.stderr.write(json.dumps({"error": {"kind": "name_taken", "message": "name " + repr(name) + " is taken"}}))
        sys.exit(1)
    if argv[0] == "run" and reply.get("rc", 0) == 0:
        replies["live"].append(name)
    if argv[0] == "rm" and name in replies["live"]:
        replies["live"].remove(name)
    json.dump(replies, open(%(replies)r, "w"))
if reply.get("sleep"):
    time.sleep(reply["sleep"])
sys.stdout.write(reply.get("out", ""))
sys.stderr.write(reply.get("err", ""))
sys.exit(reply.get("rc", 0))
'''
TOML = 'name = "implementer"\ndeveloper_instructions = """\nCODEX IMPLEMENTER RULES\n"""\n'


def raw(name, parent, state="idle", app=None):
    status = {"app": app, "state": state} if app else state
    return json.dumps({"address": "t9", "name": name, "parent": parent, "status": status})


FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROVIDERS = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
             "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
             "CLAUDE_CODE_AUTO_MODE_SERVER", "CLAUDE_CODE_AUTO_MODE_MODEL"]


def scrub(keys):
    """The `env -u NAME...` prefix a built-in spawn runs under."""
    return ["env"] + [arg for key in sorted(keys) for arg in ("-u", key)]


def unwrap(argv):
    """The CLI argv after the built-in `env -u NAME...` scrub."""
    if argv[:1] != ["env"]:
        return argv
    i = 1
    while argv[i:i + 1] == ["-u"]:
        i += 2
    return argv[i:]


class ToolCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / "bin").mkdir()
        agents = self.dir / "home" / ".codex" / "agents"
        agents.mkdir(parents=True)
        (agents / "implementer.toml").write_text(TOML)
        self.log = self.dir / "log"
        self.log.write_text("")

    def start(self, replies=None, env=None, new_cli=False):
        replies = dict(replies or {})
        replies.setdefault("get:t0", {"out": raw("lead", None)})
        replies.setdefault("get:t1", {"out": raw("worker", "t0")})
        (self.dir / "replies").write_text(json.dumps(replies))
        hc = self.dir / "bin" / "hc"
        hc.write_text(FAKE % {"python": sys.executable, "log": str(self.log), "replies": str(self.dir / "replies"),
                              "overlay": str(self.dir / "overlay")})
        hc.chmod(0o755)
        full = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"),
                "HC_TERMINAL_ID": "t0", "CODEX_THREAD_ID": "test-session"}
        full.update(env or {})
        session = Session(full, new_cli=new_cli)
        self.addCleanup(session.close)
        session.handshake()
        return session

    def call(self, session, name, arguments):
        result = session.send("tools/call", {"name": name, "arguments": arguments})["result"]
        return result, result["content"][0]["text"]

    def runs(self, command=None):
        calls = [json.loads(l) for l in self.log.read_text().splitlines()]
        return [c for c in calls if command is None or c[0] == command]


class IdentityTests(ToolCase):
    ENV = {"HC_TERMINAL_SOCKET": "/hc/sock"}

    def identity(self, **extra):
        import hc_tools
        with mock.patch.object(hc_tools.lib, "ps_entry", side_effect={100: (90, "/usr/bin/zsh"), 90: (1, "claude")}.get), \
                mock.patch.object(hc_tools.lib, "lstart", return_value="Wed Nov  4 10:00:00 2026"), \
                mock.patch.object(hc_tools.os, "getppid", return_value=100):
            return hc_tools.dispatch_identity(dict(self.ENV, **extra))

    def test_server_identity_ignores_session_ids_under_a_cli_ancestor(self):
        """agent-runtime/R107"""
        scope, generation = self.identity(CODEX_THREAD_ID="t", CLAUDE_CODE_SESSION_ID="c", CLAUDE_SESSION_ID="s")
        self.assertEqual(generation, "90:Wed Nov  4 10:00:00 2026")

    def test_clear_or_new_in_same_process_keeps_team_and_assignments(self):
        """agent-runtime/R108"""
        import hc_tools
        from dispatch_store import Store
        env = {"HOME": str(self.dir / "home")}
        store = Store(env)
        first = self.identity(CODEX_THREAD_ID="thread-a")
        team = store.team(first[0], "t0", first[1])
        assignment = store.register(team, "w1")["assignment_id"]
        again = self.identity(CODEX_THREAD_ID="thread-b")  # /new: same process, new thread id
        self.assertEqual(first, again)
        self.assertEqual(store.team(again[0], "t0", again[1]), team)
        with store.transaction() as db:
            seen = [r[0] for r in db.execute("SELECT id FROM assignments WHERE team=?", (team,))]
        self.assertEqual(seen, [assignment])


class SpawnTests(ToolCase):
    ARGS = {"kind": "worker", "name": "w1", "task": "do x", "cwd": "/tmp/c"}

    def test_S2_claude_worker_runs_hc_run_bg_with_task_and_report_line(self):
        """agent-runtime-tools/S2"""
        session = self.start({"run": {"out": json.dumps({"first_message": {"sent": True}})},
                              "get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="claude"))
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("run")
        self.assertEqual(argv[:10], ["run", "--bg", "--name", "w1", "--cwd", "/tmp/c", "--parent", "t0", "--json",
                                         "--no-notify"])
        program = unwrap(argv[10:])
        self.assertEqual(program[:5], ["claude", "--agent", "implementer", "--permission-mode", "auto"])
        self.assertTrue(program[5].startswith("do x"))
        self.assertIn("When done or stuck, call the agent-runtime report tool with your result.", program[5])
        self.assertNotIn("hc send", program[5])
        info = json.loads(text)
        self.assertEqual((info["name"], info["address"], info["parent"]), ("w1", "t9", "t0"))

    def test_workers_start_with_no_notify_before_the_program(self):
        """agent-runtime/R96"""
        for cli, program in (("claude", "claude"), ("codex", "codex")):
            with self.subTest(cli=cli):
                session = self.start({"get:w1": {"out": raw("w1", "t0")}})
                result, text = self.call(session, "spawn", dict(self.ARGS, cli=cli))
                self.assertFalse(result["isError"], text)
                argv = self.runs("run")[-1]
                self.assertIn("--no-notify", argv)
                self.assertLess(argv.index("--no-notify"), argv.index(program))

    def test_S2_codex_worker_is_no_daemon_with_implementer_instructions(self):
        """agent-runtime-tools/S2"""
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="codex"))
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("run")
        program = unwrap(argv[10:])
        self.assertEqual(program[:2], ["codex", "--no-daemon"])
        self.assertTrue(program[2].startswith("CODEX IMPLEMENTER RULES"))
        self.assertIn("do x", program[2])
        self.assertIn("call the agent-runtime report tool", program[2])
        self.assertNotIn("permission-mode", " ".join(argv))

    def spawn_argv(self, **extra):
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, **extra))
        self.assertFalse(result["isError"], text)
        return unwrap(self.runs("run")[-1][10:])

    def test_claude_model_flag(self):
        """agent-runtime/R129"""
        argv = self.spawn_argv(cli="claude", model="haiku")
        self.assertEqual(argv[:6], ["claude", "--agent", "implementer", "--permission-mode", "auto", "--model"])
        self.assertEqual(argv[6], "haiku")
        self.assertEqual(len(argv), 8)
        self.assertTrue(argv[-1].startswith("do x"))

    def test_codex_model_flag(self):
        """agent-runtime/R130"""
        argv = self.spawn_argv(cli="codex", model="gpt-5.5")
        self.assertEqual(argv[:4], ["codex", "--no-daemon", "--model", "gpt-5.5"])
        self.assertEqual(len(argv), 5)
        self.assertTrue(argv[-1].startswith("CODEX IMPLEMENTER RULES"))
        self.assertNotIn("permission-mode", " ".join(argv))

    def test_no_model_keeps_argv(self):
        """agent-runtime/R131"""
        claude = self.spawn_argv(cli="claude")
        self.assertEqual(claude[:5], ["claude", "--agent", "implementer", "--permission-mode", "auto"])
        self.assertEqual(len(claude), 6)
        codex = self.spawn_argv(cli="codex")
        self.assertEqual(codex[:2], ["codex", "--no-daemon"])
        self.assertEqual(len(codex), 3)
        self.assertNotIn("--model", claude + codex)

    def test_blank_or_non_string_model_is_an_error(self):
        """agent-runtime/R132"""
        for model in ("", "   ", 5, ["haiku"], True):
            with self.subTest(model=model):
                session = self.start()
                result, _ = self.call(session, "spawn", dict(self.ARGS, model=model))
                self.assertTrue(result["isError"])
                self.assertEqual(self.runs("run"), [])

    def test_whitespace_or_control_characters_in_model_are_an_error(self):
        """agent-runtime/R133"""
        for model in (" haiku", "haiku ", "  -x", "ha iku", "ha\niku", "ha\x00iku", "ha\x7fiku", "ha\tiku"):
            with self.subTest(model=model):
                session = self.start()
                result, _ = self.call(session, "spawn", dict(self.ARGS, model=model))
                self.assertTrue(result["isError"])
                self.assertEqual(self.runs("run"), [])

    def write_agent(self, name, body='developer_instructions = """\nREVIEWER RULES\n"""\n'):
        (self.dir / "home" / ".codex" / "agents" / ("%s.toml" % name)).write_text(body)

    def test_claude_agent_flag(self):
        """agent-runtime/R136"""
        argv = self.spawn_argv(cli="claude", agent="scene-reviewer")
        self.assertEqual(argv[:5], ["claude", "--agent", "scene-reviewer", "--permission-mode", "auto"])
        self.assertEqual(len(argv), 6)
        self.assertTrue(argv[-1].startswith("do x"))
        argv = self.spawn_argv(cli="claude", agent="scene-reviewer", model="haiku")
        self.assertEqual(argv[:7], ["claude", "--agent", "scene-reviewer", "--permission-mode", "auto", "--model", "haiku"])
        self.assertEqual(len(argv), 8)
        self.assertTrue(argv[-1].startswith("do x"))

    def test_codex_agent_instructions(self):
        """agent-runtime/R137"""
        self.write_agent("reviewer")
        argv = self.spawn_argv(cli="codex", agent="reviewer")
        self.assertEqual(argv[:2], ["codex", "--no-daemon"])
        self.assertEqual(len(argv), 3)
        self.assertTrue(argv[-1].startswith("REVIEWER RULES\n\ndo x"))
        self.assertNotIn("CODEX IMPLEMENTER RULES", argv[-1])

    def test_no_agent_keeps_implementer(self):
        """agent-runtime/R138"""
        claude = self.spawn_argv(cli="claude")
        self.assertEqual(claude[:5], ["claude", "--agent", "implementer", "--permission-mode", "auto"])
        self.assertEqual(len(claude), 6)
        codex = self.spawn_argv(cli="codex")
        self.assertTrue(codex[-1].startswith("CODEX IMPLEMENTER RULES"))
        self.assertEqual(len(codex), 3)

    def test_invalid_agent_is_an_error(self):
        """agent-runtime/R139"""
        for agent in ("", "   ", 5, ["a"], True, "-x", "../x", "a/b", "a.b", "a" * 65, " a", "a b", "a\n"):
            for cli in ("claude", "codex"):
                with self.subTest(agent=agent, cli=cli):
                    session = self.start()
                    result, _ = self.call(session, "spawn", dict(self.ARGS, cli=cli, agent=agent))
                    self.assertTrue(result["isError"])
                    self.assertEqual(self.runs("run"), [])

    def test_colon_agent_is_claude_only(self):
        """agent-runtime/R140"""
        argv = self.spawn_argv(cli="claude", agent="plugin:reviewer")
        self.assertEqual(argv[:3], ["claude", "--agent", "plugin:reviewer"])
        self.write_agent("plugin:reviewer")
        session = self.start()
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="codex", agent="plugin:reviewer"))
        self.assertTrue(result["isError"])
        self.assertIn(":", text)
        self.assertEqual(len(self.runs("run")), 1)  # only the Claude spawn above

    def test_codex_agent_file_missing_or_without_instructions(self):
        """agent-runtime/R141"""
        for body in (None, 'name = "reviewer"\n', "developer_instructions = 5\n",
                     'developer_instructions = ""\n', 'developer_instructions = "  \\n "\n'):
            with self.subTest(body=body):
                if body is not None:
                    self.write_agent("reviewer", body)
                session = self.start()
                result, text = self.call(session, "spawn", dict(self.ARGS, cli="codex", agent="reviewer"))
                self.assertTrue(result["isError"])
                self.assertIn("reviewer.toml", text)
                self.assertEqual(self.runs("run"), [])

    def test_dash_model_is_an_error(self):
        """agent-runtime/R134"""
        for cli in ("claude", "codex"):
            with self.subTest(cli=cli):
                session = self.start()
                result, _ = self.call(session, "spawn", dict(self.ARGS, cli=cli, model="--dangerous"))
                self.assertTrue(result["isError"])
                self.assertEqual(self.runs("run"), [])

    def test_claude_effort_flag(self):
        """agent-runtime/R147"""
        argv = self.spawn_argv(cli="claude", model="haiku", effort="low")
        self.assertEqual(argv[:9], ["claude", "--agent", "implementer", "--permission-mode", "auto",
                                    "--model", "haiku", "--effort", "low"])
        self.assertEqual(len(argv), 10)

    def test_codex_effort_config(self):
        """agent-runtime/R148"""
        argv = self.spawn_argv(cli="codex", effort="xhigh")
        self.assertEqual(argv[:4], ["codex", "--no-daemon", "-c", 'model_reasoning_effort="xhigh"'])
        self.assertEqual(len(argv), 5)

    def test_no_effort_keeps_argv(self):
        """agent-runtime/R148"""
        for cli in ("claude", "codex"):
            with self.subTest(cli=cli):
                argv = self.spawn_argv(cli=cli)
                self.assertNotIn("--effort", argv)
                self.assertNotIn("-c", argv)

    def test_unknown_effort_is_an_error(self):
        """agent-runtime/R149"""
        for effort in ("ultra", "", "--x", 3, "high\n"):
            with self.subTest(effort=effort):
                session = self.start()
                result, _ = self.call(session, "spawn", dict(self.ARGS, cli="codex", effort=effort))
                self.assertTrue(result["isError"])
                self.assertEqual(self.runs("run"), [])

    def test_S2_unsent_first_message_is_an_error(self):
        """agent-runtime-tools/S2"""
        session = self.start({"run": {"out": json.dumps({"first_message": {"sent": False}})}})
        result, text = self.call(session, "spawn", self.ARGS)
        self.assertTrue(result["isError"])
        self.assertIn("hc read", text)

    def test_S3_lookup_names_the_cli_subagent_tool(self):
        """agent-runtime-tools/S3"""
        session = self.start()
        result, text = self.call(session, "spawn", dict(self.ARGS, kind="lookup", cli="claude"))
        self.assertTrue(result["isError"])
        self.assertIn("Agent tool", text)
        result, text = self.call(session, "spawn", dict(self.ARGS, kind="lookup", cli="codex"))
        self.assertIn("MultiAgent", text)
        self.assertEqual(self.runs("run"), [])

    def test_S10_unsupported_cli_is_a_gap_error(self):
        """agent-runtime-tools/S10"""
        session = self.start()
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="gemini"))
        self.assertTrue(result["isError"])
        self.assertTrue(text.startswith("gap: add "), text)
        self.assertTrue(text.endswith("; ask the user"), text)
        self.assertIn("→", text)

    def test_S2_bad_names_never_reach_hc(self):
        """agent-runtime-tools/S2"""
        session = self.start()
        for name in ("-rf", "A b", "x" * 33, ""):
            result, _ = self.call(session, "spawn", dict(self.ARGS, name=name))
            self.assertTrue(result["isError"], name)
        self.assertEqual(self.runs("run"), [])


class ProfileTests(ToolCase):
    ARGS = {"kind": "worker", "name": "w1", "task": "profile task", "cwd": "/tmp"}
    PROMPT = "profile task\n\nWhen done or stuck, call the agent-runtime report tool with your result."
    BUILTIN_PROGRAMS = {"claude": ["claude", "--agent", "implementer", "--permission-mode", "auto", PROMPT],
                        "codex": ["codex", "--no-daemon", "CODEX IMPLEMENTER RULES\n\n" + PROMPT]}

    def config(self, profiles):
        path = self.dir / "home" / ".config" / "agent-runtime" / "profiles.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"profiles": profiles}))
        return path

    def test_S26_exec_failure_never_echoes_expanded_secret(self):
        """S26: exec errors name the program without leaking a path containing a credential."""
        self.config({"custom": {"cli": "claude", "env": {"ANTHROPIC_AUTH_TOKEN": "$PROFILE_TEST_SECRET"}}})
        bindir = self.dir / "launcher-bin"
        bindir.mkdir()
        (bindir / "python3").symlink_to(sys.executable)
        (bindir / "dirname").symlink_to("/usr/bin/dirname")
        session = self.start({"execute_profile": True, "exec_failure_path": str(bindir)})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
        self.assertTrue(result["isError"])
        self.assertNotIn("secret-leaked", text)
        self.assertIn("cannot exec claude", text)
        self.assertNotIn(str(bindir), text)

    def test_S19_profile_scrubs_unset_provider_variables(self):
        """S19: only explicitly configured provider settings reach the worker."""
        providers = ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL",
                     "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"]
        self.config({"custom": {"cli": "claude", "env": {"ANTHROPIC_MODEL": "profile-model"}}})
        program = self.dir / "bin" / "claude"
        program.write_text("#!%s\nimport json, os\nprint(json.dumps({k: os.environ[k] for k in %r if k in os.environ}))\n" %
                           (sys.executable, providers))
        program.chmod(0o755)
        session = self.start({"execute_profile": True, "login_env": dict.fromkeys(providers, "inherited"),
                              "get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(json.loads((self.dir / "overlay").read_text()), {"ANTHROPIC_MODEL": "profile-model"})

    def test_S18_private_references_and_failures_within_startup_window(self):
        """S18: references stay private; diagnostics and exits inside the startup window fail launch."""
        path = self.config({"custom": {"cli": "claude", "env": {"ANTHROPIC_AUTH_TOKEN": "$ANTHROPIC_API_KEY"}}})
        program = self.dir / "bin" / "claude"
        program.write_text("#!%s\nimport hashlib, os\nassert 'ANTHROPIC_API_KEY' not in os.environ\nassert hashlib.sha256(os.environ['ANTHROPIC_AUTH_TOKEN'].encode()).hexdigest() == os.environ['PROFILE_TEST_DIGEST']\nprint('reference-ok')\n" % sys.executable)
        program.chmod(0o755)
        session = self.start({"execute_profile": True, "live": [], "get:w1": {"out": raw("w1", "t0")}},
                             {"ANTHROPIC_API_KEY": "server-value-must-not-be-used", "AGENT_RUNTIME_DISPATCH": "1"})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(json.loads((self.dir / "overlay.audit").read_text()),
                         {"argv": False, "config": False, "overlay": False})
        self.assertEqual((self.dir / "overlay").read_text(), "reference-ok\n")
        self.assertNotIn("server-value-must-not-be-used", self.log.read_text())
        self.assertIn("$ANTHROPIC_API_KEY", path.read_text())
        self.call(session, "stop", {"name": "w1"})
        replies = json.loads((self.dir / "replies").read_text())
        replies["live"] = []
        (self.dir / "replies").write_text(json.dumps(replies))
        self.config({"custom": {"cli": "claude", "env": {"ANTHROPIC_AUTH_TOKEN": "$MISSING_PROFILE_SECRET"}}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
        self.assertTrue(result["isError"])
        self.assertIn("MISSING_PROFILE_SECRET", text)
        self.assertIn("agent-runtime-profile", text)
        self.assertEqual(json.loads((self.dir / "replies").read_text())["live"], [])
        # hc can accept the launch before the shim exits; its screen then holds the error.
        for delivery in ("unsent", "sent", "delayed"):
            with self.subTest(delivery=delivery):
                replies = json.loads((self.dir / "replies").read_text())
                replies["profile_exit"] = delivery
                (self.dir / "replies").write_text(json.dumps(replies))
                result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
                self.assertTrue(result["isError"])
                self.assertIn("agent-runtime-profile:", text)
                self.assertIn("MISSING_PROFILE_SECRET", text)
                self.assertEqual(json.loads((self.dir / "replies").read_text())["live"], [])
                from dispatch_store import Store
                with Store({"HOME": str(self.dir / "home")}).transaction() as db:
                    self.assertEqual(db.execute("SELECT state FROM assignments ORDER BY rowid DESC LIMIT 1").fetchone()[0], "failed")
                    self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [0])
        # Exits observed inside the startup window are failures even without a diagnostic.
        for state in ("finished", "exited"):
            with self.subTest(state=state):
                replies = json.loads((self.dir / "replies").read_text())
                replies.update(execute_profile=False, profile_ready_at=0)
                replies["get:w1"] = {"out": json.dumps({"address": "t9", "parent": "t0", "state": state})}
                replies["read:w1"] = {"out": ""}
                (self.dir / "replies").write_text(json.dumps(replies))
                result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
                self.assertTrue(result["isError"])
                self.assertIn("w1", text)
                self.assertIn("launcher exited", text)
                self.assertEqual(json.loads((self.dir / "replies").read_text())["live"], [])
                with Store({"HOME": str(self.dir / "home")}).transaction() as db:
                    self.assertEqual(db.execute("SELECT state FROM assignments ORDER BY rowid DESC LIMIT 1").fetchone()[0], "failed")
                    self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [0])

    def test_S22_override_replaces_default_config_for_schema_and_launcher(self):
        """S22: AGENT_RUNTIME_PROFILES selects a different file for listing and launching."""
        self.config({"ignored": {"cli": "claude", "env": {}}})
        override = self.dir / "home" / "alternate.json"
        override.write_text(json.dumps({"profiles": {"selected": {"cli": "claude", "env": {"PROFILE_MARKER": "selected"}}}}))
        program = self.dir / "bin" / "claude"
        program.write_text("#!%s\nimport os\nprint(os.environ['PROFILE_MARKER'])\n" % sys.executable)
        program.chmod(0o755)
        session = self.start({"execute_profile": True, "get:w1": {"out": raw("w1", "t0")},
                              "login_env": {"AGENT_RUNTIME_PROFILES": str(self.dir / "wrong.json"),
                                            "XDG_CONFIG_HOME": "/wrong"}},
                             {"AGENT_RUNTIME_PROFILES": "~/alternate.json"})
        tools = session.send("tools/list")["result"]["tools"]
        self.assertEqual(tools[0]["inputSchema"]["properties"]["cli"]["enum"], ["claude", "codex", "selected"])
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="selected"))
        self.assertFalse(result["isError"], text)
        self.assertEqual((self.dir / "overlay").read_text(), "selected\n")

    def test_S21_absent_config_keeps_builtins_and_original_argv(self):
        """S21: removing config restores exactly the original built-in schema and commands."""
        path = self.config({"custom": {"cli": "claude", "env": {}}})
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        self.assertIn("custom", session.send("tools/list")["result"]["tools"][0]["inputSchema"]["properties"]["cli"]["enum"])
        path.unlink()
        tools = session.send("tools/list")["result"]["tools"]
        self.assertEqual(tools[0]["inputSchema"]["properties"]["cli"]["enum"], ["claude", "codex"])
        for family, program in self.BUILTIN_PROGRAMS.items():
            result, text = self.call(session, "spawn", dict(self.ARGS, cli=family))
            self.assertFalse(result["isError"], text)
            self.assertEqual(self.runs("run")[-1], ["run", "--bg", "--name", "w1", "--cwd", "/tmp", "--parent", "t0",
                                                   "--json", "--no-notify"] + scrub(PROVIDERS) + program)

    MINIMAX = {"cli": "claude", "env": {"ANTHROPIC_BASE_URL": "https://provider.example/api",
                                        "ANTHROPIC_MODEL": "provider-model", "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "512000"}}
    WATCHED = PROVIDERS + ["CLAUDE_CODE_MAX_CONTEXT_TOKENS"]

    def env_printer(self, name):
        program = self.dir / "bin" / name
        program.write_text("#!%s\nimport json, os\nprint(json.dumps({k: os.environ[k] for k in %r if k in os.environ}))\n"
                           % (sys.executable, self.WATCHED))
        program.chmod(0o755)

    def overlays(self):
        return [json.loads(line) for line in (self.dir / "overlay").read_text().splitlines()]

    def test_builtin_spawn_inherits_no_provider_or_profile_variable(self):
        """#35: built-in workers run on the CLI's own login, whatever the lead, pane or an earlier profile left."""
        self.config({"minimax": self.MINIMAX})
        for family in ("claude", "codex"):
            self.env_printer(family)
        leaked = {key: "inherited" for key in self.WATCHED if key != "ANTHROPIC_BASE_URL"}
        session = self.start({"execute_profile": True, "login_env": leaked, "get:w1": {"out": raw("w1", "t0")}},
                             {"ANTHROPIC_BASE_URL": "lead-value"})
        for family in ("claude", "codex"):
            result, text = self.call(session, "spawn", dict(self.ARGS, cli=family))
            self.assertFalse(result["isError"], text)
        self.assertEqual(self.overlays(), [{}, {}])

    def test_builtin_scrub_survives_a_malformed_config(self):
        """#35: an unreadable profile config still scrubs every provider variable."""
        path = self.dir / "home" / ".config" / "agent-runtime" / "profiles.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        self.env_printer("claude")
        session = self.start({"execute_profile": True, "login_env": dict.fromkeys(PROVIDERS, "inherited"),
                              "get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="claude"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.overlays(), [{}])

    def test_profile_gets_exactly_its_own_values(self):
        """#35: another profile's overlay keys never reach a profile worker."""
        self.config({"minimax": self.MINIMAX, "other": {"cli": "claude", "env": {"ANTHROPIC_MODEL": "other-model"}}})
        self.env_printer("claude")
        session = self.start({"execute_profile": True, "get:w1": {"out": raw("w1", "t0")},
                              "login_env": {key: "inherited" for key in self.WATCHED}})
        for profile in ("other", "minimax"):
            result, text = self.call(session, "spawn", dict(self.ARGS, cli=profile))
            self.assertFalse(result["isError"], text)
        self.assertEqual(self.overlays(), [{"ANTHROPIC_MODEL": "other-model"}, self.MINIMAX["env"]])

    def test_S17_profiles_wrap_each_family_with_prompt_last(self):
        """S17: configured names launch the family's standard argv through the installed shim."""
        config = self.config({"custom-claude": {"cli": "claude", "env": {}},
                     "custom-codex": {"cli": "codex", "env": {}}})
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        for family, builtin in self.BUILTIN_PROGRAMS.items():
            with self.subTest(family=family):
                result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom-" + family, model="sample"))
                self.assertFalse(result["isError"], text)
                argv = self.runs("run")[-1][10:]
                self.assertEqual(argv, [str(SERVER.parent.parent / "bin" / "agent-runtime-profile"),
                                        "custom-" + family, str(config), "--"] +
                                 builtin[:-1] + ["--model", "sample"] + builtin[-1:])


    def test_profile_permission_mode_and_settings_replace_auto(self):
        """agent-runtime/R143 agent-runtime/R144"""
        settings = self.dir / "home" / "worker-settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("{}")
        config = self.config({"plain": {"cli": "claude", "env": {}},
                              "edits": {"cli": "claude", "env": {}, "permission_mode": "acceptEdits",
                                        "settings": "~/worker-settings.json"}})
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        launcher = str(SERVER.parent.parent / "bin" / "agent-runtime-profile")
        builtin = self.BUILTIN_PROGRAMS["claude"]
        expected = {"plain": builtin,
                    "edits": ["claude", "--agent", "implementer", "--permission-mode", "acceptEdits",
                              "--settings", str(settings), self.PROMPT]}
        for profile, program in expected.items():
            with self.subTest(profile=profile):
                result, text = self.call(session, "spawn", dict(self.ARGS, cli=profile))
                self.assertFalse(result["isError"], text)
                self.assertEqual(self.runs("run")[-1][10:], [launcher, profile, str(config), "--"] + program)

    def test_missing_settings_file_is_refused_before_hc_run(self):
        """agent-runtime/R146"""
        self.config({"edits": {"cli": "claude", "env": {}, "settings": "/nonexistent/worker-settings.json"}})
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="edits"))
        self.assertTrue(result["isError"])
        self.assertIn("settings file /nonexistent/worker-settings.json does not exist", text)
        self.assertEqual(self.runs("run"), [])

class SendTests(ToolCase):
    CHILD = {"get:w1": {"out": raw("w1", "t0")}}

    def test_S4_message_refuses_a_terminal_that_is_not_my_child(self):
        """agent-runtime-tools/S4"""
        session = self.start({"get:w1": {"out": raw("w1", "tX")}})
        for name, args in (("message", {"to": "w1", "text": "hi"}), ("resume", {"name": "w1", "task": "go"})):
            result, text = self.call(session, name, args)
            self.assertTrue(result["isError"], name)
            self.assertIn("not your child", text)
        self.assertEqual(self.runs("send"), [])

    def test_S4_message_is_hc_send_without_force(self):
        """agent-runtime-tools/S4"""
        session = self.start(self.CHILD)
        result, _ = self.call(session, "message", {"to": "w1", "text": "hello"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.runs("send"), [["send", "w1", "hello"]])

    def test_S4_resume_adds_wait_and_never_force(self):
        """agent-runtime-tools/S4"""
        session = self.start(self.CHILD)
        result, _ = self.call(session, "resume", {"name": "w1", "task": "continue"})
        self.assertFalse(result["isError"])
        (argv,) = self.runs("send")
        self.assertEqual(argv[:2], ["send", "--wait"])
        self.assertEqual(argv[-2:], ["w1", "continue"])
        self.assertNotIn("--force", argv)

    def test_S4_target_cannot_be_an_option(self):
        """agent-runtime-tools/S4"""
        session = self.start(self.CHILD)
        result, _ = self.call(session, "message", {"to": "--force", "text": "x"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("send"), [])


class WaitCollectStopReportTests(ToolCase):
    def test_S5_wait_any_waits_on_children_and_returns_their_status(self):
        """agent-runtime-tools/S5"""
        listing = json.dumps({"terminals": [{"address": "t1", "parent": "t0"}, {"address": "t8", "parent": "tX"}]})
        session = self.start({"ls": {"out": listing}})
        result, text = self.call(session, "wait", {"target": "any", "timeout": 20})
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("wait")
        self.assertEqual(argv, ["wait", "--children", "any", "--timeout", "20", "--json"])
        workers = json.loads(text)["workers"]
        self.assertEqual([w["runtime"] for w in workers], ["hc"])

    def test_S5_wait_all_and_named_worker(self):
        """agent-runtime-tools/S5"""
        session = self.start({"ls": {"out": json.dumps({"terminals": []})}, "get:w1": {"out": raw("w1", "t0")}})
        self.call(session, "wait", {"target": "all"})
        self.call(session, "wait", {"target": "w1"})
        self.assertEqual(self.runs("wait"), [["wait", "--children", "all", "--timeout", "170", "--json"],
                                             ["wait", "w1", "--timeout", "170", "--json"]])

    def test_S5_wait_returns_hcs_parsed_news_without_truncation(self):
        """agent-runtime-tools/S5"""
        news = {"reason": "idle", "terminal": "t1", "note": "x" * 900}
        session = self.start({"wait": {"out": json.dumps(news)}, "ls": {"out": json.dumps({"terminals": []})}})
        _, text = self.call(session, "wait", {"target": "any"})
        self.assertEqual(json.loads(text)["wait"]["news"], news)

    def test_S5_wait_timeout_is_capped(self):
        """agent-runtime-tools/S5"""
        session = self.start({"ls": {"out": json.dumps({"terminals": []})}})
        self.call(session, "wait", {"target": "any", "timeout": 9999})
        self.assertEqual(self.runs("wait")[0][4], "590")

    def test_S5_long_wait_does_not_block_ping_and_cancel_ends_it(self):
        """agent-runtime-tools/S5"""
        session = self.start({"wait": {"sleep": 30}, "ls": {"out": json.dumps({"terminals": []})}})
        write = lambda m: (session.proc.stdin.write(json.dumps(m) + "\n"), session.proc.stdin.flush())
        write({"jsonrpc": "2.0", "id": 50, "method": "tools/call",
               "params": {"name": "wait", "arguments": {"target": "any"}}})
        write({"jsonrpc": "2.0", "id": 51, "method": "ping"})
        self.assertEqual(json.loads(session.proc.stdout.readline())["id"], 51)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 50}})
        started = time.monotonic()
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=10), 0)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(session.proc.stdout.read(), "")

    def test_unregistered_wait_returns_empty_without_hc_wait(self):
        """agent-runtime/R34
        agent-runtime-dispatch/S1; supersedes agent-runtime-tools/S5 transport."""
        session = self.start(env={"AGENT_RUNTIME_DISPATCH": "1"})
        for target in ("any", "all", "w1"):
            result, text = self.call(session, "wait", {"target": target, "timeout": 0})
            self.assertFalse(result["isError"], text)
            self.assertEqual(json.loads(text)["reason"], "empty")
        self.assertEqual(self.runs("wait"), [])

    def test_wait_rejects_invalid_timeout(self):
        """agent-runtime/R34
        agent-runtime-dispatch/S1"""
        session = self.start(env={"AGENT_RUNTIME_DISPATCH": "1"})
        for timeout in (-1, True, "3"):
            result, _ = self.call(session, "wait", {"target": "any", "timeout": timeout})
            self.assertTrue(result["isError"])

    def test_long_collect_does_not_block_ping_and_cancel_ends_it(self):
        """agent-runtime/R34
        agent-runtime-dispatch/S1; retains transport cancellation coverage."""
        session = self.start({"read": {"sleep": 30}})
        write = lambda m: (session.proc.stdin.write(json.dumps(m) + "\n"), session.proc.stdin.flush())
        write({"jsonrpc": "2.0", "id": 50, "method": "tools/call",
               "params": {"name": "collect", "arguments": {"name": "w1"}}})
        write({"jsonrpc": "2.0", "id": 51, "method": "ping"})
        self.assertEqual(json.loads(session.proc.stdout.readline())["id"], 51)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 50}})
        started = time.monotonic()
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=10), 0)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(session.proc.stdout.read(), "")

    def test_S6_collect_returns_text_after_last_prompt_line_bounded(self):
        """agent-runtime-tools/S6"""
        body = ["old output", "❯ first task", "old answer", "› do the thing"] + ["line %d" % i for i in range(300)] + ["❯ "]
        session = self.start({"read": {"out": "\n".join(body)}, "get:w1": {"out": raw("w1", "t0", app="claude-code")}})
        result, text = self.call(session, "collect", {"name": "w1"})
        self.assertFalse(result["isError"])
        self.assertNotIn("old answer", text)
        self.assertNotIn("do the thing", text)
        self.assertLessEqual(len(text.splitlines()), 200)
        self.assertIn("line 299", text)
        self.assertTrue(self.runs("read")[0][:2] == ["read", "w1"])

    def test_S6_collect_on_codex_skips_the_composer_and_footer(self):
        """agent-runtime-tools/S6"""
        screen = (FIXTURES / "codex_working_interrupted.txt").read_text()
        session = self.start({"read": {"out": screen}, "get:w1": {"out": raw("w1", "t0", app="codex")}})
        _, text = self.call(session, "collect", {"name": "w1"})
        self.assertIn("Running npm run build", text)
        self.assertNotIn("Ask Codex to do anything", text)
        self.assertNotIn("gpt-5", text)

    def test_S6_collect_without_prompt_returns_whole_screen(self):
        """agent-runtime-tools/S6"""
        session = self.start({"read": {"out": "a\nb"}, "get:w1": {"out": raw("w1", "t0")}})
        _, text = self.call(session, "collect", {"name": "w1"})
        self.assertEqual(text, "a\nb")

    def test_S7_stop_refuses_a_terminal_that_is_not_my_child(self):
        """agent-runtime-tools/S7"""
        session = self.start({"get:other": {"out": raw("other", "tX")}})
        result, text = self.call(session, "stop", {"name": "other"})
        self.assertTrue(result["isError"])
        self.assertIn("not your child", text)
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])

    def test_S7_stop_kills_then_removes_a_child(self):
        """agent-runtime-tools/S7"""
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, _ = self.call(session, "stop", {"name": "w1"})
        self.assertFalse(result["isError"])
        self.assertEqual([c[0] for c in self.runs() if c[0] in ("kill", "rm")], ["kill", "rm"])

    def test_S8_worker_report_sends_to_parent(self):
        """agent-runtime-tools/S8"""
        session = self.start(env={"HC_TERMINAL_ID": "t1"})
        result, _ = self.call(session, "report", {"text": "done: abc"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.runs("send"), [["send", "t0", "done: abc"]])

    def test_unregistered_worker_report_refuses_injection(self):
        """agent-runtime/R34
        agent-runtime-dispatch/S1; replaces agent-runtime-tools/S8 hc-send assertion."""
        session = self.start(env={"HC_TERMINAL_ID": "t1", "AGENT_RUNTIME_DISPATCH": "1"})
        result, _ = self.call(session, "report", {"text": "done: abc"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("send"), [])

    def test_S8_lead_report_is_an_error(self):
        """agent-runtime-tools/S8"""
        session = self.start()
        result, text = self.call(session, "report", {"text": "x"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("send"), [])


class StopCleanupTests(ToolCase):
    def test_S7_stop_still_removes_a_terminal_that_already_exited(self):
        """agent-runtime-tools/S7"""
        session = self.start({"get:w1": {"out": raw("w1", "t0")}, "kill": {"rc": 1, "err": "already exited"}})
        result, text = self.call(session, "stop", {"name": "w1"})
        self.assertFalse(result["isError"], text)
        self.assertEqual([c[0] for c in self.runs() if c[0] in ("kill", "rm")], ["kill", "rm"])

    def test_S2_cancelling_spawn_ends_the_terminal_it_started(self):
        """agent-runtime-tools/S2"""
        session = self.start({"run": {"sleep": 30}})
        write = lambda m: (session.proc.stdin.write(json.dumps(m) + "\n"), session.proc.stdin.flush())
        write({"jsonrpc": "2.0", "id": 60, "method": "tools/call", "params": {"name": "spawn", "arguments": {
            "kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp"}}})
        deadline = time.monotonic() + 5
        while not self.runs("run") and time.monotonic() < deadline:
            time.sleep(0.05)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 60}})
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=15), 0)
        self.assertEqual([c[:2] for c in self.runs() if c[0] in ("kill", "rm")], [["kill", "w1"], ["rm", "w1"]])


class RuntimeGapTests(ToolCase):
    def test_S9_herdmates_worker_tools_return_a_gap_naming_the_native_tool(self):
        """agent-runtime-tools/S9"""
        session = self.start(env={"HERDR_PANE_ID": "p1", "CLAUDECODE": "1"})
        for name, args in (("spawn", {"kind": "worker", "name": "w", "task": "t", "cwd": "/"}),
                           ("message", {"to": "w", "text": "t"}), ("wait", {"target": "any"}),
                           ("stop", {"name": "w"}), ("collect", {"name": "w"})):
            result, text = self.call(session, name, args)
            self.assertTrue(result["isError"], name)
            self.assertTrue(text.startswith("gap: add "), text)
            self.assertIn("subagent", text)
        self.assertEqual(self.runs(), [])

    def test_S9_native_worker_tools_return_a_gap_naming_the_subagent_tool(self):
        """agent-runtime-tools/S9"""
        session = self.start(env={"HC_TERMINAL_ID": ""})
        result, text = self.call(session, "message", {"to": "w", "text": "t"})
        self.assertTrue(result["isError"])
        self.assertIn("subagent", text)


if __name__ == "__main__":
    unittest.main()
