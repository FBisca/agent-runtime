"""MCP server driven over stdio with a fake hc on PATH, and plugin wiring checks."""

import gc
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import weakref
from pathlib import Path
from unittest import mock

import pytest

pytestmark = pytest.mark.integration


PLUGIN = Path(__file__).resolve().parents[1]
# The runtime member holds the sources; the documented entry point stays in `server/`.
RUNTIME_SRC = PLUGIN / "packages" / "agent-runtime" / "src"
SERVER = PLUGIN / "server" / "mcp_server.py"
MARKETPLACE = PLUGIN / ".claude-plugin" / "marketplace.json"
TOOLS = [
    "spawn",
    "message",
    "resume",
    "wait",
    "status",
    "list",
    "collect",
    "stop",
    "report",
    "task",
]


class _Launched:
    """Popen-like handle for a server started by a FakeCli through named pipes."""

    def __init__(self, directory):
        self.rc = directory / "rc"
        self.stdin = open(directory / "in", "w")
        self.stdout = open(directory / "out")
        self.stderr = open(directory / "err")

    def wait(self, timeout=None):
        def read():
            try:
                self.stdout.read()  # EOF means the server exited
            except (ValueError, OSError):
                return  # the file was closed under the thread: the server is gone anyway

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            raise subprocess.TimeoutExpired("server", timeout or 0)
        return int(self.rc.read_text())


class FakeCli:
    """A long-lived fake `codex` process. Servers launched under it share its pid:lstart generation,
    like successive MCP processes of one real CLI (restart); each FakeCli is a generation of its own.
    Relies on bash exec'ing the backgrounded simple command directly (env -> python wrapper), so every
    server's nearest CLI ancestor is this one process and they share its pid:lstart.

    The harness name is the process name the ancestry walk sees: `codex` for the Claude/Codex hosts,
    `pi` for a Pi host."""

    # runs the server, then records its exit status where _Launched.wait reads it
    WRAP = "import subprocess, sys; rc = subprocess.call(sys.argv[2:]); open(sys.argv[1], 'w').write(str(rc))"

    def __init__(self, harness="codex"):
        self.dir = tempfile.TemporaryDirectory()
        self.count = 0
        self.proc = subprocess.Popen(
            [
                harness,
                "-c",
                'while IFS= read -r d; do v=(); while IFS= read -r -d "" e; do v+=("$e"); done <"$d/env"; \
env -i "${v[@]}" "$PYTHON" -B -c "$WRAP" "$d/rc" "$@" <"$d/in" >"$d/out" 2>"$d/err" & done; wait',
                harness,
                sys.executable,
                "-B",
                str(SERVER),
            ],
            executable="/bin/bash",
            stdin=subprocess.PIPE,
            text=True,
            env=dict(os.environ, PYTHON=sys.executable, WRAP=self.WRAP),
        )

    def launch(self, env):
        self.count += 1
        directory = Path(self.dir.name) / str(self.count)
        directory.mkdir()
        for name in ("in", "out", "err"):
            os.mkfifo(directory / name)
        (directory / "env").write_bytes(
            b"".join(("%s=%s" % kv).encode() + b"\0" for kv in env.items())
        )
        stdin = self.proc.stdin
        assert stdin is not None
        stdin.write(directory.as_posix() + "\n")
        stdin.flush()
        return _Launched(directory)

    def close(self):
        stdin = self.proc.stdin
        assert stdin is not None
        stdin.close()
        self.proc.wait(timeout=10)
        self.dir.cleanup()


