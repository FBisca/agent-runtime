"""The Codex worker profile: the launch flag, the usability check, and the per-worker metadata."""

import json
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
