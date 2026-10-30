import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
HOOKS = SKILL / "hooks"
REPO = SKILL.parents[2]  # .../agents
SUFFIX = " · run agent-runtime contract before delegating"


def run_hook(name, payload, args=(), env_extra=None, hc_reply='{"parent": null}'):
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "hc"
        fake.write_text("#!/bin/sh\necho '%s'\n" % hc_reply)
        fake.chmod(0o755)
        env = {"PATH": tmp + ":/usr/bin:/bin", "HOME": tmp}
        env.update(env_extra or {})
        return subprocess.run([sys.executable, "-B", str(HOOKS / name), *args],
                              input=json.dumps(payload), capture_output=True, text=True,
                              env=env, timeout=20)


def bash(command, **extra):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, **extra}


def decision(result):
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]


class SessionStartTests(unittest.TestCase):
    def test_S16_claude_session_context_has_pointer_line(self):
        r = run_hook("session_start.py", {"hook_event_name": "SessionStart"}, ["--cli", "claude"],
                     {"HC_TERMINAL_ID": "w1"}, '{"parent": "lead1"}')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(),
                         "agent-runtime: runtime=hc role=worker parent=lead1" + SUFFIX)

    def test_S16_parent_dash_when_none(self):
        r = run_hook("session_start.py", {}, ["--cli", "claude"])
        self.assertEqual(r.stdout.strip(),
                         "agent-runtime: runtime=native role=lead parent=-" + SUFFIX)

    def test_S17_codex_session_context_has_same_line(self):
        r = run_hook("session_start.py", {"hook_event_name": "SessionStart", "source": "startup"},
                     ["--cli", "codex"], {"HERDR_PANE_ID": "p1"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(),
                         "agent-runtime: runtime=native role=lead parent=-" + SUFFIX)

    def test_S16_bad_stdin_still_exits_zero(self):
        r = subprocess.run([sys.executable, "-B", str(HOOKS / "session_start.py"), "--cli", "claude"],
                           input="not json", capture_output=True, text=True, timeout=20,
                           env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 0)


class SubagentStartTests(unittest.TestCase):
    def test_S18_subagent_told_not_to_run_runtime_commands(self):
        r = run_hook("subagent_start.py", {"hook_event_name": "SubagentStart", "agent_id": "a1"})
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "SubagentStart")
        ctx = out["additionalContext"]
        self.assertEqual(ctx, "Subagents and in-process teammates run only read-only hc commands "
                              "(get, ls, read, wait, file read…) and never herdmates or the contract's "
                              "spawn/message/stop verbs; report through your return value or "
                              "SendMessage.")
        contract = (SKILL / "references/contract.md").read_text()
        self.assertIn(ctx, contract)


