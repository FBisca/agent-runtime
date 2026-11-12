"""Outcome oracle for the runtime-audit-fixes plan (the 2026-11-11 static audit, issue #34).

It drives the public seams only: the real MCP server over stdio against the fake hc of test_tools, the real
PreToolUse hook as a subprocess with JSON on stdin, the `agent-runtime` CLI, and, for the one store failure the
plan injects, the server's own call path in process with a patched store (as test_spawn_cleanup does).

A long-lived lead is limited by its active workers, not its history; the receipts guard protects only real
orchestrator state; a resume that fails on the store rolls back; the server survives malformed ids and bounds its
live calls; a repeated operation_id returns the first assignment; `resolve --json` reports a verified caller.

The `control` tests hold today and must keep holding: they prove each fixture is real, so a failing case fails
because of the defect, not the harness.
"""

import json
import os
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import pytest
from test_mcp_server import RUNTIME_SRC, SERVER
from test_tools import FAKE, ToolCase, raw

sys.path.insert(0, str(RUNTIME_SRC))
from agent_runtime import hc_tools, mcp_server  # noqa: E402
from agent_runtime.dispatch_store import Store  # noqa: E402

pytestmark = pytest.mark.integration

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "hooks" / "pre_tool_use.py"
CLI = PLUGIN / "server" / "agent_runtime.py"

LIMIT = 32
LIMIT_TEXT = "team limit is 32 active workers: stop an active worker, then retry"
ENV = {
    "CODEX_THREAD_ID": "lead-session",
    "HC_SCOPE": "lead-token",
    "HC_TERMINAL_SOCKET": "/hc/sock",
    "AGENT_RUNTIME_DISPATCH": "1",
}


def name(index):
    return "w%02d" % index


def worker_rows(count=40):
    """Fake hc replies for `count` children of t0, each with an address of its own, plus the live-name model."""
    replies = {"get:t0": {"out": raw("lead", None)}, "live": []}
    for index in range(count):
        row = {
            "address": "t%d" % (100 + index),
            "name": name(index),
            "parent": "t0",
            "status": "idle",
        }
        replies["get:" + row["name"]] = {"out": json.dumps(row)}
        replies["get:" + row["address"]] = {"out": json.dumps(row)}
    return replies


class FakeHc(ToolCase):
    """ToolCase's fake hc (argv log + replies file) without a server, for in-process and CLI callers."""

    def install(self, replies):
        replies = dict(replies)
        replies.setdefault("get:t0", {"out": raw("lead", None)})
        (self.dir / "replies").write_text(json.dumps(replies))
        hc = self.dir / "bin" / "hc"
        hc.write_text(
            FAKE
            % {
                "python": sys.executable,
                "log": str(self.log),
                "replies": str(self.dir / "replies"),
                "overlay": str(self.dir / "overlay"),
            }
        )
        hc.chmod(0o755)

    def sent_to(self, worker):
        return [c for c in self.runs("send") if len(c) > 1 and c[1] == worker]

    def launched(self, worker):
        return [c for c in self.runs("run") if worker in c]


# --- Team limit (#34) ---------------------------------------------------------------------------------------------


