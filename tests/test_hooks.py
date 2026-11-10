"""Plugin hooks driven as subprocesses with real payloads, plus settings.json and hooks.json wiring."""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
HOOKS = PLUGIN / "hooks"
PRE = HOOKS / "pre_tool_use.py"
START = HOOKS / "subagent_start.py"
TOOL = "mcp__plugin_agent-runtime_agent-runtime__"
DENY_TEXT = "use the agent-runtime tools"
CONTEXT = HOOKS / "lead_context.py"
SHIM = PLUGIN / "bin" / "agent-runtime-hook"
sys.path.insert(0, str(PLUGIN / "packages" / "agent-runtime" / "src"))
import pytest
from agent_runtime import agent_runtime as lib  # noqa: E402
from agent_runtime import (
    dispatch_store,  # noqa: E402
    dispatch_wait,  # noqa: E402
    hc_tools,  # noqa: E402
)

pytestmark = pytest.mark.integration


def run(script, payload, *args):
    done = subprocess.run(
        [sys.executable, "-B", str(script), *args],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
    )
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
                done = subprocess.run(
                    [sys.executable, "-B", str(PRE), *extra],
                    input=raw,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual((done.returncode, done.stdout.strip()), (0, ""), (extra, raw))

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
        """agent-runtime-tools/S17"""
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
        mcp = [m for m in matchers if m != "Bash"]
        self.assertEqual(len(mcp), 1)
        self.assertRegex(TOOL + "spawn", mcp[0])
        context = hooks["SessionStart"] + hooks["UserPromptSubmit"] + hooks["Stop"]
        commands = [
            h["command"] for entry in pre + hooks["SubagentStart"] + context for h in entry["hooks"]
        ]
        self.assertEqual(len(commands), 6)
        for command in commands:
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}"/hooks/', command)
        self.assertEqual({e.get("matcher") for e in hooks["SessionStart"]}, {"compact|clear"})
        for entry in context:  # the new hooks fail open when python3 is missing
            self.assertIn("command -v python3", entry["hooks"][0]["command"])


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

    def team(self, generation, lead="t0", scope="scope"):
        store = dispatch_store.Store(self.env)
        return store, store.team(scope, lead, generation)

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


