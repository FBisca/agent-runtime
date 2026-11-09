"""The harness adapters: Claude, Codex and Pi are wired by what the installer prints, never by what it reads.

The installer does not write a harness's configuration: it prints the block to add. What it does is
look at the home it runs in, so the preview names what is already there, and refuse to touch it. A
credential is never opened, and a secret never reaches the output. Every test drives the real shell
script against a real home directory with fake tools, so the file is an integration test.
"""

import os
import shutil
import subprocess
import unittest
from pathlib import Path

import pytest
import test_install as shared
import test_release as release

pytestmark = pytest.mark.integration

REPO = shared.REPO
SHIMS = shared.SHIMS
SECRET = "sk-the-user-credential-0123456789"
PI_METHOD = "curl -fsSL https://pi.dev/install.sh | sh"


def text(result):
    return result.stdout + result.stderr


def snapshot(home):
    """What a home holds: kinds, link targets, modes and times, and the bytes of what can be read.

    A credential is unreadable on purpose, so it is compared by its metadata and never opened.
    """
    seen = {}
    for path in sorted(Path(home).rglob("*")):
        relative = str(path.relative_to(home))
        if path.is_symlink():
            seen[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            seen[relative] = ("dir", None)
        else:
            status = path.stat()
            body = path.read_bytes() if os.access(path, os.R_OK) else None
            seen[relative] = ("file", status.st_mode, status.st_size, status.st_mtime_ns, body)
    return seen


class AdapterCase(shared.StreamedInstallCase):
    """A home with the dotfiles and credentials of the three harnesses."""

    CREDENTIALS = (".codex/auth.json", ".pi/agent/auth.json", ".claude/.credentials.json")
    CONFIGS = (".codex/config.toml", ".pi/agent/mcp.json", ".claude/settings.json")

    def seed(self, home=None, configs=None):
        """Write the credentials, which no one may open, and the configs the user keeps."""
        home = home or self.home
        for name in self.CREDENTIALS:
            target = home / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'{{"token": "{SECRET}"}}\n', encoding="utf-8")
            # A file nobody can read is the proof the installer never tried: it would fail on it.
            target.chmod(0)
            self.addCleanup(target.chmod, 0o600)
        for name in configs if configs is not None else self.CONFIGS:
            target = home / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'# the user\'s own\napi_key = "{SECRET}"\n', encoding="utf-8")

    def pi_less_path(self):
        """A PATH with the tools the installer needs and no `pi`: a machine Pi was never put on."""
        bin_dir = self.root / "tools"
        bin_dir.mkdir(exist_ok=True)
        for name in (
            "sh",
            "python3",
            "sed",
            "grep",
            "tr",
            "cat",
            "mktemp",
            "rm",
            "rmdir",
            "mkdir",
            "cp",
            "mv",
            "dirname",
            "head",
            "readlink",
            "kill",
            "ln",
            "date",
            "env",
            "uname",
        ):
            found = shutil.which(name)
            if found and not (bin_dir / name).exists():
                (bin_dir / name).symlink_to(found)
        return str(bin_dir)