class PreToolUseTests(unittest.TestCase):
    def assertDenied(self, command, **extra):
        r = run_hook("pre_tool_use.py", bash(command, **extra))
        self.assertEqual(r.returncode, 0)
        out = decision(r)
        self.assertIsNotNone(out, command)
        self.assertEqual(out["hookEventName"], "PreToolUse")
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertTrue(out["permissionDecisionReason"])

    def assertAllowed(self, command, **extra):
        r = run_hook("pre_tool_use.py", bash(command, **extra))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "", command)

    def test_S19_subagent_non_readonly_hc_denied(self):
        for command in ("hc run claude", "hc send t1 hi", "hc kill t1", "hc keys t7 enter",
                        "cd x && hc send t1 hi", "echo a; hc kill t1", "echo a | hc run x",
                        "FOO=1 hc send t1 hi", "~/.local/bin/hc send t1 hi",
                        "/Users/x/.local/bin/hc run c", "(hc kill t1)", "  hc   send t1 hi",
                        "hc --json send t7 x", "hc file write t7 x", "hc file edit f --old a --new b", "hc file",
                        "hc --json file write f", "hc send t7 -h", "hc -x --json keys t7 enter",
                        "if x; then hc send t7 x; fi", "for i in 1; do hc send t7 x; done",
                        "{ hc send t7 x; }", "! hc send t7 x", "timeout 5 hc send t7 x",
                        "timeout -s KILL 5 hc kill t7", "nice hc send t7 x", "nice -n 5 hc send t7 x",
                        "xargs hc send", "cd /repo\nhc send t7 \"done\"", "hc ls\nhc keys t7 enter",
                        "echo a\n\nhc kill t7", "hc exec hc send t7 x", "hc exec -- hc kill t7",
                        'eval "hc send t7 x"', "eval hc send t7 x", "stdbuf -oL hc send t7 x",
                        "watch -n 5 hc send t7 x", "echo t7 | xargs -n1 hc kill", "env A=1 hc send t7 x",
                        'bash -lc "hc send t7 x"', 'sh -ec "hc send t7 x"', "bash -c 'cd x && hc kill t7'"):
            with self.subTest(command=command):
                self.assertDenied(command, agent_id="a1")

    def test_S19_subagent_readonly_hc_allowed(self):
        for command in ("hc ls", "hc get t7", "hc --json read t9", "hc wait t7", "hc templates",
                        "hc events", "hc file read t7", "hc --json file read f", "hc file --help", "hc exec ls", "hc exec echo hi", "hc schema", "hc --help",
                        "hc -h", "hc --version", "hc keys --help", "hc send -h", "hc kill --help",
                        'echo "a\nhc send t7 x"', 'git commit -m "x\nhc kill t7"', 'echo "hc send"',
                        "git commit -m 'hc send'", "grep 'hc kill' file", "ls",
                        'bash -lc "hc get t7"'):
            with self.subTest(command=command):
                self.assertAllowed(command, agent_id="a1")

    def test_S20_teammux_launch_denied_for_everyone(self):
        self.assertDenied("herdmates teammux-launch foo")
        self.assertDenied("cd x && herdmates teammux-launch", agent_id="a1")
        self.assertAllowed('echo "herdmates teammux-launch"')

    def test_S21_lead_hc_send_allowed(self):
        for command in ("hc send t1 hi", "hc run claude", "hc kill t1"):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_S21_agent_type_without_agent_id_is_a_lead(self):
        self.assertAllowed("hc send t7 done", agent_type="implementer")

    def test_S21_internal_error_allows(self):
        r = subprocess.run([sys.executable, "-B", str(HOOKS / "pre_tool_use.py")],
                           input="garbage", capture_output=True, text=True, timeout=20)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))


