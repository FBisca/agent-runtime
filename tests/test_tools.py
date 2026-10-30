"""Tool behaviour over stdio with a fake hc on PATH that logs argv; no real hc mutation."""

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from test_mcp_server import SERVER, Session

FAKE = '''#!%(python)s
import json, sys, time
argv = sys.argv[1:]
with open(%(log)r, "a") as log:
    log.write(json.dumps(argv) + "\\n")
replies = json.load(open(%(replies)r))
key = argv[0] + ":" + argv[1] if len(argv) > 1 and argv[0] + ":" + argv[1] in replies else argv[0]
reply = replies.get(key, {"rc": 0, "out": ""})
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

    def start(self, replies=None, env=None):
        replies = dict(replies or {})
        replies.setdefault("get:t0", {"out": raw("lead", None)})
        replies.setdefault("get:t1", {"out": raw("worker", "t0")})
        (self.dir / "replies").write_text(json.dumps(replies))
        hc = self.dir / "bin" / "hc"
        hc.write_text(FAKE % {"python": sys.executable, "log": str(self.log), "replies": str(self.dir / "replies")})
        hc.chmod(0o755)
        full = {"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home"),
                "HC_TERMINAL_ID": "t0"}
        full.update(env or {})
        session = Session(full)
        self.addCleanup(session.close)
        session.handshake()
        return session

    def call(self, session, name, arguments):
        result = session.send("tools/call", {"name": name, "arguments": arguments})["result"]
        return result, result["content"][0]["text"]

    def runs(self, command=None):
        calls = [json.loads(l) for l in self.log.read_text().splitlines()]
        return [c for c in calls if command is None or c[0] == command]


class SpawnTests(ToolCase):
    ARGS = {"kind": "worker", "name": "w1", "task": "do x", "cwd": "/tmp/c"}

    def test_S2_claude_worker_runs_hc_run_bg_with_task_and_report_line(self):
        """agent-runtime-tools/S2"""
        session = self.start({"run": {"out": json.dumps({"first_message": {"sent": True}})},
                              "get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="claude"))
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("run")
        self.assertEqual(argv[:9], ["run", "--bg", "--name", "w1", "--cwd", "/tmp/c", "--parent", "t0", "--json"])
        self.assertEqual(argv[9:14], ["claude", "--agent", "implementer", "--permission-mode", "auto"])
        self.assertTrue(argv[14].startswith("do x"))
        self.assertIn("When done or stuck, call the agent-runtime report tool with your result.", argv[14])
        self.assertNotIn("hc send", argv[14])
        info = json.loads(text)
        self.assertEqual((info["name"], info["address"], info["parent"]), ("w1", "t9", "t0"))

    def test_S2_codex_worker_is_no_daemon_with_implementer_instructions(self):
        """agent-runtime-tools/S2"""
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, text = self.call(session, "spawn", dict(self.ARGS, cli="codex"))
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("run")
        self.assertEqual(argv[9:11], ["codex", "--no-daemon"])
        self.assertTrue(argv[11].startswith("CODEX IMPLEMENTER RULES"))
        self.assertIn("do x", argv[11])
        self.assertIn("call the agent-runtime report tool", argv[11])
        self.assertNotIn("permission-mode", " ".join(argv))

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