class TeamLimitOutcome(FakeHc):
    """runtime-audit-fixes/S1-S3 over the real MCP server: the limit counts active workers."""

    def lead(self):
        return self.start(worker_rows(), ENV)

    def spawn(self, session, index):
        return self.call(
            session, "spawn", {"kind": "worker", "name": name(index), "task": "work", "cwd": "/tmp"}
        )

    def fill(self, session, indexes):
        for index in indexes:
            result, text = self.spawn(session, index)
            self.assertFalse(
                result["isError"], "setup: spawn %s was refused: %s" % (name(index), text)
            )

    def stop(self, session, index):
        result, text = self.call(session, "stop", {"name": name(index)})
        self.assertFalse(result["isError"], "setup: stop %s was refused: %s" % (name(index), text))

    def full_team_with_history(self, session):
        """32 active workers and one stopped name: w00 is stopped, w01..w32 are active (33 registered)."""
        self.fill(session, range(LIMIT))
        self.stop(session, 0)
        result, text = self.spawn(session, LIMIT)
        self.assertFalse(
            result["isError"],
            "precondition (S1): the 32nd active worker after a stop was refused, so a full team cannot be "
            "built: %s" % text,
        )

    def test_S1_a_team_of_32_registered_and_31_stopped_spawns_a_new_name(self):
        """runtime-audit-fixes/S1, S17: the team limit counts active workers only."""
        session = self.lead()
        self.fill(session, range(LIMIT))
        for index in range(1, LIMIT):
            self.stop(session, index)
        result, text = self.spawn(session, LIMIT)
        self.assertFalse(
            result["isError"], "a new name was refused with one active worker: %s" % text
        )
        self.assertEqual(len(self.launched(name(LIMIT))), 1)

    def test_S2_a_new_name_at_32_active_is_refused_with_the_way_out_and_nothing_launches(self):
        """runtime-audit-fixes/S2, S17: the 33rd active worker is refused before anything launches."""
        session = self.lead()
        self.fill(session, range(LIMIT))
        result, text = self.spawn(session, LIMIT)
        self.assertTrue(result["isError"], "a 33rd active worker was spawned")
        self.assertIn(LIMIT_TEXT, text)
        self.assertEqual(self.launched(name(LIMIT)), [], "hc run was called for the refused name")

    def test_S2_resuming_a_stopped_name_at_32_active_is_refused_and_nothing_is_sent(self):
        """runtime-audit-fixes/S2: reactivating a stopped name takes a slot, so a full team refuses it."""
        session = self.lead()
        self.full_team_with_history(session)
        result, text = self.call(session, "resume", {"name": name(0), "task": "come back"})
        self.assertTrue(
            result["isError"], "a stopped name was reactivated as the 33rd active worker"
        )
        self.assertIn(LIMIT_TEXT, text)
        self.assertEqual(
            self.sent_to(name(0)), [], "the resume task was sent to the refused worker"
        )

    def concurrently(self, session, calls):
        """Send every tools/call before reading any reply: the server runs each on its own thread."""
        for index, (tool, arguments) in enumerate(calls):
            message = {
                "jsonrpc": "2.0",
                "id": 900 + index,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
            session.proc.stdin.write(json.dumps(message) + "\n")
        session.proc.stdin.flush()
        replies = {}
        while len(replies) < len(calls):
            reply = json.loads(session.proc.stdout.readline())
            replies[reply["id"]] = reply["result"]
        return [replies[900 + index] for index in range(len(calls))]

    def test_S2_a_reactivation_racing_a_new_spawn_for_the_last_slot_takes_it_once(self):
        """runtime-audit-fixes/S2: 31 active and one stopped name; the stopped name's resume and a new spawn race
        for the one free slot. The slot is reserved atomically, so exactly one wins and the other is refused."""
        session = self.lead()
        self.fill(session, range(LIMIT))
        self.stop(session, 0)
        resumed, spawned = self.concurrently(
            session,
            [
                ("resume", {"name": name(0), "task": "come back"}),
                ("spawn", {"kind": "worker", "name": name(LIMIT), "task": "work", "cwd": "/tmp"}),
            ],
        )
        self.assertEqual(
            sorted((resumed["isError"], spawned["isError"])), [False, True], (resumed, spawned)
        )
        refused = resumed if resumed["isError"] else spawned
        self.assertIn(LIMIT_TEXT, refused["content"][0]["text"])
        started = len(self.sent_to(name(0))) + len(self.launched(name(LIMIT)))
        self.assertEqual(started, 1, "both racers reached the worker")

    def test_S3_resuming_an_active_worker_in_a_full_team_with_history_takes_no_slot(self):
        """runtime-audit-fixes/S3: at 32 active (one name stopped before), an active resume succeeds."""
        session = self.lead()
        self.full_team_with_history(session)
        result, text = self.call(session, "resume", {"name": name(5), "task": "next step"})
        self.assertFalse(result["isError"], "an active resume was refused at 32 active: %s" % text)
        self.assertEqual(len(self.sent_to(name(5))), 1)
        refused, text = self.spawn(session, LIMIT + 1)
        self.assertTrue(refused["isError"], "a 33rd active worker was spawned after the resume")
        self.assertIn(LIMIT_TEXT, text)

    def test_control_a_spawn_under_the_limit_succeeds(self):
        session = self.lead()
        result, text = self.spawn(session, 0)
        self.assertFalse(result["isError"], text)
        self.assertEqual(len(self.launched(name(0))), 1)
        self.assertTrue(result["structuredContent"]["assignment_id"])

    def test_control_resuming_an_active_worker_of_a_full_team_succeeds(self):
        """S3 as it holds today: 32 registered, all active, and an active resume is sent."""
        session = self.lead()
        self.fill(session, range(LIMIT))
        result, text = self.call(session, "resume", {"name": name(5), "task": "next step"})
        self.assertFalse(result["isError"], text)
        self.assertEqual(len(self.sent_to(name(5))), 1)


# --- Receipts guard -----------------------------------------------------------------------------------------------

LEDGER = {"version": 1, "kind": "agent-orchestrator/ledger"}


class ReceiptsGuardOutcome(unittest.TestCase):
    """runtime-audit-fixes/S4-S6: the real hook as a subprocess. A worker is a native subagent (agent_id) or a
    session the runtime resolves as a spawned worker (a fake `agent-runtime` on PATH answers role worker)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "agent-runtime"
        fake.write_text('#!/bin/sh\nprintf \'{"runtime": "hc", "role": "%s"}\' "$FAKE_ROLE"\n')
        fake.chmod(0o755)

    def hook(self, payload, role="lead"):
        env = {
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "HOME": str(self.root),
            "FAKE_ROLE": role,
        }
        done = subprocess.run(
            [sys.executable, "-B", str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        return json.loads(done.stdout) if done.stdout.strip() else None

    def as_workers(self, payload):
        """The verdicts for one payload, as a native subagent and as a spawned worker."""
        return {
            "subagent": self.hook(dict(payload, agent_id="sub-1", agent_type="worker")),
            "worker": self.hook(payload, role="worker"),
        }

    @staticmethod
    def decision(out):
        return out["hookSpecificOutput"]["permissionDecision"] if out else "allow"

    def verdicts(self, payloads):
        """{(label, who): decision} for every payload, as a subagent and as a worker."""
        found = {}
        for label, payload in payloads:
            for who, out in self.as_workers(payload).items():
                found[(label, who)] = self.decision(out)
        return found

    @staticmethod
    def write(path, cwd=None, tool="Write"):
        payload = {"tool_name": tool, "tool_input": {"file_path": str(path), "content": "{}"}}
        if tool in ("Edit", "MultiEdit"):
            payload["tool_input"] = {"file_path": str(path), "old_string": "a", "new_string": "b"}
        return dict(payload, cwd=str(cwd)) if cwd else payload

    @staticmethod
    def bash(command, cwd=None):
        payload = {"tool_name": "Bash", "tool_input": {"command": command}}
        return dict(payload, cwd=str(cwd)) if cwd else payload

    def plan_state(self, state_root, ledger=LEDGER, key="agent-runtime-0123456789ab", slug="demo"):
        """`<root>/<repo-key>/<slug>/receipts/`, the sibling ledger `<slug>.json` and its `.lock`, on disk."""
        keyed = state_root / key
        receipts = keyed / slug / "receipts"
        receipts.mkdir(parents=True)
        (receipts / "S1.integration.1.json").write_text("{}")
        ledger_path = keyed / (slug + ".json")
        ledger_path.write_text(json.dumps(ledger))
        lock = keyed / (slug + ".json.lock")
        lock.write_text("")
        return keyed, receipts, ledger_path, lock

    def test_S4_a_repository_state_receipts_path_is_allowed_for_workers(self):
        """runtime-audit-fixes/S4, S17: `src/state/receipts/slice.ts` has no orchestrator ledger beside it."""
        repo = self.root / "repo"
        source = repo / "src" / "state" / "receipts" / "slice.ts"
        source.parent.mkdir(parents=True)
        source.write_text("export const a = 1;\n")
        relative = "src/state/receipts/slice.ts"
        payloads = [
            ("Write relative", self.write(relative, cwd=repo)),
            ("Write absolute", self.write(source)),
            ("Edit", self.write(source, tool="Edit")),
            ("MultiEdit", self.write(source, tool="MultiEdit")),
            (
                "apply_patch",
                {
                    "tool_name": "apply_patch",
                    "tool_input": {
                        "patch": "*** Begin Patch\n*** Update File: %s\n@@\n-a\n+b\n*** End Patch\n"
                        % relative
                    },
                    "cwd": str(repo),
                },
            ),
            ("echo >", self.bash("echo 'export const b = 2;' > %s" % relative, cwd=repo)),
            ("sed -i", self.bash("sed -i '' s/a/b/ %s" % relative, cwd=repo)),
            ("touch", self.bash("touch %s" % relative, cwd=repo)),
            ("rm", self.bash("rm %s" % source)),
        ]
        verdicts = self.verdicts(payloads)
        denied = sorted(key for key, decision in verdicts.items() if decision != "allow")
        self.assertEqual(denied, [], "repository paths were refused: %s" % denied)

    def test_S6_a_marked_ledgers_receipts_ledger_and_lock_are_denied_under_a_custom_root(self):
        """runtime-audit-fixes/S6: any root, written relative, absolute or through a symlink alias."""
        custom = self.root / "plans-root"
        keyed, receipts, ledger, lock = self.plan_state(custom)
        alias = self.root / "alias"
        alias.symlink_to(keyed)
        aliased_receipts = alias / "demo" / "receipts"
        receipt = receipts / "S1.integration.1.json"
        payloads = [
            ("Write receipt absolute", self.write(receipt)),
            (
                "Write receipt relative",
                self.write("demo/receipts/S1.integration.1.json", cwd=keyed),
            ),
            ("Write receipt via alias", self.write(aliased_receipts / "S1.integration.1.json")),
            ("Edit ledger", self.write(ledger, tool="Edit")),
            ("Write ledger via alias", self.write(alias / "demo.json")),
            ("Write lock", self.write(lock)),
            ("echo > receipt", self.bash("echo '{}' > %s" % receipt)),
            (
                "echo > receipt via alias",
                self.bash("echo '{}' > %s/S2.integration.1.json" % aliased_receipts),
            ),
            ("cp over ledger", self.bash("cp fake.json %s" % ledger)),
            ("rm lock", self.bash("rm -f %s" % lock)),
        ]
        verdicts = self.verdicts(payloads)
        allowed = sorted(key for key, decision in verdicts.items() if decision != "deny")
        self.assertEqual(allowed, [], "marked orchestrator state was writable: %s" % allowed)

    def test_S6_an_unmarked_ledger_outside_the_default_root_is_not_protected(self):
        """runtime-audit-fixes/S6: the documented gap until the orchestrator writes the marker (D5)."""
        unmarked = self.root / "elsewhere" / "state"
        _, receipts, ledger, _ = self.plan_state(unmarked, ledger={"version": 1})
        payloads = [
            ("Write receipt", self.write(receipts / "S1.integration.1.json")),
            ("Write ledger", self.write(ledger)),
            ("echo > receipt", self.bash("echo '{}' > %s/S1.integration.1.json" % receipts)),
        ]
        verdicts = self.verdicts(payloads)
        denied = sorted(key for key, decision in verdicts.items() if decision != "allow")
        self.assertEqual(
            denied, [], "an unmarked ledger outside the default root was guarded: %s" % denied
        )

    def test_control_a_default_root_receipt_is_denied_and_reading_it_is_allowed(self):
        """runtime-audit-fixes/S5, S17: the default root `~/.local/state/orchestrate/` stays protected."""
        default = self.root / ".local" / "state" / "orchestrate"
        _, receipts, _, _ = self.plan_state(default)
        receipt = receipts / "S1.integration.1.json"
        for payload in (self.write(receipt), self.bash("echo '{}' > %s" % receipt)):
            for who, out in self.as_workers(payload).items():
                self.assertEqual(self.decision(out), "deny", (who, payload))
                reason = out["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("driver", reason)
                self.assertIn("fix the code", reason)
        for who, out in self.as_workers(self.bash("cat %s" % receipt)).items():
            self.assertIsNone(out, who)

    def test_control_the_lead_is_not_guarded(self):
        default = self.root / ".local" / "state" / "orchestrate"
        _, receipts, _, _ = self.plan_state(default)
        self.assertIsNone(self.hook(self.write(receipts / "S1.integration.1.json"), role="lead"))


# --- Store failures in tool handlers ------------------------------------------------------------------------------


class ResumeRollbackOutcome(FakeHc):
    """runtime-audit-fixes/S7: the server's call path in process, with the store failing once between the
    resume's registration and its send (the plan's seam: a patched store raising sqlite3.Error)."""

    def env(self):
        env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
        }
        env.update(ENV)
        return env

    def setUp(self):
        super().setUp()
        self.install(worker_rows(2))
        self.session = {}

    def call_tool(self, tool, arguments):
        """What an MCP client sees: the server's own wrapper, which turns any failure into an error result."""
        env = self.env()
        # The resolver's `hc get` runs on the process PATH: point it at the fake hc, never a real one.
        with mock.patch.dict(os.environ, {"PATH": env["PATH"]}):
            out = mcp_server.call_tool(tool, arguments, env, hc_tools.Context(self.session))
        return out, out["content"][0]["text"]

    def task_row(self, task_id):
        with Store(self.env()).transaction() as db:
            row = db.execute(
                "SELECT status,owner,assignment FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        return dict(zip(("status", "owner", "assignment"), row))

    def linked_worker(self):
        created, text = self.call_tool("task", {"action": "create", "subject": "do it"})
        self.assertFalse(created["isError"], text)
        task_id = created["structuredContent"]["task_id"]
        spawned, text = self.call_tool(
            "spawn",
            {"kind": "worker", "name": name(0), "task": "work", "cwd": "/tmp", "task_id": task_id},
        )
        self.assertFalse(spawned["isError"], text)
        return task_id, spawned["structuredContent"]

    def test_S7_a_store_failure_after_registration_fails_the_assignment_and_releases_the_task(self):
        """runtime-audit-fixes/S7"""
        task_id, first = self.linked_worker()
        registered, armed, fired = [], [False], [False]
        real_register, real_transaction = Store.register, Store.transaction

        def register(store, *args, **kwargs):
            identity = real_register(store, *args, **kwargs)
            registered.append(identity["assignment_id"])
            armed[0] = True
            return identity

        def transaction(store):
            if armed[0] and not fired[0]:
                fired[0] = True
                raise sqlite3.OperationalError("disk I/O error")
            return real_transaction(store)

        with (
            mock.patch.object(Store, "register", register),
            mock.patch.object(Store, "transaction", transaction),
        ):
            result, text = self.call_tool(
                "resume", {"name": name(0), "task": "again", "task_id": task_id}
            )
        self.assertTrue(fired[0], "harness: no store call ran between registration and the send")
        self.assertEqual(
            self.sent_to(name(0)), [], "harness: the failure did not come before the send"
        )
        self.assertTrue(result["isError"], text)
        (pending,) = registered
        self.assertEqual(
            Store(self.env()).assignment_state(pending),
            "failed",
            "the resume's pending assignment was left behind",
        )
        self.assertEqual(
            self.task_row(task_id),
            {
                "status": "in_progress",
                "owner": first["worker_id"],
                "assignment": first["assignment_id"],
            },
            "the moved task was not released back to its previous assignment",
        )
        self.assertIn("disk I/O error", text, "the error does not name the store failure")
        self.assertIn("retry", text.lower(), "the error does not name the way out")

    def test_control_a_resume_with_a_task_moves_it(self):
        task_id, first = self.linked_worker()
        result, text = self.call_tool(
            "resume", {"name": name(0), "task": "again", "task_id": task_id}
        )
        self.assertFalse(result["isError"], text)
        second = result["structuredContent"]
        self.assertEqual(len(self.sent_to(name(0))), 1)
        self.assertEqual(self.task_row(task_id)["assignment"], second["assignment_id"])
        self.assertNotEqual(second["assignment_id"], first["assignment_id"])


# --- MCP server ---------------------------------------------------------------------------------------------------


EOF = object()


class Wire:
    """One server subprocess; replies are read on a thread and matched by id, so nothing blocks forever."""

    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-B", str(SERVER)],
            env=env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.replies = queue.Queue()
        self.seen = []
        self.eof = False
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.proc.stdout:
            try:
                self.replies.put(json.loads(line))
            except ValueError:
                self.replies.put({"unparsed": line})
        self.replies.put(EOF)

    def take(self, item):
        if item is EOF:
            self.eof = True  # the server exited: no further reply can come
        else:
            self.seen.append(item)

    def write(self, message):
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # the server is gone; the missing reply is what the test asserts on

    def request(self, request_id, method, params=None):
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.write(message)

    def tool(self, request_id, tool, arguments):
        self.request(request_id, "tools/call", {"name": tool, "arguments": arguments})

    def reply(self, match, timeout):
        """The first reply (already seen or arriving within `timeout`) for which match(reply) holds, else None."""
        deadline = time.monotonic() + timeout
        while True:
            while True:  # take every reply already read before deciding
                try:
                    self.take(self.replies.get_nowait())
                except queue.Empty:
                    break
            for index, reply in enumerate(self.seen):
                if match(reply):
                    return self.seen.pop(index)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.eof:
                return None
            try:
                self.take(self.replies.get(timeout=remaining))
            except queue.Empty:
                return None

    def answer(self, request_id, timeout):
        return self.reply(
            lambda r: "id" in r and r["id"] == request_id and type(r["id"]) is type(request_id),
            timeout,
        )

    def handshake(self):
        self.request(
            0,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "oracle", "version": "0"},
            },
        )
        assert self.answer(0, 10), "harness: the server did not answer initialize"
        self.write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


