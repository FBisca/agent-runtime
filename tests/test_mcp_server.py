"""MCP server driven over stdio with a fake hc on PATH, and plugin wiring checks."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SERVER = PLUGIN / "server" / "mcp_server.py"
MARKETPLACE = PLUGIN / ".claude-plugin" / "marketplace.json"
TOOLS = ["spawn", "message", "resume", "wait", "status", "list", "collect", "stop", "report", "task"]


class _Launched:
    """Popen-like handle for a server started by a FakeCli through named pipes."""

    def __init__(self, directory):
        self.rc = directory / "rc"
        self.stdin = open(directory / "in", "w")
        self.stdout = open(directory / "out")
        self.stderr = open(directory / "err")

    def wait(self, timeout=None):
        reader = threading.Thread(target=self.stdout.read, daemon=True)  # EOF means the server exited
        reader.start()
        reader.join(timeout)
        if reader.is_alive():
            raise subprocess.TimeoutExpired("server", timeout)
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
        self.proc = subprocess.Popen([harness, "-c", 'while IFS= read -r d; do v=(); while IFS= read -r -d "" e; do v+=("$e"); done <"$d/env"; \
env -i "${v[@]}" "$PYTHON" -B -c "$WRAP" "$d/rc" "$@" <"$d/in" >"$d/out" 2>"$d/err" & done; wait',
                                      harness, sys.executable, "-B", str(SERVER)], executable="/bin/bash",
                                     stdin=subprocess.PIPE, text=True,
                                     env=dict(os.environ, PYTHON=sys.executable, WRAP=self.WRAP))

    def launch(self, env):
        self.count += 1
        directory = Path(self.dir.name) / str(self.count)
        directory.mkdir()
        for name in ("in", "out", "err"):
            os.mkfifo(directory / name)
        (directory / "env").write_bytes(b"".join(("%s=%s" % kv).encode() + b"\0" for kv in env.items()))
        self.proc.stdin.write(directory.as_posix() + "\n")
        self.proc.stdin.flush()
        return _Launched(directory)

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.dir.cleanup()


class Session:
    """One server subprocess speaking newline-delimited JSON-RPC."""

    def __init__(self, env, new_cli=False):
        """new_cli=True runs the server under its own fake `codex` process, so it has a generation of its own
        (pid:lstart of that ancestor) regardless of what the test runner itself runs under; a FakeCli runs it under that shared fake CLI instead."""
        if isinstance(new_cli, FakeCli):  # a further server process under an existing fake CLI
            self.proc = new_cli.launch(env)
            self.next_id = 0
            return
        argv = [sys.executable, "-B", str(SERVER)]
        if new_cli:
            argv = ["codex", "-c", '"$@"; exit $?', "codex"] + argv  # non-exec shell stays the parent
        self.proc = subprocess.Popen(argv, executable="/bin/bash" if new_cli else None, env=env, text=True,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.next_id = 0

    def send(self, method, params=None, notify=False):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        if notify:
            return None
        reply = json.loads(self.proc.stdout.readline())
        assert reply["id"] == self.next_id, reply
        return reply

    def handshake(self):
        reply = self.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "test", "version": "0"}})
        self.send("notifications/initialized", notify=True)
        return reply

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        self.proc.stderr.close()


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
            words += sum(len(p.get("description", "").split()) for p in tool["inputSchema"]["properties"].values())
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
            self.assertEqual(tools[0]["inputSchema"]["properties"]["cli"]["enum"],
                             ["claude", "codex", "custom"])

    def test_spawn_schema_has_optional_string_model(self):
        """agent-runtime/R135"""
        session = self.start()
        session.handshake()
        spawn = next(t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn")
        self.assertEqual(spawn["inputSchema"]["properties"]["model"]["type"], "string")
        self.assertNotIn("model", spawn["inputSchema"]["required"])

    def test_spawn_schema_has_optional_effort_enum(self):
        """agent-runtime/R149"""
        session = self.start()
        session.handshake()
        spawn = next(t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn")
        self.assertEqual(spawn["inputSchema"]["properties"]["effort"]["enum"], ["low", "medium", "high", "xhigh", "max"])
        self.assertNotIn("effort", spawn["inputSchema"]["required"])

    def test_spawn_schema_has_optional_string_agent(self):
        """agent-runtime/R142"""
        session = self.start()
        session.handshake()
        spawn = next(t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn")
        agent = spawn["inputSchema"]["properties"]["agent"]
        self.assertEqual(agent["type"], "string")
        self.assertIn("report", agent["description"])
        self.assertNotIn("agent", spawn["inputSchema"]["required"])

    def test_spawn_schema_has_optional_string_instructions_file(self):
        """agent-runtime/R164"""
        session = self.start()
        session.handshake()
        spawn = next(t for t in session.send("tools/list")["result"]["tools"] if t["name"] == "spawn")
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
    def call(self, session, name, arguments):
        return session.send("tools/call", {"name": name, "arguments": arguments})["result"]

    def test_status_wraps_the_library_with_a_fake_hc(self):
        """agent-runtime-tools/S1"""
        raw = {"address": "t9", "name": "w1", "parent": "t0", "status": {"app": "x", "state": "idle"}}
        session = self.start("echo '%s'" % json.dumps(raw), env={"HC_TERMINAL_ID": "t0"})
        session.handshake()
        result = self.call(session, "status", {"name": "w1"})
        self.assertFalse(result.get("isError"))
        info = json.loads(result["content"][0]["text"])
        self.assertEqual((info["runtime"], info["state"], info["parent"]), ("hc", "idle", "t0"))

    def test_list_returns_children_of_the_caller(self):
        """agent-runtime-tools/S1"""
        body = ('case "$1" in ls) echo \'{"terminals":[{"address":"t5","parent":"t0"},{"address":"t6","parent":"tX"}]}\';;'
                ' get) echo \'{"address":"t5","name":"w5","parent":"t0","status":"working"}\';; esac')
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
        for name in ("HC_TERMINAL_ID", "HC_SCOPE", "HC_TERMINAL_SOCKET", "HERDR_PANE_ID", "HERDR_ENV",
                     "HERDR_SOCKET_PATH", "HERDR_SESSION"):
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
