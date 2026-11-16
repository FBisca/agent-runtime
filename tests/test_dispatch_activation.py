"""The dispatch flag: the structured path only when it is opted in, and the legacy
spawn/report/wait/resume behaviour when it is absent or off."""

import json
import sys

from test_mcp_server import RUNTIME_SRC
from tools_harness import ToolCase, raw

sys.path.insert(0, str(RUNTIME_SRC))
import pytest

pytestmark = pytest.mark.integration


class ActivationTests(ToolCase):
    def test_status_without_hc_falls_back_to_legacy_with_dispatch_on(self):
        """agent-runtime/R56"""
        out = []
        for flag in ("0", "1"):
            session = self.start(
                env={"HC_TERMINAL_ID": "", "HERDR_ENV": "", "AGENT_RUNTIME_DISPATCH": flag}
            )
            result, text = self.call(session, "status", {"name": "w1"})
            out.append((result["isError"], text))
        self.assertEqual(out[0], out[1])
        self.assertNotIn("gap: add native support", out[1][1])

    def test_default_preserves_legacy_spawn_report_wait_resume_and_schema(self):
        """agent-runtime/R58"""
        for flag in (None, "0"):
            with self.subTest(flag=flag):
                env = {} if flag is None else {"AGENT_RUNTIME_DISPATCH": flag}
                lead = self.start(
                    {
                        "get:w1": {"out": raw("w1", "t0")},
                        "wait": {"out": '{"reason":"idle"}'},
                        "ls": {"out": '{"terminals":[]}'},
                    },
                    env,
                )
                result, text = self.call(
                    lead, "spawn", {"kind": "worker", "name": "w1", "task": "work", "cwd": "/tmp"}
                )
                self.assertFalse(result["isError"], text)
                self.assertEqual(json.loads(text), {"name": "w1", "address": "t9", "parent": "t0"})
                self.assertNotIn("structuredContent", result)
                self.assertNotIn("assignment_id", self.runs("run")[-1][-1])
                self.assertIn("report(text)", self.runs("run")[-1][-1])
                tools = (lead.send("tools/list") or {})["result"]["tools"]
                report_schema = next((t["inputSchema"] for t in tools if t["name"] == "report"), {})
                self.assertEqual(report_schema["required"], ["text"])
                result, text = self.call(lead, "wait", {"target": "any", "timeout": 20})
                self.assertFalse(result["isError"], text)
                self.assertEqual(
                    json.loads(text),
                    {"wait": {"exit": 0, "news": {"reason": "idle"}}, "workers": []},
                )
                self.assertEqual(
                    self.runs("wait")[-1],
                    ["wait", "--children", "any", "--timeout", "20", "--json"],
                )
                self.call(lead, "resume", {"name": "w1", "task": "continue"})
                self.assertEqual(
                    self.runs("send")[-1], ["send", "--wait", "--timeout", "170", "w1", "continue"]
                )
                worker = self.start(env=dict(env, HC_TERMINAL_ID="t1"))
                result, text = self.call(worker, "report", {"text": "done"})
                self.assertFalse(result["isError"], text)
                self.assertEqual(text, "reported to t0")
                self.assertEqual(self.runs("send")[-1], ["send", "t0", "done"])
                self.assertFalse((self.dir / "home/.local/state/agent-runtime").exists())
