"""Spawning a worker and launching a profile through a fake hc: the argv each family gets, and
the profile overlay it runs under. These two classes stay in this module because their old path
is the only one the budget grandfathers: the spawn class holds more public methods, and the
profile class one longer test, than a new path could measure. The verbs and the terminal owner
live beside this module."""

import json
import sys
import unittest

from test_mcp_server import SERVER
from tools_harness import (
    PROVIDERS,
    RUNTIME_SRC,
    InProcessToolCase,
    raw,
    scrub,
    unwrap,
)

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import hc_tools

pytestmark = pytest.mark.integration


class SpawnTests(InProcessToolCase):
    ARGS = {"kind": "worker", "name": "w1", "task": "do x", "cwd": "/tmp/c"}

    def test_S2_claude_worker_runs_hc_run_bg_with_task_and_report_line(self):
        """agent-runtime-tools/S2"""
        session = self.start(
            {
                "run": {"out": json.dumps({"first_message": {"sent": True}})},
                "get:w1": {"out": raw("w1", "t0")},
            }
        )
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="claude"))
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("run")
        self.assertEqual(
            argv[:10],
            [
                "run",
                "--bg",
                "--name",
                "w1",
                "--cwd",
                "/tmp/c",
                "--parent",
                "t0",
                "--json",
                "--no-notify",
            ],
        )
        program = unwrap(argv[10:])
        self.assertEqual(program[:3], ["claude", "--permission-mode", "auto"])
        self.assertTrue(program[3].startswith("do x"))
        self.assertIn(
            "When done or stuck, call the agent-runtime report tool with your result.", program[3]
        )
        self.assertNotIn("hc send", program[3])
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

    def test_S1_codex_worker_defaults_to_no_role(self):
        """codex-default-role/S1"""
        (self.dir / "home" / ".codex" / "agents" / "implementer.toml").unlink()
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="codex"))
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("run")
        program = unwrap(argv[10:])
        self.assertEqual(program[:2], ["codex", "--no-daemon"])
        self.assertTrue(program[2].startswith("do x"))
        self.assertNotIn("CODEX IMPLEMENTER RULES", program[2])
        self.assertIn("Worker protocol:", program[2])
        self.assertIn("do x", program[2])
        self.assertIn("call the agent-runtime report tool", program[2])
        self.assertNotIn("permission-mode", " ".join(argv))

    def spawn_argv(self, env=None, **extra):
        session = self.start({"get:w1": {"out": raw("w1", "t0")}}, env=env)
        result, text = self.call(session, "spawn", dict(self.ARGS, **extra))
        self.assertFalse(result["isError"], text)
        return unwrap(self.runs("run")[-1][10:])

    def test_claude_model_flag(self):
        """agent-runtime/R129"""
        argv = self.spawn_argv(cli="claude", model="haiku")
        self.assertEqual(argv[:4], ["claude", "--permission-mode", "auto", "--model"])
        self.assertEqual(argv[4], "haiku")
        self.assertEqual(len(argv), 6)
        self.assertTrue(argv[-1].startswith("do x"))

    def test_codex_model_flag(self):
        """agent-runtime/R130"""
        # A CODEX_HOME without a profile file: the worker profile is not usable, so no -p is passed.
        codex_home = self.dir / "codex-home"
        codex_home.mkdir()
        argv = self.spawn_argv(env={"CODEX_HOME": str(codex_home)}, cli="codex", model="gpt-5.5")
        self.assertEqual(argv[:4], ["codex", "--no-daemon", "--model", "gpt-5.5"])
        self.assertEqual(len(argv), 5)
        self.assertTrue(argv[-1].startswith("do x"))
        self.assertNotIn("permission-mode", " ".join(argv))

    def test_no_model_keeps_argv(self):
        """agent-runtime/R131"""
        claude = self.spawn_argv(cli="claude")
        self.assertEqual(claude[:3], ["claude", "--permission-mode", "auto"])
        self.assertEqual(len(claude), 4)
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
        for model in (
            " haiku",
            "haiku ",
            "  -x",
            "ha iku",
            "ha\niku",
            "ha\x00iku",
            "ha\x7fiku",
            "ha\tiku",
        ):
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
        self.assertEqual(
            argv[:5], ["claude", "--agent", "scene-reviewer", "--permission-mode", "auto"]
        )
        self.assertEqual(len(argv), 6)
        self.assertTrue(argv[-1].startswith("do x"))
        argv = self.spawn_argv(cli="claude", agent="scene-reviewer", model="haiku")
        self.assertEqual(
            argv[:7],
            [
                "claude",
                "--agent",
                "scene-reviewer",
                "--permission-mode",
                "auto",
                "--model",
                "haiku",
            ],
        )
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

    def test_default_roles_and_explicit_codex_implementer(self):
        """agent-runtime/R138"""
        claude = self.spawn_argv(cli="claude")
        self.assertEqual(claude[:3], ["claude", "--permission-mode", "auto"])
        self.assertEqual(len(claude), 4)
        self.assertEqual(claude, self.spawn_argv(cli="claude", agent="none"))
        self.assertEqual(
            self.spawn_argv(cli="claude", agent="implementer")[:3],
            ["claude", "--agent", "implementer"],
        )
        codex = self.spawn_argv(cli="codex")
        self.assertTrue(codex[-1].startswith("do x"))
        self.assertEqual(codex, self.spawn_argv(cli="codex", agent="none"))
        explicit = self.spawn_argv(cli="codex", agent="implementer")
        self.assertTrue(explicit[-1].startswith("CODEX IMPLEMENTER RULES"))
        self.assertEqual(len(codex), 3)

    def test_S13_a_refusal_still_crosses_the_server_process(self):
        """The tool's validation runs in process; one call shows its refusal through the stdio server."""
        session = self.start(process=True)
        result, text = self.call(
            session,
            "spawn",
            {"kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp", "model": "bad model"},
        )
        self.assertTrue(result["isError"], text)
        self.assertEqual(self.runs("run"), [])

    def test_invalid_agent_is_an_error(self):
        """agent-runtime/R139"""
        for agent in (
            "",
            "   ",
            5,
            ["a"],
            True,
            "-x",
            "../x",
            "a/b",
            "a.b",
            "a" * 65,
            " a",
            "a b",
            "a\n",
        ):
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
        result, text = self.call(
            session, "spawn", dict(self.ARGS, cli="codex", agent="plugin:reviewer")
        )
        self.assertTrue(result["isError"])
        self.assertIn(":", text)
        self.assertEqual(len(self.runs("run")), 1)  # only the Claude spawn above

    def test_codex_agent_file_missing_or_without_instructions(self):
        """agent-runtime/R141"""
        for body in (
            None,
            'name = "reviewer"\n',
            "developer_instructions = 5\n",
            'developer_instructions = ""\n',
            'developer_instructions = "  \\n "\n',
        ):
            with self.subTest(body=body):
                if body is not None:
                    self.write_agent("reviewer", body)
                session = self.start()
                result, text = self.call(
                    session, "spawn", dict(self.ARGS, cli="codex", agent="reviewer")
                )
                self.assertTrue(result["isError"])
                self.assertIn("reviewer.toml", text)
                self.assertEqual(self.runs("run"), [])

    def test_codex_agent_none_reads_no_role_file(self):
        """agent-runtime/R157"""
        argv = self.spawn_argv(cli="codex", agent="none")
        self.assertEqual(argv[:2], ["codex", "--no-daemon"])
        self.assertEqual(len(argv), 3)
        self.assertTrue(argv[-1].startswith("do x"))
        self.assertNotIn("CODEX IMPLEMENTER RULES", argv[-1])

    def test_claude_agent_none_drops_the_agent_flag(self):
        """agent-runtime/R158"""
        argv = self.spawn_argv(cli="claude", agent="none")
        self.assertEqual(argv[:3], ["claude", "--permission-mode", "auto"])
        self.assertEqual(len(argv), 4)
        self.assertTrue(argv[-1].startswith("do x"))

    def test_agent_none_prompt_carries_the_worker_protocol(self):
        """agent-runtime/R159"""
        for cli in ("claude", "codex"):
            with self.subTest(cli=cli):
                prompt = self.spawn_argv(cli=cli, agent="none")[-1]
                self.assertIn("report(text)", prompt)
                self.assertNotIn("assignment_id", prompt)
                self.assertIn("never reaches the parent", prompt)
                self.assertIn("STATUS: COMPLETE | BLOCKED | PARTIAL", prompt)

    def test_codex_instructions_file_sets_the_preamble(self):
        """agent-runtime/R160"""
        home = self.dir / "home"
        (home / "role.md").write_text("ROLE FILE RULES\n")
        for path in (str(home / "role.md"), "~/role.md"):
            with self.subTest(path=path):
                argv = self.spawn_argv(cli="codex", instructions_file=path)
                self.assertEqual(argv[:2], ["codex", "--no-daemon"])
                self.assertTrue(argv[-1].startswith("ROLE FILE RULES"))
                self.assertIn("do x", argv[-1])
                self.assertNotIn("CODEX IMPLEMENTER RULES", argv[-1])

    def write_role(self, name="implementer", body="OWN THE WORK\n"):
        """A Claude role file: the user's own roles live there, front matter and all."""
        roles = self.dir / "home" / ".claude" / "agents"
        roles.mkdir(parents=True, exist_ok=True)
        path = roles / ("%s.md" % name)
        path.write_text(
            "---\nname: %s\ntools: [Read, Write]\n---\n\n# %s\n\n%s" % (name, name, body),
            encoding="utf-8",
        )
        return path

    def test_S5_a_pi_worker_receives_the_role_content(self):
        """agent-runtime/R167: orchestrator-routing-effort-role/S5 — Pi has no role file, so the role's content travels in the task."""
        self.write_role()
        prompt = self.spawn_argv(cli="pi", agent="implementer")[-1]
        self.assertIn("OWN THE WORK", prompt)
        self.assertIn("Role: implementer", prompt)
        self.assertIn("do x", prompt)
        self.assertIn("Worker protocol:", prompt)
        self.assertNotIn("tools: [Read, Write]", prompt)

    def test_S5_a_pi_worker_without_a_role_gets_no_role_text(self):
        """agent-runtime/R167: orchestrator-routing-effort-role/S5 — no role, no role text: only the protocol travels."""
        self.write_role()
        prompt = self.spawn_argv(cli="pi", agent="none")[-1]
        self.assertNotIn("OWN THE WORK", prompt)
        self.assertNotIn("Role:", prompt)
        self.assertIn("Worker protocol:", prompt)

    def test_S7_a_missing_role_file_is_refused_and_nothing_starts(self):
        """agent-runtime/R169: orchestrator-routing-effort-role/S7 — the refusal names the file, and no worker is launched."""
        session = self.start()
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="pi", agent="implementer"))
        self.assertTrue(result["isError"])
        self.assertIn("implementer.md", text)
        self.assertEqual(self.runs("run"), [])

    def test_S7_an_empty_role_file_is_refused_and_nothing_starts(self):
        """agent-runtime/R169: orchestrator-routing-effort-role/S7 — a role file with no content is a refusal too."""
        roles = self.dir / "home" / ".claude" / "agents"
        roles.mkdir(parents=True, exist_ok=True)
        (roles / "implementer.md").write_text("---\nname: implementer\n---\n", encoding="utf-8")
        session = self.start()
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="pi", agent="implementer"))
        self.assertTrue(result["isError"])
        self.assertIn("implementer.md", text)
        self.assertEqual(self.runs("run"), [])

    def test_S7_an_unreadable_role_file_is_refused_and_nothing_starts(self):
        """agent-runtime/R169: orchestrator-routing-effort-role/S7 — a role file that cannot be read is a refusal, named."""
        roles = self.dir / "home" / ".claude" / "agents"
        roles.mkdir(parents=True, exist_ok=True)
        (roles / "implementer.md").mkdir()
        session = self.start()
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="pi", agent="implementer"))
        self.assertTrue(result["isError"])
        self.assertIn("implementer.md", text)
        self.assertEqual(self.runs("run"), [])

    def test_S6_a_pi_worker_reads_the_instructions_file_it_is_given(self):
        """agent-runtime/R168: orchestrator-routing-effort-role/S6 — a backend with no role file of its own reads the file the caller names."""
        home = self.dir / "home"
        (home / "role.md").write_text("ROLE FILE RULES\n")
        argv = self.spawn_argv(cli="pi", instructions_file=str(home / "role.md"))
        self.assertTrue(argv[-1].startswith("ROLE FILE RULES"))
        self.assertIn("do x", argv[-1])
        self.assertIn("Worker protocol:", argv[-1])

    def test_S11_every_worker_is_spawned_interactive(self):
        """agent-runtime/R170: orchestrator-routing-effort-role/S11 — the worker stays in its pane: the runtime resumes that pane for the next round."""
        forbidden = {"claude": ("-p", "--print"), "codex": ("exec",), "pi": ("--print", "-p")}
        for cli, flags in forbidden.items():
            with self.subTest(cli=cli):
                argv = self.spawn_argv(cli=cli, agent="none")
                for flag in flags:
                    self.assertNotIn(flag, argv[:-1])

    def test_S12_a_pi_worker_is_launched_with_approve(self):
        """agent-runtime/R171: orchestrator-routing-effort-role/S12 — Pi asks for trust in a folder it does not know, and nobody answers in a pane nobody watches."""
        argv = self.spawn_argv(cli="pi", agent="none")
        self.assertEqual(argv[0], "pi")
        self.assertIn("--approve", argv)
        self.assertNotIn("--approve", argv[-1])

    def test_dash_model_is_an_error(self):
        """agent-runtime/R134"""
        for cli in ("claude", "codex"):
            with self.subTest(cli=cli):
                session = self.start()
                result, _ = self.call(
                    session, "spawn", dict(self.ARGS, cli=cli, model="--dangerous")
                )
                self.assertTrue(result["isError"])
                self.assertEqual(self.runs("run"), [])

    def test_claude_effort_flag(self):
        """agent-runtime/R147"""
        argv = self.spawn_argv(cli="claude", model="haiku", effort="low")
        self.assertEqual(
            argv[:7], ["claude", "--permission-mode", "auto", "--model", "haiku", "--effort", "low"]
        )
        self.assertEqual(len(argv), 8)

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


