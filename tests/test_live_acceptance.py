"""Live mixed-CLI acceptance (S24). Real hc, codex and claude workers; run only with AGENT_RUNTIME_LIVE=1
from inside an hc terminal that has the CLI session this server serves."""
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import unittest

from test_mcp_server import SERVER

MAX_PERIOD_WAITS = 4


class Lead:
    """The server under test as an MCP stdio subprocess; every stdout line must parse as JSON."""

    def __init__(self, log):
        self.log, self.i, self.lines = log, 0, 0
        env = dict(os.environ, AGENT_RUNTIME_DISPATCH="1")
        self.p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, env=env)
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "live-lead", "version": "0"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.i += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("server closed stdout")
            self.lines += 1
            message = json.loads(line)  # raises on any non-JSON stdout line
            if message.get("id") == self.i:
                return message

    def tool(self, _tool, /, **args):  # positional-only: tool arguments such as name= cannot clash
        started = time.time()
        result = self.rpc("tools/call", {"name": _tool, "arguments": args}).get("result", {})
        text = (result.get("content") or [{}])[0].get("text", "")
        structured = result.get("structuredContent")
        if structured is not None and json.loads(text) != structured:
            raise AssertionError("structuredContent differs from text for %s" % _tool)
        self.log("call", tool=_tool, secs=round(time.time() - started, 1), is_error=result.get("isError", False),
                 bytes=len(text.encode()))
        if result.get("isError"):
            raise AssertionError("%s failed: %s" % (_tool, text[:300]))
        return structured if structured is not None else text

    def close(self):
        self.p.kill()
        self.p.wait()
        for pipe in (self.p.stdin, self.p.stdout):
            pipe.close()


def large_body(nonce, kb=60):
    words = "lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor".split()
    out, size = [], 0
    while size < kb * 1024:
        word = words[secrets.randbelow(len(words))]
        out.append(word)
        size += len(word) + 1
    return " ".join(out) + "\nTAIL NONCE: " + nonce


class HarnessTests(unittest.TestCase):
    def test_harness_accepts_name_arguments(self):
        """agent-runtime/R57"""
        lead = object.__new__(Lead)
        lead.log, lead.i, lead.lines = lambda *a, **k: None, 0, 0
        seen = []
        lead.rpc = lambda method, params: seen.append(params) or {"result": {"content": [{"text": "{}"}],
                                                                                "structuredContent": {}}}
        for tool in ("spawn", "resume", "stop", "status"):
            lead.tool(tool, name="live-codex-ab12", task="x")
        self.assertEqual([p["name"] for p in seen], ["spawn", "resume", "stop", "status"])
        self.assertTrue(all(p["arguments"]["name"] == "live-codex-ab12" for p in seen))


