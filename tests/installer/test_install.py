"""The installer: the components it installs, the conflicts it refuses, and what it leaves alone.

The installer is a shell script that runs against a real home directory, so every test here
drives a real boundary: a subprocess, a filesystem. The file is an integration test.
"""

import json
import os
import pty
import re
import select
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "install.sh"
SHELL = shutil.which("sh") or "/bin/sh"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "legacy-install.stdout.txt"
PROMPTS = (b"empty to quit): ", b"[y/N] ")
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
        """installer-contract/R1: the original install still prints the Claude and Codex wiring, and nothing else."""
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = GOLDEN.read_text(encoding="utf-8")
        self.assertEqual(normalise(result.stdout, REPO, self.home), expected)
        self.assertEqual(result.stderr, "")

    def test_the_legacy_install_still_links_the_four_shims(self):
        """installer-contract/R7: the documented agent-runtime* shims are still what the legacy install links."""
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stderr)
        for shim in SHIMS:
            link = self.shim(shim)
            self.assertTrue(link.is_symlink(), shim)
            self.assertEqual(os.readlink(link), str(REPO / "bin" / shim))

    def test_the_legacy_install_still_refuses_a_file_it_does_not_own(self):
        """installer-contract/R4: the original install still refuses a file it does not own."""
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
        """installer-contract/R1: the requested components alone, their closure honoured, no other config touched."""
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
        """installer-contract/R1: the dependency closure is honoured, and it is said out loud."""
        result = self.run_installer("--components", "pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime: required by pi", result.stdout)
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertEqual(sorted(self.manifest()["components"]), ["pi", "runtime"])

    def test_an_unknown_component_is_refused_with_the_known_ones(self):
        """installer-contract/R1: a name that is not a component is refused, and the known ones are named."""
        result = self.run_installer("--components", "runtime,nope")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown component: nope", result.stderr)
        self.assertIn("runtime", result.stderr)
        self.assertFalse((self.home / ".local" / "share").exists())


