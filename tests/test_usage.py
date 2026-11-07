"""The Codex worker profile: the launch flag, the usability check, and the per-worker metadata."""

import json
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp_server import SERVER  # noqa: E402

sys.path.insert(0, str(SERVER.parent))

import usage  # noqa: E402
import hc_tools  # noqa: E402
from test_tools import ToolCase, raw, unwrap  # noqa: E402


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

    def state(self):
        return self.dir / "state"

    def metadata_path(self, worker="w1"):
        return self.state() / "agent-runtime" / "worktrees" / ("%s.json" % worker)

    def metadata(self, worker="w1"):
        return json.loads(self.metadata_path(worker).read_text())

    def spawn(self, cli="codex", name="w1", home=None, override=None, dispatch=True, cwd=None, replies=None,
              extra_env=None, **extra):
        """Spawn a worker through the tool; returns (result, text)."""
        env = dict(self.LEAD) if dispatch else {}
        if home is not None:
            env["CODEX_HOME"] = str(home)
        if override is not None:
            env["AGENT_RUNTIME_CODEX_PROFILE"] = override
        env["XDG_STATE_HOME"] = str(self.state())
        env.update(extra_env or {})
        args = dict(self.ARGS, name=name, cli=cli, **extra)
        if cwd is not None:
            args["cwd"] = cwd
        if replies is None:
            replies = {"get:%s" % name: {"out": raw(name, "t0")}}
        session = self.start(replies, env=env)
        return self.call(session, "spawn", args)

    def spawn_codex(self, **kwargs):
        """Spawn a worker that starts; returns its program argv."""
        result, text = self.spawn(**kwargs)
        self.assertFalse(result["isError"], text)
        return unwrap(self.runs("run")[-1][10:])

    def test_S1_codex_worker_launches_under_the_worker_profile(self):
        """worker-token-metrics/S1: the launch argv carries -p worker between codex --no-daemon and the prompt."""
        home = self.codex_home("usable")
        program = self.spawn_codex(home=home)
        self.assertEqual(program[:4], ["codex", "--no-daemon", "-p", "worker"])
        self.assertTrue(program[-1].startswith("CODEX IMPLEMENTER RULES"))
        self.assertIn("do x", program[-1])
        with_model = self.spawn_codex(home=home, name="w2", model="gpt-5.5")
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
        program = self.spawn_codex(home=named, name="w3", override="custom")
        self.assertEqual(program[:4], ["codex", "--no-daemon", "-p", "custom"])
        self.assertEqual(self.metadata("w3")["profile_name"], "custom")

    def test_S2_unusable_worker_profile_starts_the_worker_without_the_flag(self):
        """worker-token-metrics/S2: a missing profile file or a legacy [profiles.worker] table starts the
        worker without -p worker, and the metadata records profile_applied=false with the reason."""
        cases = (("missing", False, None, "missing_file", "w1"),
                 ("legacy", True, '[profiles.worker]\nmodel = "gpt-5.5"\n', "legacy_table", "w2"))
        for label, profile, base, reason, worker in cases:
            with self.subTest(reason):
                home = self.codex_home(label, profile=profile, base=base)
                program = self.spawn_codex(home=home, name=worker)
                self.assertEqual(program[:2], ["codex", "--no-daemon"])
                self.assertNotIn("-p", program)
                meta = self.metadata(worker)
                self.assertFalse(meta["profile_applied"])
                self.assertEqual(meta["profile_reason"], reason)
        home = self.codex_home("off")
        program = self.spawn_codex(home=home, name="w3", override="")
        self.assertEqual(program[:2], ["codex", "--no-daemon"])
        self.assertNotIn("-p", program)
        meta = self.metadata("w3")
        self.assertFalse(meta["profile_applied"])
        self.assertEqual(meta["profile_reason"], "disabled_by_env")

    def test_legacy_mode_writes_no_state(self):
        """agent-runtime/R58: without dispatch the runtime writes nothing and creates no state directory."""
        home = self.codex_home("legacy")
        result, text = self.spawn(home=home, dispatch=False)
        self.assertFalse(result["isError"], text)
        self.assertFalse(self.state().exists())

    def test_refused_spawn_writes_no_metadata(self):
        """worker-token-metrics/S2a: a spawn hc refuses, or a worker whose task was not sent, leaves no record."""
        home = self.codex_home("refused")
        result, text = self.spawn(home=home, replies={"run": {"rc": 1, "err": "name w1 is taken"}})
        self.assertTrue(result["isError"])
        self.assertFalse(self.metadata_path("w1").exists())
        result, text = self.spawn(name="w2", home=home,
                                  replies={"get:w2": {"out": raw("w2", "t0")},
                                           "run": {"out": json.dumps({"first_message": {"sent": False}})}})
        self.assertTrue(result["isError"])
        self.assertFalse(self.metadata_path("w2").exists())

    def test_cancelled_spawn_records_a_worker_that_may_survive(self):
        """worker-token-metrics/S2e: a cancelled spawn whose terminal was not discarded still records it."""
        home = self.codex_home("cancelled")
        env = dict(self.LEAD, CODEX_HOME=str(home), XDG_STATE_HOME=str(self.state()))
        lead = self.start({"run": {"sleep": 30}, "get:w1": {"rc": 1, "err": "boom"}}, env)
        write = lambda message: (lead.proc.stdin.write(json.dumps(message) + "\n"), lead.proc.stdin.flush())
        write({"jsonrpc": "2.0", "id": 70, "method": "tools/call",
               "params": {"name": "spawn", "arguments": dict(self.ARGS, name="w1", cli="codex")}})
        deadline = time.monotonic() + 5
        while not self.runs("run"):
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.05)
        write({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 70}})
        lead.proc.stdin.close()
        self.assertEqual(lead.proc.wait(timeout=15), 0)
        self.assertTrue(self.metadata_path("w1").exists())

    def test_claude_worker_has_no_codex_profile(self):
        """worker-token-metrics/S2b: the Codex worker profile does not apply to a claude worker."""
        program = self.spawn_codex(cli="claude")
        self.assertNotIn("-p", program)
        meta = self.metadata("w1")
        self.assertIsNone(meta["profile_applied"])
        self.assertIsNone(meta["profile_reason"])
        self.assertIsNone(meta["profile_name"])

    def test_profile_override_missing_file_and_unsafe_name(self):
        """worker-token-metrics/S2c: an override without its file is missing_file; an unsafe name is refused."""
        home = self.codex_home("override")
        program = self.spawn_codex(home=home, name="w1", override="custom")
        self.assertNotIn("-p", program)
        self.assertEqual(self.metadata("w1")["profile_reason"], "missing_file")
        program = self.spawn_codex(home=home, name="w2", override="../evil")
        self.assertNotIn("-p", program)
        meta = self.metadata("w2")
        self.assertFalse(meta["profile_applied"])
        self.assertIsNone(meta["profile_name"])
        self.assertFalse((self.dir / "evil.config.toml").exists())
        env = {"XDG_STATE_HOME": str(self.state()), "HOME": str(self.dir / "home")}
        self.assertIsNone(usage.write_worker_metadata(env, "../evil", cli="codex"))
        self.assertFalse((self.dir / "evil.json").exists())

    def test_metadata_records_cwd_base_commit_and_spawn_time(self):
        """worker-token-metrics/S1a: base commit and spawn time are captured before the worker starts."""
        home = self.codex_home("record")
        self.spawn_codex(home=home, cwd="/tmp/c")  # not a repository
        meta = self.metadata("w1")
        self.assertEqual(meta["cwd"], "/tmp/c")
        self.assertIsNone(meta["base_commit"])
        self.assertIsInstance(meta["spawned_ms"], int)
        self.assertLess(abs(meta["spawned_ms"] - int(time.time() * 1000)), 60000)
        repo = self.dir / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com", "-c", "user.name=Test",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
        self.spawn_codex(home=home, name="w2", cwd=str(repo))
        meta = self.metadata("w2")
        self.assertEqual(meta["cwd"], str(repo))
        self.assertEqual(meta["base_commit"], head)
        # Git's own environment must not point the command at another repository.
        self.spawn_codex(home=home, name="w3", cwd=str(repo),
                         extra_env={"GIT_DIR": str(self.dir / "elsewhere.git")})
        self.assertEqual(self.metadata("w3")["base_commit"], head)
        clock = [1000]
        env = dict(self.LEAD, HOME=str(self.dir / "home"), XDG_STATE_HOME=str(self.state()),
                   HC_TERMINAL_ID="t0")

        def worker_starts(argv, *_args):
            if argv[:2] == ["hc", "run"]:
                clock[0] = 9000  # Startup can take longer than the log creation-time slack.
                subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.com",
                                "-c", "user.name=Test", "commit", "-q", "--allow-empty", "-m", "worker"],
                               check=True)
                return 0, "{}", ""
            self.assertEqual(argv[:3], ["hc", "get", "timed"])
            return 0, raw("timed", "t0"), ""

        with mock.patch.object(hc_tools.lib, "resolve", return_value={"runtime": "hc"}), \
                mock.patch.object(hc_tools.lib, "cli_generation", return_value="clock-session"), \
                mock.patch.object(hc_tools, "run", side_effect=worker_starts), \
                mock.patch.object(usage, "now_ms", side_effect=lambda: clock[0]):
            result = hc_tools.spawn(dict(self.ARGS, name="timed", cli="claude", cwd=str(repo)),
                                    env, hc_tools.Context())
        self.assertFalse(result["isError"])
        self.assertEqual(self.metadata("timed")["spawned_ms"], 1000)
        self.assertEqual(self.metadata("timed")["base_commit"], head)

    def test_read_worker_metadata_returns_the_record_or_nothing(self):
        """worker-token-metrics/S1b: the reader returns the record that was written, and nothing else."""
        env = {"XDG_STATE_HOME": str(self.state()), "HOME": str(self.dir / "home")}
        self.assertIsNotNone(usage.write_worker_metadata(env, "w1", cli="codex", profile_applied=True))
        record = usage.read_worker_metadata(env, "w1")
        self.assertEqual(record["worker"], "w1")
        self.assertEqual(record["cli"], "codex")
        self.assertTrue(record["profile_applied"])
        self.assertIsNone(usage.read_worker_metadata(env, "w2"))
        self.assertIsNone(usage.read_worker_metadata(env, "../evil"))

    def test_unserializable_metadata_is_dropped_not_raised(self):
        """worker-token-metrics/S1d: a field that cannot be serialized drops the record, it does not fail the spawn."""
        env = {"XDG_STATE_HOME": str(self.state()), "HOME": str(self.dir / "home")}
        self.assertIsNone(usage.write_worker_metadata(env, "w1", cli="codex", broken=object()))
        self.assertIsNone(usage.read_worker_metadata(env, "w1"))

    def test_symlinked_worktrees_directory_is_refused(self):
        """worker-token-metrics/S1c: a worktrees directory that is a link is refused, never followed."""
        target = self.dir / "elsewhere"
        target.mkdir()
        root = self.state() / "agent-runtime"
        root.mkdir(parents=True)
        (root / "worktrees").symlink_to(target)
        env = {"XDG_STATE_HOME": str(self.state()), "HOME": str(self.dir / "home")}
        self.assertIsNone(usage.write_worker_metadata(env, "w1", cli="codex"))
        self.assertEqual(list(target.iterdir()), [])


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

    def log(self, cli, session_id, assignment="alpha", cwd=None, fixture_name=None):
        fixture = Path(__file__).parent / "fixtures/usage" / (fixture_name or (cli + "-head.jsonl"))
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

    def test_S4a_binding_skips_injected_context_and_stops_before_report_echoes(self):
        """worker-token-metrics/S4a: injected context and placeholders precede the real prompt."""
        for cli in ("codex", "claude"):
            with self.subTest(cli=cli):
                path = self.log(cli, "context-session", fixture_name=cli + "-context-head.jsonl")
                self.metadata("context", "alpha", cli)
                self.assertEqual(usage.resolve_session_log(self.env, "context"), path)
                original = path.read_text()
                # A different prompt assignment wins even if our id appears after the assistant.
                path.write_text(original.replace("assignment_id=alpha", "assignment_id=another")
                                .replace("assignment_id=echo", "assignment_id=alpha"))
                self.assertIsNone(usage.resolve_session_log(self.env, "context"))
                # No prompt marker: a report after the first assistant is still not a prompt.
                path.write_text(original.replace("assignment_id=alpha", "no marker")
                                .replace("assignment_id=echo", "assignment_id=alpha"))
                self.assertIsNone(usage.resolve_session_log(self.env, "context"))

    def test_S4_codex_binds_only_the_assignments_first_prompt(self):
        """worker-token-metrics/S4: same cwd, distinct assignments, creation bound and first prompt."""
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
        uuid = "01a1b0bf-6178-72b1-b259-528453e94da2"
        full_id = self.log("codex", uuid, "uuid")
        records = [json.loads(line) for line in full_id.read_text().splitlines()]
        del records[0]["payload"]["session_id"]
        for source in ("id", "filename"):
            with self.subTest(session_id_source=source):
                if source == "id":
                    records[0]["payload"]["id"] = uuid
                else:
                    del records[0]["payload"]["id"]
                full_id.write_text("\n".join(json.dumps(record) for record in records) + "\n")
                self.metadata("uuid", "uuid")
                self.assertEqual(usage.resolve_session_log(self.env, "uuid"), full_id)
                self.assertEqual(usage.read_worker_metadata(self.env, "uuid")["session_id"], uuid)

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