def refusal_text(reply):
    """The text of a refusal: a JSON-RPC error or an error tool result; None when the call was served."""
    if reply is None:
        return None
    if "error" in reply:
        return reply["error"].get("message", "")
    result = reply.get("result") or {}
    if result.get("isError"):
        return result["content"][0]["text"]
    return None


WAITS, LIVE = 56, 64


class McpServerOutcome(FakeHc):
    """runtime-audit-fixes/S9, S11 over stdio. Waits are held in flight by a fake `hc wait` that sleeps; slow
    non-wait calls by a fake `hc get` that sleeps under `collect`."""

    HOLD = 40  # seconds a held call's hc sleeps; the checks finish well inside it

    def wire(self, replies=None):
        self.install(dict(replies or {}, wait={"sleep": self.HOLD, "out": "{}"}))
        env = {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "HC_TERMINAL_ID": "t0",
        }
        wire = Wire(env)
        self.addCleanup(wire.close)
        wire.handshake()
        return wire

    def held(self, command, count, key=None, timeout=60):
        """Block until `count` fake hc invocations of `command` have started (the calls are in flight)."""
        deadline = time.monotonic() + timeout
        while True:
            started = [c for c in self.runs(command) if key is None or key(c)]
            if len(started) >= count:
                return
            if time.monotonic() > deadline:
                self.fail(
                    "harness: only %d of %d %s calls started" % (len(started), count, command)
                )
            time.sleep(0.1)

    def hold_waits(self, wire):
        for index in range(WAITS):
            wire.tool(1000 + index, "wait", {"target": "all", "timeout": 120})
        self.held("wait", WAITS)
        early = [r for r in (wire.answer(1000 + i, 0) for i in range(WAITS)) if r]
        self.assertEqual(early, [], "harness: held waits returned early")

    def test_S9_a_non_scalar_id_is_answered_with_32600_and_id_null_and_the_server_keeps_serving(
        self,
    ):
        """runtime-audit-fixes/S9, S17"""
        outcomes = {}
        for label, bad in (("array", [1, 2]), ("object", {"k": 1}), ("boolean", True)):
            wire = self.wire()
            wire.tool(bad, "status", {"name": "w1"})
            error = wire.reply(lambda r: "id" in r and r["id"] is None, 5)
            wire.tool(7, "status", {"name": "w1"})
            served = wire.answer(7, 15)
            outcomes[label] = {
                "error": (error or {}).get("error", {}).get("code"),
                "next served": bool(served and "result" in served),
            }
        expected = {"error": -32600, "next served": True}
        self.assertEqual(outcomes, {label: expected for label in outcomes})

    def test_S11_the_57th_wait_is_refused_at_once_while_non_wait_tools_and_ping_are_served(self):
        """runtime-audit-fixes/S11"""
        wire = self.wire()
        self.hold_waits(wire)
        wire.tool(1999, "wait", {"target": "all", "timeout": 120})
        extra = wire.answer(1999, 5)
        self.assertIsNotNone(extra, "the 57th wait was queued, not refused at once")
        reason = refusal_text(extra)
        self.assertIsNotNone(reason, "the 57th wait was served: %s" % extra)
        self.assertIn("retry", reason.lower(), "the refusal is not retryable: %s" % reason)
        wire.tool(2000, "status", {"name": "w1"})
        served = wire.answer(2000, 15)
        self.assertTrue(
            served and refusal_text(served) is None, "a non-wait tool was not served: %s" % served
        )
        wire.request(2001, "ping")
        self.assertEqual((wire.answer(2001, 5) or {}).get("result"), {}, "ping is inside the cap")

    def test_S11_the_65th_live_call_is_refused_at_once_while_ping_is_served(self):
        """runtime-audit-fixes/S11: 56 waits plus 8 slow non-wait calls fill the 64 live calls."""
        slow = ["slow%d" % index for index in range(LIVE - WAITS)]
        wire = self.wire({"get:" + s: {"sleep": self.HOLD, "out": raw(s, "t0")} for s in slow})
        self.hold_waits(wire)
        for index, worker in enumerate(slow):
            wire.tool(3000 + index, "collect", {"name": worker})
        self.held("get", len(slow), key=lambda c: c[1].startswith("slow"))
        wire.tool(3999, "status", {"name": "w1"})
        extra = wire.answer(3999, 5)
        self.assertIsNotNone(extra, "the 65th call was queued, not refused at once")
        reason = refusal_text(extra)
        self.assertIsNotNone(reason, "the 65th live call was served: %s" % extra)
        self.assertIn("retry", reason.lower(), "the refusal is not retryable: %s" % reason)
        wire.request(4000, "ping")
        self.assertEqual((wire.answer(4000, 5) or {}).get("result"), {}, "ping is inside the cap")

    def test_control_a_tools_call_is_answered_and_a_notification_is_not(self):
        wire = self.wire()
        wire.write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        wire.tool(5, "status", {"name": "w1"})
        served = wire.answer(5, 15)
        self.assertIsNotNone(served)
        self.assertIn("result", served)
        self.assertIsNone(wire.reply(lambda r: r.get("id") != 5, 1), "a notification was answered")

    def test_control_a_held_wait_does_not_block_other_calls(self):
        wire = self.wire()
        wire.tool(1, "wait", {"target": "all", "timeout": 120})
        self.held("wait", 1)
        wire.tool(2, "status", {"name": "w1"})
        self.assertIsNotNone(wire.answer(2, 15))
        wire.request(3, "ping")
        self.assertEqual((wire.answer(3, 5) or {}).get("result"), {})