class LeadContextTests(HookStoreCase):
    def test_unseen_work_prints_the_wait_reminder_for_all_three_events(self):
        """agent-runtime/R116"""

        def seed(generation):
            store, team = self.team(generation)
            ident = self.worker(store, team)
            self.worker(store, team, "w2", "blocked")
            self.message(
                store, team, assignment=ident["assignment_id"], message_id="m" + generation
            )

        for payload in (
            self.PROMPT,
            {"hook_event_name": "SessionStart", "source": "compact"},
            {"hook_event_name": "SessionStart", "source": "clear"},
        ):
            out = self.hook(
                payload, seed
            )  # each hook run is a new fake CLI process, so seed per run
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], payload["hook_event_name"])
            self.assertEqual(
                self.line(out), "agent-runtime: 2 unfinished, 1 undelivered — call wait(all)"
            )

    def test_working_assignment_the_lead_has_not_seen_is_enough(self):
        """agent-runtime/R116"""

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)
            self.last_wait(store, team, "timeout", 0)  # a timeout never counts as seen

        self.assertEqual(
            self.line(self.hook(self.PROMPT, seed)),
            "agent-runtime: 1 unfinished, 0 undelivered — call wait(all)",
        )

    def test_attention_already_delivered_and_unchanged_points_at_status(self):
        """agent-runtime/R117"""

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)
            self.worker(store, team, "w2")
            self.last_wait(store, team, "attention", 2)

        self.assertEqual(
            self.line(self.hook(self.PROMPT, seed)),
            "agent-runtime: 2 workers need you — check status",
        )

    def test_prints_nothing_without_needs_or_with_the_wrong_team(self):
        """agent-runtime/R118"""

        def delivered(generation):  # blocked work, one message already named by a receipt
            store, team = self.team(generation)
            self.worker(store, team, state="blocked")
            self.message(store, team)
            self.sql(
                store, "INSERT INTO receipts VALUES (?,?,?,?)", "r1", team, "t0", json.dumps(["m1"])
            )

        def seen(generation):  # working work whose wait was delivered, no attention
            store, team = self.team(generation)
            self.worker(store, team)
            self.last_wait(store, team, "completed", 0)

        def other_generation(generation):
            store, team = self.team("999:other")
            self.worker(store, team)

        def superseded(generation):  # the hook's team exists but a newer team for the lead does too
            store, team = self.team(generation)
            self.worker(store, team)
            self.team("1:newer")

        def other_lead(generation):
            store, team = self.team(generation, lead="t7")
            self.worker(store, team)

        for seed in (None, delivered, seen, other_generation, superseded, other_lead):
            self.assertIsNone(self.hook(self.PROMPT, seed), getattr(seed, "__name__", "no store"))

    def test_session_start_sources_other_than_compact_and_clear_print_nothing(self):
        """agent-runtime/R118"""

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)

        for source in ("startup", "resume", "fork"):
            self.assertIsNone(
                self.hook({"hook_event_name": "SessionStart", "source": source}, seed), source
            )

    def test_every_failure_allows_silently(self):
        """agent-runtime/R114"""

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)

        self.assertIsNone(self.hook(None, seed, raw="not json"))
        self.assertIsNone(self.hook(None, seed, raw="[1]"))
        no_identity = {k: v for k, v in self.env.items() if k != "HC_TERMINAL_ID"}
        self.assertIsNone(self.hook(self.PROMPT, seed, env=no_identity))
        locked = {}

        def seed_locked(generation):
            seed(generation)
            locked["db"] = sqlite3.connect(
                self.state / "agent-runtime" / "dispatch.sqlite3", isolation_level=None
            )
            locked["db"].execute("BEGIN EXCLUSIVE")

        try:
            self.assertIsNone(self.hook(self.PROMPT, seed_locked))
        finally:
            locked["db"].close()
        for args in (
            [],
            ["stop", "--cli", "codex"],
            ["context", "--cli", "codex"],
        ):  # default, stop, context
            done = subprocess.run(
                ["/bin/sh", str(SHIM)] + args,
                input="{}",
                env={"PATH": "/nonexistent"},
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual((done.returncode, done.stdout), (0, ""), args)

    def hook_twice(self, payload, seed, env):
        """Run the hook twice under ONE fake CLI process (one generation); returns both outputs."""
        payload_file = self.root / "payload.json"
        payload_file.write_text(json.dumps(payload))
        script = 'read -r _; for _ in 1 2; do "$@" <"$PAYLOAD"; done'
        proc = subprocess.Popen(
            ["codex", "-c", script, "codex", sys.executable, "-B", str(CONTEXT)],
            executable="/bin/bash",
            env=dict(env, PAYLOAD=str(payload_file)),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        seed("%d:%s" % (proc.pid, lib.lstart(proc.pid)))
        out, err = proc.communicate("go\n", timeout=30)
        self.assertEqual(proc.returncode, 0, err)
        return [json.loads(line) for line in out.splitlines()]

    def test_hook_never_writes_the_store_and_honours_xdg_state_home(self):
        """agent-runtime/R115"""

        def seed(generation):
            store, team = self.team(generation)
            self.worker(store, team)

        def snapshot():  # the database and any SQLite sidecar files
            return sorted(
                (p.name, p.stat().st_mtime_ns, p.stat().st_size)
                for p in (self.state / "agent-runtime").glob("dispatch.sqlite3*")
            )

        for flag in (None, "1"):
            env = dict(self.env, **({"AGENT_RUNTIME_DISPATCH": flag} if flag else {}))
            outs = self.hook_twice(self.PROMPT, seed, env)
            self.assertEqual(
                [self.line(o) for o in outs],
                ["agent-runtime: 1 unfinished, 0 undelivered — call wait(all)"] * 2,
            )
            before = snapshot()
            self.hook_twice(
                self.PROMPT, lambda generation: None, env
            )  # reads only (other generation)
            self.assertEqual(snapshot(), before, flag)
        self.assertFalse((self.root / "home").exists())
        missing = dict(self.env, XDG_STATE_HOME=str(self.root / "absent"))
        self.hook(self.PROMPT, env=missing)
        self.assertFalse((self.root / "absent").exists())

    def test_broken_import_exits_zero_silently(self):
        """agent-runtime/R114"""
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "lead_context.py").write_text((HOOKS / "lead_context.py").read_text())
        (broken / "hook_lib.py").write_text("raise ImportError('boom')\n")
        done = subprocess.run(
            [sys.executable, "-B", str(broken / "lead_context.py")],
            input=json.dumps(self.PROMPT),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_claude_hook_commands_allow_silently_without_python3(self):
        """agent-runtime/R114"""
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
        for event in ("SessionStart", "UserPromptSubmit", "Stop"):
            command = hooks[event][0]["hooks"][0]["command"]
            done = subprocess.run(
                command,
                shell=True,
                input="{}",
                capture_output=True,
                text=True,
                timeout=20,
                env={"PATH": "/nonexistent", "CLAUDE_PLUGIN_ROOT": str(PLUGIN)},
            )
            self.assertEqual((done.returncode, done.stdout), (0, ""), event)

    def test_hook_shaped_process_computes_the_servers_generation(self):
        """agent-runtime/R107"""
        code = (
            "import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); import hook_lib; "
            "from agent_runtime import hc_tools; "
            "env = {'HC_SCOPE': 's', 'HC_TERMINAL_ID': 't0', 'CODEX_THREAD_ID': 'thread-x'}; "
            "print(hook_lib.identity(env)[2]); print(hc_tools.dispatch_identity(env)[1])"
        ) % (str(HOOKS), str(PLUGIN / "packages" / "agent-runtime" / "src"))
        proc = subprocess.Popen(
            ["codex", "-c", '"$@"; exit $?', "codex", "/bin/sh", "-c", 'python3 -c "$0"', code],
            executable="/bin/bash",
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        expected = "%d:%s" % (proc.pid, lib.lstart(proc.pid))
        out, err = proc.communicate(timeout=30)
        self.assertEqual(out.split("\n")[:2], [expected, expected], err)


class FakeCliProcess:
    """One fake `codex` process that runs hook commands on request, so successive hooks (and seeding between
    them) share one generation. Each run returns (parsed stdout or None, stderr, returncode)."""

    def __init__(self, env, root):
        self.root, self.count = Path(root), 0
        self.proc = subprocess.Popen(
            ["codex", "-c", 'while read -r n; do . "$DIR/s$n.sh"; echo ok; done', "codex"],
            executable="/bin/bash",
            env=dict(env, DIR=str(root)),
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.generation = "%d:%s" % (self.proc.pid, lib.lstart(self.proc.pid))

    def run(self, script, payload=None, raw=None, args=()):
        """script is a Python hook path, or a list for a command run as-is (the shim); args follow it."""
        self.count += 1
        n = self.count
        (self.root / ("p%d.json" % n)).write_text(raw if raw is not None else json.dumps(payload))
        command = (
            " ".join(script) if isinstance(script, list) else "%s -B %s" % (sys.executable, script)
        )
        (self.root / ("s%d.sh" % n)).write_text(
            '%s %s <"$DIR/p%d.json" >"$DIR/o%d" 2>"$DIR/e%d"; echo $? >"$DIR/r%d"\n'
            % (command, " ".join(args), n, n, n, n)
        )
        self.proc.stdin.write("%d\n" % n)
        self.proc.stdin.flush()
        assert self.proc.stdout.readline().strip() == "ok"
        out = (self.root / ("o%d" % n)).read_text()
        return (
            json.loads(out) if out.strip() else None,
            (self.root / ("e%d" % n)).read_text(),
            int((self.root / ("r%d" % n)).read_text()),
        )

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


STOP = HOOKS / "stop_guard.py"


class StopGuardTests(HookStoreCase):
    STOP = {"hook_event_name": "Stop", "stop_hook_active": False}
    REASON = "agent-runtime: %d unfinished, %d undelivered — call wait(all) before ending the turn"
    MCP_WAIT = {
        "type": "MCP task",
        "server": "plugin:agent-runtime:agent-runtime",
        "tool": "wait",
        "status": "running",
    }

    def setUp(self):
        super().setUp()
        self.cli = FakeCliProcess(self.env, self.root)
        self.addCleanup(self.cli.close)
        self.store = dispatch_store.Store(self.env)
        self.team_id = self.store.team("scope", "t0", self.cli.generation)

    def stop(self, payload=None):
        out, err, code = self.cli.run(STOP, payload or self.STOP)
        self.assertEqual(code, 0, err)
        return out, err

    def prompt(self):
        out, err, code = self.cli.run(CONTEXT, self.PROMPT)
        self.assertEqual(code, 0, err)
        return out

    def budget(self):
        files = list((self.state / "agent-runtime" / "stop-guard").glob("*"))
        self.assertLessEqual(len(files), 1)
        if not files:
            return None
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        return int(files[0].read_text().strip())

    def unseen(self):
        return self.worker(self.store, self.team_id)

    def test_blocks_with_the_wait_reason_when_work_is_unseen(self):
        """agent-runtime/R119"""
        ident = self.unseen()
        self.message(self.store, self.team_id, assignment=ident["assignment_id"])
        out, _ = self.stop()
        self.assertEqual(out, {"decision": "block", "reason": self.REASON % (1, 1)})
        self.assertEqual(self.budget(), 2)

    def test_S3_stop_guard_still_blocks_after_a_partial_wait_result(self):
        """S3: a partial wait(all) result leaves the last-wait row untouched, so the guard still blocks."""
        ident = self.unseen()
        body = json.dumps(
            {
                "message_id": "m1",
                "team_id": self.team_id,
                "worker_id": "w1",
                "assignment_id": ident["assignment_id"],
                "sender": "w1",
                "recipient": "t0",
                "kind": "progress",
                "text": "half done",
            },
            separators=(",", ":"),
        )
        self.sql(
            self.store,
            "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
            "m1",
            self.team_id,
            "t0",
            ident["assignment_id"],
            body,
        )
        self.last_wait(self.store, self.team_id, "timeout", 0)
        with self.store.transaction() as db:
            before = tuple(
                db.execute("SELECT * FROM last_wait WHERE team=?", (self.team_id,)).fetchone()
            )
        batch = dispatch_wait.wait(
            self.store, self.team_id, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(batch["reason"], "messages")
        with self.store.transaction() as db:
            self.assertEqual(
                tuple(
                    db.execute("SELECT * FROM last_wait WHERE team=?", (self.team_id,)).fetchone()
                ),
                before,
            )
        out, _ = self.stop()
        self.assertEqual(out, {"decision": "block", "reason": self.REASON % (1, 0)})

    def test_unseen_working_assignment_alone_blocks(self):
        """agent-runtime/R119"""
        self.unseen()
        out, _ = self.stop()
        self.assertEqual(out, {"decision": "block", "reason": self.REASON % (1, 0)})

    def test_allows_blocked_decision_failed_seen_and_resets_the_budget(self):
        """agent-runtime/R120"""
        ident = self.unseen()
        self.stop()
        self.assertEqual(self.budget(), 2)
        for state in ("blocked", "decision", "failed"):
            self.sql(
                self.store,
                "UPDATE assignments SET state=? WHERE id=?",
                state,
                ident["assignment_id"],
            )
            self.assertEqual(self.stop()[0], None, state)
        self.assertEqual(self.budget(), 3)  # a check that finds nothing to block on resets
        self.sql(
            self.store, "UPDATE assignments SET state='working' WHERE id=?", ident["assignment_id"]
        )
        self.last_wait(self.store, self.team_id, "completed", 0)
        self.assertIsNone(self.stop()[0])  # reason != timeout and fingerprint unchanged
        self.sql(
            self.store, "UPDATE assignments SET state='failed' WHERE id=?", ident["assignment_id"]
        )
        self.assertIsNone(self.stop()[0])  # fingerprint changed but nothing left working

    def test_running_wait_task_allows_without_spending_or_resetting_budget(self):
        """agent-runtime/R121"""
        self.unseen()
        self.stop()
        self.assertEqual(self.budget(), 2)
        for variant in (
            {},
            {"server": "agent-runtime"},
            {"tool": "mcp__plugin_agent-runtime_agent-runtime__wait"},
            {"status": "pending"},
        ):  # status values are undocumented: anything non-terminal exempts
            out, _ = self.stop(dict(self.STOP, background_tasks=[dict(self.MCP_WAIT, **variant)]))
            self.assertIsNone(out, variant)
            self.assertEqual(self.budget(), 2)
        terminal = [
            dict(self.MCP_WAIT, status=status)
            for status in ("completed", "failed", "killed", "stopped", "cancelled")
        ]
        for other in (
            dict(self.MCP_WAIT, tool="status"),
            dict(self.MCP_WAIT, type="Bash"),
            dict(self.MCP_WAIT, server="plugin:other:other"),
            *terminal,
            "junk",
        ):
            self.prompt()  # fresh budget for each probe
            out, _ = self.stop(dict(self.STOP, background_tasks=[other]))
            self.assertIsNotNone(out, other)  # unknown or other work: no exemption
            self.assertEqual(self.budget(), 2)

    def test_stop_allows_silently_on_bad_stdin_no_identity_and_broken_import(self):
        """agent-runtime/R114"""
        self.unseen()
        for raw in ("not json", "[1]", ""):
            out, err, code = self.cli.run(STOP, raw=raw)
            self.assertEqual((out, err, code), (None, "", 0), raw)
        (self.root / "n").mkdir()
        nobody = FakeCliProcess(
            {k: v for k, v in self.env.items() if k != "HC_TERMINAL_ID"}, self.root / "n"
        )
        self.addCleanup(nobody.close)
        self.assertEqual(nobody.run(STOP, self.STOP), (None, "", 0))
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "stop_guard.py").write_text(STOP.read_text())
        (broken / "hook_lib.py").write_text("raise ImportError('boom')\n")
        self.assertEqual(self.cli.run(broken / "stop_guard.py", self.STOP), (None, "", 0))
        self.assertIsNone(self.budget())  # none of those touched the budget

    def test_stop_never_writes_the_dispatch_store(self):
        """agent-runtime/R115"""
        self.unseen()

        def snapshot():
            return sorted(
                (p.name, p.stat().st_mtime_ns, p.stat().st_size)
                for p in (self.state / "agent-runtime").glob("dispatch.sqlite3*")
            )

        before = snapshot()
        self.assertIsNotNone(self.stop()[0])  # blocks, spends budget
        self.assertEqual(self.stop(dict(self.STOP, background_tasks=[self.MCP_WAIT]))[0], None)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.budget(), 2)

    def test_reset_prunes_stale_budget_files_only(self):
        """agent-runtime/R123"""
        self.unseen()
        self.stop()
        folder = self.state / "agent-runtime" / "stop-guard"
        stale, fresh = folder / "stale", folder / "fresh"
        stale.write_text("1")
        fresh.write_text("1")
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        self.prompt()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_blocks_three_times_then_allows_with_one_stderr_note(self):
        """agent-runtime/R122"""
        self.unseen()
        for _ in range(3):
            self.assertIsNotNone(self.stop()[0])
        out, err = self.stop()
        self.assertIsNone(out)
        self.assertIn("agent-runtime", err)
        out, err = self.stop()
        self.assertEqual((out, err), (None, ""))  # later Stops keep allowing, silently
        self.prompt()  # UserPromptSubmit resets
        self.assertIsNotNone(self.stop()[0])

    def test_exemption_budget_spent_and_errors_do_not_reset_but_prompt_does(self):
        """agent-runtime/R123"""
        self.unseen()
        self.stop()
        self.stop(dict(self.STOP, background_tasks=[self.MCP_WAIT]))
        self.assertEqual(self.budget(), 2)  # exemption
        locked = sqlite3.connect(
            self.state / "agent-runtime" / "dispatch.sqlite3", isolation_level=None
        )
        locked.execute("BEGIN EXCLUSIVE")
        try:
            self.assertEqual(self.stop()[0], None)
        finally:
            locked.close()
        self.assertEqual(self.budget(), 2)  # error
        self.stop()
        self.stop()
        self.stop()
        self.assertEqual(self.budget(), -1)  # spent: noted once, saturated
        self.stop()
        self.assertEqual(self.budget(), -1)
        self.prompt()
        self.assertEqual(self.budget(), 3)

    def test_prompt_resets_even_when_the_context_hook_has_nothing_to_say(self):
        """agent-runtime/R123"""
        ident = self.unseen()
        self.stop()
        self.sql(
            self.store, "UPDATE assignments SET state='complete' WHERE id=?", ident["assignment_id"]
        )
        self.assertEqual(self.budget(), 2)
        self.assertIsNone(self.prompt())
        self.assertEqual(self.budget(), 3)

    def test_worker_leading_children_blocks_and_dead_or_superseded_teams_allow(self):
        """agent-runtime/R124"""
        worker_env = dict(self.env, HC_TERMINAL_ID="t5")
        child = (
            FakeCliProcess(worker_env, self.root / "w")
            if (self.root / "w").mkdir() is None
            else None
        )
        self.addCleanup(child.close)
        team = self.store.team("scope", "t5", child.generation)
        self.worker(self.store, team)
        out, err, code = child.run(STOP, self.STOP)
        self.assertEqual((out["decision"], code), ("block", 0), err)
        self.store.team("scope", "t5", "1:newer")  # a newer team supersedes the hook's
        self.assertIsNone(child.run(STOP, self.STOP)[0])
        self.assertIsNone(self.stop()[0])  # t0 leads no team with needs
        other = dispatch_store.Store(self.env)
        dead = other.team("scope", "t9", "999:dead")  # a dead generation
        self.worker(other, dead)
        nobody = (
            FakeCliProcess(dict(self.env, HC_TERMINAL_ID="t9"), self.root / "n")
            if (self.root / "n").mkdir() is None
            else None
        )
        self.addCleanup(nobody.close)
        self.assertIsNone(nobody.run(STOP, self.STOP)[0])

    def test_reported_unchanged_stall_counts_as_seen_until_progress(self):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )
        self.assertIsNone(self.stop()[0])
        self.assertIsNone(self.prompt())  # no "call wait(all)" line either
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9 WHERE assignment=?",
            ident["assignment_id"],
        )
        self.assertEqual(self.stop()[0]["decision"], "block")
        self.assertIn("call wait(all)", self.line(self.prompt()))
        self.sql(self.store, "DROP TABLE stall_progress")  # older store: no reported stalls
        self.assertEqual(self.stop()[0]["decision"], "block")

    def record_wait(self, reason="attention"):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO last_wait(team,reason,attention,fingerprint) VALUES (?,?,0,?) ON CONFLICT(team) DO UPDATE "
                "SET reason=excluded.reason,attention=0,fingerprint=excluded.fingerprint",
                (self.team_id, reason, dispatch_store.wait_fingerprint(db, self.team_id)),
            )

    def test_recovery_after_a_stall_batch_that_was_the_last_wait_is_unseen(self):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )
        self.record_wait()  # the stall batch was the lead's last wait(all)
        self.assertIsNone(self.stop()[0])
        self.assertIsNone(self.prompt())
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9,last_turn=2 WHERE assignment=?",
            ident["assignment_id"],
        )
        self.assertEqual(self.stop()[0]["decision"], "block")
        self.assertIn("call wait(all)", self.line(self.prompt()))

    def test_turns_after_a_following_wait_do_not_flip_seen(self):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9,last_turn=2 WHERE assignment=?",
            ident["assignment_id"],
        )
        self.record_wait("timeout")
        self.record_wait("attention")  # a later wait(all) delivered nothing new
        self.assertIsNone(self.stop()[0])
        for turn in (3, 4):
            self.sql(
                self.store,
                "UPDATE stall_progress SET last_progress_ms=?,last_turn=? WHERE assignment=?",
                10 + turn,
                turn,
                ident["assignment_id"],
            )
            self.assertIsNone(self.stop()[0])
            self.assertIsNone(self.prompt())

    def test_fingerprint_is_identical_for_server_and_readonly_connection_with_or_without_stall_progress(
        self,
    ):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )

        def both():
            with self.store.transaction() as db:
                server = dispatch_store.wait_fingerprint(db, self.team_id)
            ro = sqlite3.connect(
                (self.state / "agent-runtime" / "dispatch.sqlite3").as_uri() + "?mode=ro", uri=True
            )
            try:
                return server, dispatch_store.wait_fingerprint(ro, self.team_id)
            finally:
                ro.close()

        reported = both()
        self.assertEqual(reported[0], reported[1])
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9 WHERE assignment=?",
            ident["assignment_id"],
        )
        recovered = both()
        self.assertEqual(recovered[0], recovered[1])
        self.assertNotEqual(reported[0], recovered[0])
        self.sql(self.store, "DROP TABLE stall_progress")
        absent = both()
        self.assertEqual(absent[0], absent[1])
        self.assertEqual(absent[0], recovered[0])  # absent behaves as "not reported"