class WorkerGuardTests(unittest.TestCase):
    """An hc worker (no agent_id, HC_TERMINAL_ID set, non-null parent) vs its parent."""

    TERMINALS = {"t9": {"address": "t9", "parent": "t1"}, "t1": {"address": "t1", "name": "boss", "parent": None},
                 "lead": {"address": "t1", "parent": None},
                 "t12": {"address": "t12", "parent": "t9"}, "kid": {"address": "t12", "parent": "t9"},
                 "t10": {"address": "t10", "parent": "t1"}, "sib": {"address": "t10", "parent": "t1"}}

    def call(self, command, caller="t9", terminals=None, payload=None, args=(), **extra):
        terminals = self.TERMINALS if terminals is None else terminals
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            for name, reply in terminals.items():
                (data / name.replace("/", "_")).write_text(json.dumps(reply))
            fake = Path(tmp) / "bin" / "hc"
            fake.parent.mkdir()
            fake.write_text('#!/bin/sh\n[ "$1" = get ] || exit 2\n'
                            'f="%s/$(echo "$2" | tr / _)"\n[ -f "$f" ] && cat "$f" || exit 1\n' % data)
            fake.chmod(0o755)
            env = {"PATH": "%s:/usr/bin:/bin" % fake.parent, "HOME": tmp}
            if caller:
                env["HC_TERMINAL_ID"] = caller
            return subprocess.run([sys.executable, "-B", str(HOOKS / "pre_tool_use.py"), *args],
                                  input=json.dumps(payload or bash(command, **extra)), capture_output=True,
                                  text=True, env=env, timeout=20)

    def assertDenied(self, command, **kw):
        r = self.call(command, **kw)
        self.assertEqual(r.returncode, 0)
        out = decision(r)
        self.assertIsNotNone(out, command)
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertRegex(out["permissionDecisionReason"], "parent|literal")

    def assertAllowed(self, command, **kw):
        r = self.call(command, **kw)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""), command)

    def test_S28_worker_destructive_calls_at_parent_denied(self):
        for command in ("hc keys t1 enter", "hc interrupt t1", "hc kill t1", "hc send --force t1 x",
                        "hc send t1 x --force", "hc --json keys t1 enter", "hc keys lead enter",
                        "hc kill lead", "echo a\nhc keys t1 enter", "cd x\n\nhc kill t1",
                        "hc keys $P enter", "hc kill `cat p`", 'hc send --force "$PARENT" x',
                        "hc exec hc keys t1 enter", 'eval "hc keys t1 enter"', "hc send --if idle --force lead x",
                        "cd x && hc interrupt t1", 'bash -lc "hc keys t1 enter"'):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_S28_unknown_option_before_target_denied_when_parent_named(self):
        for command in ("hc keys --on m t1 enter", "hc keys --on m boss enter", "hc kill --zzz boss",
                        "hc interrupt --timeout 5 t1", "hc send --if idle --force t1 x",
                        "hc send --mystery v --force boss x"):
            with self.subTest(command=command):
                self.assertDenied(command)

    def test_S28_unknown_option_before_target_allowed_when_parent_absent(self):
        for command in ("hc keys --on m t12 enter", "hc keys --on m ghost enter",
                        "hc kill --zzz kid", "hc send --mystery v --force t10 x",
                        "hc interrupt --timeout 5 t12", "hc send --if idle --force t12 x"):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_S28_literal_parent_address_denied_when_target_unresolvable(self):
        terminals = {"t9": self.TERMINALS["t9"]}
        self.assertDenied("hc keys t1 enter", terminals=terminals)

    def test_S29_worker_sends_result_to_parent(self):
        for command in ("hc send t1 'done: all green'", "hc send lead 'done'",
                        "hc send --if idle --wait t1 x", 'echo "a\nhc keys t1 enter"', "hc get t1", "hc read t1", "hc ls"):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_S29_worker_commands_at_child_or_sibling_allowed(self):
        for command in ("hc keys t12 enter", "hc kill t12", "hc interrupt kid", "hc send --force t12 x",
                        "hc keys t10 enter", "hc kill sib", "hc send --force sib x"):
            with self.subTest(command=command):
                self.assertAllowed(command)

    def test_S29_fail_open_when_resolution_fails(self):
        self.assertAllowed("hc keys ghost enter")
        self.assertAllowed("hc keys t1 enter", terminals={})  # caller unreadable
        self.assertAllowed("hc interrupt")  # no target

    def test_S29_leads_and_non_hc_sessions_unrestricted(self):
        self.assertAllowed("hc keys t9 enter", caller="t1")
        self.assertAllowed("hc kill t1", caller=None)
        self.assertAllowed("hc kill t1", agent_type="implementer", caller="t1")


def codex_bash(command):
    return {"session_id": "s1", "turn_id": "u1", "transcript_path": None, "cwd": "/repo",
            "hook_event_name": "PreToolUse", "model": "gpt-5.5", "permission_mode": "default",
            "tool_name": "Bash", "tool_use_id": "call_1", "tool_input": {"command": command}}


