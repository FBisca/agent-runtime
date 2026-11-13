"""Per-assignment usage measured from a worker's own session log: `usage.assignment_usage`.

The logs are written under a temporary HOME the way each CLI writes them; the runtime finds a log from
the worker's metadata alone. Fixtures under tests/fixtures/usage are trimmed real shapes.
"""

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

RUNTIME_SRC = Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"
sys.path.insert(0, str(RUNTIME_SRC))

from agent_runtime import usage  # noqa: E402

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures" / "usage"
CLAUDE_SESSION = "11111111-2222-4333-8444-555555555555"
CODEX_ROOT = "019a0000-0000-7000-8000-000000000001"
CODEX_CHILD = "019a0000-0000-7000-8000-00000000000b"


def stamp(second):
    return "2026-11-12T10:00:%02d.000Z" % second


def prompt(task, assignment):
    """The prompt `spawn` and `resume` send: the task, then the runtime's own marker last."""
    return "%s\n\nUse assignment_id=%s for report(kind=progress|complete|blocked|decision)." % (
        task,
        assignment,
    )


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def append_jsonl(path, rows):
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row) + "\n")


def claude_user(text, at, **extra):
    return dict(
        {
            "type": "user",
            "timestamp": stamp(at),
            "sessionId": CLAUDE_SESSION,
            "message": {"role": "user", "content": text},
        },
        **extra,
    )


def claude_assistant(message_id, at, usage_fields, blocks=(), model="claude-opus-5-5"):
    return {
        "type": "assistant",
        "timestamp": stamp(at),
        "sessionId": CLAUDE_SESSION,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "content": list(blocks),
            "usage": usage_fields,
        },
    }


def claude_tokens(fresh, write, read, output):
    return {
        "input_tokens": fresh,
        "cache_creation_input_tokens": write,
        "cache_read_input_tokens": read,
        "output_tokens": output,
    }


def cost_state(total):
    return {"type": "cost-state", "sessionId": CLAUDE_SESSION, "totalCostUSD": total}


TOOL = {"type": "tool_use", "id": "tu", "name": "Bash", "input": {"command": "echo hi"}}


class UsageCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="usage_parse.")
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
        }
        self.spawned_ms = usage.now_ms() - 60000

    def register(self, worker, cli, assignment):
        path = usage.write_worker_metadata(
            self.env,
            worker,
            cli=cli,
            cwd=str(self.cwd),
            assignment_id=assignment,
            spawned_ms=self.spawned_ms,
            base_commit=None,
            session_id=None,
            session_log=None,
        )
        self.assertIsNotNone(path)

    def sidecar(self, worker):
        return self.root / "state" / "agent-runtime" / "worktrees" / ("%s.usage.json" % worker)

    def claude_dir(self):
        slug = re.sub(r"[^A-Za-z0-9]", "-", str(self.cwd))
        return self.home / ".claude" / "projects" / slug

    def claude_log(self):
        return self.claude_dir() / (CLAUDE_SESSION + ".jsonl")

    def claude_fixture(self):
        """The trimmed transcript and its two subagent files, in place."""
        directory = self.claude_dir()
        directory.mkdir(parents=True)
        shutil.copy(FIXTURES / "claude-session.jsonl", self.claude_log())
        subagents = directory / CLAUDE_SESSION / "subagents"
        subagents.mkdir(parents=True)
        shutil.copy(FIXTURES / "claude-subagent-a1.jsonl", subagents / "agent-a1.jsonl")
        shutil.copy(FIXTURES / "claude-subagent-a2.jsonl", subagents / "agent-a2.jsonl")
        return self.claude_log()

    def claude_rows(self, rows):
        write_jsonl(self.claude_log(), rows)
        return self.claude_log()

    def codex_day(self):
        return self.home / ".codex" / "sessions" / "2026" / "11" / "12"

    def codex_fixture(self, child=True):
        """The root rollout and the guardian child thread's own file, in place."""
        day = self.codex_day()
        day.mkdir(parents=True)
        files = [("codex-root.jsonl", "rollout-2026-11-12T10-00-00-%s.jsonl" % CODEX_ROOT)]
        if child:
            files.append(
                ("codex-child.jsonl", "rollout-2026-11-12T10-00-23-%s.jsonl" % CODEX_CHILD)
            )
        for source, name in files:
            text = (FIXTURES / source).read_text().replace("CWD", str(self.cwd))
            (day / name).write_text(text)
        return day / files[0][1]

    def measure(self, worker):
        return usage.assignment_usage(self.env, worker)


