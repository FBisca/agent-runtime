"""The profile rules in process: what a config may say, and what a family keeps.

Pure logic: no subprocess, no server, no clock. The boundary tests live in `test_tools.py`.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME_SRC = Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))
import pytest  # noqa: E402
from agent_runtime import profiles  # noqa: E402

pytestmark = pytest.mark.unit


class ProfileConfigTests(unittest.TestCase):
    """`load` accepts a valid config and refuses everything else, with a reason."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def write(self, data):
        (self.dir / "agent-runtime").mkdir(exist_ok=True)
        path = self.dir / "agent-runtime" / "profiles.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return {"HOME": str(self.dir), "XDG_CONFIG_HOME": str(self.dir)}

    @pytest.mark.spec("Raw hc guard")
    def test_a_missing_config_is_no_profiles(self):
        self.assertEqual(profiles.load({"HOME": str(self.dir)}), {})

    @pytest.mark.spec("Raw hc guard")
    def test_a_valid_config_is_read(self):
        env = self.write({"profiles": {"mini": {"cli": "claude", "env": {"ANTHROPIC_MODEL": "m"}}}})
        self.assertEqual(list(profiles.load(env)), ["mini"])

    @pytest.mark.spec("Raw hc guard")
    def test_a_profile_may_not_shadow_a_builtin(self):
        """`pi` is a built-in family, so no profile may take its name."""
        env = self.write({"profiles": {"pi": {"cli": "claude", "env": {}}}})
        with self.assertRaises(profiles.ProfileError) as error:
            profiles.load(env)
        self.assertIn("built-ins", str(error.exception))

    @pytest.mark.spec("Claude profile settings")
    def test_a_malformed_config_is_refused_without_echoing_it(self):
        env = self.write("{")
        with self.assertRaises(profiles.ProfileError) as error:
            profiles.load(env)
        self.assertNotIn("{", str(error.exception))

    @pytest.mark.spec("Claude profile settings")
    def test_a_reserved_variable_is_refused(self):
        env = self.write({"profiles": {"mini": {"cli": "claude", "env": {"HC_TERMINAL_ID": "t0"}}}})
        with self.assertRaises(profiles.ProfileError) as error:
            profiles.load(env)
        self.assertIn("reserved", str(error.exception))

    @pytest.mark.spec("Claude profile settings")
    def test_a_credential_must_be_a_reference(self):
        """a secret never travels in the config."""
        env = self.write(
            {"profiles": {"mini": {"cli": "claude", "env": {"ANTHROPIC_API_KEY": "sk-live"}}}}
        )
        with self.assertRaises(profiles.ProfileError) as error:
            profiles.load(env)
        self.assertIn("credential reference", str(error.exception))

    @pytest.mark.spec("Claude profile settings")
    def test_launch_options_belong_to_the_claude_family(self):
        env = self.write(
            {"profiles": {"mini": {"cli": "codex", "env": {}, "permission_mode": "auto"}}}
        )
        with self.assertRaises(profiles.ProfileError) as error:
            profiles.load(env)
        self.assertIn("claude family", str(error.exception))


class ScrubTests(unittest.TestCase):
    """The family decides what the worker keeps: a pi worker keeps Pi's own credentials."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.env = {"HOME": str(self.dir), "XDG_CONFIG_HOME": str(self.dir)}
        (self.dir / "agent-runtime").mkdir()
        (self.dir / "agent-runtime" / "profiles.json").write_text(
            json.dumps({"profiles": {"mini": {"cli": "claude", "env": {"PROFILE_MARKER": "set"}}}}),
            encoding="utf-8",
        )

    def scrubbed(self, cli):
        """The variables the spawn removes: the prefix is `env -u NAME...`."""
        prefix = profiles.scrub_prefix(self.env, cli)
        return {prefix[index + 1] for index, arg in enumerate(prefix) if arg == "-u"}

    def test_a_pi_worker_keeps_the_pi_provider_variables(self):
        """a pi worker authenticates with them, so they are not removed."""
        removed = self.scrubbed("pi")
        for name in profiles.PI_PROVIDER_VARIABLES:
            self.assertNotIn(name, removed, name)

    def test_a_claude_or_codex_worker_loses_them(self):
        """a pi worker keeps its profile."""
        for family in ("claude", "codex"):
            removed = self.scrubbed(family)
            for name in profiles.PI_PROVIDER_VARIABLES:
                self.assertIn(name, removed, family)

    @pytest.mark.spec("Raw hc guard")
    def test_every_family_loses_the_anthropic_variables(self):
        for family in ("claude", "codex", "pi"):
            removed = self.scrubbed(family)
            self.assertIn("ANTHROPIC_API_KEY", removed, family)
            self.assertIn("ANTHROPIC_BASE_URL", removed, family)

    @pytest.mark.spec("Raw hc guard")
    def test_a_profile_variable_is_scrubbed_from_a_built_in_spawn(self):
        """a built-in worker inherits no credential of a configured profile."""
        self.assertIn("PROFILE_MARKER", self.scrubbed("claude"))

    def test_the_built_in_names_are_the_families_the_runtime_knows(self):
        """a pi worker keeps its profile."""
        self.assertEqual(profiles.BUILTINS, ("claude", "codex", "pi"))
        self.assertEqual(profiles.cli_names(self.env), ["claude", "codex", "pi", "mini"])

    @pytest.mark.spec("Claude profile settings")
    def test_a_broken_config_still_leaves_the_built_ins(self):
        """the schema never disappears because a config is broken."""
        (self.dir / "agent-runtime" / "profiles.json").write_text("{", encoding="utf-8")
        self.assertEqual(profiles.cli_names(self.env), ["claude", "codex", "pi"])


class ConfigPathTests(unittest.TestCase):
    """Where the config lives, and what an override does."""

    @pytest.mark.spec("Raw hc guard")
    def test_the_default_path_is_under_the_config_home(self):
        path = profiles.config_path({"HOME": "/home/you", "XDG_CONFIG_HOME": "/cfg"})
        self.assertEqual(path, Path("/cfg/agent-runtime/profiles.json"))

    def test_an_override_wins(self):
        path = profiles.config_path(
            {"HOME": "/home/you", "AGENT_RUNTIME_PROFILES": "/elsewhere.json"}
        )
        self.assertEqual(path, Path("/elsewhere.json"))

    def test_a_tilde_override_expands_to_the_home(self):
        path = profiles.config_path({"HOME": "/home/you", "AGENT_RUNTIME_PROFILES": "~/alt.json"})
        self.assertEqual(path, Path("/home/you/alt.json"))


if __name__ == "__main__":
    unittest.main()
