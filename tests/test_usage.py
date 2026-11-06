"""The Codex worker profile: the launch flag, the usability check, and the per-worker metadata."""

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_tools import ToolCase, raw, unwrap  # noqa: E402
import hc_tools  # noqa: E402
import usage  # noqa: E402


class CodexProfileTests(ToolCase):
    ARGS = {"kind": "worker", "task": "do x", "cwd": "/tmp/c"}
    LEAD = {"HC_SCOPE": "lead-token", "HC_TERMINAL_SOCKET": "/hc/sock", "AGENT_RUNTIME_DISPATCH": "1"}

    def codex_home(self, label, profile=True, base=None, name="worker"):
        """A CODEX_HOME with (or without) a profile file and an optional base config."""
        home = self.dir / ("codex-" + label)
        home.mkdir()
        if profile:
            (home / ("%s.config.toml" % name)).write_text('approval_policy = "never"\n')
        if base is not None:
            (home / "config.toml").write_text(base)
        return home

    def spawn_codex(self, home, name="w1", override=None, **extra):
        """Spawn a Codex worker and return its program argv."""
        env = dict(self.LEAD, CODEX_HOME=str(home), XDG_STATE_HOME=str(self.dir / "state"))
        if override is not None:
            env["AGENT_RUNTIME_CODEX_PROFILE"] = override
        session = self.start({"get:%s" % name: {"out": raw(name, "t0")}}, env=env)
        result, text = self.call(session, "spawn", dict(self.ARGS, name=name, cli="codex", **extra))
        self.assertFalse(result["isError"], text)
        return unwrap(self.runs("run")[-1][10:])

    def metadata_path(self, worker="w1"):
        return self.dir / "state" / "agent-runtime" / "worktrees" / ("%s.json" % worker)

    def metadata(self, worker="w1"):
        return json.loads(self.metadata_path(worker).read_text())

    def test_S1_codex_worker_launches_under_the_worker_profile(self):
        """agent-runtime/S1: the launch argv carries -p worker between codex --no-daemon and the prompt."""
        home = self.codex_home("usable")
        program = self.spawn_codex(home)
        self.assertEqual(program[:4], ["codex", "--no-daemon", "-p", "worker"])
        self.assertTrue(program[-1].startswith("CODEX IMPLEMENTER RULES"))
        self.assertIn("do x", program[-1])
        with_model = self.spawn_codex(home, name="w2", model="gpt-5.5")
        self.assertEqual(with_model[:6], ["codex", "--no-daemon", "-p", "worker", "--model", "gpt-5.5"])
        meta = self.metadata()
        self.assertTrue(meta["profile_applied"])
        self.assertEqual(meta["profile_name"], "worker")
        self.assertEqual(meta["worker"], "w1")
        self.assertEqual(meta["cli"], "codex")
        self.assertTrue(meta["assignment_id"])
        self.assertIsNone(meta["session_id"])
        self.assertEqual(stat.S_IMODE(self.metadata_path().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.metadata_path().parent.stat().st_mode), 0o700)
        named = self.codex_home("named", name="custom")
        program = self.spawn_codex(named, name="w3", override="custom")
        self.assertEqual(program[:4], ["codex", "--no-daemon", "-p", "custom"])
        self.assertEqual(self.metadata("w3")["profile_name"], "custom")

    def test_S2_unusable_worker_profile_starts_the_worker_without_the_flag(self):
        """agent-runtime/S2: a missing profile file or a legacy [profiles.worker] table starts the
        worker without -p worker, and the metadata records profile_applied=false with the reason."""
        cases = (("missing", False, None, "missing_file"),
                 ("legacy", True, '[profiles.worker]\nmodel = "gpt-5.5"\n', "legacy_table"))
        for label, profile, base, reason in cases:
            with self.subTest(reason):
                home = self.codex_home(label, profile=profile, base=base)
                program = self.spawn_codex(home)
                self.assertEqual(program[:2], ["codex", "--no-daemon"])
                self.assertNotIn("-p", program)
                meta = self.metadata()
                self.assertFalse(meta["profile_applied"])
                self.assertEqual(meta["profile_reason"], reason)
        home = self.codex_home("off")
        program = self.spawn_codex(home, name="w3", override="")
        self.assertEqual(program[:2], ["codex", "--no-daemon"])
        self.assertNotIn("-p", program)
        meta = self.metadata("w3")
        self.assertFalse(meta["profile_applied"])
        self.assertEqual(meta["profile_reason"], "disabled_by_env")


class SessionBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {"HOME": str(self.root), "CODEX_HOME": str(self.root / "codex"),
                    "XDG_STATE_HOME": str(self.root / "state")}
        self.cwd = "/tmp/project.with.dots"
        self.spawned_ms = usage.now_ms()

    def metadata(self, worker, assignment, cli="codex", **extra):
        fields = dict(assignment_id=assignment, cli=cli, cwd=self.cwd,
                      spawned_ms=self.spawned_ms, session_id=None, base_commit="abc")
        fields.update(extra)
        usage.write_worker_metadata(self.env, worker, **fields)

    def log(self, cli, session_id, assignment="alpha", cwd=None):
        fixture = Path(__file__).parent / "fixtures/usage" / (cli + "-head.jsonl")
        text = fixture.read_text().replace("ASSIGNMENT", assignment)
        text = text.replace("SESSION", session_id).replace("CWD", cwd or self.cwd)
        if cli == "codex":
            path = Path(self.env["CODEX_HOME"]) / "sessions/2026/11/06" / (
                "rollout-2026-11-06T12-00-00-" + session_id + ".jsonl")
        else:
            slug = self.cwd.replace("/", "-").replace(".", "-")
            path = self.root / ".claude/projects" / slug / (session_id + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_S4_codex_binds_only_the_assignments_first_user_message(self):
        """worker-token-metrics/S4: same cwd, distinct assignments, creation bound and first user."""
        alpha = self.log("codex", "thread-alpha")
        beta = self.log("codex", "thread-beta", "beta")
        for worker, assignment, path in (("w1", "alpha", alpha), ("w2", "beta", beta)):
            with self.subTest(assignment=assignment):
                self.metadata(worker, assignment)
                self.assertEqual(usage.resolve_session_log(self.env, worker), path)
                meta = usage.read_worker_metadata(self.env, worker)
                self.assertEqual(meta["session_id"], "thread-" + assignment)
                self.assertEqual(meta["session_log"], str(path))
                self.assertEqual(meta["base_commit"], "abc")
                with mock.patch.object(Path, "glob", side_effect=AssertionError("cache missed")):
                    self.assertEqual(usage.resolve_session_log(self.env, worker), path)
        # A reused worker's cached path cannot bind a different assignment.
        self.metadata("w1", "beta", session_log=str(alpha), session_id="thread-alpha")
        self.assertEqual(usage.resolve_session_log(self.env, "w1"), beta)
        self.metadata("old", "alpha", spawned_ms=usage.now_ms() + 10000)
        self.assertIsNone(usage.resolve_session_log(self.env, "old"))
        self.log("codex", "other-cwd", "different-cwd", cwd="/another/project")
        self.metadata("wrong-cwd", "different-cwd")
        self.assertIsNone(usage.resolve_session_log(self.env, "wrong-cwd"))
        report = {"type": "response_item", "payload": {"role": "user", "content": [
            {"type": "input_text", "text": "report assignment_id=report-only"}]}}
        with alpha.open("a") as stream:
            stream.write(json.dumps(report) + "\n")
        for assignment in ("report-only", "alph", "unrelated"):
            with self.subTest(rejected=assignment):
                self.metadata("reject", assignment)
                self.assertIsNone(usage.resolve_session_log(self.env, "reject"))
        fallback = self.log("codex", "fallback", "fallback")
        records = [json.loads(line) for line in fallback.read_text().splitlines()]
        del records[0]["payload"]["session_id"]
        fallback.write_text("\n".join(json.dumps(record) for record in records) + "\n")
        self.metadata("fallback", "fallback")
        # Linux has no st_birthtime: exercise its ctime fallback on every host.
        real_stat = Path.stat
        def ctime_only(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            return SimpleNamespace(st_ctime=info.st_ctime) if path == fallback else info
        with mock.patch.object(Path, "stat", ctime_only):
            self.assertEqual(usage.resolve_session_log(self.env, "fallback"), fallback)
        self.assertEqual(usage.read_worker_metadata(self.env, "fallback")["session_id"], "fallback")

    def test_S5_claude_binds_transcript_and_uses_current_session_directly(self):
        """worker-token-metrics/S5: Claude first-user binding, slug and direct environment lookup."""
        alpha = self.log("claude", "claude-alpha")
        beta = self.log("claude", "claude-beta", "beta")
        self.metadata("w1", "alpha", "claude")
        self.assertEqual(usage.resolve_session_log(self.env, "w1"), alpha)
        self.assertEqual(usage.read_worker_metadata(self.env, "w1")["session_id"], "claude-alpha")
        self.metadata("w2", "beta", "claude")
        self.env["CLAUDE_CODE_SESSION_ID"] = "claude-beta"
        with mock.patch.object(Path, "glob", side_effect=AssertionError("direct lookup missed")):
            self.assertEqual(usage.resolve_session_log(self.env, "w2"), beta)
        # A lead's own transcript is not evidence for a different assignment.
        self.env["CLAUDE_CODE_SESSION_ID"] = "claude-alpha"
        self.metadata("w3", "beta", "claude")
        self.assertEqual(usage.resolve_session_log(self.env, "w3"), beta)
        with alpha.open("a") as stream:
            stream.write(json.dumps({"type": "user", "message": {
                "content": "report assignment_id=report-only"}}) + "\n")
        self.metadata("reject", "report-only", "claude")
        self.assertIsNone(usage.resolve_session_log(self.env, "reject"))
        self.metadata("old", "alpha", "claude", spawned_ms=usage.now_ms() + 10000)
        self.assertIsNone(usage.resolve_session_log(self.env, "old"))

    def test_S5a_identity_fallback_reads_claude_code_session_id(self):
        """worker-token-metrics/S5a: identity fallback uses the actual Claude session variable."""
        env = dict(self.env, HC_SCOPE="scope", CLAUDE_CODE_SESSION_ID="current",
                   CLAUDE_SESSION_ID="obsolete")
        with mock.patch.object(hc_tools.lib, "cli_generation", return_value=None):
            self.assertEqual(hc_tools.dispatch_identity(env), ("scope", "current"))

    def test_S16_session_resolution_fails_open(self):
        """worker-token-metrics/S16: missing, unreadable, malformed or truncated logs are absent."""
        for cli in ("codex", "claude"):
            with self.subTest(cli=cli, failure="missing"):
                self.metadata("missing", "alpha", cli)
                self.assertIsNone(usage.resolve_session_log(self.env, "missing"))
            path = self.log(cli, "broken")
            real_open = Path.open
            def unreadable(candidate, *args, **kwargs):
                if candidate == path:
                    raise PermissionError("unreadable test log")
                return real_open(candidate, *args, **kwargs)
            with self.subTest(cli=cli, failure="unreadable"):
                with mock.patch.object(Path, "open", unreadable):
                    self.assertIsNone(usage.resolve_session_log(self.env, "missing"))
            for content in ('{"type":', 'garbage\n', '[]\n', '\ufffd\n'):
                with self.subTest(cli=cli, content=content):
                    path.write_text(content)
                    self.assertIsNone(usage.resolve_session_log(self.env, "missing"))
            # A first user record beyond the read budget is deliberately not resolved.
            path.write_text(json.dumps({"type": "padding", "text": "x" * usage.SESSION_HEAD_BYTES})
                            + "\n" + '{"type":"user"}\n')
            with mock.patch.object(Path, "open") as opened:
                stream = opened.return_value.__enter__.return_value
                stream.read.return_value = b'{"type":'
                self.assertIsNone(usage._session_match(path, {
                    "cli": cli, "assignment_id": "alpha", "spawned_ms": self.spawned_ms}))
                stream.read.assert_called_once_with(usage.SESSION_HEAD_BYTES)
            self.assertIsNone(usage.resolve_session_log(self.env, "missing"))
            path.unlink()
        self.assertIsNone(usage.resolve_session_log(self.env, "unknown-worker"))
