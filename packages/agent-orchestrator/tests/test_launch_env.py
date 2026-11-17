"""launch hands the Lead's environment to the driver without putting a value on argv."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest
from agent_orchestrator.driver import commands

pytestmark = pytest.mark.integration  # writes the env file

SECRET = "sk-launch-env-secret-value"


class LaunchEnvTests(unittest.TestCase):
    def launched_argv(self, env):
        seen = {}

        def fake_run(argv, **_kwargs):
            seen["argv"] = list(argv)
            # What the pane does first: read the env file named in argv.
            seen["env_file"] = next((a for a in argv if "agent-orchestrator-env-" in a), None)
            if seen["env_file"]:
                seen["forwarded"] = json.loads(Path(seen["env_file"]).read_text(encoding="utf-8"))
                seen["mode"] = os.stat(seen["env_file"]).st_mode & 0o777
            return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(commands.subprocess, "run", fake_run),
        ):
            commands._launched("pane", Path("/repo"), Path("/repo/plan.yaml"), [])
        return seen

    def test_R_the_lead_environment_reaches_the_driver_but_not_argv(self):
        seen = self.launched_argv(
            {
                "LEAD_ONLY_VAR": SECRET,
                "HC_TERMINAL_ID": "t0",
                "CLAUDECODE": "1",
                "CLAUDE_CODE_ENTRYPOINT": "cli",
                "PATH": "/lead/bin",
            }
        )
        self.assertIsNotNone(seen["env_file"], seen["argv"])
        self.assertEqual(seen["forwarded"], {"LEAD_ONLY_VAR": SECRET, "PATH": "/lead/bin"})
        self.assertEqual(seen["mode"], 0o600)
        self.assertNotIn(SECRET, " ".join(seen["argv"]))
        self.assertFalse(Path(seen["env_file"]).exists(), "the launcher leaves no env file behind")

    def test_R_the_pane_bootstrap_applies_the_file_deletes_it_and_runs_the_driver(self):
        path = Path(self.id() + ".env")
        path.write_text(json.dumps({"LEAD_ONLY_VAR": SECRET}), encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                commands._ENV_BOOTSTRAP,
                str(path),
                sys.executable,
                "-c",
                "import os; print(os.environ['LEAD_ONLY_VAR'])",
            ],
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"]},
        )
        self.assertEqual(result.stdout.strip(), SECRET, result.stderr)
        self.assertFalse(path.exists())