class ClaudeUsageTests(UsageCase):
    def test_S1_each_message_counts_once_and_subagents_are_credited_by_time(self):
        self.register("cw", "claude", "CA1")
        self.claude_fixture()
        got = self.measure("cw")
        self.assertEqual(list(got), ["CA1", "CA2"])
        # m1 repeats under one message.id; m3 is <synthetic>; the subagent's m9 is CA1's by time.
        self.assertEqual(
            got["CA1"],
            {
                "input_tokens": 117,
                "cached_input_tokens": 2100,
                "cache_write_tokens": 50,
                "output_tokens": 53,
                "model_calls": 3,
                "tool_calls": 4,
                "cost_usd": 0.25,
            },
        )
        self.assertEqual(
            got["CA2"],
            {
                "input_tokens": 7,
                "cached_input_tokens": 2000,
                "cache_write_tokens": 200,
                "output_tokens": 41,
                "model_calls": 2,
                "tool_calls": 1,
                "cost_usd": 0.15,
            },
        )

    def test_S1_a_subagent_record_before_any_marked_prompt_is_credited_to_nothing(self):
        self.register("cw", "claude", "CA1")
        self.claude_rows(
            [
                claude_user("<command-name>/clear</command-name>", 0),
                claude_user(prompt("go", "CA1"), 10),
                claude_assistant("m1", 11, claude_tokens(1, 0, 0, 1)),
            ]
        )
        side = self.claude_dir() / CLAUDE_SESSION / "subagents" / "agent-early.jsonl"
        write_jsonl(side, [claude_assistant("e1", 5, claude_tokens(99, 0, 0, 99))])
        self.assertEqual(self.measure("cw")["CA1"]["input_tokens"], 1)

    def test_S4_a_checkpoints_increase_is_credited_to_the_assignment_active_when_written(self):
        self.register("cw", "claude", "CA1")
        self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                cost_state(1.5),
                claude_user(prompt("two", "CA2"), 10),
                cost_state(1.75),
                cost_state(2.0),
                claude_user(prompt("three", "CA3"), 20),
                cost_state(5.0),
            ]
        )
        got = self.measure("cw")
        self.assertEqual(
            {key: value["cost_usd"] for key, value in got.items()},
            {"CA1": 1.5, "CA2": 0.5, "CA3": 3.0},
        )

    def test_S4_a_slice_without_a_checkpoint_carries_zero_when_a_later_one_exists_else_none(self):
        self.register("cw", "claude", "CA1")
        self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_user(prompt("two", "CA2"), 10),
                cost_state(0.5),
                claude_user(prompt("three", "CA3"), 20),
            ]
        )
        got = self.measure("cw")
        self.assertEqual(got["CA1"]["cost_usd"], 0)
        self.assertEqual(got["CA2"]["cost_usd"], 0.5)
        self.assertNotIn("cost_usd", got["CA3"])

    def test_S4_a_log_without_any_checkpoint_carries_no_cost(self):
        self.register("cw", "claude", "CA1")
        self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_assistant("m1", 2, claude_tokens(3, 0, 0, 4)),
            ]
        )
        self.assertNotIn("cost_usd", self.measure("cw")["CA1"])

    def test_S5_a_figure_no_record_carries_is_absent_not_zero(self):
        self.register("cw", "claude", "CA1")
        self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_assistant("m1", 2, {"input_tokens": 3, "output_tokens": 4}),
                claude_user(prompt("two", "CA2"), 10),
                claude_assistant("m2", 11, {"input_tokens": 1, "cache_read_input_tokens": 0}),
                claude_user(prompt("three", "CA3"), 20),
            ]
        )
        got = self.measure("cw")
        self.assertEqual(
            got["CA1"], {"input_tokens": 3, "output_tokens": 4, "model_calls": 1, "tool_calls": 0}
        )
        self.assertEqual(
            got["CA2"],
            {"input_tokens": 1, "cached_input_tokens": 0, "model_calls": 1, "tool_calls": 0},
        )
        self.assertEqual(got["CA3"], {"model_calls": 0, "tool_calls": 0})

    def test_S6_each_slice_runs_from_the_prompt_whose_last_marker_is_its_id(self):
        self.register("cw", "claude", "CA1")
        quoted = prompt("fix the findings of assignment_id=CA1 and CA7", "CA2")
        self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_assistant(
                    "m1",
                    2,
                    claude_tokens(1, 0, 0, 1),
                    [
                        {"type": "text", "text": "I am assignment_id=CA5"},
                        dict(TOOL, input={"command": "echo assignment_id=CA6"}),
                    ],
                ),
                claude_user(
                    [{"type": "tool_result", "tool_use_id": "tu", "content": "assignment_id=CA5"}],
                    3,
                ),
                claude_user("assignment_id=CA8", 4, isMeta=True),
                claude_user("Summary of assignment_id=CA9", 5, isCompactSummary=True),
                claude_assistant("m2", 6, claude_tokens(10, 0, 0, 10)),
                claude_user(quoted, 20),
                claude_assistant("m3", 21, claude_tokens(100, 0, 0, 100)),
                claude_user("thanks, no marker here", 30),
                claude_assistant("m4", 31, claude_tokens(1000, 0, 0, 1000)),
            ]
        )
        side = self.claude_dir() / CLAUDE_SESSION / "subagents" / "agent-a.jsonl"
        write_jsonl(
            side,
            [
                claude_user("go, assignment_id=CA4", 7),
                claude_assistant("s1", 8, claude_tokens(5, 0, 0, 5)),
            ],
        )
        got = self.measure("cw")
        self.assertEqual(list(got), ["CA1", "CA2"])
        self.assertEqual(got["CA1"]["input_tokens"], 1 + 10 + 5)
        self.assertEqual(got["CA1"]["tool_calls"], 1)
        # An unmarked prompt starts no slice: its records stay with the assignment before it.
        self.assertEqual(got["CA2"]["input_tokens"], 100 + 1000)

    def test_S7_no_metadata_no_log_or_a_foreign_log_measures_nothing(self):
        self.assertEqual(self.measure("ghost"), {})
        self.register("cw", "claude", "CA1")
        self.assertEqual(self.measure("cw"), {})  # no transcript yet
        self.claude_rows([claude_user(prompt("someone else's", "CA99"), 1)])
        self.assertEqual(self.measure("cw"), {})  # a transcript that is not this worker's
        self.assertFalse(self.sidecar("cw").exists())

    def test_S7_a_truncated_last_line_or_garbage_counts_only_the_complete_lines(self):
        self.register("cw", "claude", "CA1")
        log = self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_assistant("m1", 2, claude_tokens(3, 0, 0, 4)),
            ]
        )
        with log.open("a", encoding="utf-8") as stream:
            stream.write("not json at all\n[1, 2]\n")
            stream.write(json.dumps(claude_assistant("m2", 3, claude_tokens(100, 0, 0, 100)))[:40])
        got = self.measure("cw")
        self.assertEqual(got["CA1"]["input_tokens"], 3)
        self.assertEqual(got["CA1"]["model_calls"], 1)
        # The writer finishes the line later: the next call counts it, and only once.
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(claude_assistant("m2", 3, claude_tokens(100, 0, 0, 100)))[40:])
            stream.write("\n")
        self.assertEqual(self.measure("cw")["CA1"]["input_tokens"], 103)

    def test_S7_an_unreadable_or_unwritable_state_never_raises(self):
        self.register("cw", "claude", "CA1")
        log = self.claude_rows(
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_assistant("m1", 2, claude_tokens(3, 0, 0, 4)),
            ]
        )
        real_open = Path.open

        def unreadable(candidate, *args, **kwargs):
            if candidate == log:
                raise PermissionError("unreadable test log")
            return real_open(candidate, *args, **kwargs)

        with mock.patch.object(Path, "open", unreadable):
            self.assertEqual(self.measure("cw"), {})
        with mock.patch.object(usage.os, "replace", side_effect=OSError("read-only state")):
            self.assertEqual(self.measure("cw")["CA1"]["input_tokens"], 3)
        log.write_bytes(b"\xff\xfe\x00 not utf-8 \xc3\n" + log.read_bytes())
        self.assertIsInstance(self.measure("cw"), dict)

    def test_S8_a_second_call_reads_only_the_appended_bytes_and_matches_a_fresh_parse(self):
        self.register("cw", "claude", "CA1")
        log = self.claude_fixture()
        first = self.measure("cw")
        sidecar = self.sidecar("cw")
        self.assertEqual(stat.S_IMODE(sidecar.stat().st_mode), 0o600)
        size = log.stat().st_size
        self.assertIn(str(size), sidecar.read_text())
        # json.loads is the parse of one line; metadata, sidecar and binding reads are the same on every call.
        with mock.patch.object(usage.json, "loads", wraps=json.loads) as loads:
            self.assertEqual(self.measure("cw"), first)
            baseline = loads.call_count
        rows = [
            claude_assistant("m5", 23, claude_tokens(4, 0, 0, 6), [TOOL]),
            claude_assistant("m5", 23, claude_tokens(4, 0, 0, 6)),
            claude_user(prompt("third", "CA3"), 30),
            claude_assistant("m6", 31, claude_tokens(1, 1, 1, 1)),
            cost_state(0.9),
        ]
        append_jsonl(log, rows)
        append_jsonl(
            self.claude_dir() / CLAUDE_SESSION / "subagents" / "agent-a2.jsonl",
            [claude_assistant("m11", 32, claude_tokens(2, 0, 0, 2))],
        )
        with mock.patch.object(usage.json, "loads", wraps=json.loads) as loads:
            grown = self.measure("cw")
            self.assertEqual(loads.call_count, baseline + len(rows) + 1)
        self.assertEqual(grown["CA1"], first["CA1"])
        self.assertEqual(grown["CA2"]["model_calls"], first["CA2"]["model_calls"] + 1)
        self.assertEqual(grown["CA3"]["model_calls"], 2)
        self.assertEqual(grown["CA3"]["input_tokens"], 3)
        self.assertEqual(grown["CA3"]["cost_usd"], 0.5)
        sidecar.unlink()
        self.assertEqual(self.measure("cw"), grown)

    def test_S8_a_log_that_shrank_or_was_replaced_is_read_again_from_the_start(self):
        self.register("cw", "claude", "CA1")
        log = self.claude_fixture()
        self.measure("cw")
        keep = log.read_text().splitlines()[:6]
        log.write_text("\n".join(keep) + "\n")
        shrunk = self.measure("cw")
        self.assertEqual(list(shrunk), ["CA1"])
        self.assertEqual(shrunk["CA1"]["model_calls"], 4)  # m1, m2 and both subagents
        self.assertNotIn("cost_usd", shrunk["CA1"])
        replaced = self.claude_dir() / "replacement.jsonl"
        write_jsonl(
            replaced,
            [
                claude_user(prompt("one", "CA1"), 1),
                claude_assistant("n1", 2, claude_tokens(500, 0, 0, 500)),
                claude_assistant("n2", 3, claude_tokens(500, 0, 0, 500)),
                claude_assistant("n3", 4, claude_tokens(500, 0, 0, 500)),
            ],
        )
        os.replace(replaced, log)  # a new file of another identity, bigger than the old offset
        got = self.measure("cw")
        self.assertEqual(got["CA1"]["input_tokens"], 1500 + 7 + 2)  # and the subagents
        self.sidecar("cw").unlink()
        self.assertEqual(self.measure("cw"), got)

    def test_S8_the_sidecar_holds_offsets_and_numbers_never_log_text(self):
        self.register("cw", "claude", "CA1")
        log = self.claude_fixture()
        append_jsonl(
            log,
            [
                claude_assistant(
                    "m7",
                    24,
                    claude_tokens(1, 1, 1, 1),
                    [
                        {"type": "text", "text": "SECRET-ANSWER"},
                        dict(TOOL, input={"command": "cat SECRET-FILE"}),
                    ],
                )
            ],
        )
        self.measure("cw")
        text = self.sidecar("cw").read_text()
        for secret in ("do the task", "fix the findings", "SECRET", "echo", "Bash", "look around"):
            self.assertNotIn(secret, text)
        self.assertEqual(stat.S_IMODE(self.sidecar("cw").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.sidecar("cw").parent.stat().st_mode), 0o700)


