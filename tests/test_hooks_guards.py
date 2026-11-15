"""The PreToolUse guard: what it refuses to a subagent, to a native child and to a raw `hc` command.

The guard is defence in depth behind the CLI, so these tests are about the refusal and the reason it
gives, not about the tool behind it.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
import pytest  # noqa: E402
from agent_runtime import (  # noqa: E402
    agent_runtime as lib,
)
from hook_harness import (  # noqa: E402
    HOOKS,
    PRE,
    START,
    run_pre,
    run_pre_raw,
)
from hook_helpers import (  # noqa: E402
    bash,
    denied,
)

pytestmark = pytest.mark.integration

TOOL = "mcp__plugin_agent-runtime_agent-runtime__"

DENY_TEXT = "use the agent-runtime tools"


def run(script, payload, *args):
    if script == PRE:
        return run_pre(payload, args)
    done = subprocess.run(
        [sys.executable, "-B", str(script), *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(done.stdout) if done.stdout.strip() else None


class SubagentToolTests(unittest.TestCase):
    def test_S16_subagent_is_denied_the_five_mutating_tools(self):
        """agent-runtime-tools/S16"""
        for name in ("spawn", "message", "resume", "stop", "report"):
            out = run(PRE, {"tool_name": TOOL + name, "tool_input": {}, "agent_id": "sub-1"})
            self.assertTrue(denied(out), name)

    def test_S16_main_agent_may_call_mutating_tools_and_subagent_may_read(self):
        """agent-runtime-tools/S16"""
        self.assertIsNone(run(PRE, {"tool_name": TOOL + "spawn", "tool_input": {}}))
        for name in ("status", "list", "collect"):
            self.assertIsNone(
                run(PRE, {"tool_name": TOOL + name, "tool_input": {}, "agent_id": "sub-1"}), name
            )

    def test_S16_subagent_start_notice_names_the_rule(self):
        """agent-runtime-tools/S16"""
        out = run(START, {})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        self.assertIn("spawn/message/resume/stop/report", ctx)
        self.assertIn("return value", ctx)


CODEX = "mcp__agent-runtime__"


class NativeChildGuardTests(unittest.TestCase):
    def test_native_children_cannot_mutate_tasks_or_ack_receipts_on_either_client(self):
        """agent-runtime/R48
        agent-runtime-dispatch/S15"""
        for prefix, extra in ((TOOL, ()), (CODEX, ("--cli", "codex"))):

            def child(name, arguments=None):
                return run(
                    PRE,
                    {
                        "tool_name": prefix + name,
                        "tool_input": arguments or {},
                        "agent_id": "sub-1",
                        "agent_type": "worker",
                    },
                    *extra,
                )

            for action in ("create", "claim", "update"):
                self.assertTrue(denied(child("task", {"action": action})), (prefix, action))
            for name in ("spawn", "message", "resume", "stop", "report"):
                self.assertTrue(denied(child(name)), (prefix, name))
            self.assertTrue(denied(child("status", {"name": "w1", "receipt": "r1"})), prefix)
            self.assertTrue(denied(child("wait", {"target": "all", "receipt": "r1"})), prefix)
            self.assertIsNone(child("task", {"action": "list"}), prefix)
            self.assertIsNone(child("status", {"name": "w1"}), prefix)
            self.assertIsNone(
                run(PRE, {"tool_name": prefix + "task", "tool_input": {"action": "claim"}}, *extra),
                prefix,
            )
            self.assertIsNone(
                run(PRE, {"tool_name": prefix + "status", "tool_input": {"receipt": "r1"}}, *extra),
                prefix,
            )

    def test_native_children_cannot_wait_or_address_an_assignment(self):
        """agent-runtime/R74"""
        for prefix, extra in ((TOOL, ()), (CODEX, ("--cli", "codex"))):

            def child(name, arguments=None):
                return run(
                    PRE,
                    {
                        "tool_name": prefix + name,
                        "tool_input": arguments or {},
                        "agent_id": "sub-1",
                        "agent_type": "worker",
                    },
                    *extra,
                )

            self.assertTrue(denied(child("wait", {"target": "all"})), prefix)
            for name in ("status", "list", "collect", "task"):
                self.assertTrue(
                    denied(child(name, {"assignment_id": "a1", "action": "list"})), (prefix, name)
                )
            self.assertIsNone(child("task", {"action": "list"}), prefix)
            for name in ("status", "list", "collect"):
                self.assertIsNone(child(name, {"name": "w1"}), (prefix, name))
            self.assertIsNone(
                run(PRE, {"tool_name": prefix + "wait", "tool_input": {"target": "all"}}, *extra),
                prefix,
            )
            self.assertIsNone(
                run(
                    PRE,
                    {"tool_name": prefix + "status", "tool_input": {"assignment_id": "a1"}},
                    *extra,
                ),
                prefix,
            )

    def test_string_tool_input_is_parsed_and_unparseable_input_is_denied_for_children(self):
        """agent-runtime/R74"""
        for prefix, extra in ((CODEX, ("--cli", "codex")), (TOOL, ())):

            def child(raw):
                return run(
                    PRE,
                    {
                        "tool_name": prefix + "status",
                        "tool_input": raw,
                        "agent_id": "sub-1",
                        "agent_type": "worker",
                    },
                    *extra,
                )

            self.assertTrue(denied(child('{"assignment_id": "a1"}')), prefix)
            self.assertTrue(denied(child('{"receipt": "r1"}')), prefix)
            self.assertTrue(denied(child("not json")), prefix)
            self.assertTrue(denied(child("[1]")), prefix)
            self.assertTrue(denied(child(None)), prefix)
            self.assertIsNone(child('{"name": "w1"}'), prefix)
            main = {"tool_name": prefix + "status", "tool_input": "not json"}
            self.assertIsNone(run(PRE, main, *extra), prefix)

    def test_subagent_string_input_that_is_not_an_object_is_denied_but_a_lead_passes(self):
        """agent-runtime/R89"""
        for prefix, extra in ((CODEX, ("--cli", "codex")), (TOOL, ())):
            for raw in ("not json", "[1]", '"text"', "42", "null", ""):
                sub = {
                    "tool_name": prefix + "status",
                    "tool_input": raw,
                    "agent_id": "sub-1",
                    "agent_type": "worker",
                }
                out = run(PRE, sub, *extra)
                self.assertTrue(denied(out), (prefix, raw))
                self.assertIn(
                    "unreadable input", out["hookSpecificOutput"]["permissionDecisionReason"]
                )
                self.assertIsNone(
                    run(PRE, {"tool_name": prefix + "status", "tool_input": raw}, *extra),
                    (prefix, raw),
                )

    def test_stdin_that_is_not_json_emits_no_decision_and_exits_zero(self):
        """agent-runtime/R90"""
        for extra in ((), ("--cli", "codex")):
            for raw in ("not json", "", "{", "[]", "null", "42"):
                code, out = run_pre_raw(raw, extra)
                self.assertEqual((code, out.strip()), (0, ""), (extra, raw))

    def test_subagent_start_notice_names_allowed_reads(self):
        """agent-runtime/R74"""
        ctx = run(START, {})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("wait", ctx)
        self.assertIn("assignment_id", ctx)
        self.assertIn("task list", ctx)

    def test_codex_bash_guard_remains_and_unrelated_mcp_servers_are_ignored(self):
        """agent-runtime/R48
        agent-runtime-dispatch/S15"""
        self.assertTrue(denied(run(PRE, bash("hc kill w", agent_id="sub-1"), "--cli", "codex")))
        other = {
            "tool_name": "mcp__other__task",
            "tool_input": {"action": "claim"},
            "agent_id": "sub-1",
        }
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
        self.assertEqual(
            run(PRE, bash("ls; hc keys w enter"))["hookSpecificOutput"]["permissionDecision"], "ask"
        )
        self.assertTrue(denied(run(PRE, bash("hc keys w enter; hc kill w"))))

    def test_S17_command_substitutions_are_inspected(self):
        """agent-runtime-tools/S17"""
        for command in (
            "echo `hc kill w`",
            'echo "$(hc send w hi)"',
            "x=$(hc rm w)",
            "echo $(echo $(hc kill w))",
            'echo "`hc run x`"',
        ):
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
        for command in (
            "hc get w",
            "hc ls",
            "hc read w",
            "hc wait w",
            "hc --help",
            "hc file read f",
            "echo hc run",
            "git status",
        ):
            self.assertIsNone(run(PRE, bash(command)), command)

    def test_S17_wrapped_and_nested_forms_are_denied(self):
        """agent-runtime-tools/S17"""
        for command in (
            "FOO=1 hc send w hi",
            "timeout 5 hc kill w",
            "sudo -u x hc rm w",
            "ls; hc kill w",
            "ls\nhc send w hi",
            "sh -c 'hc send w hi'",
            'bash -lc "hc run x"',
            "eval 'hc kill w'",
            "hc exec -- hc send w hi",
            "if true; then hc rm w; fi",
            "/usr/local/bin/hc rm w",
            "env X=1 nohup hc send w hi",
            "true && (hc kill w)",
            "xargs -n1 hc rm",
        ):
            self.assertTrue(denied(run(PRE, bash(command))), command)

    def test_S17_codex_payload_is_supported_and_ignores_other_tools(self):
        """agent-runtime-tools/S17"""
        self.assertTrue(denied(run(PRE, bash("hc send w hi"), "--cli", "codex")))
        self.assertIsNone(
            run(
                PRE,
                {"tool_name": "Edit", "tool_input": {"command": "hc send w hi"}},
                "--cli",
                "codex",
            )
        )
        self.assertIsNone(run(PRE, bash("hc get w"), "--cli", "codex"))

    def test_S17_garbage_input_does_not_deny(self):
        """agent-runtime-tools/S17; S13: the hook's one process boundary for its input handling"""
        done = subprocess.run(
            [sys.executable, "-B", str(PRE)], input="not json", capture_output=True, text=True
        )
        self.assertEqual((done.returncode, done.stdout.strip()), (0, ""))


