"""The Stop guard: one predicate, three dialects, and the budget that bounds how often it speaks.

The guard blocks a turn that ends while work is unseen; the Codex adapter and the Pi dialect answer
the same state with the same reason, so they are tested here beside the guard they mirror.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"))
import pytest  # noqa: E402
from agent_runtime import (  # noqa: E402
    dispatch_store,
    dispatch_wait,
    hc_tools,
)
from hook_harness import (  # noqa: E402
    CONTEXT,
    PLUGIN,
    SHIM,
    STOP,
    FakeCliProcess,
)
from hook_helpers import (  # noqa: E402
    HookStoreCase,
    denied,
)

pytestmark = pytest.mark.integration

# A generation whose process is gone: no process carries that pid, so the row is provably dead.
DEAD_GENERATION = "999999999:Wed Nov  4 10:00:00 2026"


class StopGuardCase(HookStoreCase):
    """The Stop guard's own fixture: the fake CLI process, the team it owns and its budget."""

    STOP = {"hook_event_name": "Stop", "stop_hook_active": False}

    REASON = "agent-runtime: %d unfinished, %d undelivered — call wait(all) before ending the turn"

    MCP_WAIT = {
        "type": "MCP task",
        "server": "plugin:agent-runtime:agent-runtime",
        "tool": "wait",
        "status": "running",
    }

    WORKER_REASON = (
        "agent-runtime: assignment %s is unreported — call report complete or report blocked "
        "before ending the turn"
    )

    def setUp(self):
        super().setUp()
        self.cli = FakeCliProcess(self.env, self.root)
        self.addCleanup(self.cli.close)
        self.store = dispatch_store.Store(self.env)
        self.team_id = self.store.team("scope", "t0", self.cli.generation)

    def stop(self, payload=None, args=()):
        out, err, code = self.cli.run(STOP, payload or self.STOP, args=args)
        self.assertEqual(code, 0, err)
        return out, err

    def prompt(self):
        out, err, code = self.cli.run(CONTEXT, self.PROMPT)
        self.assertEqual(code, 0, err)
        return out

    def budget(self):
        files = list((self.state / "agent-runtime" / "stop-guard").glob("*"))
        self.assertLessEqual(len(files), 1)
        if not files:
            return None
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        return int(files[0].read_text().strip())

    def unseen(self):
        return self.worker(self.store, self.team_id)

    def record_wait(self, reason="attention"):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO last_wait(team,reason,attention,fingerprint) VALUES (?,?,0,?) ON CONFLICT(team) DO UPDATE "
                "SET reason=excluded.reason,attention=0,fingerprint=excluded.fingerprint",
                (self.team_id, reason, dispatch_store.wait_fingerprint(db, self.team_id)),
            )