def codex_row(at, kind, payload):
    return {"timestamp": stamp(at), "type": kind, "payload": payload}


def codex_user(at, text):
    return codex_row(
        at,
        "response_item",
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    )


def codex_total(at, gross, cached, output, reasoning, cache_write=0):
    usage_fields = {
        "input_tokens": gross,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": gross + output,
    }
    return codex_row(
        at,
        "event_msg",
        {"type": "token_count", "info": {"total_token_usage": usage_fields}},
    )


def codex_meta(at, thread, source="user", **extra):
    return codex_row(
        at,
        "session_meta",
        dict(
            {
                "id": thread,
                "session_id": CODEX_ROOT,
                "cwd": "CWD",
                "source": "exec",
                "thread_source": source,
            },
            **extra,
        ),
    )


class CodexUsageTests(UsageCase):
    def test_S2_the_root_threads_cumulative_increase_per_slice_and_children_credited_by_time(self):
        self.register("xw", "codex", "XA1")
        self.codex_fixture()
        got = self.measure("xw")
        # The child's prompt quotes XA1, XA2 and XA7, and starts no slice.
        self.assertEqual(list(got), ["XA1", "XA2"])
        # An identical repeat and `info: null` add nothing and count as no model call.
        self.assertEqual(
            got["XA1"],
            {
                "input_tokens": 700,
                "cached_input_tokens": 1800,
                "cache_write_tokens": 0,
                "output_tokens": 120,
                "reasoning_tokens": 40,
                "model_calls": 2,
                "tool_calls": 2,
            },
        )
        # XA2: the root's increase plus the child's own totals (its cumulative starts from zero).
        self.assertEqual(
            got["XA2"],
            {
                "input_tokens": 800,
                "cached_input_tokens": 1300,
                "cache_write_tokens": 0,
                "output_tokens": 80,
                "reasoning_tokens": 30,
                "model_calls": 3,
                "tool_calls": 1,
            },
        )

    def test_S2_the_worker_binds_to_the_root_rollout_whichever_file_is_found_first(self):
        self.register("xw", "codex", "XA1")
        root = self.codex_fixture()
        child = root.with_name("rollout-2026-11-12T09-59-59-%s.jsonl" % CODEX_CHILD)
        root.with_name("rollout-2026-11-12T10-00-23-%s.jsonl" % CODEX_CHILD).rename(child)
        self.assertEqual(usage.resolve_session_log(self.env, "xw"), root)
        self.assertEqual(self.measure("xw")["XA1"]["model_calls"], 2)

    def test_S2_a_child_thread_credits_by_time_across_several_assignments(self):
        self.register("xw", "codex", "XA1")
        day = self.codex_day()
        write_jsonl(
            day / ("rollout-2026-11-12T10-00-00-%s.jsonl" % CODEX_ROOT),
            [
                codex_meta(0, CODEX_ROOT, cwd=str(self.cwd)),
                codex_user(1, prompt("one", "XA1")),
                codex_user(10, prompt("two", "XA2")),
            ],
        )
        write_jsonl(
            day / ("rollout-2026-11-12T10-00-05-%s.jsonl" % CODEX_CHILD),
            [
                codex_meta(5, CODEX_CHILD, "guardian_review", cwd=str(self.cwd)),
                codex_user(5, "assignment_id=XA2"),
                codex_total(6, 100, 40, 10, 2),
                codex_total(11, 300, 100, 30, 6),
                codex_row(11, "response_item", {"type": "function_call", "name": "x"}),
            ],
        )
        got = self.measure("xw")
        self.assertEqual((got["XA1"]["input_tokens"], got["XA1"]["model_calls"]), (60, 1))
        self.assertEqual((got["XA2"]["input_tokens"], got["XA2"]["model_calls"]), (140, 1))
        self.assertEqual((got["XA1"]["tool_calls"], got["XA2"]["tool_calls"]), (0, 1))

    def test_S5_codex_never_carries_a_dollar_figure(self):
        self.register("xw", "codex", "XA1")
        root = self.codex_fixture()
        append_jsonl(
            root,
            [
                {"type": "cost-state", "totalCostUSD": 9.9},
                codex_row(30, "event_msg", {"type": "token_count", "info": {"total_cost_usd": 1}}),
            ],
        )
        for assignment in self.measure("xw").values():
            self.assertNotIn("cost_usd", assignment)

    def test_S5_a_total_without_a_field_leaves_the_figure_out(self):
        self.register("xw", "codex", "XA1")
        write_jsonl(
            self.codex_day() / ("rollout-2026-11-12T10-00-00-%s.jsonl" % CODEX_ROOT),
            [
                codex_meta(0, CODEX_ROOT, cwd=str(self.cwd)),
                codex_user(1, prompt("one", "XA1")),
                codex_row(
                    2,
                    "event_msg",
                    {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 10, "output_tokens": 3}},
                    },
                ),
            ],
        )
        self.assertEqual(
            self.measure("xw")["XA1"],
            {"input_tokens": 10, "output_tokens": 3, "model_calls": 1, "tool_calls": 0},
        )

    def test_S6_only_root_thread_user_messages_start_slices_and_the_last_marker_wins(self):
        self.register("xw", "codex", "XA1")
        self.codex_fixture()
        root = self.codex_day() / ("rollout-2026-11-12T10-00-00-%s.jsonl" % CODEX_ROOT)
        append_jsonl(
            root,
            [
                codex_row(
                    25,
                    "response_item",
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "see assignment_id=XA5"}],
                    },
                ),
                codex_row(
                    25,
                    "response_item",
                    {"type": "function_call_output", "output": "assignment_id=XA6"},
                ),
                codex_total(26, 4300, 3000, 200, 65),
            ],
        )
        got = self.measure("xw")
        self.assertEqual(list(got), ["XA1", "XA2"])
        self.assertEqual(got["XA2"]["model_calls"], 4)

    def test_S7_a_codex_log_with_garbage_and_a_truncated_tail_counts_the_complete_lines(self):
        self.register("xw", "codex", "XA1")
        root = self.codex_fixture(child=False)
        with root.open("a", encoding="utf-8") as stream:
            stream.write("garbage\n")
            stream.write(json.dumps(codex_total(30, 9000, 1, 1, 1))[:50])
        got = self.measure("xw")
        self.assertEqual(got["XA2"]["model_calls"], 2)
        self.assertEqual(got["XA2"]["input_tokens"], 500)

    def test_S8_codex_incremental_equals_fresh_including_children(self):
        self.register("xw", "codex", "XA1")
        root = self.codex_fixture()
        self.measure("xw")
        append_jsonl(root, [codex_total(30, 5000, 3000, 200, 70)])
        child = self.codex_day() / ("rollout-2026-11-12T10-00-23-%s.jsonl" % CODEX_CHILD)
        append_jsonl(child, [codex_total(31, 600, 150, 20, 7)])
        grown = self.measure("xw")
        self.assertEqual(grown["XA2"]["model_calls"], 5)
        self.sidecar("xw").unlink()
        self.assertEqual(self.measure("xw"), grown)