class ReleaseTests(InstallerCase):
    """The release: one version for the whole install, never a mixture."""

    def test_s3_the_orchestrator_needs_the_runtime_and_one_release(self):
        """installer-contract/R3: the runtime requirement is explicit, and both come from one release."""
        result = self.run_installer("--components", "orchestrator")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime: required by orchestrator", result.stdout)

        manifest = self.manifest()
        self.assertEqual(sorted(manifest["components"]), ["orchestrator", "runtime"])
        releases = {entry["release"] for entry in manifest["components"].values()}
        self.assertEqual(releases, {"v0.1.0"})
        self.assertTrue(self.shim("agent-runtime").is_symlink())

    def test_a_checkout_that_mixes_releases_is_refused(self):
        """installer-contract/R3: a checkout whose members disagree is a mixture, and it is refused."""
        mixed = self.checkout("mixed", version="0.1.0", orchestrator_version="0.2.0")
        result = self.run_installer("--components", "orchestrator", script=mixed / "install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to mix releases", result.stderr)
        self.assertIn("v0.1.0", result.stderr)
        self.assertIn("v0.2.0", result.stderr)
        self.assertFalse(self.shim("agent-runtime").exists())

    def test_an_install_that_would_mix_releases_is_refused(self):
        """installer-contract/R3: an install that would leave a component at another release is refused."""
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
        """installer-contract/R4: the user's own files are kept, and the refusal names the reason and the way out."""
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
        """installer-contract/R7: the unrelated components and the user's data survive, and the shims stay usable."""
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

        # Uninstalling the runtime while a component needs it is refused, with the way out.
        refused = self.run_installer("uninstall", "--components", "runtime")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("refusing to uninstall runtime", refused.stderr)
        self.assertIn("needed by claude", refused.stderr)
        self.assertIn("--components runtime, claude", refused.stderr)
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertIn("claude", self.manifest()["components"])

        # Uninstalling them together takes the shims the runtime linked, and leaves the rest.
        gone = self.run_installer("uninstall", "--components", "runtime,claude")
        self.assertEqual(gone.returncode, 0, gone.stderr)
        for shim in SHIMS:
            self.assertFalse(self.shim(shim).exists(), shim)
        self.assertNotIn("claude", self.manifest()["components"])
        self.assertEqual((user_file / "config.toml").read_text(encoding="utf-8"), "mine\n")

    def test_s7_a_component_is_not_orphaned_by_the_uninstall_of_what_it_needs(self):
        """installer-contract/R7: the runtime stays while a component needs it, and the way out is named."""
        installed = self.run_installer("--components", "runtime,orchestrator,pi")
        self.assertEqual(installed.returncode, 0, installed.stderr)

        refused = self.run_installer("uninstall", "--components", "runtime")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("refusing to uninstall runtime", refused.stderr)
        self.assertIn("needed by orchestrator, pi", refused.stderr)
        self.assertIn("--components runtime, orchestrator, pi", refused.stderr)
        # Nothing moved: the runtime and its dependents are still installed.
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertEqual(sorted(self.manifest()["components"]), ["orchestrator", "pi", "runtime"])

        # Naming the dependents in the same call removes the whole set.
        gone = self.run_installer("uninstall", "--components", "runtime,orchestrator,pi")
        self.assertEqual(gone.returncode, 0, gone.stderr)
        for shim in SHIMS:
            self.assertFalse(self.shim(shim).exists(), shim)
        self.assertEqual(self.manifest()["components"], {})

    def test_an_upgrade_that_would_leave_a_component_behind_is_refused(self):
        """installer-contract/R7: an upgrade that would strand a component at another release is refused."""
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
        """installer-contract/R8: fake binaries stand in for the tools; nothing reaches the network."""
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
        """installer-contract/R8: every failure path names the reason and the way out."""
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
        """installer-contract/R8: the installer is shellcheck clean."""
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck is not installed")
        result = subprocess.run(
            ["shellcheck", str(INSTALLER)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


def snapshot(home):
    """Everything under a home directory: what it is, where a link points, and what a file holds."""
    seen = {}
    for path in sorted(Path(home).rglob("*")):
        relative = str(path.relative_to(home))
        if path.is_symlink():
            seen[relative] = ("link", os.readlink(path))
        elif path.is_dir():
            seen[relative] = ("dir", None)
        else:
            seen[relative] = ("file", path.read_bytes(), path.stat().st_mtime_ns)
    return seen


class StreamedInstallCase(InstallerCase):
    """A bootstrap streamed through a shell: the script is stdin, and stdin is never an answer."""

    def fake_tools(self, names=("curl", "wget", "pi", "claude", "codex")):
        """Stand-ins for the network and the provider tools; any call is logged, none is real."""
        self.calls = self.root / "calls.log"
        fake = self.root / "fake-bin"
        fake.mkdir(exist_ok=True)
        for name in names:
            tool = fake / name
            tool.write_text(f'#!/bin/sh\necho {name} >> "{self.calls}"\nexit 1\n', encoding="utf-8")
            tool.chmod(0o755)
        return f"{fake}:{os.environ.get('PATH', '/usr/bin:/bin')}"

    def stream(self, *args, answers=None, env=None, path=None, script=None):
        """Pipe the installer into `sh -s`, with a terminal that is answered line by line, or none.

        The terminal is a pseudo-terminal, and an answer is typed only once the prompt it answers
        has been printed: input written ahead of the open is dropped by some terminals.
        """
        environment = {
            "HOME": str(self.home),
            "PATH": path if path is not None else os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "AGENT_RUNTIME_TTY": str(self.root / "no-such-terminal"),
        }
        master = slave = None
        if answers is not None:
            master, slave = pty.openpty()
            environment["AGENT_RUNTIME_TTY"] = os.ttyname(slave)
        environment.update(env or {})
        terminal = b""
        try:
            process = subprocess.Popen(
                [SHELL, "-s", "--", *args],
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                cwd=self.root,
            )
            collected = {}
            worker = threading.Thread(
                target=lambda: collected.update(
                    zip(("out", "err"), process.communicate(Path(script or INSTALLER).read_bytes()))
                )
            )
            worker.start()
            if master is not None:
                deadline = time.monotonic() + 15
                typed = 0
                while worker.is_alive() and time.monotonic() < deadline:
                    if select.select([master], [], [], 0.05)[0]:
                        terminal += os.read(master, 4096)
                    prompts = sum(terminal.count(mark) for mark in PROMPTS)
                    if typed < len(answers) and prompts > typed:
                        os.write(master, (answers[typed] + "\n").encode())
                        typed += 1
                while select.select([master], [], [], 0.2)[0]:
                    chunk = os.read(master, 4096)
                    if not chunk:
                        break
                    terminal += chunk
            worker.join(timeout=15)
            if worker.is_alive():
                process.kill()
                worker.join()
        finally:
            for descriptor in (master, slave):
                if descriptor is not None:
                    os.close(descriptor)
        result = subprocess.CompletedProcess(
            process.args, process.returncode, collected["out"].decode(), collected["err"].decode()
        )
        result.terminal = terminal.decode(errors="replace")
        return result


class ComponentMenuTests(StreamedInstallCase):
    """The streamed bootstrap: a menu on the terminal, or an explicit component list."""

    def test_s2_without_a_terminal_a_streamed_bootstrap_needs_components(self):
        """installer-contract/R2: no terminal, no list: the bootstrap exits with usage, and never reads stdin."""
        before = snapshot(self.home)
        for arguments in ((), ("--yes",), ("--dry-run",)):
            result = self.stream(*arguments)
            self.assertEqual(result.returncode, 2, (arguments, result.stderr))
            self.assertIn("--components", result.stderr)
            self.assertIn("sh -s -- --components runtime,pi --yes", result.stderr)
            self.assertIn("usage: install.sh", result.stderr)
            self.assertEqual(result.stdout, "")
        self.assertEqual(snapshot(self.home), before)

    def test_s2_the_default_terminal_is_dev_tty(self):
        """installer-contract/R2: with no controlling terminal /dev/tty does not open, and the answer is the usage."""
        environment = {"HOME": str(self.home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        result = subprocess.run(
            [SHELL, "-s", "--"],
            input=INSTALLER.read_bytes(),
            env=environment,
            capture_output=True,
            check=False,
            start_new_session=True,
            cwd=self.root,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(b"--components", result.stderr)

    def test_s2_with_a_terminal_the_menu_is_asked_there_and_not_on_stdin(self):
        """installer-contract/R2: the menu and its answer are on the terminal; the piped script bytes are no answer."""
        result = self.stream("--dry-run", answers=["pi"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1) runtime", result.terminal)
        self.assertIn("5) pi", result.terminal)
        # The answer picked pi, and the plan says what comes with it.
        self.assertIn("components: runtime, pi", result.stdout)
        self.assertIn("runtime: required by pi", result.stdout)
        self.assertEqual(snapshot(self.home), {})

    def test_s2_the_menu_takes_numbers_and_all(self):
        """installer-contract/R2: numbers and `all` name components too, and the closure is still resolved."""
        numbers = self.stream("--dry-run", answers=["2, 5"])
        self.assertEqual(numbers.returncode, 0, numbers.stderr)
        self.assertIn("components: runtime, orchestrator, pi", numbers.stdout)
        everything = self.stream("--dry-run", answers=["all"])
        self.assertEqual(everything.returncode, 0, everything.stderr)
        self.assertIn("components: runtime, orchestrator, claude, codex, pi", everything.stdout)

    def test_s2_the_menu_can_be_declined_and_a_bad_answer_is_refused(self):
        """installer-contract/R2: quitting changes nothing, and an answer that is no component is a usage error."""
        quit_ = self.stream("--dry-run", answers=["q"])
        self.assertEqual(quit_.returncode, 1)
        self.assertIn("nothing was changed", quit_.stderr)
        bad = self.stream("--dry-run", answers=["nope"])
        self.assertEqual(bad.returncode, 2)
        self.assertIn("unknown component: nope", bad.stderr)
        empty = self.stream("--dry-run", answers=[""])
        self.assertEqual(empty.returncode, 1)
        self.assertEqual(snapshot(self.home), {})

    def test_s2_an_explicit_list_never_asks_even_with_a_terminal(self):
        """installer-contract/R2: --components is the answer: the terminal is left alone."""
        result = self.stream("--components", "runtime,pi", "--dry-run", answers=["all"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("components: runtime, pi", result.stdout)
        self.assertNotIn("1) runtime", result.terminal)

    def test_s2_a_streamed_bootstrap_without_a_release_is_refused_as_a_usage_error(self):
        """installer-contract/R2: a stream names no checkout and no release: the way out is to pin one."""
        result = self.stream("--components", "runtime", "--yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("names no release", result.stderr)
        self.assertIn("--release vX.Y.Z", result.stderr)
        self.assertEqual(snapshot(self.home), {})

    def test_s2_a_local_checkout_run_without_components_never_asks(self):
        """installer-contract/R2: the original run keeps its output even when a terminal is there to ask on."""
        master, slave = pty.openpty()
        try:
            os.write(master, b"pi\n")
            result = self.run_installer(env={"AGENT_RUNTIME_TTY": os.ttyname(slave)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                normalise(result.stdout, REPO, self.home), GOLDEN.read_text(encoding="utf-8")
            )
            ready = select.select([master], [], [], 0.2)[0]
            asked = os.read(master, 4096).decode(errors="replace") if ready else ""
            self.assertNotIn("1) runtime", asked)
        finally:
            os.close(master)
            os.close(slave)

    def test_s2_what_the_menu_chose_is_confirmed_unless_yes_is_given(self):
        """installer-contract/R2: the menu path asks before it acts; declining changes nothing, --yes skips the ask."""
        installed = self.run_installer("--components", "runtime,pi")
        self.assertEqual(installed.returncode, 0, installed.stderr)

        declined = self.stream("uninstall", answers=["pi", "n"])
        self.assertEqual(declined.returncode, 1)
        self.assertIn("Proceed? [y/N]", declined.terminal)
        self.assertIn("declined: nothing was changed", declined.stderr)
        self.assertIn("pi", self.manifest()["components"])

        confirmed = self.stream("uninstall", answers=["pi", "y"])
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertNotIn("pi", self.manifest()["components"])

        self.run_installer("--components", "pi")
        skipped = self.stream("uninstall", "--yes", answers=["pi"])
        self.assertEqual(skipped.returncode, 0, skipped.stderr)
        self.assertNotIn("Proceed?", skipped.terminal)
        self.assertNotIn("pi", self.manifest()["components"])

    def test_s1_yes_is_accepted_on_the_headless_path(self):
        """installer-contract/R1: --yes names the headless run; the result is the same install."""
        result = self.run_installer("--yes", "--components", "runtime,pi")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(self.manifest()["components"]), ["pi", "runtime"])
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)


class DryRunTests(StreamedInstallCase):
    """--dry-run: the plan, and not one change."""

    def test_s5_a_dry_run_prints_the_plan_and_changes_nothing(self):
        """installer-contract/R5: actions, dependencies, versions and paths are printed; the home is untouched."""
        path = self.fake_tools()
        before = snapshot(self.home)
        result = self.run_installer("--components", "pi", "--dry-run", path=path)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("dry run", out)
        self.assertIn("components: runtime, pi", out)
        self.assertIn("runtime: required by pi", out)
        self.assertIn("release: v0.1.0", out)
        self.assertIn("runtime 0.1.0", out)
        bin_dir = self.home / ".local" / "bin"
        manifest = self.home / ".local" / "share" / "agent-runtime" / "install-manifest.json"
        for shim in SHIMS:
            self.assertIn(f"link {bin_dir / shim} -> {REPO / 'bin' / shim}", out)
        self.assertIn(str(manifest), out)
        self.assertIn(str(REPO), out)
        self.assertIn("~/.pi/agent/mcp.json", out)
        self.assertNotIn("~/.codex/config.toml", out)
        # Nothing changed, and nothing outside was run: no network, no provider.
        self.assertEqual(snapshot(self.home), before)
        self.assertFalse(self.calls.exists())

    def test_s5_a_dry_run_leaves_an_existing_home_and_manifest_as_they_were(self):
        """installer-contract/R5: an existing install and the user's dotfiles are exactly as they were."""
        installed = self.run_installer("--components", "runtime,claude")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        for name in (".claude/settings.json", ".codex/config.toml", ".pi/agent/mcp.json"):
            target = self.home / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("mine\n", encoding="utf-8")
        before = snapshot(self.home)
        for arguments in (
            ("--components", "runtime,codex,pi", "--dry-run"),
            ("upgrade", "--components", "runtime,claude", "--dry-run"),
            ("uninstall", "--components", "runtime,claude", "--dry-run"),
            ("--dry-run",),
        ):
            result = self.run_installer(*arguments)
            self.assertEqual(result.returncode, 0, (arguments, result.stderr))
            self.assertIn("dry run", result.stdout)
        self.assertEqual(snapshot(self.home), before)

    def test_s5_a_dry_run_uninstall_says_what_it_would_remove(self):
        """installer-contract/R5: the uninstall plan names the recorded files, and removes none of them."""
        self.run_installer("--components", "runtime")
        result = self.run_installer("uninstall", "--components", "runtime", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for shim in SHIMS:
            self.assertIn(f"would remove {self.shim(shim)}", result.stdout)
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertIn("runtime", self.manifest()["components"])

    def test_s5_a_dry_run_refuses_what_the_install_would_refuse(self):
        """installer-contract/R5: the preview is truthful: a conflict is refused with its reason, and nothing moves."""
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "agent-runtime").write_text("mine\n", encoding="utf-8")
        before = snapshot(self.home)
        result = self.run_installer("--components", "runtime", "--dry-run")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(snapshot(self.home), before)
        unknown = self.run_installer("--components", "runtime,nope", "--dry-run")
        self.assertEqual(unknown.returncode, 2)
        self.assertEqual(snapshot(self.home), before)

    def test_s5_a_streamed_dry_run_has_no_release_yet_and_says_so(self):
        """installer-contract/R5: a stream carries no payload, so its plan names the release as still to be pinned."""
        result = self.stream("--components", "runtime,orchestrator", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("components: runtime, orchestrator", result.stdout)
        self.assertIn("release: unresolved", result.stdout)
        # No checkout, so no link target of its own: never a made-up system path.
        self.assertNotIn("-> /bin/", result.stdout)
        self.assertIn("-> the payload, pinned by the release", result.stdout)
        self.assertEqual(snapshot(self.home), {})


class MatrixTests(StreamedInstallCase):
    """The environments: a clean home, a home with dotfiles, and CI without a terminal."""

    def homes(self):
        clean = self.root / "clean home"
        clean.mkdir()
        existing = self.root / "existing"
        existing.mkdir()
        for name in (".claude/settings.json", ".codex/config.toml", ".pi/agent/mcp.json", ".zshrc"):
            target = existing / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export KEY=the-user-key\n", encoding="utf-8")
        return {"clean": clean, "existing": existing}

    def test_s8_every_environment_runs_without_network_credentials_or_a_terminal(self):
        """installer-contract/R8: clean and existing homes, no terminal, fake tools: install, dry run, usage error."""
        path = self.fake_tools()
        for kind, home in self.homes().items():
            before = snapshot(home)
            dotfiles = {k: v for k, v in before.items() if v[0] == "file"}
            for arguments in (
                ("--components", "runtime,pi", "--dry-run"),
                ("--components", "runtime,pi", "--yes"),
                ("--components", "runtime,pi"),
                ("--components", "orchestrator,claude,codex", "--yes"),
            ):
                result = self.run_installer(*arguments, home=home, path=path)
                self.assertEqual(result.returncode, 0, (kind, arguments, result.stderr))
                self.assertNotIn("the-user-key", result_text(result))
            for name, entry in dotfiles.items():
                self.assertEqual((home / name).read_bytes(), entry[1], (kind, name))
            self.assertFalse(self.calls.exists(), kind)
            self.assertTrue((home / ".local" / "bin" / "agent-runtime").is_symlink(), kind)

    def test_s8_the_failure_paths_of_the_menu_and_the_flags_name_the_way_out(self):
        """installer-contract/R8: a stream with no terminal, a hostile list, and a flag without its value."""
        no_terminal = self.stream("--yes")
        self.assertEqual(no_terminal.returncode, 2)
        self.assertIn("--components", no_terminal.stderr)

        marker = self.root / "injected"
        for hostile in (f"runtime;touch {marker}", "runtime pi", "$(touch x)", "runtime|pi"):
            result = self.run_installer("--components", hostile, "--dry-run")
            self.assertEqual(result.returncode, 2, hostile)
            self.assertIn("unknown component", result.stderr)
        self.assertFalse(marker.exists())

        for arguments in (("--dry-run", "--components"), ("--yes", "--components=")):
            result = self.run_installer(*arguments)
            self.assertEqual(result.returncode, 2, arguments)
            self.assertIn("--components needs", result.stderr)

        upgrade = self.stream("upgrade", "--yes")
        self.assertEqual(upgrade.returncode, 2)
        self.assertIn("--components", upgrade.stderr)
