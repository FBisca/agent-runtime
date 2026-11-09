"""The installer: the components it installs, the conflicts it refuses, and what it leaves alone.

The installer is a shell script that runs against a real home directory, so every test here
drives a real boundary: a subprocess, a filesystem. The file is an integration test.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "install.sh"
SHELL = shutil.which("sh") or "/bin/sh"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "legacy-install.stdout.txt"
SHIMS = ("agent-runtime", "agent-runtime-hook", "agent-runtime-mcp", "agent-runtime-profile")


def normalise(text, root, home):
    """The installer prints the paths it works with; the snapshot keeps them readable."""
    return text.replace(str(root), "<ROOT>").replace(str(home), "<HOME>")


class InstallerCase(unittest.TestCase):
    """A home directory, a checkout to install from, and the installer itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def run_installer(self, *args, home=None, script=None, env=None, path=None):
        """Run the installer and hand back what it said and what it did."""
        environment = {
            "HOME": str(home or self.home),
            "PATH": path if path is not None else os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
        }
        environment.update(env or {})
        return subprocess.run(
            [SHELL, str(script or INSTALLER), *args],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def manifest(self):
        """The install manifest: what the installer believes it owns."""
        path = self.home / ".local" / "share" / "agent-runtime" / "install-manifest.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def checkout(self, name="checkout", version="0.1.0", orchestrator_version=None):
        """A copy of the checkout the installer can run from, with its own release version."""
        target = self.root / name
        (target / "bin").mkdir(parents=True)
        shutil.copy2(INSTALLER, target / "install.sh")
        for shim in SHIMS:
            shutil.copy2(REPO / "bin" / shim, target / "bin" / shim)
        for member, member_version in (
            ("agent-runtime", version),
            ("agent-orchestrator", orchestrator_version or version),
        ):
            directory = target / "packages" / member
            directory.mkdir(parents=True)
            source = (REPO / "packages" / member / "pyproject.toml").read_text(encoding="utf-8")
            pinned = re.sub(
                r'^version = ".*"$', f'version = "{member_version}"', source, flags=re.MULTILINE
            )
            (directory / "pyproject.toml").write_text(pinned, encoding="utf-8")
        return target

    def shim(self, name):
        return self.home / ".local" / "bin" / name


class LegacyInstallSnapshotTests(InstallerCase):
    """The original behaviour, snapshotted: the component work may not change it."""

    def test_the_legacy_install_still_prints_what_it_printed(self):
        """S1: the original install still prints the Claude and Codex wiring, and nothing else."""
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = GOLDEN.read_text(encoding="utf-8")
        self.assertEqual(normalise(result.stdout, REPO, self.home), expected)
        self.assertEqual(result.stderr, "")

    def test_the_legacy_install_still_links_the_four_shims(self):
        """S7: the documented agent-runtime* shims are still what the legacy install links."""
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        for shim in SHIMS:
            link = self.shim(shim)
            self.assertTrue(link.is_symlink(), shim)
            self.assertEqual(os.readlink(link), str(REPO / "bin" / shim))

    def test_the_legacy_install_still_refuses_a_file_it_does_not_own(self):
        """S4: the original install still refuses a file it does not own."""
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "agent-runtime").write_text("mine\n", encoding="utf-8")
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertFalse((bin_dir / "agent-runtime-hook").exists())