@unittest.skipUnless(os.environ.get("AGENT_RUNTIME_LIVE") == "1", "set AGENT_RUNTIME_LIVE=1 for the live run")
class LiveAcceptance(unittest.TestCase):
    def test_live_mixed_cli_dispatch(self):
        """agent-runtime/R57"""
        work = tempfile.mkdtemp(prefix="agent-runtime-live-")
        log_path = os.environ.get("AGENT_RUNTIME_LIVE_LOG") or os.path.join(work, "live.log")
        logfile = open(log_path, "a")
        self.addCleanup(logfile.close)

        def log(event, **data):
            logfile.write(json.dumps(dict(event=event, t=round(time.time(), 1), **data)) + "\n")
            logfile.flush()

        suffix = secrets.token_hex(3)
        codex, claude = "live-codex-" + suffix, "live-claude-" + suffix
        lead = Lead(log)
        started, receipt = [], None
        try:
            listed = lead.tool("list")  # leftover workers from earlier aborted runs
            records = listed.get("result", []) if isinstance(listed, dict) else []
            for record in records if isinstance(records, list) else []:
                leftover = record.get("name", "") if isinstance(record, dict) else ""
                if leftover.startswith(("live-", "diag-")):
                    try:
                        lead.tool("stop", name=leftover)
                        log("leftover_stopped", name=leftover)
                    except Exception as error:
                        log("leftover_stop_error", name=leftover, error=repr(error))
            pending, drained = None, False  # acknowledge stale reports so they cannot pollute assertions
            for iteration in range(1, 21):
                batch = lead.tool("wait", target="inbox", timeout=0, **({"receipt": pending} if pending else {}))
                pending = batch.get("receipt")
                if not batch.get("messages") and not batch.get("more"):
                    drained = True
                    break
            log("stale_inbox_drain", iterations=iteration, drained=drained)
            self.assertTrue(drained, "stale inbox did not drain after 20 wait(inbox) calls; cannot trust assertions")
            nonce_cx, nonce_cl = secrets.token_hex(8), secrets.token_hex(8)
            first = lead.tool("task", action="create", subject="live first step")
            second = lead.tool("task", action="create", subject="live depends on first", depends_on=[first["task_id"]])
            common = ("This is a live acceptance test of the agent-runtime MCP tools. Use ONLY the agent-runtime MCP "
                      "tools (never shell wrappers around them, never read saved tool-output files). Do not edit files. ")
            cx_task = common + (
                "Steps: (1) Call the agent-runtime wait tool with target='inbox', timeout=300 and your assignment_id. "
                "Repeat until you receive a message from your lead; its text ends with 'TAIL NONCE: <hex>'. "
                "(2) Call task with action='claim', task_id='%s', passing the receipt from step 1. "
                "(3) Call message with to='%s' and text 'PEER NONCE: <the hex from step 1>'. "
                "(4) Call report with kind='complete' and text 'lead_nonce=<hex> claimed=%s'. Then stop."
                % (first["task_id"], claude, first["task_id"]))
            cl_task = common + (
                "Steps: (1) Call the agent-runtime wait tool with target='inbox', timeout=300 and your assignment_id, "
                "repeating (passing each returned receipt) until you have BOTH a message from your lead ending with "
                "'TAIL NONCE: <hex>' AND a peer message from %s starting 'PEER NONCE:'. "
                "(2) Call report with kind='decision' and text 'lead_nonce=<hex> peer_nonce=<hex> "
                "peer_sender=<sender field of the peer message>; may I finish?'. "
                "Then end your turn and wait for further instructions." % codex)
            started.append(codex)  # registered before spawn so a failed spawn is still cleaned up
            cx = lead.tool("spawn", kind="worker", cli="codex", name=codex, task=cx_task, cwd=work)
            started.append(claude)
            cl = lead.tool("spawn", kind="worker", cli="claude", name=claude, task=cl_task, cwd=work)
            lead.tool("message", to=codex, text=large_body(nonce_cx))
            lead.tool("message", to=claude, text=large_body(nonce_cl))
            counts = {}

            def period(label, receipt=None):
                messages, last, calls = [], [], 0
                while True:
                    calls += 1
                    args = {"target": "all", "timeout": 590}
                    if receipt:
                        args["receipt"] = receipt
                    batch = lead.tool("wait", **args)
                    receipt = batch.get("receipt") or receipt
                    last = [m["message_id"] for m in batch.get("messages", [])]  # empty batch: all acked
                    messages += batch.get("messages", [])
                    if batch.get("reason") != "timeout" or calls >= MAX_PERIOD_WAITS:
                        counts[label] = calls
                        log("period", label=label, wait_calls=calls, reason=batch.get("reason"),
                            unfinished=batch.get("unfinished"))
                        return batch, receipt, messages, last

            batch, receipt, messages, last_ids = period("period1")
            lead.close()  # restart in the same CLI generation; replay must return the unacknowledged messages
            lead = None
            lead = Lead(log)
            replay = lead.tool("wait", target="all", timeout=0)
            replay_ids = [m["message_id"] for m in replay.get("messages", [])]
            log("replay", last_ids=last_ids, replay_ids=replay_ids)
            self.assertEqual(replay_ids[:len(last_ids)], last_ids)
            receipt = replay.get("receipt") or receipt
            # Peer messages go only to the claude worker, so the exchange is checked through its decision report.
            decisions = [m for m in messages if m.get("kind") == "decision"]
            self.assertTrue(decisions, "no decision report observed")
            text = decisions[0]["text"]
            self.assertEqual(re.search(r"peer_sender=([^\s;]+)", text).group(1), cx["worker_id"])
            self.assertEqual(re.search(r"peer_nonce=([^\s;]+)", text).group(1), nonce_cx)
            resumed = None
            if batch.get("reason") != "completed":
                resumed = lead.tool("resume", name=claude, receipt=receipt,
                                    task="Decision: yes. Call task with action='claim', task_id='%s'; then report "
                                         "kind='complete' with text 'final peer_nonce=<hex> lead_nonce=<hex>'."
                                         % second["task_id"])
                self.assertNotEqual(resumed["assignment_id"], cl["assignment_id"])
                batch, receipt, more, _ = period("period2")
                messages += more
            owners = {cx["assignment_id"]: cx["worker_id"], cl["assignment_id"]: cl["worker_id"]}
            if resumed:
                owners[resumed["assignment_id"]] = cl["worker_id"]
            for m in messages:  # provenance: each report comes from the worker its assignment belongs to
                self.assertEqual(m["sender"], m["worker_id"])
                self.assertEqual(m["worker_id"], owners[m["assignment_id"]])
            texts = " ".join(m.get("text", "") for m in messages)
            log("verdict", nonces=[nonce_cx in texts, nonce_cl in texts], final_reason=batch.get("reason"),
                wait_calls=counts, stdout_lines=lead.lines)
            self.assertIn(nonce_cx, texts)
            self.assertIn(nonce_cl, texts)
            self.assertEqual(batch["reason"], "completed")
            self.assertEqual(batch["unfinished"], [])
            self.assertGreater(lead.lines, 0)
        finally:
            # The live lead shares the real CLI's session generation (a new generation would make the real
            # lead's team stale), so leave nothing unacknowledged behind.
            if lead is None or lead.p.poll() is not None:
                try:
                    lead = Lead(log)
                except Exception as error:
                    lead = None
                    log("restart_error", error=repr(error))
            if lead is not None:
                for name in started:
                    try:
                        lead.tool("stop", name=name, receipt=receipt)
                    except Exception as error:  # cleanup must continue for the other worker
                        log("stop_error", name=name, error=repr(error))
                for _ in range(10):  # drain: each pass acknowledges the previous batch
                    try:
                        batch = lead.tool("wait", target="all", timeout=0, receipt=receipt)
                    except Exception as error:
                        log("drain_error", error=repr(error))
                        break
                    receipt = batch.get("receipt")
                    if not batch.get("messages") and not batch.get("more"):
                        break
                lead.close()

    def test_S17_externally_killed_worker_stops_cleanly(self):
        work = tempfile.mkdtemp(prefix="agent-runtime-live-kill-")
        logfile = open(os.environ.get("AGENT_RUNTIME_LIVE_LOG") or os.path.join(work, "live.log"), "a")
        self.addCleanup(logfile.close)

        def log(event, **data):
            logfile.write(json.dumps(dict(event=event, t=round(time.time(), 1), **data)) + "\n")
            logfile.flush()

        name = "live-kill-" + secrets.token_hex(3)
        lead, receipt, started = Lead(log), None, False
        try:
            started = True
            spawned = lead.tool("spawn", kind="worker", cli="claude", name=name, cwd=work,
                                task="Live test: call the agent-runtime wait tool with target='inbox', timeout=300 and "
                                     "your assignment_id. Do not edit files. Then stop.")
            address = spawned.get("address")
            if not address:  # the spawn result carries no address: read it from hc
                address = json.loads(subprocess.run(["hc", "get", name, "--json"], capture_output=True, text=True,
                                                    check=True).stdout)["address"]
            # External kill: plain subprocesses, outside the runtime (the Bash hook only guards agent shells).
            subprocess.run(["hc", "kill", address], capture_output=True, text=True)
            subprocess.run(["hc", "rm", address], capture_output=True, text=True, check=True)
            log("external_kill", name=name, address=address)
            lead.tool("stop", name=name)  # raises on isError
            batch = None
            for _ in range(3):
                batch = lead.tool("wait", target="all", timeout=5, **({"receipt": receipt} if receipt else {}))
                receipt = batch.get("receipt") or receipt
                log("wait_after_stop", reason=batch.get("reason"), unfinished=batch.get("unfinished"))
                if batch.get("reason") == "empty":
                    break  # a persisted failure message was acknowledged by the receipt on the next call
            self.assertEqual(batch["reason"], "empty")
            self.assertEqual(batch["unfinished"], [])
            self.assertGreater(lead.lines, 0)  # every stdout line parsed as JSON (Lead.rpc raises otherwise)
        finally:
            try:
                if started:
                    try:
                        lead.tool("stop", name=name, receipt=receipt)
                    except Exception as error:  # already stopped by the test body
                        log("stop_error", name=name, error=repr(error))
                for _ in range(10):  # drain: each pass acknowledges the previous batch
                    batch = lead.tool("wait", target="all", timeout=0, **({"receipt": receipt} if receipt else {}))
                    receipt = batch.get("receipt")
                    if not batch.get("messages") and not batch.get("more"):
                        break
            except Exception as error:
                log("drain_error", error=repr(error))
            finally:
                lead.close()
