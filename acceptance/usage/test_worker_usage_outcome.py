"""worker-usage/S12: a Claude, a Codex and a Pi worker, each on two assignments, measured from their own session logs.

Each worker's log is written under a temporary HOME the way its CLI writes it; the runtime finds it from the worker's
metadata alone and returns one numeric dict per assignment. A resume is a prompt sent into the live session, so the
second assignment continues the same log; Claude subagents and Codex guardian-review threads write their own files
and are credited by time.
"""

import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

import pytest

RUNTIME_SRC = Path(__file__).resolve().parents[2] / "packages" / "agent-runtime" / "src"

pytestmark = pytest.mark.integration


def load_usage():
    """The runtime's usage module, imported lazily so the file collects before it exists."""
    if str(RUNTIME_SRC) not in sys.path:
        sys.path.insert(0, str(RUNTIME_SRC))
    from agent_runtime import usage

    return usage


def iso(seconds):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + ".000Z"


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def prompt(task, assignment):
    """The prompt `spawn` and `resume` send: the task, then the runtime's own marker last."""
    return "%s\n\nUse assignment_id=%s for report(kind=progress|complete|blocked|decision)." % (
        task,
        assignment,
    )


class WorkerUsageOutcome(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="usage_outcome.")
        self.addCleanup(temp.cleanup)
        self.root = Path(os.path.realpath(temp.name))
        self.home = self.root / "home"
        self.home.mkdir()
        self.cwd = self.root / "my_work.tree"
        self.cwd.mkdir()
        self.env = {
            "HOME": str(self.home),
            "XDG_STATE_HOME": str(self.root / "state"),
            "CODEX_HOME": str(self.home / ".codex"),
            "PI_CODING_AGENT_DIR": str(self.home / ".pi" / "agent"),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        self.usage = load_usage()
        self.spawned = time.time() - 60
        self.t0 = self.spawned + 5

    def register(self, worker, cli, assignment):
        """The metadata `spawn` writes for a worker (its first assignment)."""
        path = self.usage.write_worker_metadata(
            self.env,
            worker,
            cli=cli,
            cwd=str(self.cwd),
            assignment_id=assignment,
            spawned_ms=int(self.spawned * 1000),
            base_commit=None,
            session_id=None,
            session_log=None,
            profile_applied=None,
            profile_reason=None,
            profile_name=None,
        )
        self.assertIsNotNone(path, "worker metadata was not written")

    def claude_log(self):
        # Claude Code names a project directory after its cwd, every non-alphanumeric character a dash.
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(self.cwd))
        directory = self.home / ".claude" / "projects" / slug
        session = "11111111-2222-4333-8444-555555555555"

        def user(text, at, **extra):
            return dict(
                {
                    "type": "user",
                    "timestamp": iso(at),
                    "sessionId": session,
                    "message": {"role": "user", "content": text},
                },
                **extra,
            )

        def assistant(message_id, at, usage, blocks, model="claude-opus-5-5"):
            return {
                "type": "assistant",
                "timestamp": iso(at),
                "sessionId": session,
                "message": {
                    "id": message_id,
                    "role": "assistant",
                    "model": model,
                    "content": blocks,
                    "usage": usage,
                },
            }

        def tokens(fresh, write, read, output):
            return {
                "input_tokens": fresh,
                "cache_creation_input_tokens": write,
                "cache_read_input_tokens": read,
                "output_tokens": output,
            }

        tool = {
            "type": "tool_use",
            "id": "tu",
            "name": "Bash",
            "input": {"command": "echo assignment_id=CA9"},
        }
        rows = [
            user(prompt("do the task", "CA1"), self.t0 + 1),
            user("<system-reminder>assignment_id=CA8</system-reminder>", self.t0 + 1, isMeta=True),
            assistant(
                "m1", self.t0 + 2, tokens(100, 50, 1000, 20), [{"type": "text", "text": "ok"}]
            ),
            assistant("m1", self.t0 + 2, tokens(100, 50, 1000, 20), [dict(tool, id="tu1")]),
            user(
                [{"type": "tool_result", "tool_use_id": "tu1", "content": "assignment_id=CA9"}],
                self.t0 + 3,
            ),
            assistant(
                "m2",
                self.t0 + 4,
                tokens(10, 0, 1100, 30),
                [dict(tool, id="tu2"), dict(tool, id="tu3")],
            ),
            assistant(
                "m3",
                self.t0 + 5,
                tokens(9, 9, 9, 9),
                [{"type": "text", "text": "x"}],
                "<synthetic>",
            ),
            {"type": "cost-state", "sessionId": session, "totalCostUSD": 0.25, "modelUsage": {}},
            user(prompt("fix the findings of assignment_id=CA1", "CA2"), self.t0 + 20),
            assistant(
                "m4", self.t0 + 21, tokens(5, 200, 2000, 40), [{"type": "text", "text": "done"}]
            ),
            {"type": "cost-state", "sessionId": session, "totalCostUSD": 0.40, "modelUsage": {}},
        ]
        write_jsonl(directory / (session + ".jsonl"), rows)

        def sidechain(row):
            return dict(row, isSidechain=True)

        write_jsonl(
            directory / session / "subagents" / "agent-a1.jsonl",
            [
                sidechain(user("look around, assignment_id=CA7", self.t0 + 3)),
                sidechain(assistant("m9", self.t0 + 3, tokens(7, 0, 0, 3), [dict(tool, id="tu9")])),
            ],
        )
        write_jsonl(
            directory / session / "subagents" / "agent-a2.jsonl",
            [
                sidechain(user("check the fix", self.t0 + 22)),
                sidechain(
                    assistant("m10", self.t0 + 22, tokens(2, 0, 0, 1), [dict(tool, id="tu10")])
                ),
            ],
        )

    def codex_logs(self):
        day = self.home / ".codex" / "sessions" / "2026" / "11" / "12"
        session = "019a0000-0000-7000-8000-000000000001"
        child = "019a0000-0000-7000-8000-00000000000b"

        def line(at, kind, payload):
            return {"timestamp": iso(at), "type": kind, "payload": payload}

        def meta(at, thread, source="user", **extra):
            return line(
                at,
                "session_meta",
                dict(
                    {
                        "id": thread,
                        "session_id": session,
                        "timestamp": iso(at),
                        "cwd": str(self.cwd),
                        "source": "exec",
                        "thread_source": source,
                    },
                    **extra,
                ),
            )

        def user(at, text):
            return line(
                at,
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            )

        state = {"last": None}

        def total(at, gross, cached, output, reasoning, restart=False):
            usage = {
                "input_tokens": gross,
                "cached_input_tokens": cached,
                "cache_write_input_tokens": 0,
                "output_tokens": output,
                "reasoning_output_tokens": reasoning,
                "total_tokens": gross + output,
            }
            before = None if restart else state["last"]
            last = (
                {key: usage[key] - before[key] for key in usage}
                if before and before != usage
                else usage
            )
            state["last"] = usage
            return line(
                at,
                "event_msg",
                {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": usage,
                        "last_token_usage": last,
                        "last_usage_includes_reasoning": False,
                        "model_context_window": 258400,
                    },
                },
            )

        def call(at, kind):
            return line(
                at,
                "response_item",
                {"type": kind, "name": "exec", "call_id": "c", "input": "echo assignment_id=XA9"},
            )

        empty = line(self.t0 + 1, "event_msg", {"type": "token_count", "info": None})
        write_jsonl(
            day / ("rollout-2026-11-12T10-00-00-%s.jsonl" % session),
            [
                meta(self.t0, session),
                user(self.t0, "<environment_context>synthetic</environment_context>"),
                user(self.t0 + 1, prompt("do the task", "XA1")),
                empty,
                total(self.t0 + 2, 1000, 600, 50, 20),
                call(self.t0 + 2, "custom_tool_call"),
                total(self.t0 + 3, 1000, 600, 50, 20),
                call(self.t0 + 3, "function_call"),
                total(self.t0 + 4, 2500, 1800, 120, 40),
                user(self.t0 + 20, prompt("fix the findings of assignment_id=XA1", "XA2")),
                total(self.t0 + 21, 3300, 2300, 150, 50),
                call(self.t0 + 21, "custom_tool_call"),
                total(self.t0 + 22, 4200, 3000, 190, 65),
            ],
        )
        write_jsonl(
            day / ("rollout-2026-11-12T10-00-23-%s.jsonl" % child),
            [
                meta(
                    self.t0 + 23,
                    child,
                    source="guardian_review",
                    parent_thread_id=session,
                ),
                user(
                    self.t0 + 23,
                    "<transcript>assignment_id=XA1 ... assignment_id=XA2 ... assignment_id=XA7"
                    "</transcript>",
                ),
                total(self.t0 + 24, 400, 100, 10, 5, restart=True),
            ],
        )

    def pi_log(self):
        slug = "--" + str(self.cwd).lstrip("/").replace("/", "-") + "--"
        directory = self.home / ".pi" / "agent" / "sessions" / slug
        session = "019a0000-0000-7000-8000-0000000000fe"

        def message(at, body):
            return {"type": "message", "id": "r%d" % at, "timestamp": iso(at), "message": body}

        def user(text, at):
            return message(at, {"role": "user", "content": [{"type": "text", "text": text}]})

        def assistant(at, usage, blocks):
            return message(
                at,
                {
                    "role": "assistant",
                    "provider": "ollama-cloud",
                    "model": "glm-5.3",
                    "content": blocks,
                    "usage": usage,
                },
            )

        def tokens(fresh, output, read, write, cost, reasoning=None):
            usage = {
                "input": fresh,
                "output": output,
                "cacheRead": read,
                "cacheWrite": write,
                "totalTokens": fresh + output + read + write,
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": cost},
            }
            if reasoning is not None:
                usage["reasoning"] = reasoning
            return usage

        tool = {"type": "toolCall", "id": "tc", "name": "bash", "arguments": {"command": "ls"}}
        stamp = iso(self.t0).replace(":", "-").replace(".", "-")
        write_jsonl(
            directory / ("%s_%s.jsonl" % (stamp, session)),
            [
                {
                    "type": "session",
                    "version": 3,
                    "id": session,
                    "timestamp": iso(self.t0),
                    "cwd": str(self.cwd),
                },
                user(prompt("do the task", "PA1"), int(self.t0 + 1)),
                assistant(
                    int(self.t0 + 2),
                    tokens(300, 40, 900, 100, 0.012, reasoning=10),
                    [{"type": "text", "text": "about assignment_id=PA9"}, tool],
                ),
                message(
                    int(self.t0 + 3),
                    {
                        "role": "toolResult",
                        "toolCallId": "tc",
                        "toolName": "bash",
                        "content": [{"type": "text", "text": "assignment_id=PA9"}],
                    },
                ),
                assistant(
                    int(self.t0 + 4),
                    tokens(50, 60, 1200, 0, 0.008),
                    [{"type": "text", "text": "ok"}],
                ),
                user(prompt("fix the findings of assignment_id=PA1", "PA2"), int(self.t0 + 20)),
                assistant(
                    int(self.t0 + 21), tokens(20, 10, 1300, 0, 0.005), [tool, dict(tool, id="tc2")]
                ),
            ],
        )

    def measure(self, env, worker):
        """`usage.assignment_usage`, the interface D4 fixes; missing at base, that is an assertion, not an error."""
        self.assertTrue(
            hasattr(self.usage, "assignment_usage"),
            "usage.assignment_usage(env, worker) is not built yet",
        )
        return self.usage.assignment_usage(env, worker)

    def assertUsage(self, got, expected):
        """Every expected figure exactly (cost to six places); a `None` figure must be absent, never 0."""
        for key, value in expected.items():
            if value is None:
                self.assertNotIn(key, got, "%s must be absent, not 0" % key)
            elif key == "cost_usd":
                self.assertIn(key, got)
                self.assertAlmostEqual(got[key], value, places=6)
            else:
                self.assertEqual(got.get(key), value, key)
        for key, value in got.items():
            self.assertIsInstance(value, (int, float), key)
            self.assertNotIsInstance(value, bool, key)

    def test_S12_each_assignment_gets_its_own_slice_for_all_three_harnesses(self):
        """agent-runtime: worker-usage/S12"""
        self.register("cw", "claude", "CA1")
        self.register("xw", "codex", "XA1")
        self.register("pw", "pi", "PA1")
        self.claude_log()
        self.codex_logs()
        self.pi_log()

        claude = self.measure(self.env, "cw")
        self.assertEqual(set(claude), {"CA1", "CA2"})
        self.assertUsage(
            claude["CA1"],
            {
                "input_tokens": 117,
                "cached_input_tokens": 2100,
                "cache_write_tokens": 50,
                "output_tokens": 53,
                "reasoning_tokens": None,
                "model_calls": 3,
                "tool_calls": 4,
                "cost_usd": 0.25,
            },
        )
        self.assertUsage(
            claude["CA2"],
            {
                "input_tokens": 7,
                "cached_input_tokens": 2000,
                "cache_write_tokens": 200,
                "output_tokens": 41,
                "reasoning_tokens": None,
                "model_calls": 2,
                "tool_calls": 1,
                "cost_usd": 0.15,
            },
        )

        codex = self.measure(self.env, "xw")
        self.assertEqual(set(codex), {"XA1", "XA2"})
        self.assertUsage(
            codex["XA1"],
            {
                "input_tokens": 700,
                "cached_input_tokens": 1800,
                "cache_write_tokens": 0,
                "output_tokens": 120,
                "reasoning_tokens": 40,
                "model_calls": 2,
                "tool_calls": 2,
                "cost_usd": None,
            },
        )
        self.assertUsage(
            codex["XA2"],
            {
                "input_tokens": 800,
                "cached_input_tokens": 1300,
                "cache_write_tokens": 0,
                "output_tokens": 80,
                "reasoning_tokens": 30,
                "model_calls": 3,
                "tool_calls": 1,
                "cost_usd": None,
            },
        )

        pi = self.measure(self.env, "pw")
        self.assertEqual(set(pi), {"PA1", "PA2"})
        self.assertUsage(
            pi["PA1"],
            {
                "input_tokens": 350,
                "cached_input_tokens": 2100,
                "cache_write_tokens": 100,
                "output_tokens": 100,
                "reasoning_tokens": 10,
                "model_calls": 2,
                "tool_calls": 1,
                "cost_usd": 0.02,
            },
        )
        self.assertUsage(
            pi["PA2"],
            {
                "input_tokens": 20,
                "cached_input_tokens": 1300,
                "cache_write_tokens": 0,
                "output_tokens": 10,
                "reasoning_tokens": None,
                "model_calls": 1,
                "tool_calls": 2,
                "cost_usd": 0.005,
            },
        )

    def test_S12_a_second_measurement_after_the_log_grew_matches_a_fresh_one(self):
        """agent-runtime: worker-usage/S12"""
        self.register("pw", "pi", "PA1")
        self.pi_log()
        first = self.measure(self.env, "pw")
        self.assertEqual(first["PA2"]["output_tokens"], 10)
        log = next((self.home / ".pi" / "agent" / "sessions").rglob("*.jsonl"))
        with log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "message",
                        "id": "late",
                        "timestamp": iso(self.t0 + 30),
                        "message": {
                            "role": "assistant",
                            "content": [],
                            "usage": {
                                "input": 1,
                                "output": 2,
                                "cacheRead": 3,
                                "cacheWrite": 4,
                                "totalTokens": 10,
                                "cost": {"total": 0.001},
                            },
                        },
                    }
                )
                + "\n"
            )
        grown = self.measure(self.env, "pw")
        self.assertEqual(grown["PA2"]["output_tokens"], 12)
        self.assertEqual(grown["PA2"]["model_calls"], 2)
        self.assertAlmostEqual(grown["PA2"]["cost_usd"], 0.006, places=6)
        self.assertEqual(grown["PA1"], first["PA1"])
        sidecar = self.root / "state" / "agent-runtime" / "worktrees" / "pw.usage.json"
        self.assertTrue(sidecar.is_file(), "the incremental offsets live in <worker>.usage.json")
        sidecar.unlink()
        self.assertEqual(self.measure(self.env, "pw"), grown)

    def test_control_the_fixture_logs_are_json_and_carry_each_workers_markers(self):
        """The fixtures hold today: every line parses, and each worker's log carries both of its markers."""
        self.claude_log()
        self.codex_logs()
        self.pi_log()
        roots = {
            "CA": self.home / ".claude" / "projects",
            "XA": self.home / ".codex" / "sessions",
            "PA": self.home / ".pi" / "agent" / "sessions",
        }
        for prefix, root in roots.items():
            with self.subTest(prefix):
                text = ""
                for path in root.rglob("*.jsonl"):
                    for line in path.read_text(encoding="utf-8").splitlines():
                        json.loads(line)
                    text += path.read_text(encoding="utf-8")
                self.assertIn("assignment_id=%s1" % prefix, text)
                self.assertIn("assignment_id=%s2" % prefix, text)

    def test_control_the_metadata_spawn_writes_reads_back(self):
        """The metadata the measurement starts from is the one spawn writes today."""
        self.register("xw", "codex", "XA1")
        meta = self.usage.read_worker_metadata(self.env, "xw")
        self.assertEqual((meta["cli"], meta["assignment_id"]), ("codex", "XA1"))

    def test_S12_a_worker_without_a_log_or_metadata_measures_nothing(self):
        """agent-runtime: worker-usage/S12"""
        for worker, cli in (("nc", "claude"), ("nx", "codex"), ("np", "pi")):
            with self.subTest(cli):
                self.register(worker, cli, "N-" + cli)
                self.assertEqual(self.measure(self.env, worker), {})
        self.assertEqual(self.measure(self.env, "ghost"), {})


if __name__ == "__main__":
    unittest.main()