class SelectiveInstallTests(InstallerCase):
    """The components: what the user asks for, what comes with it, and what stays out."""

    def test_s1_only_the_requested_components_are_installed(self):
        """S1: the requested components alone, their closure honoured, no other config touched."""
        result = self.run_installer("--components", "runtime,pi")
        self.assertEqual(result.returncode, 0, result.stderr)

        # The runtime is installed: its four documented shims, and nothing else.
        for shim in SHIMS:
            link = self.shim(shim)
            self.assertTrue(link.is_symlink(), shim)
            self.assertEqual(os.readlink(link), str(REPO / "bin" / shim))
        self.assertFalse((self.home / ".local" / "bin" / "agent-orchestrator").exists())

        # The pi integration is configured; the other harnesses are not.
        self.assertIn("~/.pi/agent/mcp.json", result.stdout)
        self.assertNotIn("~/.codex/config.toml", result.stdout)
        self.assertNotIn("/plugin marketplace add", result.stdout)

        # No unrelated tool config is written.
        for path in (".claude", ".codex", ".pi", ".config"):
            self.assertFalse((self.home / path).exists(), path)

        # The manifest records the requested components and the closure.
        manifest = self.manifest()
        self.assertEqual(sorted(manifest["components"]), ["pi", "runtime"])

    def test_s1_the_runtime_comes_with_every_component_that_needs_it(self):
        """S1: the dependency closure is honoured, and it is said out loud."""
        result = self.run_installer("--components", "pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime: required by pi", result.stdout)
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertEqual(sorted(self.manifest()["components"]), ["pi", "runtime"])

    def test_an_unknown_component_is_refused_with_the_known_ones(self):
        """S1: a name that is not a component is refused, and the known ones are named."""
        result = self.run_installer("--components", "runtime,nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown component: nope", result.stderr)
        self.assertIn("runtime", result.stderr)
        self.assertFalse((self.home / ".local" / "share").exists())


class ReleaseTests(InstallerCase):
    """The release: one version for the whole install, never a mixture."""

    def test_s3_the_orchestrator_needs_the_runtime_and_one_release(self):
        """S3: the runtime requirement is explicit, and both come from one release."""
        result = self.run_installer("--components", "orchestrator")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime: required by orchestrator", result.stdout)

        manifest = self.manifest()
        self.assertEqual(sorted(manifest["components"]), ["orchestrator", "runtime"])
        releases = {entry["release"] for entry in manifest["components"].values()}
        self.assertEqual(releases, {"v0.1.0"})
        self.assertTrue(self.shim("agent-runtime").is_symlink())

    def test_a_checkout_that_mixes_releases_is_refused(self):
        """S3: a checkout whose members disagree is a mixture, and it is refused."""
        mixed = self.checkout("mixed", version="0.1.0", orchestrator_version="0.2.0")
        result = self.run_installer("--components", "orchestrator", script=mixed / "install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to mix releases", result.stderr)
        self.assertIn("v0.1.0", result.stderr)
        self.assertIn("v0.2.0", result.stderr)
        self.assertFalse(self.shim("agent-runtime").exists())

    def test_an_install_that_would_mix_releases_is_refused(self):
        """S3: an install that would leave a component at another release is refused."""
        first = self.run_installer("--components", "claude")
        self.assertEqual(first.returncode, 0, first.stderr)
        newer = self.checkout("newer", version="0.2.0")
        second = self.run_installer("--components", "pi", script=newer / "install.sh")
        self.assertEqual(second.returncode, 1)
        self.assertIn("refusing to mix releases", result_text(second))
        self.assertIn("claude", result_text(second))
        # Nothing moved: the installed set is still the one release.
        manifest = self.manifest()
        self.assertEqual(manifest["components"]["claude"]["release"], "v0.1.0")
        self.assertNotIn("pi", manifest["components"])


def result_text(result):
    """What the installer said, on either stream."""
    return result.stdout + result.stderr