class StopGuardTests(StopGuardCase):
    def test_S7_the_lead_liveness_hooks_read_the_same_ownership_rule(self):
        """The context reminder and the Stop guard read the same rule as the server: a newer row that is
        dead and empty does not hide the lead's own team, and a newer row that owns the terminal does."""
        self.unseen()  # the lead's own team holds work
        _, dead = self.stray_team(DEAD_GENERATION)  # a newer row that left nothing behind
        self.assertIsNotNone(self.prompt())
        self.assertEqual(self.stop()[0], {"decision": "block", "reason": self.REASON % (1, 0)})

        self.worker(self.store, dead)  # the gone session holds work: it owns the terminal
        self.assertIsNone(self.prompt())
        self.assertIsNone(self.stop()[0])

        self.stray_team(self.other_live_generation())  # a running generation owns it
        self.assertIsNone(self.prompt())
        self.assertIsNone(self.stop()[0])

    def test_S7_a_lead_behind_a_dead_empty_team_may_end_its_turn_once_it_has_seen_the_result(self):
        """The ownership rule does not change when the lead stops: a lead whose wait result it has seen
        may end its turn, while the reminder still points at its own team."""
        self.unseen()  # the lead's own team holds work
        self.stray_team(DEAD_GENERATION)  # a newer row that left nothing behind
        self.assertEqual(self.stop()[0], {"decision": "block", "reason": self.REASON % (1, 0)})

        self.last_wait(self.store, self.team_id, "attention", 2)  # the lead has seen the result
        self.assertIn("workers need you", self.line(self.prompt()))
        self.assertIsNone(self.stop()[0])

    def test_blocks_with_the_wait_reason_when_work_is_unseen(self):
        """agent-runtime/R119"""
        ident = self.unseen()
        self.message(self.store, self.team_id, assignment=ident["assignment_id"])
        out, _ = self.stop()
        self.assertEqual(out, {"decision": "block", "reason": self.REASON % (1, 1)})
        self.assertEqual(self.budget(), 2)

    def test_S3_stop_guard_still_blocks_after_a_partial_wait_result(self):
        """S3: a partial wait(all) result leaves the last-wait row untouched, so the guard still blocks."""
        ident = self.unseen()
        body = json.dumps(
            {
                "message_id": "m1",
                "team_id": self.team_id,
                "worker_id": "w1",
                "assignment_id": ident["assignment_id"],
                "sender": "w1",
                "recipient": "t0",
                "kind": "progress",
                "text": "half done",
            },
            separators=(",", ":"),
        )
        self.sql(
            self.store,
            "INSERT INTO messages(id,team,recipient,assignment,body) VALUES (?,?,?,?,?)",
            "m1",
            self.team_id,
            "t0",
            ident["assignment_id"],
            body,
        )
        self.last_wait(self.store, self.team_id, "timeout", 0)
        with self.store.transaction() as db:
            before = tuple(
                db.execute("SELECT * FROM last_wait WHERE team=?", (self.team_id,)).fetchone()
            )
        batch = dispatch_wait.wait(
            self.store, self.team_id, "t0", "all", 0, None, hc_tools.Context(), lambda *_: {}
        )
        self.assertEqual(batch["reason"], "messages")
        with self.store.transaction() as db:
            self.assertEqual(
                tuple(
                    db.execute("SELECT * FROM last_wait WHERE team=?", (self.team_id,)).fetchone()
                ),
                before,
            )
        out, _ = self.stop()
        self.assertEqual(out, {"decision": "block", "reason": self.REASON % (1, 0)})

    def test_unseen_working_assignment_alone_blocks(self):
        """agent-runtime/R119"""
        self.unseen()
        out, _ = self.stop()
        self.assertEqual(out, {"decision": "block", "reason": self.REASON % (1, 0)})

    def test_allows_blocked_decision_failed_seen_and_resets_the_budget(self):
        """agent-runtime/R120"""
        ident = self.unseen()
        self.stop()
        self.assertEqual(self.budget(), 2)
        for state in ("blocked", "decision", "failed"):
            self.sql(
                self.store,
                "UPDATE assignments SET state=? WHERE id=?",
                state,
                ident["assignment_id"],
            )
            self.assertEqual(self.stop()[0], None, state)
        self.assertEqual(self.budget(), 3)  # a check that finds nothing to block on resets
        self.sql(
            self.store, "UPDATE assignments SET state='working' WHERE id=?", ident["assignment_id"]
        )
        self.last_wait(self.store, self.team_id, "completed", 0)
        self.assertIsNone(self.stop()[0])  # reason != timeout and fingerprint unchanged
        self.sql(
            self.store, "UPDATE assignments SET state='failed' WHERE id=?", ident["assignment_id"]
        )
        self.assertIsNone(self.stop()[0])  # fingerprint changed but nothing left working

    def test_running_wait_task_allows_without_spending_or_resetting_budget(self):
        """agent-runtime/R121"""
        self.unseen()
        self.stop()
        self.assertEqual(self.budget(), 2)
        for variant in (
            {},
            {"server": "agent-runtime"},
            {"tool": "mcp__plugin_agent-runtime_agent-runtime__wait"},
            {"status": "pending"},
        ):  # status values are undocumented: anything non-terminal exempts
            out, _ = self.stop(dict(self.STOP, background_tasks=[dict(self.MCP_WAIT, **variant)]))
            self.assertIsNone(out, variant)
            self.assertEqual(self.budget(), 2)
        terminal = [
            dict(self.MCP_WAIT, status=status)
            for status in ("completed", "failed", "killed", "stopped", "cancelled")
        ]
        for other in (
            dict(self.MCP_WAIT, tool="status"),
            dict(self.MCP_WAIT, type="Bash"),
            dict(self.MCP_WAIT, server="plugin:other:other"),
            *terminal,
            "junk",
        ):
            self.prompt()  # fresh budget for each probe
            out, _ = self.stop(dict(self.STOP, background_tasks=[other]))
            self.assertIsNotNone(out, other)  # unknown or other work: no exemption
            self.assertEqual(self.budget(), 2)

    def test_stop_allows_silently_on_bad_stdin_no_identity_and_broken_import(self):
        """agent-runtime/R114"""
        self.unseen()
        for raw in ("not json", "[1]", ""):
            out, err, code = self.cli.run(STOP, raw=raw)
            self.assertEqual((out, err, code), (None, "", 0), raw)
        (self.root / "n").mkdir()
        nobody = FakeCliProcess(
            {k: v for k, v in self.env.items() if k != "HC_TERMINAL_ID"}, self.root / "n"
        )
        self.addCleanup(nobody.close)
        self.assertEqual(nobody.run(STOP, self.STOP), (None, "", 0))
        broken = self.root / "broken"
        broken.mkdir()
        (broken / "stop_guard.py").write_text(STOP.read_text())
        (broken / "hook_lib.py").write_text("raise ImportError('boom')\n")
        self.assertEqual(
            self.cli.run(broken / "stop_guard.py", self.STOP, process=True), (None, "", 0)
        )
        self.assertIsNone(self.budget())  # none of those touched the budget

    def test_stop_never_writes_the_dispatch_store(self):
        """agent-runtime/R115"""
        self.unseen()

        def snapshot():
            return sorted(
                (p.name, p.stat().st_mtime_ns, p.stat().st_size)
                for p in (self.state / "agent-runtime").glob("dispatch.sqlite3*")
            )

        before = snapshot()
        self.assertIsNotNone(self.stop()[0])  # blocks, spends budget
        self.assertEqual(self.stop(dict(self.STOP, background_tasks=[self.MCP_WAIT]))[0], None)
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.budget(), 2)

    def test_reset_prunes_stale_budget_files_only(self):
        """agent-runtime/R123"""
        self.unseen()
        self.stop()
        folder = self.state / "agent-runtime" / "stop-guard"
        stale, fresh = folder / "stale", folder / "fresh"
        stale.write_text("1")
        fresh.write_text("1")
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        self.prompt()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())

    def test_blocks_three_times_then_allows_with_one_stderr_note(self):
        """agent-runtime/R122"""
        self.unseen()
        for _ in range(3):
            self.assertIsNotNone(self.stop()[0])
        out, err = self.stop()
        self.assertIsNone(out)
        self.assertIn("agent-runtime", err)
        out, err = self.stop()
        self.assertEqual((out, err), (None, ""))  # later Stops keep allowing, silently
        self.prompt()  # UserPromptSubmit resets
        self.assertIsNotNone(self.stop()[0])

    def test_exemption_budget_spent_and_errors_do_not_reset_but_prompt_does(self):
        """agent-runtime/R123"""
        self.unseen()
        self.stop()
        self.stop(dict(self.STOP, background_tasks=[self.MCP_WAIT]))
        self.assertEqual(self.budget(), 2)  # exemption
        locked = sqlite3.connect(
            self.state / "agent-runtime" / "dispatch.sqlite3", isolation_level=None
        )
        locked.execute("BEGIN EXCLUSIVE")
        try:
            self.assertEqual(self.stop()[0], None)
        finally:
            locked.close()
        self.assertEqual(self.budget(), 2)  # error
        self.stop()
        self.stop()
        self.stop()
        self.assertEqual(self.budget(), -1)  # spent: noted once, saturated
        self.stop()
        self.assertEqual(self.budget(), -1)
        self.prompt()
        self.assertEqual(self.budget(), 3)

    def test_prompt_resets_even_when_the_context_hook_has_nothing_to_say(self):
        """agent-runtime/R123"""
        ident = self.unseen()
        self.stop()
        self.sql(
            self.store, "UPDATE assignments SET state='complete' WHERE id=?", ident["assignment_id"]
        )
        self.assertEqual(self.budget(), 2)
        self.assertIsNone(self.prompt())
        self.assertEqual(self.budget(), 3)

    def test_worker_leading_children_blocks_and_dead_or_superseded_teams_allow(self):
        """agent-runtime/R124"""
        worker_env = dict(self.env, HC_TERMINAL_ID="t5")
        child = (
            FakeCliProcess(worker_env, self.root / "w")
            if (self.root / "w").mkdir() is None
            else None
        )
        self.addCleanup(child.close)
        team = self.store.team("scope", "t5", child.generation)
        self.worker(self.store, team)
        out, err, code = child.run(STOP, self.STOP)
        self.assertEqual((out["decision"], code), ("block", 0), err)
        _, newer = self.stray_team("1:newer", lead="t5")  # a newer team supersedes the hook's
        self.worker(self.store, newer)  # it still holds work, so it owns the terminal
        self.assertIsNone(child.run(STOP, self.STOP)[0])
        self.assertIsNone(self.stop()[0])  # t0 leads no team with needs
        bound_env = dict(self.env, HC_TERMINAL_ID="t6")
        (self.root / "b").mkdir()
        bound = FakeCliProcess(bound_env, self.root / "b")
        self.addCleanup(bound.close)
        ident = self.store.register(self.team_id, "w-bound")
        self.store.bind(ident["assignment_id"], "t6", "scope")
        self.assertEqual(
            bound.run(STOP, self.STOP)[0],
            {"decision": "block", "reason": self.WORKER_REASON % ident["assignment_id"]},
        )
        self.sql(
            self.store, "UPDATE assignments SET state='blocked' WHERE id=?", ident["assignment_id"]
        )
        self.assertIsNone(bound.run(STOP, self.STOP)[0])  # no such assignment: allowed
        (self.root / "s").mkdir()
        superseded = FakeCliProcess(dict(self.env, HC_TERMINAL_ID="t7"), self.root / "s")
        self.addCleanup(superseded.close)
        old = self.store.register(self.team_id, "w-old")
        self.store.bind(old["assignment_id"], "t7", "scope")
        self.assertEqual(  # its team still owns the terminal: refused
            superseded.run(STOP, self.STOP)[0],
            {"decision": "block", "reason": self.WORKER_REASON % old["assignment_id"]},
        )
        self.stray_team(self.other_live_generation())  # a newer live team now owns t0
        self.assertIsNone(superseded.run(STOP, self.STOP)[0])  # superseded team: allowed
        other = dispatch_store.Store(self.env)
        dead = other.team("scope", "t9", "999:dead")  # a dead generation
        gone = other.register(dead, "w-gone")
        other.bind(gone["assignment_id"], "t9", "scope")
        (self.root / "n").mkdir()
        nobody = FakeCliProcess(dict(self.env, HC_TERMINAL_ID="t9"), self.root / "n")
        self.addCleanup(nobody.close)
        self.assertIsNone(nobody.run(STOP, self.STOP)[0])  # dead team generation: allowed

    def test_reported_unchanged_stall_counts_as_seen_until_progress(self):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )
        self.assertIsNone(self.stop()[0])
        self.assertIsNone(self.prompt())  # no "call wait(all)" line either
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9 WHERE assignment=?",
            ident["assignment_id"],
        )
        self.assertEqual(self.stop()[0]["decision"], "block")
        self.assertIn("call wait(all)", self.line(self.prompt()))
        self.sql(self.store, "DROP TABLE stall_progress")  # older store: no reported stalls
        self.assertEqual(self.stop()[0]["decision"], "block")

    def test_recovery_after_a_stall_batch_that_was_the_last_wait_is_unseen(self):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )
        self.record_wait()  # the stall batch was the lead's last wait(all)
        self.assertIsNone(self.stop()[0])
        self.assertIsNone(self.prompt())
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9,last_turn=2 WHERE assignment=?",
            ident["assignment_id"],
        )
        self.assertEqual(self.stop()[0]["decision"], "block")
        self.assertIn("call wait(all)", self.line(self.prompt()))

    def test_turns_after_a_following_wait_do_not_flip_seen(self):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9,last_turn=2 WHERE assignment=?",
            ident["assignment_id"],
        )
        self.record_wait("timeout")
        self.record_wait("attention")  # a later wait(all) delivered nothing new
        self.assertIsNone(self.stop()[0])
        for turn in (3, 4):
            self.sql(
                self.store,
                "UPDATE stall_progress SET last_progress_ms=?,last_turn=? WHERE assignment=?",
                10 + turn,
                turn,
                ident["assignment_id"],
            )
            self.assertIsNone(self.stop()[0])
            self.assertIsNone(self.prompt())

    def test_fingerprint_is_identical_for_server_and_readonly_connection_with_or_without_stall_progress(
        self,
    ):
        """agent-runtime/R128"""
        ident = self.unseen()
        self.sql(
            self.store,
            "INSERT INTO stall_progress(assignment,last_progress_ms,last_turn,stall_reported_for) "
            "VALUES (?,?,?,?)",
            ident["assignment_id"],
            5,
            1,
            5,
        )

        def both():
            with self.store.transaction() as db:
                server = dispatch_store.wait_fingerprint(db, self.team_id)
            ro = sqlite3.connect(
                (self.state / "agent-runtime" / "dispatch.sqlite3").as_uri() + "?mode=ro", uri=True
            )
            try:
                return server, dispatch_store.wait_fingerprint(ro, self.team_id)
            finally:
                ro.close()

        reported = both()
        self.assertEqual(reported[0], reported[1])
        self.sql(
            self.store,
            "UPDATE stall_progress SET last_progress_ms=9 WHERE assignment=?",
            ident["assignment_id"],
        )
        recovered = both()
        self.assertEqual(recovered[0], recovered[1])
        self.assertNotEqual(reported[0], recovered[0])
        self.sql(self.store, "DROP TABLE stall_progress")
        absent = both()
        self.assertEqual(absent[0], absent[1])
        self.assertEqual(absent[0], recovered[0])  # absent behaves as "not reported"

    def test_S5_a_worker_with_its_own_unreported_assignment_is_blocked_until_it_reports(self):
        """pi-lead-rhythm/S5: a worker's own assignment, not a lead's team, keeps its Stop open on
        Claude, Codex and Pi with the lead guard's reason shape, and `report blocked` is the way out."""
        worker_env = dict(self.env, HC_TERMINAL_ID="wt0")
        (self.root / "wrk").mkdir()  # the worker session is its own terminal, leading no team
        worker = FakeCliProcess(worker_env, self.root / "wrk")
        self.addCleanup(worker.close)
        ident = self.store.register(self.team_id, "w1")
        self.store.bind(ident["assignment_id"], "wt0", "scope")
        reason = self.WORKER_REASON % ident["assignment_id"]
        out, err, code = worker.run(STOP, self.STOP)
        self.assertEqual((out, code), ({"decision": "block", "reason": reason}, 0), err)
        out, err, code = worker.run(STOP, self.STOP, args=("--cli", "codex"))
        self.assertEqual((out, code), ({"decision": "block", "reason": reason}, 0), err)
        out, err, code = worker.run(STOP, self.STOP, args=("--cli", "pi", "--dialect", "json"))
        self.assertEqual((out, code), ({"block": True, "reason": reason}, 0), err)
        self.store.publish(
            "scope", "wt0", "t0", ident["assignment_id"], "stuck on the fixture", "blocked"
        )
        self.assertIsNone(worker.run(STOP, self.STOP)[0])  # reporting blocked is the way out
        self.assertEqual(self.budget(), 3)  # and the work period starts over

    def test_S5_a_workers_parked_wait_does_not_exempt_its_own_assignment(self):
        """pi-lead-rhythm/S5: parking a wait is the lead's way out; a worker's own unreported
        assignment still keeps its turn open, and only a report ends it."""
        (self.root / "wp").mkdir()
        worker = FakeCliProcess(dict(self.env, HC_TERMINAL_ID="wt1"), self.root / "wp")
        self.addCleanup(worker.close)
        ident = self.store.register(self.team_id, "w-park")
        self.store.bind(ident["assignment_id"], "wt1", "scope")
        payload = dict(self.STOP, background_tasks=[self.MCP_WAIT])
        self.assertEqual(
            worker.run(STOP, payload)[0],
            {"decision": "block", "reason": self.WORKER_REASON % ident["assignment_id"]},
        )

    def test_S1_the_claude_codex_and_pi_stops_refuse_one_state_with_one_reason(self):
        """pi-lead-rhythm/S1: one predicate, three dialects, one reason."""
        ident = self.unseen()
        self.message(self.store, self.team_id, assignment=ident["assignment_id"])
        reason = self.REASON % (1, 1)
        self.assertEqual(self.stop()[0], {"decision": "block", "reason": reason})
        self.prompt()  # a fresh budget for each host; the budget itself belongs to the terminal
        self.assertEqual(
            self.stop(args=("--cli", "codex"))[0], {"decision": "block", "reason": reason}
        )
        self.prompt()
        self.assertEqual(
            self.stop(args=("--cli", "pi", "--dialect", "json"))[0],
            {"block": True, "reason": reason},
        )

    def test_S2_the_pi_dialect_prints_a_neutral_answer_and_the_claude_shape_is_unchanged(self):
        """pi-lead-rhythm/S2: `agent-runtime-hook stop --cli pi --dialect json` answers `{block, reason}`."""
        shim = ["/bin/sh", str(SHIM)]
        ident = self.unseen()
        reason = self.REASON % (1, 0)
        out, err, code = self.cli.run(
            shim, self.STOP, args=("stop", "--cli", "pi", "--dialect", "json")
        )
        self.assertEqual((code, out), (0, {"block": True, "reason": reason}), err)
        self.prompt()  # the row names the blocking dialect: `--cli pi` alone blocks in the neutral shape
        out, err, code = self.cli.run(shim, self.STOP, args=("stop", "--cli", "pi"))
        self.assertEqual((code, out), (0, {"block": True, "reason": reason}), err)
        self.prompt()
        out, err, code = self.cli.run(shim, self.STOP, args=("stop", "--cli", "codex"))
        self.assertEqual((code, out), (0, {"decision": "block", "reason": reason}), err)
        self.sql(
            self.store, "UPDATE assignments SET state='complete' WHERE id=?", ident["assignment_id"]
        )
        # an allowed Stop is answered only when the host asked for the neutral dialect; the implicit
        # row keeps block-or-silence, so a Claude or Codex host is never handed a new object here
        out, err, code = self.cli.run(shim, self.STOP, args=("stop", "--cli", "pi"))
        self.assertEqual((code, out), (0, None), err)
        out, err, code = self.cli.run(
            shim, self.STOP, args=("stop", "--cli", "pi", "--dialect", "json")
        )
        self.assertEqual((code, out), (0, {"block": False, "reason": ""}), err)

    def test_S10_a_spent_budget_is_spent_for_every_host_of_one_terminal(self):
        """pi-lead-rhythm/S10: switching host grants no fresh blocks; one budget per work period."""
        spent = (
            "agent-runtime: stop guard budget spent; allowing the stop (resets on your next prompt)"
        )
        self.unseen()
        for _ in range(3):
            self.assertIsNotNone(self.stop()[0])
        self.assertEqual(self.budget(), 0)
        self.cli.close()  # the first host exits; the second one takes the same terminal
        folder = self.root / "second"
        folder.mkdir()
        other = FakeCliProcess(self.env, folder)
        self.addCleanup(other.close)
        self.worker(self.store, self.store.team("scope", "t0", other.generation))
        out, err, code = other.run(STOP, self.STOP, args=("--cli", "pi", "--dialect", "json"))
        self.assertEqual((code, out), (0, {"block": False, "reason": spent}), err)
        self.assertEqual(self.budget(), -1)
        files = list((self.state / "agent-runtime" / "stop-guard").glob("*"))
        self.assertEqual(len(files), 1)  # one budget for the terminal, not one per host
        _, err, code = other.run(CONTEXT, self.PROMPT)  # a new user work period
        self.assertEqual(code, 0, err)
        out, err, code = other.run(STOP, self.STOP, args=("--cli", "pi", "--dialect", "json"))
        self.assertEqual((code, out), (0, {"block": True, "reason": self.REASON % (1, 0)}), err)


