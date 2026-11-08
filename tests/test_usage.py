"""The Codex worker profile: the launch flag, the usability check, and the per-worker metadata."""

import json
import stat
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mcp_server import SERVER  # noqa: E402

sys.path.insert(0, str(SERVER.parent))

import usage  # noqa: E402
from test_tools import ToolCase, raw, unwrap  # noqa: E402

import pytest

pytestmark = pytest.mark.integration



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
        self.assertTrue(program[-1].startswith("do x"))
        self.assertIn("Worker protocol:", program[-1])
        self.assertIn("report(kind, assignment_id)", program[-1])
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
        """worker-token-metrics/S1a: the record carries the checkout, its base commit and the spawn time."""
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
