"""Which session owns the terminal: the identity resolved from the CLI ancestor, and the
refusal of a session that inherits a live lead's terminal."""

import sys
from unittest import mock

from tools_harness import (
    RUNTIME_SRC,
    ToolCase,
    raw,
)

sys.path.insert(0, str(RUNTIME_SRC))
import pytest
from agent_runtime import hc_tools
from agent_runtime.dispatch_store import Store

pytestmark = pytest.mark.integration


class IdentityTests(ToolCase):
    ENV = {"HC_TERMINAL_SOCKET": "/hc/sock"}

    def identity(self, **extra):
        from agent_runtime import hc_tools

        with (
            mock.patch.object(
                hc_tools.lib,
                "ps_entry",
                side_effect={100: (90, "/usr/bin/zsh"), 90: (1, "claude")}.get,
            ),
            mock.patch.object(hc_tools.lib, "lstart", return_value="Wed Nov  4 10:00:00 2026"),
            mock.patch.object(hc_tools.os, "getppid", return_value=100),
        ):
            return hc_tools.dispatch_identity(dict(self.ENV, **extra))

    def test_server_identity_ignores_session_ids_under_a_cli_ancestor(self):
        """agent-runtime/R107"""
        scope, generation = self.identity(
            CODEX_THREAD_ID="t", CLAUDE_CODE_SESSION_ID="c", CLAUDE_SESSION_ID="s"
        )
        self.assertEqual(generation, "90:Wed Nov  4 10:00:00 2026")

    def test_the_session_identity_is_resolved_once_and_never_changes(self):
        """agent-runtime/R165
        A session's identity is decided once: a walk that misses its budget on a later call must not
        fork the team, or the workers of the first team go stale."""
        ctx = hc_tools.Context()
        with mock.patch.object(
            hc_tools.lib, "cli_generation", return_value="90:Wed Nov  4 10:00:00 2026"
        ):
            first = hc_tools.dispatch_identity(self.ENV, ctx)
        with mock.patch.object(hc_tools.lib, "cli_generation", return_value=None):
            second = hc_tools.dispatch_identity(self.ENV, ctx)
        self.assertEqual(first, second)

    def test_clear_or_new_in_same_process_keeps_team_and_assignments(self):
        """agent-runtime/R108"""
        from agent_runtime import dispatch_store as store_module
        from agent_runtime import hc_tools

        Store = store_module.Store
        env = {"HOME": str(self.dir / "home")}
        store = Store(env)
        first = self.identity(CODEX_THREAD_ID="thread-a")
        team = store.team(first[0], "t0", first[1])
        assignment = store.register(team, "w1")["assignment_id"]
        again = self.identity(CODEX_THREAD_ID="thread-b")  # /new: same process, new thread id
        self.assertEqual(first, again)
        self.assertEqual(store.team(again[0], "t0", again[1]), team)
        with store.transaction() as db:
            seen = [r[0] for r in db.execute("SELECT id FROM assignments WHERE team=?", (team,))]
        self.assertEqual(seen, [assignment])


class TerminalOwnerTests(ToolCase):
    """One terminal, one owning session: a session that inherits a live lead's terminal is refused.

    Every session runs under its own fake CLI, so every session presents a generation of its own.
    The lead registers the team for the terminal and holds work in it; a second session that
    inherits the same terminal id asks for the terminal and is refused, and the lead keeps it.
    """

    ENV = {"HC_SCOPE": "scope", "AGENT_RUNTIME_DISPATCH": "1"}

    def setUp(self):
        super().setUp()
        self.replies = {
            "get:t0": {"out": raw("lead", None)},
            "get:w1": {"out": raw("w1", "t0")},
            "read:w1": {"out": "the worker's screen"},
        }

    def session(self):
        """A session of its own: its own fake CLI, so its own generation."""
        return self.start(self.replies, dict(self.ENV), new_cli=True)

    def store(self):
        return Store({"HOME": str(self.dir / "home")})

    def teams(self):
        with self.store().transaction() as db:
            return [
                dict(row) for row in db.execute("SELECT id,generation FROM teams ORDER BY rowid")
            ]

    def lead(self):
        """The lead's session: it owns the terminal, and its team holds work."""
        session = self.session()
        result, text = self.call(session, "wait", {"target": "all", "timeout": 0})
        self.assertFalse(result["isError"], text)
        team = self.teams()[0]
        self.store().register(team["id"], "w1")
        return session, team

    def test_S1_a_session_that_inherits_a_live_leads_terminal_is_refused(self):
        """stale-team-generation/S1: a session that inherits the terminal never owns it."""
        lead, team = self.lead()
        other = self.session()
        result, text = self.call(other, "wait", {"target": "all", "timeout": 0})
        self.assertTrue(result["isError"], text)
        self.assertIn("t0", text, "the refusal does not name the terminal")
        self.assertIn(team["generation"], text, "the refusal does not name the owning generation")
        self.assertRegex(
            text,
            r"(?i)hc terminal|own terminal|new session",
            "the refusal does not name the way out",
        )
        self.assertEqual(len(self.teams()), 1, "the other session registered a team")

    def test_S2_the_live_lead_keeps_its_team_after_the_other_session_called(self):
        """stale-team-generation/S2: the lead reaches its own workers after the other session called."""
        lead, team = self.lead()
        other = self.session()
        self.call(other, "wait", {"target": "all", "timeout": 0})
        result, text = self.call(lead, "message", {"to": "w1", "text": "still mine"})
        self.assertFalse(result["isError"], text)
        self.assertNotIn("stale team generation", text, "the lead was fenced")
        self.assertEqual(len(self.teams()), 1, "the terminal carries more than one team")
        with self.store().transaction() as db:
            workers = {row[0] for row in db.execute("SELECT DISTINCT team FROM workers")}
            messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(workers, {team["id"]}, "the worker left the lead's team")
        self.assertEqual(messages, 1, "the lead's message never reached its worker")

    def test_S9_a_refused_session_gets_the_diagnostic_view_with_a_delivery_error(self):
        """stale-team-generation/S9: a diagnostic call never fails the session that asks."""
        lead, team = self.lead()
        other = self.session()
        for verb, arguments in (
            ("status", {"name": "w1"}),
            ("collect", {"name": "w1"}),
            ("list", {}),
        ):
            result, text = self.call(other, verb, arguments)
            self.assertFalse(result["isError"], "the %s call failed: %s" % (verb, text))
            payload = result.get("structuredContent") or {}
            self.assertIn("delivery_error", payload, "the %s view has none" % verb)
            self.assertIn("t0", payload["delivery_error"], verb)
            self.assertIn(team["generation"], payload["delivery_error"], verb)
        with self.store().transaction() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                0,
                "the refused session reached the lead's worker",
            )