class CallCliTests(unittest.TestCase):
    def test_S6_subagent_is_denied_agent_runtime_call_through_bash(self):
        """agent-runtime/R155"""
        for command in (
            'agent-runtime call wait --json \'{"target":"all"}\'',
            "agent-runtime call spawn --lead L1 --json '{}'",
            'bin/agent-runtime call task --json \'{"action":"list","receipt":"r"}\'',
            "agent-runtime call status --json '{not json'",
            "cd x && agent-runtime call stop --json '{}'",
            'agent-runtime call --json \'{"target":"all"}\' wait',
            'agent-runtime call --lead L1 wait --json \'{"target":"all"}\'',
            "agent-runtime call --lead L1 spawn --json '{}'",
            'agent-runtime call status --js \'{"assignment_id":"x"}\'',
            "agent-runtime call status --lead",
            "agent-runtime call --bogus status",
        ):
            self.assertTrue(denied(run(PRE, bash(command, agent_id="sub-1"))), command)

    def test_S6_subagent_is_denied_the_entrypoint_run_by_path_or_python(self):
        """agent-runtime/R155"""
        for command in (
            "python3 server/agent_runtime.py call wait --json '{}'",
            "python3 -B /x/server/agent_runtime.py call spawn --json '{}'",
            "/x/server/agent_runtime.py call wait --json '{}'",
        ):
            self.assertTrue(denied(run(PRE, bash(command, agent_id="sub-1"))), command)
        self.assertIsNone(run(PRE, bash("python3 other.py call wait", agent_id="sub-1")))

    def test_S6_hook_reads_call_arguments_the_way_the_cli_parser_does(self):
        """agent-runtime/R155"""
        sys.path.insert(0, str(HOOKS))
        import pre_tool_use

        parser = lib.build_parser()
        for argv in (
            ["wait", "--json", "{}"],
            ["--json", "{}", "wait"],
            ["--lead", "L1", "wait"],
            ["--lead=L1", "spawn", "--json={}"],
            ["status", "--js", "{}"],
            ["status", "--lead"],
            ["--bogus", "status"],
            ["wait", "extra"],
        ):
            with self.subTest(argv=argv):
                hook = pre_tool_use.parse_call(argv)
                try:
                    cli = parser.parse_args(["call", *argv])
                except SystemExit:
                    cli = None
                self.assertEqual(hook is None, cli is None)
                if cli is not None:
                    self.assertEqual(
                        (hook.tool, hook.lead, hook.json), (cli.tool, cli.lead, cli.json)
                    )

    def test_S6_non_subagent_is_asked_on_mutating_tools_only(self):
        """agent-runtime/R155"""
        for tool in ("spawn", "message", "resume", "stop", "report"):
            out = run(PRE, bash("agent-runtime call %s --lead L1 --json '{}'" % tool))
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask", tool)
        for command in (
            "agent-runtime call --lead L1 spawn --json '{}'",
            "agent-runtime call --json '{}' stop",
            "agent-runtime call status --lead",
            "agent-runtime call status --js '{}'",
        ):
            out = run(PRE, bash(command))
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask", command)
        for command in (
            "agent-runtime call wait --json '{}'",
            "agent-runtime call status --json '{}'",
            'agent-runtime call task --json \'{"action":"list"}\'',
            "agent-runtime status w1",
            "agent-runtime resolve",
            "agent-runtime call --lead L1 wait --json '{}'",
            "agent-runtime call --help",
        ):
            self.assertIsNone(run(PRE, bash(command)), command)
        self.assertIsNone(
            run(PRE, bash('agent-runtime call task --json \'{"action":"list"}\'', agent_id="sub-1"))
        )