class ConflictTests(InstallerCase):
    """The conflicts: what the installer refuses, and what it never touches."""

    def test_s4_an_existing_file_is_kept_and_the_install_is_refused(self):
        """S4: the user's own files are kept, and the refusal names the reason and the way out."""
        # A file the user owns is never overwritten, and nothing else is installed.
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "agent-runtime").write_text("mine\n", encoding="utf-8")

        result = self.run_installer("--components", "runtime")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertIn("not a symlink", result.stderr)
        self.assertFalse((bin_dir / "agent-runtime-hook").exists())
        self.assertEqual((bin_dir / "agent-runtime").read_text(encoding="utf-8"), "mine\n")

        # A shim of the user's own is refused too, and it is left where it is.
        other = self.root / "other-home"
        other.mkdir()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "agent-runtime-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
        other_bin = other / ".local" / "bin"
        other_bin.mkdir(parents=True)
        (other_bin / "agent-runtime-mcp").symlink_to(elsewhere / "agent-runtime-mcp")

        refused = self.run_installer("--components", "runtime", home=other)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("which this install does not own", refused.stderr)
        self.assertIn("BIN_DIR", refused.stderr)
        self.assertEqual(
            os.readlink(other_bin / "agent-runtime-mcp"), str(elsewhere / "agent-runtime-mcp")
        )

        # An existing credential never reaches the output, and the same install again is the same.
        third = self.root / "third-home"
        third.mkdir()
        codex = third / ".codex"
        codex.mkdir()
        secret = "the-user-api-key"
        config = codex / "config.toml"
        config.write_text(f'anthropic_api_key = "{secret}"\n', encoding="utf-8")

        first = self.run_installer("--components", "runtime,codex", home=third)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertNotIn(secret, result_text(first))
        self.assertEqual(config.read_text(encoding="utf-8"), f'anthropic_api_key = "{secret}"\n')

        again = self.run_installer("--components", "runtime,codex", home=third)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout, first.stdout)
        self.assertNotIn(secret, result_text(again))


class LifecycleTests(InstallerCase):
    """The lifecycle: a rerun, an upgrade, and a selective uninstall."""

    def test_s7_a_rerun_an_upgrade_and_an_uninstall_keep_the_rest(self):
        """S7: the unrelated components and the user's data survive, and the shims stay usable."""
        first = self.run_installer("--components", "runtime,pi,claude")
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self.manifest()

        # A rerun is idempotent: the same install, the same result.
        again = self.run_installer("--components", "runtime,pi,claude")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.manifest(), before)

        # An upgrade moves the whole installed set to the new release.
        newer = self.checkout("newer", version="0.2.0")
        upgraded = self.run_installer(
            "upgrade", "--components", "runtime,pi,claude", script=newer / "install.sh"
        )
        self.assertEqual(upgraded.returncode, 0, upgraded.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest["components"]["runtime"]["release"], "v0.2.0")
        self.assertEqual(manifest["components"]["pi"]["release"], "v0.2.0")
        self.assertEqual(manifest["components"]["claude"]["release"], "v0.2.0")
        # The documented shims still work: they point at the checkout that is installed now.
        for shim in SHIMS:
            link = self.shim(shim)
            self.assertTrue(link.is_symlink(), shim)
            self.assertEqual(os.readlink(link), str(newer / "bin" / shim))

        # A selective uninstall removes what it owns, and nothing else.
        user_file = self.home / ".codex"
        user_file.mkdir()
        (user_file / "config.toml").write_text("mine\n", encoding="utf-8")
        removed = self.run_installer("uninstall", "--components", "pi")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertNotIn("pi", self.manifest()["components"])
        self.assertIn("runtime", self.manifest()["components"])
        self.assertIn("claude", self.manifest()["components"])
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertEqual((user_file / "config.toml").read_text(encoding="utf-8"), "mine\n")

        # Uninstalling the runtime takes the shims it linked, and leaves the rest.
        gone = self.run_installer("uninstall", "--components", "runtime")
        self.assertEqual(gone.returncode, 0, gone.stderr)
        for shim in SHIMS:
            self.assertFalse(self.shim(shim).exists(), shim)
        self.assertIn("claude", self.manifest()["components"])
        self.assertEqual((user_file / "config.toml").read_text(encoding="utf-8"), "mine\n")

    def test_an_upgrade_that_would_leave_a_component_behind_is_refused(self):
        """S7: an upgrade that would strand a component at another release is refused."""
        installed = self.run_installer("--components", "runtime,pi")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        newer = self.checkout("newer", version="0.2.0")
        refused = self.run_installer(
            "upgrade", "--components", "runtime", script=newer / "install.sh"
        )
        self.assertEqual(refused.returncode, 1)
        self.assertIn("refusing to mix releases", refused.stderr)
        self.assertIn("pi", refused.stderr)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.1.0")
        self.assertEqual(self.manifest()["components"]["pi"]["release"], "v0.1.0")


