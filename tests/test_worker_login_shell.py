"""A spawned worker's environment comes from the user's shell startup files as of spawn time."""

import shlex
import subprocess
import sys
import unittest
from pathlib import Path

RUNTIME_SRC = Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))
import pytest  # noqa: E402
from agent_runtime import profiles  # noqa: E402
from tools_harness import PROVIDERS, InProcessToolCase, raw, scrub, unwrap  # noqa: E402

pytestmark = pytest.mark.integration

PROMPT = "fix 'the' \"bug\" in /tmp/a dir/$HOME; echo `x`\nsecond line"


class LoginShellSpawnTests(InProcessToolCase):
    ARGS = {"kind": "worker", "name": "w1", "task": PROMPT, "cwd": "/tmp/a dir"}

    def spawn(self, env, **extra):
        session = self.start({"get:w1": {"out": raw("w1", "t0")}}, env=env)
        result, text = self.call(session, "spawn", dict(self.ARGS, **extra))
        self.assertFalse(result["isError"], text)
        return self.runs("run")[-1][10:]

    @pytest.mark.spec("Worker login shell")
    def test_a_builtin_worker_runs_through_the_login_shell_with_the_scrub_inside(self):
        for cli in ("claude", "codex", "pi"):
            with self.subTest(cli=cli):
                argv = self.spawn({"SHELL": "/bin/zsh"}, cli=cli)
                self.assertEqual(argv[:2], ["/bin/zsh", "-lc"])
                self.assertEqual(len(argv), 3)
                inner = shlex.split(argv[2])
                self.assertEqual(inner[0], "env")
                self.assertIn("-u", inner)
                self.assertEqual(unwrap(inner)[0], cli)
                self.assertIn(PROMPT, unwrap(inner)[-1])

    @pytest.mark.spec("Worker login shell")
    def test_a_profile_worker_runs_through_the_login_shell(self):
        path = self.dir / "home" / ".config" / "agent-runtime" / "profiles.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"profiles": {"custom": {"cli": "claude", "env": {}}}}')
        argv = self.spawn({"SHELL": "/bin/zsh"}, cli="custom")
        self.assertEqual(argv[:2], ["/bin/zsh", "-lc"])
        inner = shlex.split(argv[2])
        self.assertTrue(inner[0].endswith("agent-runtime-profile"))
        self.assertEqual(inner[1], "custom")

    @pytest.mark.spec("Worker login shell")
    def test_without_a_shell_the_command_runs_directly(self):
        argv = self.spawn({"SHELL": ""}, cli="claude")
        self.assertEqual(argv[: len(scrub(PROVIDERS))], scrub(PROVIDERS))


class LoginShellWrapTests(unittest.TestCase):
    @pytest.mark.spec("Worker login shell")
    def test_a_wrapped_command_round_trips_through_a_real_shell(self):
        program = [
            "env",
            "-u",
            "NOPE",
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            PROMPT,
        ]
        argv = profiles.login_shell({"SHELL": "/bin/sh"}, program)
        out = subprocess.run(argv, capture_output=True, text=True, check=True).stdout
        self.assertEqual(out, PROMPT + "\n")
