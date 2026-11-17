"""The tool verbs over stdio with a fake hc: send, message, resume, wait, collect, stop and
report, and the gap a non-dispatch runtime answers instead."""

import json
import sys
import time
from types import SimpleNamespace
from unittest import mock

from tools_harness import (
    FIXTURES,
    RUNTIME_SRC,
    InProcessToolCase,
    raw,
)

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import hc_tools

pytestmark = pytest.mark.integration


class SendTests(InProcessToolCase):
    CHILD = {"get:w1": {"out": raw("w1", "t0")}}

    def test_S4_message_refuses_a_terminal_that_is_not_my_child(self):
        session = self.start({"get:w1": {"out": raw("w1", "tX")}})
        for name, args in (
            ("message", {"to": "w1", "text": "hi"}),
            ("resume", {"name": "w1", "task": "go"}),
        ):
            result, text = self.call(session, name, args)
            self.assertTrue(result["isError"], name)
            self.assertIn("not your child", text)
        self.assertEqual(self.runs("send"), [])

    def test_S4_message_is_hc_send_without_force(self):
        session = self.start(self.CHILD)
        result, _ = self.call(session, "message", {"to": "w1", "text": "hello"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.runs("send"), [["send", "w1", "hello"]])

    def test_S4_resume_adds_wait_and_never_force(self):
        session = self.start(self.CHILD)
        result, _ = self.call(session, "resume", {"name": "w1", "task": "continue"})
        self.assertFalse(result["isError"])
        (argv,) = self.runs("send")
        self.assertEqual(argv[:2], ["send", "--wait"])
        self.assertEqual(argv[-2:], ["w1", "continue"])
        self.assertNotIn("--force", argv)

    def test_S4_target_cannot_be_an_option(self):
        session = self.start(self.CHILD)
        result, _ = self.call(session, "message", {"to": "--force", "text": "x"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("send"), [])


class WaitCollectStopReportTests(InProcessToolCase):
    def test_S5_wait_any_waits_on_children_and_returns_their_status(self):
        listing = json.dumps(
            {"terminals": [{"address": "t1", "parent": "t0"}, {"address": "t8", "parent": "tX"}]}
        )
        session = self.start({"ls": {"out": listing}})
        result, text = self.call(session, "wait", {"target": "any", "timeout": 20})
        self.assertFalse(result["isError"], text)
        (argv,) = self.runs("wait")
        self.assertEqual(argv, ["wait", "--children", "any", "--timeout", "20", "--json"])
        workers = json.loads(text)["workers"]
        self.assertEqual([w["runtime"] for w in workers], ["hc"])

    def test_S5_wait_all_and_named_worker(self):
        session = self.start(
            {"ls": {"out": json.dumps({"terminals": []})}, "get:w1": {"out": raw("w1", "t0")}}
        )
        self.call(session, "wait", {"target": "all"})
        self.call(session, "wait", {"target": "w1"})
        self.assertEqual(
            self.runs("wait"),
            [
                ["wait", "--children", "all", "--timeout", "170", "--json"],
                ["wait", "w1", "--timeout", "170", "--json"],
            ],
        )

    def test_S5_wait_returns_hcs_parsed_news_without_truncation(self):
        news = {"reason": "idle", "terminal": "t1", "note": "x" * 900}
        session = self.start(
            {"wait": {"out": json.dumps(news)}, "ls": {"out": json.dumps({"terminals": []})}}
        )
        _, text = self.call(session, "wait", {"target": "any"})
        self.assertEqual(json.loads(text)["wait"]["news"], news)

    def test_S5_wait_timeout_is_capped(self):
        session = self.start({"ls": {"out": json.dumps({"terminals": []})}})
        self.call(session, "wait", {"target": "any", "timeout": 9999})
        self.assertEqual(self.runs("wait")[0][4], "590")

    def test_S5_long_wait_does_not_block_ping_and_cancel_ends_it(self):
        session = self.start(
            {"wait": {"sleep": 30}, "ls": {"out": json.dumps({"terminals": []})}}, process=True
        )
        write = lambda m: (  # noqa: E731
            session.proc.stdin.write(json.dumps(m) + "\n"),
            session.proc.stdin.flush(),
        )
        write(
            {
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {"name": "wait", "arguments": {"target": "any"}},
            }
        )
        write({"jsonrpc": "2.0", "id": 51, "method": "ping"})
        self.assertEqual(json.loads(session.proc.stdout.readline())["id"], 51)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 50}})
        started = time.monotonic()
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=10), 0)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(session.proc.stdout.read(), "")

    @pytest.mark.spec("Structured report delivery")
    def test_unregistered_wait_returns_empty_without_hc_wait(self):
        """supersedes  transport."""
        session = self.start(env={"AGENT_RUNTIME_DISPATCH": "1"})
        for target in ("any", "all", "w1"):
            result, text = self.call(session, "wait", {"target": target, "timeout": 0})
            self.assertFalse(result["isError"], text)
            self.assertEqual(json.loads(text)["reason"], "empty")
        self.assertEqual(self.runs("wait"), [])

    @pytest.mark.spec("Structured report delivery")
    def test_wait_rejects_invalid_timeout(self):
        session = self.start(env={"AGENT_RUNTIME_DISPATCH": "1"})
        for timeout in (-1, True, "3"):
            result, _ = self.call(session, "wait", {"target": "any", "timeout": timeout})
            self.assertTrue(result["isError"])

    def returned_batch(self, usage_of):
        """hc_tools.wait on a canned batch of three rows (two of one resumed worker), usage_of measuring."""
        batch = {
            "reason": "completed",
            "messages": [],
            "assignments": [
                {"assignment_id": "A1", "name": "ok", "state": "complete"},
                {"assignment_id": "A2", "name": "ok", "state": "complete"},
                {"assignment_id": "A3", "name": "boom", "state": "complete"},
                {"assignment_id": "A4", "name": "none", "state": "complete"},
            ],
            "unfinished": [],
            "receipt": None,
            "more": False,
        }
        ctx = SimpleNamespace(actor=None, owned_team="team", store=object())
        env = {"AGENT_RUNTIME_DISPATCH": "1", "HC_TERMINAL_ID": "t0"}
        with (
            mock.patch.object(hc_tools.dispatch_wait, "wait", return_value=batch),
            mock.patch.object(hc_tools.usage, "assignment_usage", side_effect=usage_of) as measure,
        ):
            result = hc_tools.wait({"target": "all", "timeout": 0}, env, ctx)
        return json.loads(result["content"][0]["text"]), measure

    @pytest.mark.spec("Wait usage rows")
    def test_S9_a_failure_while_measuring_never_fails_the_wait(self):
        """S9: one worker's measurement raising costs that row its usage, not the wait; each worker is measured once."""

        def usage_of(env, worker):
            if worker == "boom":
                raise RuntimeError("log exploded")
            return {"A1": {"input_tokens": 1}, "A2": {"input_tokens": 2}} if worker == "ok" else {}

        body, measure = self.returned_batch(usage_of)
        rows = {a["assignment_id"]: a for a in body["assignments"]}
        self.assertEqual(rows["A1"]["usage"], {"input_tokens": 1})
        self.assertEqual(rows["A2"]["usage"], {"input_tokens": 2})
        self.assertNotIn("usage", rows["A3"])
        self.assertNotIn("usage", rows["A4"])
        self.assertEqual(sorted(c.args[1] for c in measure.call_args_list), ["boom", "none", "ok"])
        self.assertEqual(body["reason"], "completed")

    @pytest.mark.spec("Structured report delivery")
    def test_long_collect_does_not_block_ping_and_cancel_ends_it(self):
        """retains transport cancellation coverage."""
        session = self.start({"read": {"sleep": 30}}, process=True)
        write = lambda m: (  # noqa: E731
            session.proc.stdin.write(json.dumps(m) + "\n"),
            session.proc.stdin.flush(),
        )
        write(
            {
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {"name": "collect", "arguments": {"name": "w1"}},
            }
        )
        write({"jsonrpc": "2.0", "id": 51, "method": "ping"})
        self.assertEqual(json.loads(session.proc.stdout.readline())["id"], 51)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 50}})
        started = time.monotonic()
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=10), 0)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(session.proc.stdout.read(), "")

    def test_S6_collect_returns_text_after_last_prompt_line_bounded(self):
        body = (
            ["old output", "❯ first task", "old answer", "› do the thing"]
            + ["line %d" % i for i in range(300)]
            + ["❯ "]
        )
        session = self.start(
            {
                "read": {"out": "\n".join(body)},
                "get:w1": {"out": raw("w1", "t0", app="claude-code")},
            }
        )
        result, text = self.call(session, "collect", {"name": "w1"})
        self.assertFalse(result["isError"])
        self.assertNotIn("old answer", text)
        self.assertNotIn("do the thing", text)
        self.assertLessEqual(len(text.splitlines()), 200)
        self.assertIn("line 299", text)
        self.assertTrue(self.runs("read")[0][:2] == ["read", "w1"])

    def test_S6_collect_on_codex_skips_the_composer_and_footer(self):
        screen = (FIXTURES / "codex_working_interrupted.txt").read_text()
        session = self.start(
            {"read": {"out": screen}, "get:w1": {"out": raw("w1", "t0", app="codex")}}
        )
        _, text = self.call(session, "collect", {"name": "w1"})
        self.assertIn("Running npm run build", text)
        self.assertNotIn("Ask Codex to do anything", text)
        self.assertNotIn("gpt-5", text)

    def test_S6_collect_without_prompt_returns_whole_screen(self):
        session = self.start({"read": {"out": "a\nb"}, "get:w1": {"out": raw("w1", "t0")}})
        _, text = self.call(session, "collect", {"name": "w1"})
        self.assertEqual(text, "a\nb")

    def test_S7_stop_refuses_a_terminal_that_is_not_my_child(self):
        session = self.start({"get:other": {"out": raw("other", "tX")}})
        result, text = self.call(session, "stop", {"name": "other"})
        self.assertTrue(result["isError"])
        self.assertIn("not your child", text)
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])

    def test_S7_stop_kills_then_removes_a_child(self):
        session = self.start({"get:w1": {"out": raw("w1", "t0")}})
        result, _ = self.call(session, "stop", {"name": "w1"})
        self.assertFalse(result["isError"])
        self.assertEqual([c[0] for c in self.runs() if c[0] in ("kill", "rm")], ["kill", "rm"])

    def test_S8_worker_report_sends_to_parent(self):
        session = self.start(env={"HC_TERMINAL_ID": "t1"})
        result, _ = self.call(session, "report", {"text": "done: abc"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.runs("send"), [["send", "t0", "done: abc"]])

    @pytest.mark.spec("Structured report delivery")
    def test_unregistered_worker_report_refuses_injection(self):
        """replaces  hc-send assertion."""
        session = self.start(env={"HC_TERMINAL_ID": "t1", "AGENT_RUNTIME_DISPATCH": "1"})
        result, _ = self.call(session, "report", {"text": "done: abc"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("send"), [])

    def test_S8_lead_report_is_an_error(self):
        session = self.start()
        result, text = self.call(session, "report", {"text": "x"})
        self.assertTrue(result["isError"])
        self.assertEqual(self.runs("send"), [])


class StopCleanupTests(InProcessToolCase):
    def test_S7_stop_still_removes_a_terminal_that_already_exited(self):
        session = self.start(
            {"get:w1": {"out": raw("w1", "t0")}, "kill": {"rc": 1, "err": "already exited"}}
        )
        result, text = self.call(session, "stop", {"name": "w1"})
        self.assertFalse(result["isError"], text)
        self.assertEqual([c[0] for c in self.runs() if c[0] in ("kill", "rm")], ["kill", "rm"])

    def test_S2_cancelling_spawn_ends_the_terminal_it_started(self):
        session = self.start({"run": {"sleep": 30}}, process=True)
        write = lambda m: (  # noqa: E731
            session.proc.stdin.write(json.dumps(m) + "\n"),
            session.proc.stdin.flush(),
        )
        write(
            {
                "jsonrpc": "2.0",
                "id": 60,
                "method": "tools/call",
                "params": {
                    "name": "spawn",
                    "arguments": {"kind": "worker", "name": "w1", "task": "t", "cwd": "/tmp"},
                },
            }
        )
        deadline = time.monotonic() + 5
        while not self.runs("run") and time.monotonic() < deadline:
            time.sleep(0.05)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 60}})
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=15), 0)
        self.assertEqual(
            [c[:2] for c in self.runs() if c[0] in ("kill", "rm")], [["kill", "w1"], ["rm", "w1"]]
        )


class RuntimeGapTests(InProcessToolCase):
    def test_S9_herdmates_worker_tools_return_a_gap_naming_the_native_tool(self):
        session = self.start(env={"HERDR_PANE_ID": "p1", "CLAUDECODE": "1"})
        for name, args in (
            ("spawn", {"kind": "worker", "name": "w", "task": "t", "cwd": "/"}),
            ("message", {"to": "w", "text": "t"}),
            ("wait", {"target": "any"}),
            ("stop", {"name": "w"}),
            ("collect", {"name": "w"}),
        ):
            result, text = self.call(session, name, args)
            self.assertTrue(result["isError"], name)
            self.assertTrue(text.startswith("gap: add "), text)
            self.assertIn("subagent", text)
        self.assertEqual(self.runs(), [])

    def test_S9_native_worker_tools_return_a_gap_naming_the_subagent_tool(self):
        session = self.start(env={"HC_TERMINAL_ID": ""})
        result, text = self.call(session, "message", {"to": "w", "text": "t"})
        self.assertTrue(result["isError"])
        self.assertIn("subagent", text)