class ProfileTests(InProcessToolCase):
    ARGS = {"kind": "worker", "name": "w1", "task": "profile task", "cwd": "/tmp"}
    PROMPT = (
        "profile task\n\nWhen done or stuck, call the agent-runtime report tool with your result."
    )
    BUILTIN_PROGRAMS = {
        "claude": [
            "claude",
            "--permission-mode",
            "auto",
            PROMPT + "\n\n" + hc_tools.LEGACY_WORKER_PROTOCOL,
        ],
        "codex": ["codex", "--no-daemon", PROMPT + "\n\n" + hc_tools.LEGACY_WORKER_PROTOCOL],
    }

    def config(self, profiles):
        path = self.dir / "home" / ".config" / "agent-runtime" / "profiles.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"profiles": profiles}))
        return path

    def test_S26_exec_failure_never_echoes_expanded_secret(self):
        """S26: exec errors name the program without leaking a path containing a credential."""
        self.config(
            {"custom": {"cli": "claude", "env": {"ANTHROPIC_AUTH_TOKEN": "$PROFILE_TEST_SECRET"}}}
        )
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
        providers = [
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_MODEL",
            "ANTHROPIC_DEFAULT_SONNET_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        ]
        self.config({"custom": {"cli": "claude", "env": {"ANTHROPIC_MODEL": "profile-model"}}})
        program = self.dir / "bin" / "claude"
        program.write_text(
            "#!%s\nimport json, os\nprint(json.dumps({k: os.environ[k] for k in %r if k in os.environ}))\n"
            % (sys.executable, providers)
        )
        program.chmod(0o755)
        session = self.start(
            {
                "execute_profile": True,
                "login_env": dict.fromkeys(providers, "inherited"),
                "get:w1": {"out": raw("w1", "t0")},
            }
        )
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(
            json.loads((self.dir / "overlay").read_text()), {"ANTHROPIC_MODEL": "profile-model"}
        )

    def test_S18_private_references_and_failures_within_startup_window(self):
        """S18: references stay private; diagnostics and exits inside the startup window fail launch."""
        path = self.config(
            {"custom": {"cli": "claude", "env": {"ANTHROPIC_AUTH_TOKEN": "$ANTHROPIC_API_KEY"}}}
        )
        program = self.dir / "bin" / "claude"
        program.write_text(
            "#!%s\nimport hashlib, os\nassert 'ANTHROPIC_API_KEY' not in os.environ\nassert hashlib.sha256(os.environ['ANTHROPIC_AUTH_TOKEN'].encode()).hexdigest() == os.environ['PROFILE_TEST_DIGEST']\nprint('reference-ok')\n"
            % sys.executable
        )
        program.chmod(0o755)
        session = self.start(
            {"execute_profile": True, "live": [], "get:w1": {"out": raw("w1", "t0")}},
            {"ANTHROPIC_API_KEY": "server-value-must-not-be-used", "AGENT_RUNTIME_DISPATCH": "1"},
        )
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(
            json.loads((self.dir / "overlay.audit").read_text()),
            {"argv": False, "config": False, "overlay": False},
        )
        self.assertEqual((self.dir / "overlay").read_text(), "reference-ok\n")
        self.assertNotIn("server-value-must-not-be-used", self.log.read_text())
        self.assertIn("$ANTHROPIC_API_KEY", path.read_text())
        self.call(session, "stop", {"name": "w1"})
        replies = json.loads((self.dir / "replies").read_text())
        replies["live"] = []
        (self.dir / "replies").write_text(json.dumps(replies))
        self.config(
            {
                "custom": {
                    "cli": "claude",
                    "env": {"ANTHROPIC_AUTH_TOKEN": "$MISSING_PROFILE_SECRET"},
                }
            }
        )
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
                from agent_runtime.dispatch_store import Store

                with Store({"HOME": str(self.dir / "home")}).transaction() as db:
                    self.assertEqual(
                        db.execute(
                            "SELECT state FROM assignments ORDER BY rowid DESC LIMIT 1"
                        ).fetchone()[0],
                        "failed",
                    )
                    self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [0])
        # Exits observed inside the startup window are failures even without a diagnostic.
        for state in ("finished", "exited"):
            with self.subTest(state=state):
                replies = json.loads((self.dir / "replies").read_text())
                replies.update(execute_profile=False, profile_ready_at=0)
                replies["get:w1"] = {
                    "out": json.dumps({"address": "t9", "parent": "t0", "state": state})
                }
                replies["read:w1"] = {"out": ""}
                (self.dir / "replies").write_text(json.dumps(replies))
                result, text = self.call(session, "spawn", dict(self.ARGS, cli="custom"))
                self.assertTrue(result["isError"])
                self.assertIn("w1", text)
                self.assertIn("launcher exited", text)
                self.assertEqual(json.loads((self.dir / "replies").read_text())["live"], [])
                with Store({"HOME": str(self.dir / "home")}).transaction() as db:
                    self.assertEqual(
                        db.execute(
                            "SELECT state FROM assignments ORDER BY rowid DESC LIMIT 1"
                        ).fetchone()[0],
                        "failed",
                    )
                    self.assertEqual([r[0] for r in db.execute("SELECT active FROM workers")], [0])

    def test_S22_override_replaces_default_config_for_schema_and_launcher(self):
        """S22: AGENT_RUNTIME_PROFILES selects a different file for listing and launching."""
        self.config({"ignored": {"cli": "claude", "env": {}}})
        override = self.dir / "home" / "alternate.json"
        override.write_text(
            json.dumps(
                {"profiles": {"selected": {"cli": "claude", "env": {"PROFILE_MARKER": "selected"}}}}
            )
        )
        program = self.dir / "bin" / "claude"
        program.write_text(
            "#!%s\nimport os\nprint(os.environ['PROFILE_MARKER'])\n" % sys.executable
        )
        program.chmod(0o755)
        session = self.start(
            {
                "execute_profile": True,
                "get:w1": {"out": raw("w1", "t0")},
                "login_env": {
                    "AGENT_RUNTIME_PROFILES": str(self.dir / "wrong.json"),
                    "XDG_CONFIG_HOME": "/wrong",
                },
            },
            {"AGENT_RUNTIME_PROFILES": "~/alternate.json"},
        )
        tools = session.send("tools/list")["result"]["tools"]
        self.assertEqual(
            tools[0]["inputSchema"]["properties"]["cli"]["enum"],
            ["claude", "codex", "pi", "selected"],
        )
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="selected"))
        self.assertFalse(result["isError"], text)
        self.assertEqual((self.dir / "overlay").read_text(), "selected\n")

    def test_S21_absent_config_keeps_builtins_and_original_argv(self):
        """S21: removing config restores exactly the original built-in schema and commands."""
        path = self.config({"custom": {"cli": "claude", "env": {}}})
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        self.assertIn(
            "custom",
            session.send("tools/list")["result"]["tools"][0]["inputSchema"]["properties"]["cli"][
                "enum"
            ],
        )
        path.unlink()
        tools = session.send("tools/list")["result"]["tools"]
        self.assertEqual(
            tools[0]["inputSchema"]["properties"]["cli"]["enum"], ["claude", "codex", "pi"]
        )
        for family, program in self.BUILTIN_PROGRAMS.items():
            result, text = self.call(session, "spawn", dict(self.ARGS, cli=family))
            self.assertFalse(result["isError"], text)
            self.assertEqual(
                self.runs("run")[-1],
                [
                    "run",
                    "--bg",
                    "--name",
                    "w1",
                    "--cwd",
                    "/tmp",
                    "--parent",
                    "t0",
                    "--json",
                    "--no-notify",
                ]
                + scrub(PROVIDERS)
                + program,
            )

    MINIMAX = {
        "cli": "claude",
        "env": {
            "ANTHROPIC_BASE_URL": "https://provider.example/api",
            "ANTHROPIC_MODEL": "provider-model",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "512000",
        },
    }
    WATCHED = PROVIDERS + ["CLAUDE_CODE_MAX_CONTEXT_TOKENS"]

    def env_printer(self, name):
        program = self.dir / "bin" / name
        program.write_text(
            "#!%s\nimport json, os\nprint(json.dumps({k: os.environ[k] for k in %r if k in os.environ}))\n"
            % (sys.executable, self.WATCHED)
        )
        program.chmod(0o755)

    def overlays(self):
        return [json.loads(line) for line in (self.dir / "overlay").read_text().splitlines()]

    def test_builtin_spawn_inherits_no_provider_or_profile_variable(self):
        """#35: built-in workers run on the CLI's own login, whatever the lead, pane or an earlier profile left."""
        self.config({"minimax": self.MINIMAX})
        for family in ("claude", "codex"):
            self.env_printer(family)
        leaked = {key: "inherited" for key in self.WATCHED if key != "ANTHROPIC_BASE_URL"}
        session = self.start(
            {"execute_profile": True, "login_env": leaked, "get:w1": {"out": raw("w1", "t0")}},
            {"ANTHROPIC_BASE_URL": "lead-value"},
        )
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
        session = self.start(
            {
                "execute_profile": True,
                "login_env": dict.fromkeys(PROVIDERS, "inherited"),
                "get:w1": {"out": raw("w1", "t0")},
            }
        )
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="claude"))
        self.assertFalse(result["isError"], text)
        self.assertEqual(self.overlays(), [{}])

    def test_profile_gets_exactly_its_own_values(self):
        """#35: another profile's overlay keys never reach a profile worker."""
        self.config(
            {
                "minimax": self.MINIMAX,
                "other": {"cli": "claude", "env": {"ANTHROPIC_MODEL": "other-model"}},
            }
        )
        self.env_printer("claude")
        session = self.start(
            {
                "execute_profile": True,
                "get:w1": {"out": raw("w1", "t0")},
                "login_env": {key: "inherited" for key in self.WATCHED},
            }
        )
        for profile in ("other", "minimax"):
            result, text = self.call(session, "spawn", dict(self.ARGS, cli=profile))
            self.assertFalse(result["isError"], text)
        self.assertEqual(self.overlays(), [{"ANTHROPIC_MODEL": "other-model"}, self.MINIMAX["env"]])

    def test_S17_profiles_wrap_each_family_with_prompt_last(self):
        """S17: configured names launch the family's standard argv through the installed shim."""
        config = self.config(
            {
                "custom-claude": {"cli": "claude", "env": {}},
                "custom-codex": {"cli": "codex", "env": {}},
            }
        )
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        for family, builtin in self.BUILTIN_PROGRAMS.items():
            with self.subTest(family=family):
                result, text = self.call(
                    session, "spawn", dict(self.ARGS, cli="custom-" + family, model="sample")
                )
                self.assertFalse(result["isError"], text)
                argv = self.runs("run")[-1][10:]
                self.assertEqual(
                    argv,
                    [
                        str(SERVER.parent.parent / "bin" / "agent-runtime-profile"),
                        "custom-" + family,
                        str(config),
                        "--",
                    ]
                    + builtin[:-1]
                    + ["--model", "sample"]
                    + builtin[-1:],
                )

    def test_profile_permission_mode_and_settings_replace_auto(self):
        """agent-runtime/R143 agent-runtime/R144"""
        settings = self.dir / "home" / "worker-settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text("{}")
        config = self.config(
            {
                "plain": {"cli": "claude", "env": {}},
                "edits": {
                    "cli": "claude",
                    "env": {},
                    "permission_mode": "acceptEdits",
                    "settings": "~/worker-settings.json",
                },
            }
        )
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        launcher = str(SERVER.parent.parent / "bin" / "agent-runtime-profile")
        builtin = self.BUILTIN_PROGRAMS["claude"]
        expected = {
            "plain": builtin,
            "edits": [
                "claude",
                "--permission-mode",
                "acceptEdits",
                "--settings",
                str(settings),
                self.PROMPT + "\n\n" + hc_tools.LEGACY_WORKER_PROTOCOL,
            ],
        }
        for profile, program in expected.items():
            with self.subTest(profile=profile):
                result, text = self.call(session, "spawn", dict(self.ARGS, cli=profile))
                self.assertFalse(result["isError"], text)
                self.assertEqual(
                    self.runs("run")[-1][10:], [launcher, profile, str(config), "--"] + program
                )

    def test_missing_settings_file_is_refused_before_hc_run(self):
        """agent-runtime/R146"""
        self.config(
            {"edits": {"cli": "claude", "env": {}, "settings": "/nonexistent/worker-settings.json"}}
        )
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="edits"))
        self.assertTrue(result["isError"])
        self.assertIn("settings file /nonexistent/worker-settings.json does not exist", text)
        self.assertEqual(self.runs("run"), [])


if __name__ == "__main__":
    unittest.main()