class PiPrerequisiteTests(AdapterCase):
    """S9: Pi is a prerequisite the installer reports and never provides."""

    def test_s9_a_missing_pi_is_reported_with_the_official_way_to_install_it(self):
        """S9: the adapter installs, the missing prerequisite is named, and Pi is not installed."""
        calls = self.root / "calls.log"
        fake = self.root / "fake-bin"
        fake.mkdir()
        for name in ("curl", "wget", "npm", "claude", "codex"):
            tool = fake / name
            tool.write_text(f'#!/bin/sh\necho {name} "$@" >> "{calls}"\nexit 1\n', encoding="utf-8")
            tool.chmod(0o755)
        path = f"{fake}:{self.pi_less_path()}"
        result = self.run_installer("--components", "runtime,pi", path=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pi: not found on PATH", result.stdout)
        self.assertIn("https://pi.dev/docs/latest/quickstart", result.stdout)
        self.assertIn(PI_METHOD, result.stdout)
        self.assertIn("this installer does not install or sign in to Pi", result.stdout)
        # Nothing ran: no installer, no package manager, no tool of any harness.
        self.assertFalse(
            calls.exists(), calls.read_text(encoding="utf-8") if calls.exists() else ""
        )
        self.assertFalse((self.home / ".pi").exists())
        # The integration itself is installed: Pi can come after it.
        self.assertIn("pi", self.manifest()["components"])
        self.assertIn("~/.pi/agent/mcp.json", result.stdout)

    def test_s9_a_present_pi_is_found_and_never_run(self):
        """S9: with Pi on the PATH the notice is gone, and Pi is not invoked, not even for its version."""
        path = self.fake_tools(("pi",))
        result = self.run_installer("--components", "runtime,pi", path=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("not found on PATH", result.stdout)
        self.assertNotIn(PI_METHOD, result.stdout)
        self.assertFalse(self.calls.exists())

    def test_s9_the_prerequisite_is_reported_by_a_dry_run_and_by_a_streamed_run(self):
        """S9: the preview says it too, and an adapter that is not selected says nothing about Pi."""
        path = self.pi_less_path()
        dry = self.run_installer("--components", "runtime,pi", "--dry-run", path=path)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertIn("pi: not found on PATH", dry.stdout)
        self.assertIn(PI_METHOD, dry.stdout)
        self.assertEqual(snapshot(self.home), {})

        other = self.run_installer("--components", "runtime,codex", "--dry-run", path=path)
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertNotIn("Pi", other.stdout.replace("print", ""))
        self.assertNotIn(PI_METHOD, other.stdout)

        streamed = self.stream("--components", "pi", "--dry-run", path=path)
        self.assertEqual(streamed.returncode, 0, streamed.stderr)
        self.assertIn(PI_METHOD, streamed.stdout)

    def test_s9_a_pi_that_cannot_be_found_is_a_notice_not_a_refusal(self):
        """S9: the exit code stays 0, so a headless run does not stop on a tool the user adds later."""
        result = self.run_installer("--components", "pi", "--yes", path=self.pi_less_path())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")


class ExistingConfigurationTests(AdapterCase):
    """S4: what is already configured is kept, named in the preview, and never read for its secrets."""

    def test_s4_the_configuration_and_the_credentials_of_the_harnesses_are_left_alone(self):
        """S4: no config is rewritten, no credential is opened, and no secret is printed."""
        self.seed()
        before = snapshot(self.home)
        path = self.fake_tools()
        environment = {"OPENAI_API_KEY": SECRET, "ANTHROPIC_API_KEY": SECRET}
        for arguments in (
            ("--components", "runtime,claude,codex,pi", "--dry-run"),
            ("--components", "runtime,claude,codex,pi", "--yes"),
            ("--components", "runtime,claude,codex,pi"),
        ):
            result = self.run_installer(*arguments, path=path, env=environment)
            self.assertEqual(result.returncode, 0, (arguments, result.stderr))
            self.assertNotIn(SECRET, text(result))
        self.assertFalse(self.calls.exists())
        after = snapshot(self.home)
        for name in self.CREDENTIALS + self.CONFIGS:
            self.assertEqual(after[name], before[name], name)
        # Whatever else appeared is the installer's own: the shims and its manifest.
        extra = set(after) - set(before)
        self.assertTrue(
            all(name == ".local" or name.startswith(".local/") for name in extra), sorted(extra)
        )

    def test_s4_an_existing_config_is_named_and_the_block_to_add_is_still_printed(self):
        """S4: the preview says the file exists and is left as it is, and what to add by hand."""
        self.seed()
        result = self.run_installer("--components", "runtime,claude,codex,pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        for target in ("~/.codex/config.toml", "~/.pi/agent/mcp.json"):
            self.assertIn(f"{target} exists and is left as it is: add this by hand", result.stdout)
        self.assertIn("[mcp_servers.agent-runtime]", result.stdout)
        self.assertIn('"agent-runtime": {', result.stdout)

    def test_s4_a_config_that_already_names_this_install_is_reported_as_wired(self):
        """S4: the second time round the block is not printed again, because it is already there."""
        first = self.run_installer("--components", "runtime,codex,pi")
        self.assertEqual(first.returncode, 0, first.stderr)
        bin_dir = self.home / ".local" / "bin"
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text(
            f'[mcp_servers.agent-runtime]\ncommand = "{bin_dir / "agent-runtime-mcp"}"\n',
            encoding="utf-8",
        )
        pi = self.home / ".pi" / "agent" / "mcp.json"
        pi.parent.mkdir(parents=True)
        pi.write_text(
            '{"mcpServers": {"agent-runtime": {"command": "python3", "args": ["%s"]}}}\n'
            % (REPO / "server" / "mcp_server.py"),
            encoding="utf-8",
        )
        again = self.run_installer("--components", "runtime,codex,pi")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn(
            "~/.codex/config.toml already wires agent-runtime: nothing to add", again.stdout
        )
        self.assertIn(
            "~/.pi/agent/mcp.json already wires agent-runtime: nothing to add", again.stdout
        )
        self.assertNotIn("[mcp_servers.agent-runtime]", again.stdout)
        self.assertNotIn('"mcpServers"', again.stdout)

    def test_s4_an_entry_that_points_somewhere_else_is_a_conflict_and_is_not_rewritten(self):
        """S4: a wired entry for another install is named as a conflict, with the way out, and kept."""
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        original = '[mcp_servers.agent-runtime]\ncommand = "/somewhere/else/agent-runtime-mcp"\n'
        codex.write_text(original, encoding="utf-8")
        result = self.run_installer("--components", "runtime,codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "~/.codex/config.toml has an agent-runtime entry that points elsewhere", result.stdout
        )
        self.assertIn("change its command to", result.stdout)
        self.assertEqual(codex.read_text(encoding="utf-8"), original)

    def test_s4_the_dry_run_previews_the_conflicts_and_changes_nothing(self):
        """S4: the preview names each existing config and the prerequisite, and writes nothing."""
        self.seed()
        before = snapshot(self.home)
        result = self.run_installer("--components", "claude,codex,pi", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("codex: ~/.codex/config.toml exists and is left as it is", result.stdout)
        self.assertIn("pi: ~/.pi/agent/mcp.json exists and is left as it is", result.stdout)
        self.assertIn("claude: ~/.claude/settings.json exists and is left as it is", result.stdout)
        self.assertNotIn(SECRET, text(result))
        self.assertEqual(snapshot(self.home), before)

    def test_s4_the_pi_host_policy_of_the_user_is_kept(self):
        """S4: an existing hosts.json is the user's; a Pi lead is told what to add and nothing is merged."""
        hosts = self.home / ".config" / "agent-runtime" / "hosts.json"
        hosts.parent.mkdir(parents=True)
        original = '{"hosts": {"codex": {"mutations": false}}}\n'
        hosts.write_text(original, encoding="utf-8")
        result = self.run_installer("--components", "pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "~/.config/agent-runtime/hosts.json exists and is left as it is", result.stdout
        )
        self.assertEqual(hosts.read_text(encoding="utf-8"), original)

    def test_s4_a_config_that_cannot_be_read_is_left_as_it_is(self):
        """S4: an unreadable config is no reason to fail, and no reason to guess: it is kept, said so."""
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text("mine\n", encoding="utf-8")
        codex.chmod(0)
        self.addCleanup(codex.chmod, 0o600)
        result = self.run_installer("--components", "codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("~/.codex/config.toml exists and is left as it is", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_s4_the_same_install_twice_prints_the_same_and_changes_nothing(self):
        """S4: idempotent: the second run leaves the filesystem and the output as the first did."""
        self.seed()
        first = self.run_installer("--components", "runtime,claude,codex,pi")
        before = snapshot(self.home)
        second = self.run_installer("--components", "runtime,claude,codex,pi")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        after = snapshot(self.home)
        self.assertEqual(sorted(after), sorted(before))
        for name, entry in after.items():
            if name.startswith(".local/") and entry[0] == "file":
                # The installer's own manifest is rewritten with the same content: the time moves.
                self.assertEqual(entry[:3] + entry[4:], before[name][:3] + before[name][4:], name)
            else:
                self.assertEqual(entry, before[name], name)

    def test_s4_a_shim_of_the_user_is_refused_with_a_preview_that_says_so(self):
        """S4: the dry run refuses the same shim conflict the install would, and names the way out."""
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "agent-runtime-hook").write_text("#!/bin/sh\n", encoding="utf-8")
        for arguments in (("--dry-run",), ()):
            result = self.run_installer("--components", "runtime,pi", *arguments)
            self.assertEqual(result.returncode, 1, arguments)
            self.assertIn("refusing to overwrite", result.stderr)
            self.assertIn("agent-runtime-hook", result.stderr)
        self.assertFalse((bin_dir / "agent-runtime").exists())


class SelectiveLifecycleTests(AdapterCase):
    """S7: an adapter comes and goes without moving the runtime, another adapter, or the user's data."""

    def test_s7_an_adapter_is_uninstalled_alone_and_the_others_and_the_shims_stay(self):
        """S7: each adapter can leave by itself; the runtime and the other adapters are as they were."""
        self.seed()
        installed = self.run_installer("--components", "runtime,orchestrator,claude,codex,pi")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        before = snapshot(self.home)
        for leaving in ("codex", "pi", "claude"):
            result = self.run_installer("uninstall", "--components", leaving)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"uninstalled {leaving}", result.stdout)
            self.assertNotIn(leaving, self.manifest()["components"])
            for shim in SHIMS:
                self.assertTrue(self.shim(shim).is_symlink(), (leaving, shim))
            self.assertIn("runtime", self.manifest()["components"])
            self.assertIn("orchestrator", self.manifest()["components"])
        for name in self.CREDENTIALS + self.CONFIGS:
            self.assertEqual(snapshot(self.home)[name], before[name], name)

    def test_s7_the_shims_of_the_runtime_work_after_install_and_after_an_upgrade(self):
        """S7: the documented shims are the entry points, before and after the upgrade."""
        first = self.run_installer("--components", "runtime,pi")
        self.assertEqual(first.returncode, 0, first.stderr)
        newer = self.checkout("newer", version="0.2.0")
        # The checkout copy carries the shims; the code they run comes from the real tree.
        shutil.copytree(REPO / "server", newer / "server")
        shutil.copytree(
            REPO / "packages" / "agent-runtime" / "src",
            newer / "packages" / "agent-runtime" / "src",
        )
        upgraded = self.run_installer(
            "upgrade", "--components", "runtime,pi", script=newer / "install.sh"
        )
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        for shim in SHIMS:
            link = self.shim(shim)
            self.assertEqual(os.readlink(link), str(newer / "bin" / shim))
            self.assertTrue(os.access(link, os.X_OK), shim)
        shown = subprocess.run(
            [str(self.shim("agent-runtime")), "--help"],
            env={
                "HOME": str(self.home),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(shown.returncode, 0, shown.stderr)

    def test_s7_a_shim_the_user_replaced_since_is_kept_by_the_uninstall(self):
        """S7: the uninstall removes the shims it made and keeps the file the user put in their place."""
        installed = self.run_installer("--components", "runtime")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        mine = self.shim("agent-runtime-profile")
        mine.unlink()
        mine.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        theirs = self.shim("agent-runtime-mcp")
        theirs.unlink()
        elsewhere = self.root / "elsewhere"
        elsewhere.write_text("#!/bin/sh\n", encoding="utf-8")
        theirs.symlink_to(elsewhere)

        removed = self.run_installer("uninstall", "--components", "runtime")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(mine.read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")
        self.assertEqual(os.readlink(theirs), str(elsewhere))
        self.assertIn("kept", removed.stdout)
        for shim in ("agent-runtime", "agent-runtime-hook"):
            self.assertFalse(self.shim(shim).exists(), shim)
        self.assertNotIn("runtime", self.manifest()["components"])

    def test_s7_the_data_of_the_user_survives_an_uninstall_of_everything(self):
        """S7: the runtime's state, the host policy and the harness dotfiles are not the installer's."""
        self.seed()
        data = self.home / ".local" / "share" / "agent-runtime"
        data.mkdir(parents=True)
        (data / "state.db").write_text("the durable store\n", encoding="utf-8")
        hosts = self.home / ".config" / "agent-runtime" / "hosts.json"
        hosts.parent.mkdir(parents=True)
        hosts.write_text('{"hosts": {}}\n', encoding="utf-8")
        profiles = self.home / ".config" / "agent-runtime" / "profiles.json"
        profiles.write_text("{}\n", encoding="utf-8")
        installed = self.run_installer("--components", "runtime,orchestrator,claude,codex,pi")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        before = snapshot(self.home)

        gone = self.run_installer(
            "uninstall", "--components", "runtime,orchestrator,claude,codex,pi"
        )
        self.assertEqual(gone.returncode, 0, gone.stderr)
        after = snapshot(self.home)
        for name in (
            ".local/share/agent-runtime/state.db",
            ".config/agent-runtime/hosts.json",
            ".config/agent-runtime/profiles.json",
            *self.CREDENTIALS,
            *self.CONFIGS,
        ):
            self.assertEqual(after[name], before[name], name)
        for shim in SHIMS:
            self.assertFalse(self.shim(shim).exists(), shim)


class StreamedLifecycleTests(release.ReleaseCase, AdapterCase):
    """S7 for a streamed install: the release it staged is the installer's, and nothing else is."""

    def test_s7_an_uninstall_removes_the_release_it_staged_and_nothing_the_user_kept(self):
        """S7: the staged payload is the installer's own, so it goes with the runtime; user data stays."""
        self.release("0.1.0")
        installed = self.bootstrap("--components", "runtime,pi", "--release", "v0.1.0", "--yes")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        home = self.install_home()
        staged = home / "releases" / "v0.1.0"
        self.assertTrue(staged.is_dir())
        (home / "state.db").write_text("the durable store\n", encoding="utf-8")

        # An adapter leaving does not take the release: the runtime still runs from it.
        partial = self.bootstrap("uninstall", "--components", "pi", "--yes")
        self.assertEqual(partial.returncode, 0, partial.stderr)
        self.assertTrue(staged.is_dir())
        self.assertTrue(self.shim("agent-runtime").is_symlink())

        gone = self.bootstrap("uninstall", "--components", "runtime", "--yes")
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertFalse(staged.exists())
        for shim in SHIMS:
            self.assertFalse(self.shim(shim).exists(), shim)
        self.assertEqual((home / "state.db").read_text(encoding="utf-8"), "the durable store\n")

    def test_s7_an_uninstall_never_removes_a_directory_it_did_not_stage(self):
        """S7: a source the manifest names that is not a staged release is a checkout, and it stays."""
        installed = self.run_installer("--components", "runtime")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        removed = self.run_installer("uninstall", "--components", "runtime")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertTrue((REPO / "bin" / "agent-runtime").is_file())
        self.assertTrue(REPO.is_dir())


if __name__ == "__main__":
    unittest.main()
