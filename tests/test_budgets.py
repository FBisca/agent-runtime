"""Complete-envelope budgets, lossless pagination and host output limits (D22)."""
import json
import tomllib
from pathlib import Path

import test_event_wait as event_helpers
from dispatch_store import MAX_RESPONSE_BYTES
from test_mcp_server import TOOLS
from test_tools import ToolCase

import dispatch_wait
import hc_tools
from dispatch_store import MAX_BASE_BYTES, Store, StoreError

CONFIG = Path(__file__).resolve().parents[3] / ".codex" / "config.toml"


def canonical(payload):
    return len(json.dumps(payload, separators=(",", ":")).encode())


class BudgetTests(ToolCase):
    prepare = event_helpers.EventWaitTests.prepare
    spawn = event_helpers.EventWaitTests.spawn
    publish = event_helpers.EventWaitTests.publish

    def ask(self, tool, ok=True, **arguments):
        result = self.rpc.get(self.rpc.tool(tool, arguments))["result"]
        self.assertEqual(result["isError"], not ok, result)
        return result

    def test_maximum_legal_report_fits_the_complete_wait_envelope(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        self.prepare()
        self.publish(0, kind="progress", text="é" * 10900)  # 65400 JSON bytes after escaping
        result = self.ask("wait", target="any", timeout=0)
        payload = result["structuredContent"]
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(len(payload["messages"][0]["text"]), 10900)
        self.assertLessEqual(canonical(payload), MAX_RESPONSE_BYTES)
        self.assertEqual(json.loads(result["content"][0]["text"]), payload)

    def test_task_base_plus_inbox_is_paginated_without_losing_ids_or_over_acking(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        self.prepare()
        created = [self.ask("task", action="create", subject="%03d%s" % (i, "s" * 996))["structuredContent"]["task_id"]
                   for i in range(70)]
        sent = [self.publish(i, kind="progress", text=chr(97 + n) * 60000)["message_id"]
                for n, i in enumerate((0, 1, 0))]
        # Reads piggyback the inbox only with a receipt (D15): the mutating create below delivers the first page.
        seed = self.ask("task", action="create", subject="seed")["structuredContent"]
        created.append(seed["task_id"])
        first = seed["inbox"]
        self.assertEqual(self.store_receipt(first["receipt"]), [m["message_id"] for m in first["messages"]])
        tasks, delivered, arguments, pages = [], [m["message_id"] for m in first["messages"]], {"action": "list", "receipt": first["receipt"]}, 0
        while True:
            payload = self.ask("task", **arguments)["structuredContent"]
            self.assertLessEqual(canonical(payload), MAX_RESPONSE_BYTES)
            pages += 1
            self.assertLess(pages, 20)
            tasks += [t["task_id"] for t in payload["tasks"]]
            inbox = payload.get("inbox") or {"messages": [], "receipt": None, "more": False}
            ids = [m["message_id"] for m in inbox["messages"]]
            if inbox["receipt"]:
                self.assertEqual(self.store_receipt(inbox["receipt"]), ids)  # receipt names exactly the delivered IDs
            delivered += ids
            if not payload.get("next_cursor") and not inbox["more"]:
                break
            arguments = {"action": "list", "receipt": inbox["receipt"]}
            if payload.get("next_cursor"):
                arguments["cursor"] = payload["next_cursor"]
        self.assertEqual(tasks, created)
        self.assertEqual(sorted(delivered), sorted(sent))
        self.assertGreater(pages, 1)

    def store_receipt(self, receipt):
        from dispatch_store import Store
        with Store(self.env).transaction() as db:
            return json.loads(db.execute("SELECT ids FROM receipts WHERE id=?", (receipt,)).fetchone()[0])

    def test_oversized_collect_text_is_explicitly_partial_not_silently_clipped(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        self.prepare()
        self.replies["read"] = {"out": ("z" * 4000 + "\n") * 2000}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        payload = self.ask("collect", name="w1")["structuredContent"]
        self.assertTrue(payload["partial"])
        self.assertGreater(payload["original_bytes"], len(payload["result"]))
        self.assertLessEqual(canonical(payload), MAX_RESPONSE_BYTES)

    def test_host_metadata_and_isolated_codex_limits_cover_every_tool_above_the_envelope_bound(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        session = self.start()
        listed = {t["name"]: t for t in session.send("tools/list")["result"]["tools"]}
        self.assertEqual(set(listed), set(TOOLS))
        tools = tomllib.loads(CONFIG.read_text())["mcp_servers"]["agent-runtime"]["tools"]
        self.assertEqual(set(tools), set(TOOLS))
        for name in TOOLS:
            self.assertEqual(listed[name]["_meta"]["anthropic/maxResultSizeChars"], 500000, name)
            self.assertEqual(tools[name]["output_token_limit"], 524288, name)
        self.assertEqual(tools["spawn"]["approval_mode"], "approve")
        # text + structuredContent compatibility copies of one canonical payload (plus framing) fit both hosts.
        self.assertLess(2 * MAX_RESPONSE_BYTES + 4096, 500000)
        self.assertLess(2 * MAX_RESPONSE_BYTES + 4096, 524288)

    def test_worst_case_wait_all_fixed_arithmetic_fits_and_defers_losslessly(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        self.env = {"HOME": str(self.dir / "home")}
        store = Store(self.env)
        team = store.team("worst", "t0", "lead")
        ids = []
        for n in range(32):
            assignment = store.register(team, "w%02d%s" % (n, "n" * 40))["assignment_id"]
            store.bind(assignment, "t%d" % (n + 1), "worst")
            ids.append(store.publish("worst", "t%d" % (n + 1), "t0", assignment,
                                     chr(97 + n % 26) * 4000, "progress")["message_id"])
        def observe(members, budget):
            return {m["assignment_id"]: "interrupted" for m in members}
        delivered, failures, receipt, rounds = [], [], None, 0
        while True:
            rounds += 1
            self.assertLess(rounds, 6)
            batch = dispatch_wait.wait(store, team, "t0", "all", 1, receipt, hc_tools.Context(), observe)
            if batch["more"]:  # the last page keeps only assignments with unacknowledged results (D26)
                self.assertEqual(len(batch["assignments"]), 32)
            if rounds == 1:
                self.assertEqual(batch["assignments"][0]["runtime_state"], "interrupted")
            else:  # persisted: the store, not the probe, now says failed
                self.assertEqual({a["state"] for a in batch["assignments"]}, {"failed"})
                with store.transaction() as db:
                    self.assertEqual({r[0] for r in db.execute("SELECT state FROM assignments")}, {"failed"})
            self.assertLessEqual(canonical(batch), MAX_RESPONSE_BYTES)
            self.assertTrue(batch["messages"])
            names = [m["message_id"] for m in batch["messages"]]
            failures += [m["message_id"] for m in batch["messages"] if m["kind"] == "failed"]
            self.assertEqual(self.store_receipt(batch["receipt"]), names)
            delivered += names
            receipt = batch["receipt"]
            if not batch["more"]:
                break
        self.assertEqual([m for m in delivered if m not in failures], ids)  # D2 adds one persisted failure each
        self.assertEqual(len(failures), 32)
        self.assertGreater(rounds, 1)
        final = dispatch_wait.wait(store, team, "t0", "all", 0, receipt, hc_tools.Context(), observe)
        self.assertEqual(final["messages"], [])

    def test_structured_base_at_the_cap_still_admits_the_first_message(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        store = Store({"HOME": str(self.dir / "home")})
        team = store.team("starve", "t0", "lead")
        assignment = store.register(team, "w1")["assignment_id"]
        store.bind(assignment, "t1", "starve")
        sent = store.publish("starve", "t1", "t0", assignment, "é" * 10900, "progress")["message_id"]
        batch = store.read(team, "t0", "any", assignment_ids=[], inbox=True, reserve=MAX_BASE_BYTES + len(',"inbox":'))
        self.assertEqual([m["message_id"] for m in batch["messages"]], [sent])
        self.assertTrue(batch["receipt"])
        self.assertFalse(batch["more"])
        with self.assertRaises(StoreError):  # an unbounded base could starve the inbox: refuse, never "more" with nothing
            store.read(team, "t0", "any", assignment_ids=[], inbox=True, reserve=MAX_BASE_BYTES + 1000)

    def test_collect_partial_keeps_the_tail_with_a_single_bounded_trim(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        self.prepare()
        out = "head-marker\n" + ("z" * 4000 + "\n") * 40 + "final-result-marker"
        self.replies["read"] = {"out": out}
        (self.dir / "replies").write_text(json.dumps(self.replies))
        payload = self.ask("collect", name="w1")["structuredContent"]
        self.assertTrue(payload["partial"])
        self.assertEqual(payload["kept"], "tail")
        self.assertTrue(payload["result"].endswith("final-result-marker"))
        self.assertNotIn("head-marker", payload["result"])
        self.assertGreater(len(json.dumps(payload["result"])), MAX_BASE_BYTES // 2 - 4096)
        self.assertLessEqual(len(json.dumps(payload["result"])), MAX_BASE_BYTES // 2)

    def test_task_list_rejects_unicode_digit_cursor_with_a_store_error(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        self.prepare()
        result = self.ask("task", ok=False, action="list", cursor="\u00b2")
        self.assertIn("cursor", result["content"][0]["text"])

    def test_task_schema_advertises_depends_on_limits(self):
        """agent-runtime/R61
        agent-runtime-dispatch/S28"""
        session = self.start()
        listed = {t["name"]: t for t in session.send("tools/list")["result"]["tools"]}
        depends = listed["task"]["inputSchema"]["properties"]["depends_on"]
        self.assertEqual(depends["maxItems"], 32)
        self.assertEqual(depends["items"]["maxLength"], 64)
