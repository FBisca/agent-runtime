"""Plugin hooks driven as subprocesses with real payloads, plus settings.json and hooks.json wiring."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
SETTINGS = PLUGIN.parents[1] / ".claude" / "settings.json"
PRE = HOOKS / "pre_tool_use.py"
START = HOOKS / "subagent_start.py"
TOOL = "mcp__plugin_agent-runtime_agent-runtime__"
DENY_TEXT = "use the agent-runtime tools"


def run(script, payload, *args):
    done = subprocess.run([sys.executable, "-B", str(script), *args], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=20)
    return json.loads(done.stdout) if done.stdout.strip() else None


def bash(command, **extra):
    return {"tool_name": "Bash", "tool_input": {"command": command}, **extra}


def denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


class SubagentToolTests(unittest.TestCase):
    def test_S16_subagent_is_denied_the_five_mutating_tools(self):
        """agent-runtime-tools/S16"""
        for name in ("spawn", "message", "resume", "stop", "report"):
            out = run(PRE, {"tool_name": TOOL + name, "tool_input": {}, "agent_id": "sub-1"})
            self.assertTrue(denied(out), name)

    def test_S16_main_agent_may_call_mutating_tools_and_subagent_may_read(self):
        """agent-runtime-tools/S16"""
        self.assertIsNone(run(PRE, {"tool_name": TOOL + "spawn", "tool_input": {}}))
        for name in ("wait", "status", "list", "collect"):
            self.assertIsNone(run(PRE, {"tool_name": TOOL + name, "tool_input": {}, "agent_id": "sub-1"}), name)

    def test_S16_subagent_start_notice_names_the_rule(self):
        """agent-runtime-tools/S16"""
        out = run(START, {})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertIn("spawn/message/resume/stop/report", ctx)
        self.assertIn("return value", ctx)


CODEX = "mcp__agent-runtime__"


class NativeChildGuardTests(unittest.TestCase):
    def test_S15_native_children_cannot_mutate_tasks_or_ack_receipts_on_either_client(self):
        """agent-runtime-dispatch/S15"""
        for prefix, extra in ((TOOL, ()), (CODEX, ("--cli", "codex"))):
            def child(name, arguments=None):
                return run(PRE, {"tool_name": prefix + name, "tool_input": arguments or {}, "agent_id": "sub-1",
                                 "agent_type": "worker"}, *extra)
            for action in ("create", "claim", "update"):
                self.assertTrue(denied(child("task", {"action": action})), (prefix, action))
            for name in ("spawn", "message", "resume", "stop", "report"):
                self.assertTrue(denied(child(name)), (prefix, name))
            self.assertTrue(denied(child("status", {"name": "w1", "receipt": "r1"})), prefix)
            self.assertTrue(denied(child("wait", {"target": "all", "receipt": "r1"})), prefix)
            self.assertIsNone(child("task", {"action": "list"}), prefix)
            self.assertIsNone(child("status", {"name": "w1"}), prefix)
            self.assertIsNone(run(PRE, {"tool_name": prefix + "task", "tool_input": {"action": "claim"}}, *extra), prefix)
            self.assertIsNone(run(PRE, {"tool_name": prefix + "status", "tool_input": {"receipt": "r1"}}, *extra), prefix)

    def test_S15_codex_bash_guard_remains_and_unrelated_mcp_servers_are_ignored(self):
        """agent-runtime-dispatch/S15"""
        self.assertTrue(denied(run(PRE, bash("hc kill w", agent_id="sub-1"), "--cli", "codex")))
        other = {"tool_name": "mcp__other__task", "tool_input": {"action": "claim"}, "agent_id": "sub-1"}
        self.assertIsNone(run(PRE, other, "--cli", "codex"))


class RawHcTests(unittest.TestCase):
    def test_S17_mutating_hc_is_denied_for_every_caller(self):
        """agent-runtime-tools/S17"""
        for sub in ("run --bg x", "send w hi", "kill w", "rm w", "file write f", "file edit f"):
            for extra in ({}, {"agent_id": "sub-1"}):
                out = run(PRE, bash("hc " + sub, **extra))
                self.assertTrue(denied(out), (sub, extra))
                self.assertIn(DENY_TEXT, out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_S17_keys_and_interrupt_ask_the_main_agent_and_deny_subagents(self):
        """agent-runtime-tools/S17"""
        for sub in ("keys w enter", "interrupt w"):
            out = run(PRE, bash("hc " + sub))["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "ask", sub)
            self.assertIn("approve to press keys in a worker", out["permissionDecisionReason"])
            self.assertTrue(denied(run(PRE, bash("hc " + sub, agent_id="sub-1"))), sub)
        self.assertEqual(run(PRE, bash("ls; hc keys w enter"))["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertTrue(denied(run(PRE, bash("hc keys w enter; hc kill w"))))

    def test_S17_command_substitutions_are_inspected(self):
        """agent-runtime-tools/S17"""
        for command in ("echo `hc kill w`", 'echo "$(hc send w hi)"', "x=$(hc rm w)", "echo $(echo $(hc kill w))",
                        'echo "`hc run x`"'):
            self.assertTrue(denied(run(PRE, bash(command))), command)
        self.assertIsNone(run(PRE, bash("echo $(hc get w) `hc ls`")))

    def test_S17_unparseable_or_non_string_commands_mentioning_hc_are_denied(self):
        """agent-runtime-tools/S17"""
        for command in ("hc send w 'unterminated", ["hc", "kill", "w"], {"cmd": "hc kill w"}):
            self.assertTrue(denied(run(PRE, bash(command))), command)
        self.assertIsNone(run(PRE, bash("echo 'unterminated")))
        self.assertIsNone(run(PRE, bash(["ls"])))

    def test_S17_reads_stay_allowed(self):
        """agent-runtime-tools/S17"""
        for command in ("hc get w", "hc ls", "hc read w", "hc wait w", "hc --help", "hc file read f",
                        "echo hc run", "git status"):
            self.assertIsNone(run(PRE, bash(command)), command)

    def test_S17_wrapped_and_nested_forms_are_denied(self):
        """agent-runtime-tools/S17"""
        for command in ("FOO=1 hc send w hi", "timeout 5 hc kill w", "sudo -u x hc rm w", "ls; hc kill w",
                        "ls\nhc send w hi", "sh -c 'hc send w hi'", "bash -lc \"hc run x\"", "eval 'hc kill w'",
                        "hc exec -- hc send w hi", "if true; then hc rm w; fi", "/usr/local/bin/hc rm w",
                        "env X=1 nohup hc send w hi", "true && (hc kill w)", "xargs -n1 hc rm"):
            self.assertTrue(denied(run(PRE, bash(command))), command)

    def test_S17_codex_payload_is_supported_and_ignores_other_tools(self):
        """agent-runtime-tools/S17"""
        self.assertTrue(denied(run(PRE, bash("hc send w hi"), "--cli", "codex")))
        self.assertIsNone(run(PRE, {"tool_name": "Edit", "tool_input": {"command": "hc send w hi"}}, "--cli", "codex"))
        self.assertIsNone(run(PRE, bash("hc get w"), "--cli", "codex"))

    def test_S17_garbage_input_does_not_deny(self):
        """agent-runtime-tools/S17"""
        done = subprocess.run([sys.executable, "-B", str(PRE)], input="not json", capture_output=True, text=True)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, ""))


class WiringTests(unittest.TestCase):
    def test_S18_hooks_json_registers_both_hooks_through_plugin_root(self):
        """agent-runtime-tools/S18"""
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
        pre = hooks["PreToolUse"]
        matchers = {entry["matcher"] for entry in pre}
        self.assertIn("Bash", matchers)
        mcp = [m for m in matchers if m != "Bash"]
        self.assertEqual(len(mcp), 1)
        self.assertRegex(TOOL + "spawn", mcp[0])
        commands = [h["command"] for entry in pre + hooks["SubagentStart"] for h in entry["hooks"]]
        self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/", command)
        self.assertNotIn("SessionStart", hooks)

    def test_S18_settings_json_drops_agent_runtime_hooks_and_keeps_the_rest(self):
        """agent-runtime-tools/S18"""
        text = SETTINGS.read_text()
        self.assertNotIn("agent-runtime", text)
        hooks = json.loads(text)["hooks"]
        self.assertIn("SessionEnd", hooks)
        self.assertIn("SessionStart", hooks)
        self.assertTrue(any("agent-fleet/hooks/session_start.py" in h["command"]
                            for entry in hooks["SessionStart"] for h in entry["hooks"]))
        self.assertNotIn("SubagentStart", hooks)
        self.assertNotIn("PreToolUse", hooks)


if __name__ == "__main__":
    unittest.main()