PI_SESSION = "019a0000-0000-7000-8000-0000000000fe"


class PiUsageTests(UsageCase):
    def pi_dir(self):
        """<PI_CODING_AGENT_DIR or ~/.pi/agent>/sessions/--<cwd slug>--/, the cwd without its leading slash."""
        slug = "--" + str(self.cwd).lstrip("/").replace("/", "-") + "--"
        return self.home / ".pi" / "agent" / "sessions" / slug

    def pi_fixture(self):
        return (FIXTURES / "pi-session.jsonl").read_text().replace("CWD", str(self.cwd))

    def pi_log(self, rows=None):
        directory = self.pi_dir()
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / ("2026-11-12T10-00-00-000Z_%s.jsonl" % PI_SESSION)
        if rows is None:
            path.write_text(self.pi_fixture())
        else:
            write_jsonl(path, rows)
        return path

    def test_S3_each_slice_sums_its_messages_usage_calls_tools_and_cost(self):
        self.register("pw", "pi", "PA1")
        self.pi_log()
        got = self.measure("pw")
        self.assertEqual(list(got), ["PA1", "PA2"])
        self.assertEqual(
            got["PA1"],
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
        self.assertEqual(
            got["PA2"],
            {
                "input_tokens": 20,
                "cached_input_tokens": 1300,
                "cache_write_tokens": 0,
                "output_tokens": 10,
                "model_calls": 1,
                "tool_calls": 2,
                "cost_usd": 0.005,
            },
        )

    def test_S3_the_session_is_bound_by_the_first_prompt_after_spawn_under_the_cwd_slug(self):
        self.register("pw", "pi", "PA1")
        path = self.pi_log()
        self.assertEqual(usage.resolve_session_log(self.env, "pw"), path)
        # Another worker's marker in the only session of this cwd: nothing is bound.
        other = self.pi_dir() / "other.jsonl"
        other.write_text(path.read_text().replace("assignment_id=PA1", "assignment_id=PA7"))
        path.unlink()
        self.assertIsNone(usage.resolve_session_log(self.env, "pw"))
        self.assertEqual(self.measure("pw"), {})

    def test_S3_the_pi_agent_directory_comes_from_the_environment(self):
        self.register("pw", "pi", "PA1")
        alternative = self.root / "alt-pi"
        self.env["PI_CODING_AGENT_DIR"] = str(alternative)
        slug = "--" + str(self.cwd).lstrip("/").replace("/", "-") + "--"
        (alternative / "sessions" / slug).mkdir(parents=True)
        path = alternative / "sessions" / slug / ("x_%s.jsonl" % PI_SESSION)
        path.write_text(self.pi_fixture())
        self.assertEqual(usage.resolve_session_log(self.env, "pw"), path)
        self.assertEqual(self.measure("pw")["PA1"]["model_calls"], 2)

    def test_S7_a_truncated_pi_tail_counts_only_the_complete_lines(self):
        self.register("pw", "pi", "PA1")
        rows = self.pi_fixture().splitlines()
        self.pi_log([json.loads(line) for line in rows[:5]])
        log = next(self.pi_dir().glob("*.jsonl"))
        with log.open("a", encoding="utf-8") as stream:
            stream.write("not json at all\n[1, 2]\n")
            stream.write(rows[5][:60])
        got = self.measure("pw")
        self.assertEqual(got["PA1"]["input_tokens"], 300)
        self.assertEqual(got["PA1"]["model_calls"], 1)
        self.assertEqual(got["PA1"]["tool_calls"], 1)
        with log.open("a", encoding="utf-8") as stream:
            stream.write(rows[5][60:] + "\n")
        completed = self.measure("pw")["PA1"]
        self.assertEqual((completed["input_tokens"], completed["model_calls"]), (350, 2))

    def test_S7_a_pi_log_without_a_usage_record_carries_no_figure(self):
        self.register("pw", "pi", "PA1")
        header = {
            "type": "session",
            "version": 3,
            "id": PI_SESSION,
            "timestamp": "2026-11-12T10:00:00.000Z",
            "cwd": str(self.cwd),
        }
        prompt_row = {
            "type": "message",
            "id": "u1",
            "timestamp": "2026-11-12T10:00:01.000Z",
            "message": {"role": "user", "content": [{"type": "text", "text": prompt("do", "PA1")}]},
        }
        self.pi_log([header, prompt_row])
        self.assertEqual(self.measure("pw"), {"PA1": {"model_calls": 0, "tool_calls": 0}})

    def test_S8_a_second_pi_call_reads_only_the_appended_bytes_and_matches_a_fresh_parse(self):
        self.register("pw", "pi", "PA1")
        log = self.pi_log()
        first = self.measure("pw")
        appended = {
            "type": "message",
            "id": "late",
            "timestamp": "2026-11-12T10:00:30.000Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "toolCall",
                        "id": "tc9",
                        "name": "bash",
                        "arguments": {"command": "pwd"},
                    }
                ],
                "usage": {
                    "input": 1,
                    "output": 2,
                    "cacheRead": 3,
                    "cacheWrite": 4,
                    "reasoning": 5,
                    "cost": {"total": 0.001},
                },
            },
        }
        append_jsonl(log, [appended])
        grown = self.measure("pw")
        self.assertEqual(grown["PA2"]["output_tokens"], 12)
        self.assertEqual(grown["PA2"]["model_calls"], 2)
        self.assertEqual(grown["PA2"]["tool_calls"], 3)
        self.assertAlmostEqual(grown["PA2"]["cost_usd"], 0.006, places=6)
        self.assertEqual(grown["PA1"], first["PA1"])
        self.assertTrue(self.sidecar("pw").is_file())
        self.sidecar("pw").unlink()
        self.assertEqual(self.measure("pw"), grown)

    def test_S8_a_pi_log_that_shrank_or_was_replaced_is_read_again_from_the_start(self):
        self.register("pw", "pi", "PA1")
        log = self.pi_log()
        self.measure("pw")
        rows = [json.loads(line) for line in self.pi_fixture().splitlines()]
        write_jsonl(log, rows[:4])  # shrank
        shrunk = self.measure("pw")
        self.assertEqual(
            shrunk,
            {
                "PA1": {
                    "input_tokens": 300,
                    "cached_input_tokens": 900,
                    "cache_write_tokens": 100,
                    "output_tokens": 40,
                    "reasoning_tokens": 10,
                    "model_calls": 1,
                    "tool_calls": 1,
                    "cost_usd": 0.012,
                }
            },
        )
        self.pi_log()  # replaced by the full log
        self.assertEqual(self.measure("pw")["PA2"]["model_calls"], 1)


if __name__ == "__main__":
    unittest.main()