class Session:
    """One server subprocess speaking newline-delimited JSON-RPC."""

    def __init__(self, env, new_cli: bool | FakeCli = False):
        """new_cli=True runs the server under its own fake `codex` process, so it has a generation of its own
        (pid:lstart of that ancestor) regardless of what the test runner itself runs under; a FakeCli runs it under that shared fake CLI instead."""
        if isinstance(new_cli, FakeCli):  # a further server process under an existing fake CLI
            self.proc = new_cli.launch(env)
            self.next_id = 0
            return
        argv = [sys.executable, "-B", str(SERVER)]
        if new_cli:
            argv = [
                "codex",
                "-c",
                '"$@"; exit $?',
                "codex",
            ] + argv  # non-exec shell stays the parent
        self.proc = subprocess.Popen(
            argv,
            executable="/bin/bash" if new_cli else None,
            env=env,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        stdin, stdout = self.proc.stdin, self.proc.stdout
        assert stdin is not None and stdout is not None
        stdin.write(json.dumps(message) + "\n")
        stdin.flush()
        if notify:
            return None
        reply = json.loads(stdout.readline())
        assert reply["id"] == self.next_id, reply
        return reply

    def handshake(self):
        reply = self.send(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        self.send("notifications/initialized", notify=True)
        return reply

    def close(self):
        stdin, stdout, stderr = self.proc.stdin, self.proc.stdout, self.proc.stderr
        assert stdin is not None and stdout is not None and stderr is not None
        stdin.close()
        self.proc.wait(timeout=10)
        stdout.close()
        stderr.close()


class InProcessSession:
    """The server's request handling in this process: the same `handle` and `hc_tools` the stdio server
    runs, with no subprocess for the server itself. Tests that only prove what a tool decides use it; the
    boundary tests that need the pipe, the thread per call or the process ancestry keep `Session`."""

    def __init__(self, env):
        sys.path.insert(0, str(RUNTIME_SRC))
        from agent_runtime import hc_tools, mcp_server

        self.mcp_server, self.hc_tools = mcp_server, hc_tools
        self.env = dict(env)
        self.state = {}  # what one server process shares across its calls
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if notify:
            return None
        self.next_id += 1
        message["id"] = self.next_id
        if method == "tools/call":
            name = (params or {}).get("name")
            arguments = (params or {}).get("arguments")
            if name not in self.mcp_server.TOOL_NAMES:
                return self.mcp_server.rpc_error(self.next_id, -32602, "unknown tool: %s" % name)
            # clear=True: the tool may read the process environment (HOME) as well as the env it is given
            with mock.patch.dict(os.environ, self.env, clear=True):
                result = self.mcp_server.call_tool(
                    name,
                    arguments if isinstance(arguments, dict) else {},
                    self.env,
                    self.hc_tools.Context(self.state),
                )
            return {"jsonrpc": "2.0", "id": self.next_id, "result": result}
        return self.mcp_server.handle(message, self.env)

    def handshake(self):
        return self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

    def close(self):
        pass


class ServerCase(unittest.TestCase):
    def start(self, hc_body=None, env=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bindir = Path(temp.name)
        if hc_body is not None:
            hc = bindir / "hc"
            hc.write_text("#!/bin/sh\n" + hc_body + "\n")
            hc.chmod(0o755)
        full = {"PATH": "%s:/usr/bin:/bin" % bindir}
        full.update(env or {})
        session = Session(full)
        self.addCleanup(session.close)
        return session


class ToolListTests(ServerCase):
    def test_S1_tools_list_returns_the_ten_tools_within_400_words(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        init = session.handshake()
        self.assertIn("tools", init["result"]["capabilities"])
        tools = session.send("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], TOOLS)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
        words = 0
        for tool in tools:
            words += len(tool["description"].split())
            words += sum(
                len(p.get("description", "").split())
                for p in tool["inputSchema"]["properties"].values()
            )
        self.assertLessEqual(words, 400)

    def test_S16_profiles_are_listed_dynamically(self):
        """S16: tools/list reads configured names on every request."""
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "agent-runtime" / "profiles.json"
            config.parent.mkdir()
            session = self.start(env={"XDG_CONFIG_HOME": directory})
            session.handshake()
            config.write_text(json.dumps({"profiles": {"custom": {"cli": "claude", "env": {}}}}))
            tools = session.send("tools/list")["result"]["tools"]
            self.assertEqual(
                tools[0]["inputSchema"]["properties"]["cli"]["enum"],
                ["claude", "codex", "pi", "custom"],
            )

    def test_spawn_schema_has_optional_string_model(self):
        """agent-runtime/R135"""
        session = self.start()
        session.handshake()
        spawn = next(
            t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn"
        )
        self.assertEqual(spawn["inputSchema"]["properties"]["model"]["type"], "string")
        self.assertNotIn("model", spawn["inputSchema"]["required"])

    def test_spawn_schema_has_optional_effort_enum(self):
        """agent-runtime/R149"""
        session = self.start()
        session.handshake()
        spawn = next(
            t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn"
        )
        self.assertEqual(
            spawn["inputSchema"]["properties"]["effort"]["enum"],
            ["low", "medium", "high", "xhigh", "max"],
        )
        self.assertNotIn("effort", spawn["inputSchema"]["required"])

    def test_spawn_schema_has_optional_string_agent(self):
        """agent-runtime/R142"""
        session = self.start()
        session.handshake()
        spawn = next(
            t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn"
        )
        agent = spawn["inputSchema"]["properties"]["agent"]
        self.assertEqual(agent["type"], "string")
        self.assertIn("report", agent["description"])
        self.assertNotIn("agent", spawn["inputSchema"]["required"])

    def test_spawn_schema_has_optional_string_instructions_file(self):
        """agent-runtime/R164"""
        session = self.start()
        session.handshake()
        spawn = next(
            t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn"
        )
        properties = spawn["inputSchema"]["properties"]
        self.assertEqual(properties["instructions_file"]["type"], "string")
        self.assertNotIn("instructions_file", spawn["inputSchema"]["required"])
        self.assertIn("none", properties["agent"]["description"])

    def test_ping_and_unknown_method(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        session.handshake()
        self.assertEqual(session.send("ping")["result"], {})
        self.assertEqual(session.send("nope")["error"]["code"], -32601)

    def test_notification_gets_no_reply_and_eof_exits(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        session.handshake()
        session.send("notifications/initialized", notify=True)
        self.assertEqual(session.send("ping")["result"], {})
        session.proc.stdin.close()
        self.assertEqual(session.proc.wait(timeout=10), 0)
        self.assertEqual(session.proc.stdout.read(), "")

    def test_bad_params_and_stray_responses(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        session.handshake()
        session.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}) + "\n")
        session.proc.stdin.flush()
        reply = session.send("tools/call", [1])
        self.assertEqual(reply["error"]["code"], -32602)
        init = session.send("initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")

    def test_stdout_carries_only_protocol_json(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        session.handshake()
        session.send("tools/list")
        session.proc.stdin.write("not json\n")
        session.proc.stdin.flush()
        reply = json.loads(session.proc.stdout.readline())
        self.assertEqual(reply["error"]["code"], -32700)


class ToolCallTests(ServerCase):
    def test_operation_is_refused_by_both_mcp_dispatch_paths(self):
        """agent-runtime/R262: operation is available only through the call CLI."""
        from agent_runtime import mcp_server

        params = {"name": "operation", "arguments": {"operation_id": "private-key"}}
        with mock.patch.object(mcp_server, "call_tool") as handler:
            reply = mcp_server.dispatch("tools/call", 1, params, {})
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIn("unknown tool: operation", reply["error"]["message"])
        handler.assert_not_called()
        session = self.start()
        session.handshake()
        reply = session.send("tools/call", params)
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIn("unknown tool: operation", reply["error"]["message"])
        self.assertEqual(session.send("ping")["result"], {})

    def call(self, session, name, arguments):
        return session.send("tools/call", {"name": name, "arguments": arguments})["result"]

    def test_status_wraps_the_library_with_a_fake_hc(self):
        """agent-runtime-tools/S1"""
        raw = {
            "address": "t9",
            "name": "w1",
            "parent": "t0",
            "status": {"app": "x", "state": "idle"},
        }
        session = self.start("echo '%s'" % json.dumps(raw), env={"HC_TERMINAL_ID": "t0"})
        session.handshake()
        result = self.call(session, "status", {"name": "w1"})
        self.assertFalse(result.get("isError"))
        info = json.loads(result["content"][0]["text"])
        self.assertEqual((info["runtime"], info["state"], info["parent"]), ("hc", "idle", "t0"))

    def test_list_returns_children_of_the_caller(self):
        """agent-runtime-tools/S1"""
        body = (
            'case "$1" in ls) echo \'{"terminals":[{"address":"t5","parent":"t0"},{"address":"t6","parent":"tX"}]}\';;'
            ' get) echo \'{"address":"t5","name":"w5","parent":"t0","status":"working"}\';; esac'
        )
        session = self.start(body, env={"HC_TERMINAL_ID": "t0"})
        session.handshake()
        result = self.call(session, "list", {})
        rows = json.loads(result["content"][0]["text"])
        self.assertEqual([r["name"] for r in rows], ["w5"])

    def test_list_outside_hc_is_an_error(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        session.handshake()
        result = self.call(session, "list", {})
        self.assertTrue(result["isError"])
        self.assertIn("hc-only", result["content"][0]["text"])

    def test_unknown_tool_is_a_protocol_error(self):
        """agent-runtime-tools/S1"""
        session = self.start()
        session.handshake()
        reply = session.send("tools/call", {"name": "bogus", "arguments": {}})
        self.assertEqual(reply["error"]["code"], -32602)


class InProcessServer:
    """`serve` on a queue-fed stdin in this process, with `hc_tools.call` replaced by a fake whose
    calls block until released, so the number of calls in flight is under the test's control."""

    def __init__(self):
        sys.path.insert(0, str(RUNTIME_SRC))
        from agent_runtime import mcp_server

        self.mcp_server = mcp_server
        self.lines = queue.Queue()
        self.out = io.StringIO()
        self.release = threading.Event()
        self.started = []
        self.threads = weakref.WeakSet()
        self.patches = []

        def fake_call(name, arguments, env, ctx):
            self.started.append(name)
            if name != "status":
                self.release.wait(10)
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

        real_thread = threading.Thread
        tracked = self.threads

        class Tracked(real_thread):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                tracked.add(self)

        self.patches = [
            mock.patch.object(mcp_server.hc_tools, "call", fake_call),
            mock.patch.object(mcp_server.threading, "Thread", Tracked),
        ]
        for patch in self.patches:
            patch.start()
        feed = iter(self.lines.get, None)
        self.server = real_thread(target=mcp_server.serve, args=(feed, self.out, {}), daemon=True)
        self.server.start()

    def send(self, message):
        self.lines.put(json.dumps(message) + "\n")

    def call(self, request_id, name):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": {}},
            }
        )

    def replies(self):
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    def reply_for(self, request_id, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for reply in self.replies():
                if reply.get("id") == request_id:
                    return reply
            time.sleep(0.01)
        raise AssertionError("no reply for %r in %s" % (request_id, self.out.getvalue()))

    def wait_started(self, count, timeout=5):
        deadline = time.time() + timeout
        while len(self.started) < count and time.time() < deadline:
            time.sleep(0.01)
        self.assert_started = len(self.started) >= count
        if not self.assert_started:
            raise AssertionError("only %d calls started" % len(self.started))

    def close(self):
        self.release.set()
        self.lines.put(None)
        self.server.join(15)
        for patch in self.patches:
            patch.stop()


class CallLimitTests(unittest.TestCase):
    def setUp(self):
        self.server = InProcessServer()
        self.addCleanup(self.server.close)

    def test_S9_a_non_scalar_id_is_invalid_and_the_server_keeps_serving(self):
        """agent-runtime/R248"""
        for bad in ([1], {"a": 1}, True, False):
            self.server.send(
                {
                    "jsonrpc": "2.0",
                    "id": bad,
                    "method": "tools/call",
                    "params": {"name": "status", "arguments": {}},
                }
            )
        # a cancellation naming an unhashable request id is a notification: no reply, no crash
        self.server.send(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": [1]}}
        )
        self.server.send({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(self.server.reply_for(7)["result"], {})
        replies = self.server.replies()
        errors = [r for r in replies if "error" in r]
        self.assertEqual(len(errors), 4)
        for reply in errors:
            self.assertEqual(reply["error"]["code"], -32600)
            self.assertIsNone(reply["id"])
        self.assertEqual(len(replies), 5)
        self.server.call("after", "status")
        self.assertFalse(self.server.reply_for("after")["result"]["isError"])

    def test_S10_finished_call_threads_are_not_retained(self):
        """agent-runtime/R249"""
        for number in range(40):
            self.server.call(number, "status")
        for number in range(40):
            self.server.reply_for(number)
        self.server.send({"jsonrpc": "2.0", "id": "p", "method": "ping"})
        self.server.reply_for("p")
        deadline = time.time() + 5
        held = [None]
        while held and time.time() < deadline:
            gc.collect()
            held = list(self.server.threads)
            time.sleep(0.05)
        self.assertEqual(len(held), 0, "finished call threads are still referenced")

    def test_S11_the_wait_and_total_caps_refuse_at_once_and_retryably(self):
        """agent-runtime/R250"""
        for number in range(56):
            self.server.call("w%d" % number, "wait")
        self.server.wait_started(56)
        self.server.call("w-extra", "wait")
        refusal = self.server.reply_for("w-extra", timeout=2)
        self.assertTrue(refusal["result"]["isError"])
        self.assertIn("retry", refusal["result"]["content"][0]["text"])
        for number in range(8):
            self.server.call("s%d" % number, "task")
        self.server.wait_started(64)
        self.server.call("s-extra", "task")
        refusal = self.server.reply_for("s-extra", timeout=2)
        self.assertTrue(refusal["result"]["isError"])
        self.assertIn("retry", refusal["result"]["content"][0]["text"])
        self.assertEqual(len(self.server.started), 64, "a refused call never started")
        # ping and cancellation stay outside the cap
        self.server.send({"jsonrpc": "2.0", "id": "p", "method": "ping"})
        self.assertEqual(self.server.reply_for("p", timeout=2)["result"], {})
        self.server.send(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "w0"}}
        )
        # finishing calls frees slots: a later call is served
        self.server.release.set()
        self.server.reply_for("w1")
        deadline = time.time() + 5
        while time.time() < deadline and len(self.server.replies()) < 64 + 2:
            time.sleep(0.01)
        self.server.call("again", "wait")
        self.assertFalse(self.server.reply_for("again")["result"]["isError"])


class WiringTests(unittest.TestCase):
    def load(self, path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def test_S18_manifest_mcp_and_hooks_files_are_valid_and_wired(self):
        """agent-runtime-tools/S18"""
        manifest = self.load(PLUGIN / ".claude-plugin" / "plugin.json")
        self.assertEqual(manifest["name"], "agent-runtime")
        self.assertTrue(manifest["description"])
        mcp = self.load(PLUGIN / ".mcp.json")
        server = mcp["mcpServers"]["agent-runtime"]
        self.assertEqual(server["command"], "python3")
        self.assertEqual(server["args"], ["${CLAUDE_PLUGIN_ROOT}/server/mcp_server.py"])
        for name in (
            "HC_TERMINAL_ID",
            "HC_SCOPE",
            "HC_TERMINAL_SOCKET",
            "HERDR_PANE_ID",
            "HERDR_ENV",
            "HERDR_SOCKET_PATH",
            "HERDR_SESSION",
        ):
            self.assertEqual(server["env"][name], "${%s:-}" % name)
        hooks = self.load(PLUGIN / "hooks" / "hooks.json")
        self.assertIsInstance(hooks["hooks"], dict)
        self.assertTrue(SERVER.is_file())

    def test_S18_local_marketplace_lists_the_plugin_by_relative_source(self):
        """agent-runtime-tools/S18"""
        market = self.load(MARKETPLACE)
        self.assertTrue(market["name"])
        self.assertTrue(market["owner"]["name"])
        entry = {p["name"]: p for p in market["plugins"]}["agent-runtime"]
        self.assertEqual(entry["source"], "./")
        self.assertTrue((PLUGIN / entry["source"] / ".claude-plugin" / "plugin.json").is_file())


if __name__ == "__main__":
    unittest.main()