class CodexWorkerGuardTests(WorkerGuardTests):
    """Codex PreToolUse payloads (no agent_id marker): only the worker-to-parent rule applies."""

    def call(self, command, payload=None, **kw):
        return super().call(command, payload=payload or codex_bash(command),
                            args=("--cli", "codex"), **kw)

    test_S28_worker_destructive_calls_at_parent_denied = None  # replaced below
    test_S28_literal_parent_address_denied_when_target_unresolvable = None
    test_S29_worker_sends_result_to_parent = None
    test_S29_worker_commands_at_child_or_sibling_allowed = None
    test_S29_fail_open_when_resolution_fails = None
    test_S29_leads_and_non_hc_sessions_unrestricted = None

    def test_S31_codex_worker_destructive_calls_at_parent_denied(self):
        for command in ("hc keys t1 enter", "hc interrupt t1", "hc kill lead", "hc send --force t1 x",
                        "echo a\nhc keys t1 enter", "hc keys $P enter"):
            with self.subTest(command=command):
                self.assertDenied(command)
        r = self.call("hc kill t1")
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"], "PreToolUse")

    def test_S31_codex_worker_allowed_calls(self):
        for command in ("hc send t1 'done'", "hc send lead 'done'", "hc keys t12 enter",
                        "hc kill sib", "hc get t1", "ls"):
            with self.subTest(command=command):
                self.assertAllowed(command)
        self.assertAllowed("hc keys t1 enter", caller="t1")  # lead
        self.assertAllowed("hc keys t1 enter", caller=None)

    def test_S31_codex_fail_open_when_resolution_fails(self):
        self.assertAllowed("hc keys ghost enter")
        self.assertAllowed("hc keys t1 enter", terminals={})  # caller unreadable
        self.assertAllowed("hc interrupt")  # no target

    def test_S31_codex_literal_parent_denied_when_target_unresolvable(self):
        self.assertDenied("hc keys t1 enter", terminals={"t9": self.TERMINALS["t9"]})

    def test_S31_codex_non_bash_tools_ignored(self):
        payload = codex_bash("hc keys t1 enter")
        payload["tool_name"] = "apply_patch"
        self.assertAllowed("", payload=payload)

    def test_S31_codex_config_has_pre_tool_use_hook(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib unavailable on this python; config.toml not parsed")
        config = tomllib.loads((REPO / ".codex/config.toml").read_text())
        groups = [g for g in config["hooks"]["PreToolUse"]
                  if any("agent-runtime/hooks/pre_tool_use.py" in h["command"] for h in g["hooks"])]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], "^Bash$")
        hook = groups[0]["hooks"][0]
        self.assertIn("--cli codex", hook["command"])
        self.assertEqual(hook["timeout"], 5)
        script = hook["command"].split()[0].strip("'\"").replace("$HOME/", "")
        self.assertTrue((REPO / script).exists())


class WiringTests(unittest.TestCase):
    NEW = {"SessionStart": "session_start.py", "SubagentStart": "subagent_start.py",
           "PreToolUse": "pre_tool_use.py"}

    def test_S16_hook_scripts_executable(self):
        for name in self.NEW.values():
            self.assertTrue(os.access(HOOKS / name, os.X_OK), name)

    def test_S16_settings_json_has_new_entries_pointing_at_existing_files(self):
        settings = json.loads((REPO / ".claude/settings.json").read_text())
        for event, script in self.NEW.items():
            commands = [h["command"] for g in settings["hooks"][event] for h in g["hooks"]]
            match = [c for c in commands if c.split()[0].endswith("agent-runtime/hooks/" + script)]
            self.assertEqual(len(match), 1, event)
            path = match[0].split()[0].replace('"', "").replace("$HOME/", "")
            self.assertTrue((REPO / path).exists(), path)
        self.assertIn("--cli claude", [c for g in settings["hooks"]["SessionStart"]
                                       for h in g["hooks"] for c in [h["command"]]
                                       if "agent-runtime" in c][0])
        pre = [g for g in settings["hooks"]["PreToolUse"]
               if any("agent-runtime" in h["command"] for h in g["hooks"])]
        self.assertEqual(pre[0]["matcher"], "Bash")
        # pre-existing entries are still present (by command substring; files need not exist here)
        others = [h["command"] for event in ("SessionStart", "SessionEnd", "PreCompact", "UserPromptSubmit")
                  for g in settings["hooks"][event] for h in g["hooks"]
                  if "agent-runtime" not in h["command"]]
        self.assertGreaterEqual(len(others), 5)
        self.assertTrue(any("herdr-agent-state" in c for c in others))
        self.assertTrue(any("session_start.py" in c for c in others))

    def test_S17_codex_config_has_session_start_hook(self):
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib unavailable on this python; config.toml not parsed")
        config = tomllib.loads((REPO / ".codex/config.toml").read_text())
        hooks = [h for g in config["hooks"]["SessionStart"] for h in g["hooks"]]
        match = [h for h in hooks if "agent-runtime/hooks/session_start.py" in h["command"]]
        self.assertEqual(len(match), 1)
        self.assertIn("--cli codex", match[0]["command"])
        self.assertEqual(match[0]["type"], "command")


if __name__ == "__main__":
    unittest.main()