class CodexHookTests(HookStoreCase):
    """The Codex adapter: the shim routes `stop` / `context` subcommands to the same hooks, fed with Codex-shaped
    payloads (docs/verification/codex-hooks.md)."""

    SHIM_CMD = ["/bin/sh", str(SHIM)]
    STOP = {
        "session_id": "s",
        "turn_id": "t",
        "cwd": "/",
        "hook_event_name": "Stop",
        "model": "m",
        "permission_mode": "default",
        "transcript_path": None,
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    PROMPT = {
        "session_id": "s",
        "turn_id": "t",
        "cwd": "/",
        "hook_event_name": "UserPromptSubmit",
        "model": "m",
        "permission_mode": "default",
        "transcript_path": None,
        "prompt": "hi",
    }

    def setUp(self):
        super().setUp()
        self.cli = FakeCliProcess(self.env, self.root)
        self.addCleanup(self.cli.close)
        self.store = dispatch_store.Store(self.env)
        self.team_id = self.store.team("scope", "t0", self.cli.generation)

    def stop(self, **extra):
        out, err, code = self.cli.run(
            self.SHIM_CMD, dict(self.STOP, **extra), args=("stop", "--cli", "codex")
        )
        self.assertEqual(code, 0, err)
        return out, err

    def context(self, payload):
        out, err, code = self.cli.run(self.SHIM_CMD, payload, args=("context", "--cli", "codex"))
        self.assertEqual(code, 0, err)
        return out

    def budget(self):
        files = list((self.state / "agent-runtime" / "stop-guard").glob("*"))
        return int(files[0].read_text()) if files else None

    def test_codex_stop_blocks_with_the_wait_reason_and_allows_with_empty_output(self):
        """agent-runtime/R125"""
        ident = self.worker(self.store, self.team_id)
        out, _ = self.stop()
        self.assertEqual(
            out,
            {
                "decision": "block",
                "reason": "agent-runtime: 1 unfinished, 0 undelivered — "
                "call wait(all) before ending the turn",
            },
        )
        self.sql(
            self.store, "UPDATE assignments SET state='blocked' WHERE id=?", ident["assignment_id"]
        )
        self.assertEqual(
            self.cli.run(self.SHIM_CMD, self.STOP, args=("stop", "--cli", "codex")), (None, "", 0)
        )

    def test_codex_budget_ignores_stop_hook_active_and_background_tasks_then_resets_on_prompt(self):
        """agent-runtime/R125"""
        self.worker(self.store, self.team_id)
        mcp = [
            {
                "type": "MCP task",
                "server": "plugin:agent-runtime:agent-runtime",
                "tool": "wait",
                "status": "running",
            }
        ]
        self.assertIsNotNone(self.stop(background_tasks=mcp)[0])  # no D10 exemption on Codex
        self.assertIsNotNone(self.stop(stop_hook_active=True)[0])  # only the budget bounds it
        self.assertIsNotNone(self.stop(stop_hook_active=True)[0])
        out, err = self.stop(stop_hook_active=True)
        self.assertEqual(out, None)
        self.assertIn("agent-runtime", err)
        self.assertEqual(self.budget(), -1)
        self.context(self.PROMPT)  # Codex UserPromptSubmit resets
        self.assertEqual(self.budget(), 3)
        self.assertIsNotNone(self.stop()[0])

    def test_codex_session_start_and_prompt_carry_the_context_line(self):
        """agent-runtime/R126"""
        self.worker(self.store, self.team_id)
        line = "agent-runtime: 1 unfinished, 0 undelivered — call wait(all)"
        base = {
            "session_id": "s",
            "cwd": "/",
            "model": "m",
            "permission_mode": "default",
            "transcript_path": None,
        }
        for source in ("compact", "clear"):
            out = self.context(dict(base, hook_event_name="SessionStart", source=source))
            self.assertEqual(
                out,
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": line,
                    }
                },
            )
        for source in ("startup", "resume", "fork"):
            self.assertIsNone(
                self.context(dict(base, hook_event_name="SessionStart", source=source)), source
            )
        out = self.context(self.PROMPT)
        self.assertEqual(
            out,
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": line,
                }
            },
        )

    def test_install_prints_the_codex_lead_hooks_only_on_request(self):
        """agent-runtime/R125"""
        base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root / "home"),
            "BIN_DIR": str(self.root / "bin"),
        }
        plain = subprocess.run(
            [str(PLUGIN / "install.sh")], env=base, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertNotIn("hooks.Stop", plain.stdout)
        asked = subprocess.run(
            [str(PLUGIN / "install.sh")],
            env=dict(base, CODEX_LEAD_HOOKS="1"),
            capture_output=True,
            text=True,
            timeout=30,
        )

        def toml(out):  # the printed config starts after the instructions
            return tomllib.loads(out[out.index("[mcp_servers.agent-runtime]") :])

        hooks = toml(asked.stdout)["hooks"]
        for event, suffix in (
            ("Stop", " stop --cli codex"),
            ("SessionStart", " context --cli codex"),
            ("UserPromptSubmit", " context --cli codex"),
        ):
            commands = [h["command"] for group in hooks[event] for h in group["hooks"]]
            self.assertEqual(len(commands), 1, event)
            self.assertTrue(commands[0].endswith(suffix), commands)
            self.assertIn("agent-runtime-hook", commands[0])
        self.assertIn("PreToolUse", hooks)  # the existing guard blocks stay
        self.assertNotIn("Stop", toml(plain.stdout).get("hooks", {}))

    def test_shim_exits_zero_when_its_script_is_missing(self):
        """agent-runtime/R125"""
        (self.root / "bin").mkdir()
        (self.root / "hooks").mkdir()
        shim = self.root / "bin" / "agent-runtime-hook"
        shutil.copy(SHIM, shim)  # no hooks/stop_guard.py next to it
        done = subprocess.run(
            ["/bin/sh", str(shim), "stop", "--cli", "codex"],
            input="{}",
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)

    def test_attention_line_and_default_subcommand_is_still_the_pre_tool_use_guard(self):
        """agent-runtime/R126"""
        self.worker(self.store, self.team_id)
        self.last_wait(self.store, self.team_id, "attention", 1)
        self.assertEqual(
            self.line(self.context(self.PROMPT)), "agent-runtime: 1 workers need you — check status"
        )
        out, err, code = self.cli.run(
            self.SHIM_CMD,
            {"tool_name": "Bash", "tool_input": {"command": "hc send w hi"}},
            args=("--cli", "codex"),
        )
        self.assertTrue(denied(out), err)


if __name__ == "__main__":
    unittest.main()