class WiringTests(unittest.TestCase):
    def test_S18_hooks_json_registers_both_hooks_through_plugin_root(self):
        """agent-runtime-tools/S18"""
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
        pre = hooks["PreToolUse"]
        matchers = {entry["matcher"] for entry in pre}
        self.assertIn("Bash", matchers)
        files = "Write|Edit|MultiEdit|NotebookEdit"  # yaml-eec D31: the receipts branch runs for file edits
        self.assertIn(files, matchers)
        for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            self.assertRegex(tool, "^(?:%s)$" % files)
        self.assertNotRegex("Read", "^(?:%s)$" % files)
        mcp = [m for m in matchers if m not in ("Bash", files)]
        self.assertEqual(len(mcp), 1)
        self.assertRegex(TOOL + "spawn", mcp[0])
        context = hooks["SessionStart"] + hooks["UserPromptSubmit"] + hooks["Stop"]
        commands = [
            h["command"] for entry in pre + hooks["SubagentStart"] + context for h in entry["hooks"]
        ]
        self.assertEqual(len(commands), 7)
        for command in commands:
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}"/hooks/', command)
        self.assertEqual({e.get("matcher") for e in hooks["SessionStart"]}, {"compact|clear"})
        for entry in context:  # the new hooks fail open when python3 is missing
            self.assertIn("command -v python3", entry["hooks"][0]["command"])


if __name__ == "__main__":
    unittest.main()