# --- Operation keys -----------------------------------------------------------------------------------------------

# sh and awk, not python: the ancestry walk gives each ps a short timeout (as test_agent_runtime's FAKE_PS)
FAKE_PS = """#!/bin/sh
[ "$1 $2" = "-o ppid=,args=" ] && [ "$3" = "-p" ] || exit 1
awk -v pid="$4" '$1 == pid { found = 1; $1 = ""; sub(/^ /, ""); print } END { exit !found }' %(table)s
"""


class OperationKeyOutcome(FakeHc):
    """runtime-audit-fixes/S13-S15: spawn with an operation_id over MCP, and the owner-scoped `call operation`."""

    SPAWN = {"kind": "worker", "name": "w00", "task": "work", "cwd": "/tmp"}

    def lead(self):
        return self.start(worker_rows(4), ENV)

    def assignments(self):
        with Store({"HOME": str(self.dir / "home")}).transaction() as db:
            return [r[0] for r in db.execute("SELECT id FROM assignments ORDER BY rowid")]

    def test_S13_a_repeated_operation_id_returns_the_first_assignment_and_launches_once(self):
        """runtime-audit-fixes/S13, S17"""
        session = self.lead()
        first, text = self.call(session, "spawn", dict(self.SPAWN, operation_id="op-13"))
        self.assertFalse(first["isError"], text)
        again, text = self.call(session, "spawn", dict(self.SPAWN, operation_id="op-13"))
        self.assertFalse(again["isError"], "the replay was refused: %s" % text)
        self.assertEqual(
            again["structuredContent"]["assignment_id"], first["structuredContent"]["assignment_id"]
        )
        self.assertEqual(len(self.runs("run")), 1, "the fake hc saw more than one launch")
        self.assertEqual(self.runs("send"), [])
        self.assertEqual(len(self.assignments()), 1)

    def test_S14_a_changed_fingerprint_is_refused_naming_the_conflict_and_nothing_is_registered(
        self,
    ):
        """runtime-audit-fixes/S14"""
        session = self.lead()
        first, text = self.call(session, "spawn", dict(self.SPAWN, operation_id="op-14"))
        self.assertFalse(first["isError"], text)
        changed = (
            ("task", dict(self.SPAWN, task="other work", operation_id="op-14")),
            ("name", dict(self.SPAWN, name="w01", operation_id="op-14")),
        )
        refusals = {}
        for label, arguments in changed:
            result, text = self.call(session, "spawn", arguments)
            refusals[label] = (
                result["isError"],
                "conflict" in text.lower(),
                "operation_id" in text,
            )
        self.assertEqual(refusals, {label: (True, True, True) for label, _ in changed}, refusals)
        self.assertEqual(len(self.runs("run")), 1, "a conflicting operation launched a worker")
        self.assertEqual(
            len(self.assignments()), 1, "a conflicting operation registered an assignment"
        )

    # `agent-runtime call` runs in a plain hc terminal: a fake ps table whose ancestry reaches the terminal's root
    # process (pid 6000, from `hc get t0`) without any agent CLI on the way.

    def cli_env(self):
        table = self.dir / "ps-table"
        chain = [
            (os.getpid(), "python3 -m pytest"),
            (5000, "/bin/zsh -l"),
            (6000, "/usr/local/bin/hc-term"),
        ]
        lines = [
            "%d %d %s" % (pid, (chain[i + 1][0] if i + 1 < len(chain) else 1), args)
            for i, (pid, args) in enumerate(chain)
        ]
        table.write_text("\n".join(lines) + "\n")
        (self.dir / "bin" / "ps").write_text(FAKE_PS % {"table": str(table)})
        (self.dir / "bin" / "ps").chmod(0o755)
        return {
            "PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"),
            "HOME": str(self.dir / "home"),
            "XDG_STATE_HOME": str(self.dir / "state"),
            "XDG_CONFIG_HOME": str(self.dir / "config"),
            "HC_TERMINAL_ID": "t0",
        }

    def cli_hc(self):
        replies = worker_rows(4)
        replies["get:t0"] = {
            "out": json.dumps({"address": "t0", "name": "lead", "parent": None, "pid": 6000})
        }
        replies["run"] = {"out": json.dumps({"first_message": {"sent": True}})}
        self.install(replies)

    def run_call(self, tool, arguments):
        done = subprocess.run(
            [
                sys.executable,
                "-B",
                str(CLI),
                "call",
                tool,
                "--lead",
                "L1",
                "--json",
                json.dumps(arguments),
            ],
            env=self.cli_env(),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        return done.returncode, done.stdout, done.stderr

    def test_S15_call_operation_prints_the_owner_scoped_state_and_assignment(self):
        """runtime-audit-fixes/S15"""
        self.cli_hc()
        code, out, err = self.run_call(
            "spawn", dict(self.SPAWN, cwd=str(self.dir), operation_id="op-15")
        )
        self.assertEqual(code, 0, "setup: the keyed spawn failed: %s %s" % (out, err))
        spawned = json.loads(out)
        code, out, err = self.run_call("operation", {"operation_id": "op-15"})
        self.assertEqual(code, 0, "call operation failed: %s %s" % (out, err))
        found = json.loads(out)
        self.assertIn(found.get("state"), ("bound", "working"), found)
        self.assertEqual(found.get("assignment_id"), spawned["assignment_id"])
        code, out, err = self.run_call("operation", {"operation_id": "op-never-used"})
        self.assertEqual(code, 0, "call operation failed for an unknown id: %s %s" % (out, err))
        self.assertEqual(json.loads(out).get("state"), "absent")
        database = self.dir / "state" / "agent-runtime" / "dispatch.sqlite3"
        with sqlite3.connect(database) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_control_a_spawn_without_an_operation_id_launches_once(self):
        session = self.lead()
        result, text = self.call(session, "spawn", self.SPAWN)
        self.assertFalse(result["isError"], text)
        self.assertEqual(len(self.runs("run")), 1)

    def test_control_call_spawn_runs_in_a_plain_hc_terminal(self):
        self.cli_hc()
        code, out, err = self.run_call("spawn", dict(self.SPAWN, cwd=str(self.dir)))
        self.assertEqual(code, 0, "%s %s" % (out, err))
        self.assertTrue(json.loads(out)["assignment_id"])
        self.assertEqual(len(self.runs("run")), 1)


# --- Caller identity ----------------------------------------------------------------------------------------------


class CallerIdentityOutcome(unittest.TestCase):
    """runtime-audit-fixes/S16: `resolve --json` with a fake hc and a process table that cannot be read."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bin = Path(self.temp.name)
        for tool, body in (
            ("ps", "exit 1"),
            (
                "hc",
                'case "$2" in t1) echo \'{"address":"t1","name":"w1","parent":"t0"}\';; '
                '*) echo \'{"address":"t0","name":"lead","parent":null}\';; esac',
            ),
        ):
            path = self.bin / tool
            path.write_text("#!/bin/sh\n%s\n" % body)
            path.chmod(0o755)

    def resolve(self, **env):
        done = subprocess.run(
            [sys.executable, "-B", str(CLI), "resolve", "--json"],
            env=dict({"PATH": "%s:/usr/bin:/bin" % self.bin}, **env),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_S16_resolve_json_carries_the_caller_kind_and_whether_it_is_verified(self):
        """runtime-audit-fixes/S16"""
        for env in ({}, {"HC_TERMINAL_ID": "t0"}, {"HC_TERMINAL_ID": "t1"}):
            info = self.resolve(**env)
            self.assertIn("caller", info, "resolve --json has no caller: %s" % info)
            caller = info["caller"]
            self.assertIn(caller.get("kind"), ("lead", "worker", "unknown"), info)
            self.assertIsInstance(caller.get("verified"), bool, info)

    def test_S16_worker_environment_markers_alone_never_verify_the_caller(self):
        """runtime-audit-fixes/S16: no ancestry could be observed, so nothing is verified."""
        info = self.resolve(
            HC_TERMINAL_ID="t1",
            CODEX_THREAD_ID="worker-thread",
            CLAUDECODE="1",
            AGENT_RUNTIME_DISPATCH="1",
        )
        self.assertIn("caller", info, "resolve --json has no caller: %s" % info)
        self.assertIs(info["caller"].get("verified"), False, info)

    def test_control_resolve_json_prints_the_runtime_and_role(self):
        self.assertEqual(self.resolve()["runtime"], "native")
        info = self.resolve(HC_TERMINAL_ID="t1")
        self.assertEqual((info["runtime"], info["role"], info["parent"]), ("hc", "worker", "t0"))


# --- The outcome --------------------------------------------------------------------------------------------------


class OutcomeScenario(FakeHc):
    """runtime-audit-fixes/S17: one lead session over stdio, one fake hc, every fixed defect in the same run."""

    def hook_decision(self, payload):
        """The real PreToolUse hook as a spawned worker would meet it (a fake agent-runtime answers `worker`)."""
        fake = self.dir / "bin" / "agent-runtime"
        fake.write_text('#!/bin/sh\nprintf \'{"runtime": "hc", "role": "worker"}\'\n')
        fake.chmod(0o755)
        done = subprocess.run(
            [sys.executable, "-B", str(HOOK)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=20,
            env={"PATH": "%s:/usr/bin:/bin" % (self.dir / "bin"), "HOME": str(self.dir / "home")},
        )
        out = json.loads(done.stdout) if done.stdout.strip() else None
        return out["hookSpecificOutput"]["permissionDecision"] if out else "allow"

    def test_S17_the_fixed_runtime_serves_a_long_lived_lead(self):
        """runtime-audit-fixes/S17"""
        session = self.start(worker_rows(), ENV)
        spawn = {"kind": "worker", "task": "work", "cwd": "/tmp"}
        # A team of 32 registered workers, 31 of them stopped.
        for index in range(LIMIT):
            result, text = self.call(session, "spawn", dict(spawn, name=name(index)))
            self.assertFalse(result["isError"], "setup: %s" % text)
        for index in range(1, LIMIT):
            result, text = self.call(session, "stop", {"name": name(index)})
            self.assertFalse(result["isError"], "setup: %s" % text)
        # The new spawn succeeds; the 33rd active worker is refused with the way out.
        fresh, text = self.call(session, "spawn", dict(spawn, name=name(LIMIT)))
        self.assertFalse(
            fresh["isError"], "a new name was refused with 2 active workers: %s" % text
        )
        for index in range(1, LIMIT - 1):
            result, text = self.call(session, "resume", {"name": name(index), "task": "again"})
            self.assertFalse(result["isError"], "setup: %s" % text)
        refused, text = self.call(session, "spawn", dict(spawn, name=name(LIMIT + 1)))
        self.assertTrue(refused["isError"], "the 33rd active worker was spawned")
        self.assertIn(LIMIT_TEXT, text)
        # The repository path is allowed, the default-root receipt is denied.
        repo = self.dir / "repo"
        (repo / "src" / "state" / "receipts").mkdir(parents=True)
        source = repo / "src" / "state" / "receipts" / "x.ts"
        receipts = (
            self.dir / "home" / ".local" / "state" / "orchestrate" / "k" / "demo" / "receipts"
        )
        receipts.mkdir(parents=True)

        def write(path):
            return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "{}"}}

        self.assertEqual(self.hook_decision(write(source)), "allow")
        self.assertEqual(self.hook_decision(write(receipts / "S1.integration.1.json")), "deny")
        # A malformed request id is answered with -32600 and the server serves the next call.
        malformed = {
            "jsonrpc": "2.0",
            "id": [1],
            "method": "tools/call",
            "params": {"name": "status", "arguments": {"name": "w00"}},
        }
        session.proc.stdin.write(json.dumps(malformed) + "\n")
        session.proc.stdin.flush()
        error = json.loads(session.proc.stdout.readline())
        self.assertEqual((error.get("id"), error["error"]["code"]), (None, -32600), error)
        self.assertFalse(self.call(session, "status", {"name": "w00"})[0]["isError"])
        # A spawn repeated with the same operation_id returns the first assignment; the fake hc launched once.
        stopped, text = self.call(session, "stop", {"name": name(LIMIT)})
        self.assertFalse(stopped["isError"], text)
        keyed = dict(spawn, name=name(LIMIT + 2), operation_id="op-17")
        before = len(self.runs("run"))
        first, text = self.call(session, "spawn", keyed)
        self.assertFalse(first["isError"], text)
        again, text = self.call(session, "spawn", keyed)
        self.assertFalse(again["isError"], "the replay was refused: %s" % text)
        self.assertEqual(
            again["structuredContent"]["assignment_id"], first["structuredContent"]["assignment_id"]
        )
        self.assertEqual(len(self.runs("run")) - before, 1, "the fake hc saw more than one launch")


if __name__ == "__main__":
    unittest.main()