class EnvironmentTests(InstallerCase):
    """The environment: a clean home, an existing home, and no network, no credentials."""

    def test_s8_the_installer_needs_no_network_no_credentials_and_no_real_tools(self):
        """S8: fake binaries stand in for the tools; nothing reaches the network."""
        calls = self.root / "calls.log"
        fake = self.root / "fake-bin"
        fake.mkdir()
        for name in ("pi", "claude", "codex"):
            tool = fake / name
            tool.write_text(f'#!/bin/sh\necho {name} >> "$FAKE_CALLS"\n', encoding="utf-8")
            tool.chmod(0o755)
        for name in ("curl", "wget"):
            tool = fake / name
            tool.write_text(f'#!/bin/sh\necho {name} >> "$FAKE_CALLS"\nexit 1\n', encoding="utf-8")
            tool.chmod(0o755)
        path = f"{fake}:{os.environ.get('PATH', '/usr/bin:/bin')}"

        # A clean home: the install works, and touches neither the network nor a real tool.
        clean = self.run_installer(
            "--components", "runtime,pi", env={"FAKE_CALLS": str(calls)}, path=path
        )
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertFalse(calls.exists())
        self.assertTrue(self.shim("agent-runtime").is_symlink())
        # Only the components that were asked for are configured.
        self.assertIn("~/.pi/agent/mcp.json", clean.stdout)
        self.assertNotIn("~/.codex/config.toml", clean.stdout)

        # An existing home with dotfiles: they are left exactly as they were.
        for name in (".claude/settings.json", ".codex/config.toml", ".pi/agent/mcp.json"):
            path_to_write = self.home / name
            path_to_write.parent.mkdir(parents=True, exist_ok=True)
            path_to_write.write_text("mine\n", encoding="utf-8")
        existing = self.run_installer(
            "--components", "runtime,pi", env={"FAKE_CALLS": str(calls)}, path=path
        )
        self.assertEqual(existing.returncode, 0, existing.stderr)
        self.assertFalse(calls.exists())
        for name in (".claude/settings.json", ".codex/config.toml", ".pi/agent/mcp.json"):
            self.assertEqual((self.home / name).read_text(encoding="utf-8"), "mine\n")

    def test_s8_the_failure_paths_are_refused_with_a_reason(self):
        """S8: every failure path names the reason and the way out."""
        unknown = self.run_installer("--components", "runtime,nope")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("unknown component: nope", unknown.stderr)

        empty = self.run_installer("--components", "runtime,,pi")
        self.assertEqual(empty.returncode, 2)
        self.assertIn("empty component name", empty.stderr)

        empty_list = self.run_installer("--components", "")
        self.assertEqual(empty_list.returncode, 2)
        self.assertIn("--components needs at least one component", empty_list.stderr)

        argument = self.run_installer("--wat")
        self.assertEqual(argument.returncode, 2)
        self.assertIn("unknown argument: --wat", argument.stderr)

        missing_value = self.run_installer("--components")
        self.assertEqual(missing_value.returncode, 2)
        self.assertIn("--components needs a list", missing_value.stderr)

        # Without python3 the installer says so instead of failing halfway.
        without_python = self.root / "no-python"
        without_python.mkdir()
        no_python3 = self.run_installer("--components", "runtime", path=str(without_python))
        self.assertEqual(no_python3.returncode, 1)
        self.assertIn("python3", no_python3.stderr)

        # Without a home there is nowhere to install into.
        no_home = self.run_installer("--components", "runtime", env={"HOME": ""})
        self.assertEqual(no_home.returncode, 1)
        self.assertIn("HOME", no_home.stderr)

    def test_s8_shellcheck_is_happy_with_the_installer(self):
        """S8: the installer is shellcheck clean."""
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck is not installed")
        result = subprocess.run(
            ["shellcheck", str(INSTALLER)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