class CodexHookTests(HookStoreCase):
    """The Codex adapter: the shim routes `stop` / `context` subcommands to the same hooks, fed with Codex-shaped
    payloads (docs/verification/codex-hooks.md)."""

    SHIM_CMD = ["/bin/sh", str(SHIM)]
    STOP = {
        "session_id": "s",
        "turn_id": "t",
        "cwd": "/",
        "hook_event_name": "Stop",
        "model": "m",
        "permission_mode": "default",
        "transcript_path": None,
        "stop_hook_active": False,
        "last_assistant_message": "done",
    }
    PROMPT = {
        "session_id": "s",
        "turn_id": "t",
        "cwd": "/",
        "hook_event_name": "UserPromptSubmit",
        "model": "m",
        "permission_mode": "default",
        "transcript_path": None,
        "prompt": "hi",
    }

    def setUp(self):
        super().setUp()
        self.cli = FakeCliProcess(self.env, self.root)
        self.addCleanup(self.cli.close)
        self.store = dispatch_store.Store(self.env)
        self.team_id = self.store.team("scope", "t0", self.cli.generation)

    def stop(self, **extra):
        out, err, code = self.cli.run(
            self.SHIM_CMD, dict(self.STOP, **extra), args=("stop", "--cli", "codex")
        )
        self.assertEqual(code, 0, err)
        return out, err

    def context(self, payload):
        out, err, code = self.cli.run(self.SHIM_CMD, payload, args=("context", "--cli", "codex"))
        self.assertEqual(code, 0, err)
        return out

    def budget(self):
        files = list((self.state / "agent-runtime" / "stop-guard").glob("*"))
        return int(files[0].read_text()) if files else None

    def test_codex_stop_blocks_with_the_wait_reason_and_allows_with_empty_output(self):
        """agent-runtime/R125"""
        ident = self.worker(self.store, self.team_id)
        out, _ = self.stop()
        self.assertEqual(
            out,
            {
                "decision": "block",
                "reason": "agent-runtime: 1 unfinished, 0 undelivered — "
                "call wait(all) before ending the turn",
            },
        )
        self.sql(
            self.store, "UPDATE assignments SET state='blocked' WHERE id=?", ident["assignment_id"]
        )
        self.assertEqual(
            self.cli.run(self.SHIM_CMD, self.STOP, args=("stop", "--cli", "codex")), (None, "", 0)
        )

    def test_codex_budget_ignores_stop_hook_active_and_background_tasks_then_resets_on_prompt(self):
        """agent-runtime/R125"""
        self.worker(self.store, self.team_id)
        mcp = [
            {
                "type": "MCP task",
                "server": "plugin:agent-runtime:agent-runtime",
                "tool": "wait",
                "status": "running",
            }
        ]
        self.assertIsNotNone(self.stop(background_tasks=mcp)[0])  # no D10 exemption on Codex
        self.assertIsNotNone(self.stop(stop_hook_active=True)[0])  # only the budget bounds it
        self.assertIsNotNone(self.stop(stop_hook_active=True)[0])
        out, err = self.stop(stop_hook_active=True)
        self.assertEqual(out, None)
        self.assertIn("agent-runtime", err)
        self.assertEqual(self.budget(), -1)
        self.context(self.PROMPT)  # Codex UserPromptSubmit resets
        self.assertEqual(self.budget(), 3)
        self.assertIsNotNone(self.stop()[0])

    def test_codex_session_start_and_prompt_carry_the_context_line(self):
        """agent-runtime/R126"""
        self.worker(self.store, self.team_id)
        line = "agent-runtime: 1 unfinished, 0 undelivered — call wait(all)"
        base = {
            "session_id": "s",
            "cwd": "/",
            "model": "m",
            "permission_mode": "default",
            "transcript_path": None,
        }
        for source in ("compact", "clear"):
            out = self.context(dict(base, hook_event_name="SessionStart", source=source))
            self.assertEqual(
                out,
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": line,
                    }
                },
            )
        for source in ("startup", "resume", "fork"):
            self.assertIsNone(
                self.context(dict(base, hook_event_name="SessionStart", source=source)), source
            )
        out = self.context(self.PROMPT)
        self.assertEqual(
            out,
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": line,
                }
            },
        )

    def test_install_prints_the_codex_lead_hooks_only_on_request(self):
        """agent-runtime/R125"""
        base = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root / "home"),
            "BIN_DIR": str(self.root / "bin"),
        }
        plain = subprocess.run(
            [str(PLUGIN / "install.sh")], env=base, capture_output=True, text=True, timeout=30
        )
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertNotIn("hooks.Stop", plain.stdout)
        asked = subprocess.run(
            [str(PLUGIN / "install.sh")],
            env=dict(base, CODEX_LEAD_HOOKS="1"),
            capture_output=True,
            text=True,
            timeout=30,
        )

        def toml(out):  # the printed config starts after the instructions
            return tomllib.loads(out[out.index("[mcp_servers.agent-runtime]") :])

        hooks = toml(asked.stdout)["hooks"]
        for event, suffix in (
            ("Stop", " stop --cli codex"),
            ("SessionStart", " context --cli codex"),
            ("UserPromptSubmit", " context --cli codex"),
        ):
            commands = [h["command"] for group in hooks[event] for h in group["hooks"]]
            self.assertEqual(len(commands), 1, event)
            self.assertTrue(commands[0].endswith(suffix), commands)
            self.assertIn("agent-runtime-hook", commands[0])
        self.assertIn("PreToolUse", hooks)  # the existing guard blocks stay
        self.assertNotIn("Stop", toml(plain.stdout).get("hooks", {}))

    def test_shim_exits_zero_when_its_script_is_missing(self):
        """agent-runtime/R125"""
        (self.root / "bin").mkdir()
        (self.root / "hooks").mkdir()
        shim = self.root / "bin" / "agent-runtime-hook"
        shutil.copy(SHIM, shim)  # no hooks/stop_guard.py next to it
        done = subprocess.run(
            ["/bin/sh", str(shim), "stop", "--cli", "codex"],
            input="{}",
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)

    def test_attention_line_and_default_subcommand_is_still_the_pre_tool_use_guard(self):
        """agent-runtime/R126"""
        self.worker(self.store, self.team_id)
        self.last_wait(self.store, self.team_id, "attention", 1)
        self.assertEqual(
            self.line(self.context(self.PROMPT)), "agent-runtime: 1 workers need you — check status"
        )
        out, err, _ = self.cli.run(
            self.SHIM_CMD,
            {"tool_name": "Bash", "tool_input": {"command": "hc send w hi"}},
            args=("--cli", "codex"),
        )
        self.assertTrue(denied(out), err)


if __name__ == "__main__":
    unittest.main()
