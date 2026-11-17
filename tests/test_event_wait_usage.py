"""What a wait returns to its caller: per-worker usage rows, and the wait description's words."""

import json
import sys

from test_event_wait import (
    AsyncRPC,
    EventRig,
)
from test_mcp_server import RUNTIME_SRC
from tools_harness import ToolCase

sys.path.insert(0, str(RUNTIME_SRC))
import pytest

pytestmark = pytest.mark.integration


class WaitUsageTests(EventRig):
    rpc: AsyncRPC
    assignments: list

    def transcript(self, assignment, name="session-w1"):
        """The Claude transcript a worker of cwd /tmp writes: one marked prompt, one model call."""
        directory = self.dir / "home" / ".claude" / "projects" / "-tmp"
        directory.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "type": "user",
                "timestamp": "2026-11-12T10:00:01.000Z",
                "message": {
                    "role": "user",
                    "content": "work\n\nUse assignment_id=%s." % assignment,
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-11-12T10:00:02.000Z",
                "message": {
                    "id": "m1",
                    "role": "assistant",
                    "model": "claude-opus-5-5",
                    "content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}],
                    "usage": {
                        "input_tokens": 7,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 3,
                        "output_tokens": 2,
                    },
                },
            },
        ]
        path = directory / (name + ".jsonl")
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    @pytest.mark.spec("Wait usage rows")
    def test_S9_wait_rows_of_bound_workers_carry_usage_and_the_rest_carry_none(self):
        """S9: a returned row whose worker has a measurement carries `usage`; another row has no key."""
        self.prepare()
        self.transcript(self.assignments[0]["assignment_id"])
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        rows = {a["name"]: a for a in batch["assignments"]}
        self.assertEqual(
            rows["w1"]["usage"],
            {
                "input_tokens": 7,
                "cached_input_tokens": 3,
                "cache_write_tokens": 5,
                "output_tokens": 2,
                "model_calls": 1,
                "tool_calls": 1,
            },
        )
        self.assertNotIn("usage", rows["w2"])


class WaitDescriptionTests(ToolCase):
    def test_S7_wait_description_names_the_early_return_and_the_word_budget(self):
        """S7: the wait description says it returns as soon as a report is undelivered, that unfinished lists
        the work still open and that the caller waits again for the rest; all descriptions stay within 400 words."""
        session = self.start()
        tools = session.send("tools/list")["result"]["tools"]
        description = next(t for t in tools if t["name"] == "wait")["description"]
        self.assertIn("undelivered", description)
        self.assertIn("unfinished", description)
        self.assertIn("wait again", description)
        words = 0
        for tool in tools:
            words += len(tool["description"].split())
            words += sum(
                len(p.get("description", "").split())
                for p in tool["inputSchema"]["properties"].values()
            )
        self.assertLessEqual(words, 400)
