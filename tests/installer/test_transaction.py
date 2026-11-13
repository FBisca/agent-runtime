"""The install is transactional: it is activated whole, or not at all.

T3's tests cover the download: a payload that fails verification never runs. These cover what comes
after it: the shims are swapped in and the manifest is recorded, and a failure or a signal part way
through restores the prior installation exactly. A fake `python3` (an external binary the installer
calls) is the injected fault: it fails, or signals the installer, on the step a test names. Every test
runs against a local fixture: no network and no credentials. The file is an integration test: it
drives a shell, subprocesses and the filesystem.
"""

import os
import shutil
import signal
import unittest

import pytest
import test_install as shared
import test_release as release

pytestmark = pytest.mark.integration

SHIMS = shared.SHIMS
RELEASE = "agent-runtime-v0.1.0.tar.gz"

# A stand-in for python3 that is the real one until a step is named. A step is `add:<component>` for
# the manifest or `swap:<shim>` for a link; the mode says what happens there: `fail` exits 1, `term`
# signals the installer shell that called it, `term-bootstrap` signals the bootstrap above that one.
FAKE_PYTHON = """#!/bin/sh
real="$FAKE_REAL_PYTHON"
step=
case $2 in
  swap) step="swap:${4##*/}" ;;
  *) step="$3:$4" ;;
esac
# A second signal, on the first link the rollback restores: it arrives while the rollback runs.
if [ -n "${FAKE_PYTHON_AGAIN:-}" ] && [ -f "$FAKE_MARK" ] && [ ! -f "$FAKE_MARK.again" ] && [ "$2" = swap ]; then
  : > "$FAKE_MARK.again"
  kill -TERM "$PPID"
fi
if [ -n "${FAKE_PYTHON_STEP:-}" ] && [ "$step" = "$FAKE_PYTHON_STEP" ]; then
  echo "python3 $step" >> "$FAKE_CALLS"
  : > "$FAKE_MARK"
  case ${FAKE_PYTHON_MODE:-fail} in
    fail) exit 1 ;;
    term) kill -TERM "$PPID"; exit 1 ;;
    term-bootstrap)
      # Never the test runner: with no bootstrap above the installer, the signal is not sent.
      above=$(ps -o ppid= -p "$PPID" | tr -d ' ')
      [ "$above" = "$FAKE_RUNNER_PID" ] || kill -TERM "$above"
      exit 1 ;;
  esac
fi
exec "$real" "$@"
"""


class TransactionCase(release.ReleaseCase):
    """A prior install, a release to move to, and a python3 that can be made to fail."""

    def setUp(self):
        super().setUp()
        real = shutil.which("python3")
        assert real is not None
        python = self.root / "fake-bin" / "python3"
        python.write_text(FAKE_PYTHON, encoding="utf-8")
        python.chmod(0o755)
        # Nothing may reach for the network or a provider while an install is rolled back.
        for name in ("wget", "pi", "claude", "codex"):
            tool = self.root / "fake-bin" / name
            tool.write_text(f'#!/bin/sh\necho {name} >> "$FAKE_CALLS"\nexit 1\n', encoding="utf-8")
            tool.chmod(0o755)
        self.environment["FAKE_REAL_PYTHON"] = real
        self.environment["FAKE_RUNNER_PID"] = str(os.getpid())
        self.environment["FAKE_MARK"] = str(self.root / "fault-fired")

    def fault(self, step, mode="fail"):
        return {"FAKE_PYTHON_STEP": step, "FAKE_PYTHON_MODE": mode}

    def install_prior(self, home=None, components="runtime,orchestrator"):
        """The prior version, installed from a local checkout: it has to stay usable."""
        prior = self.checkout("prior", version="0.0.9")
        result = self.run_installer(
            "--components",
            components,
            home=home,
            script=prior / "install.sh",
            env={"AGENT_RUNTIME_RELEASE": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return prior

    def move_to_release(self, *extra, components="runtime,orchestrator", env=None, home=None):
        environment = dict(self.environment)
        environment.update(env or {})
        if home is not None:
            environment["HOME"] = str(home)
        return self.stream(
            "--components",
            components,
            "--yes",
            "--release",
            "v0.1.0",
            *extra,
            path=self.path,
            env=environment,
        )

    def assert_prior_usable(self, prior, home=None):
        """The prior install is exactly what it was: its shims run from the prior checkout."""
        home = home or self.home
        for shim in SHIMS:
            link = home / ".local" / "bin" / shim
            self.assertTrue(link.is_symlink(), shim)
            self.assertEqual(os.readlink(link), str(prior / "bin" / shim), shim)
            self.assertTrue(link.resolve().is_file(), shim)

    def assert_no_network_or_provider(self):
        calls = self.curl_calls()
        self.assertEqual([c for c in calls if c.startswith(("wget", "pi", "claude", "codex"))], [])


class ActivationRollbackTests(TransactionCase):
    """A failure after the payload verified: the activation is undone, whole."""

    def test_s6_a_failure_while_activating_restores_the_prior_version(self):
        """installer-contract/R6: the second component fails to record: shims, manifest and staging are as they were."""
        prior = self.install_prior()
        self.release("0.1.0")
        before = shared.snapshot(self.home)

        result = self.move_to_release(env=self.fault("add:orchestrator"))

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.0.9")
        self.assertEqual(self.manifest()["components"]["orchestrator"]["release"], "v0.0.9")
        self.assertIn("previous installation is as it was", result.stderr)

    def test_s6_a_failure_while_linking_leaves_no_mixture_of_old_and_new_shims(self):
        """installer-contract/R6: the third shim cannot be swapped: the two already swapped go back to the prior ones."""
        prior = self.install_prior()
        self.release("0.1.0")
        before = shared.snapshot(self.home)

        result = self.move_to_release(env=self.fault("swap:agent-runtime-mcp"))

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_failed_first_install_leaves_a_clean_home(self):
        """installer-contract/R6: nothing was installed before: nothing is left, not even the directories made for it."""
        self.release("0.1.0")
        result = self.move_to_release(env=self.fault("add:runtime"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.clean(result)

    def test_s6_a_failure_after_the_shims_were_linked_leaves_a_clean_home(self):
        """installer-contract/R6: the shims go in before the manifest: when the manifest fails they go out again."""
        self.release("0.1.0")
        result = self.move_to_release(components="runtime", env=self.fault("add:runtime"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.clean(result)

    def test_s6_a_signal_while_activating_restores_the_prior_version(self):
        """installer-contract/R6: the installer is killed after the shims moved: the prior version is back."""
        prior = self.install_prior()
        self.release("0.1.0")
        before = shared.snapshot(self.home)

        result = self.move_to_release(env=self.fault("add:orchestrator", "term"))

        self.assertEqual(result.returncode, 128 + signal.SIGTERM, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_signal_to_the_bootstrap_is_passed_on_and_the_prior_version_is_kept(self):
        """installer-contract/R6: the bootstrap is killed while the installer inside it works: that one rolls back too."""
        prior = self.install_prior()
        self.release("0.1.0")
        before = shared.snapshot(self.home)

        result = self.move_to_release(env=self.fault("add:orchestrator", "term-bootstrap"))

        self.assertEqual(result.returncode, 128 + signal.SIGTERM, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_second_signal_during_the_rollback_does_not_interrupt_it(self):
        """installer-contract/R6: the rollback runs to the end: a signal that arrives in it leaves no mixture of shims."""
        prior = self.install_prior()
        self.release("0.1.0")
        before = shared.snapshot(self.home)

        result = self.move_to_release(
            env={**self.fault("add:orchestrator", "term"), "FAKE_PYTHON_AGAIN": "1"}
        )

        self.assertEqual(result.returncode, 128 + signal.SIGTERM, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_staged_release_that_was_already_there_is_kept_when_the_install_fails(self):
        """installer-contract/R6: only what this run staged is removed: a release staged earlier stays staged."""
        self.release("0.1.0")
        staged = self.move_to_release(components="runtime")
        self.assertEqual(staged.returncode, 0, staged.stderr)
        before = shared.snapshot(self.install_home() / "releases")

        failed = self.move_to_release(components="runtime", env=self.fault("add:runtime"))

        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertEqual(shared.snapshot(self.install_home() / "releases"), before)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.1.0")

    def test_s6_a_manifest_that_cannot_be_written_leaves_the_user_state_and_no_temporary_file(self):
        """installer-contract/R6: a manifest path that is a directory is the user's: it stays, and nothing is left."""
        self.release("0.1.0")
        directory = self.install_home() / "install-manifest.json"
        directory.mkdir(parents=True)
        (directory / "keep").write_text("mine\n", encoding="utf-8")
        before = shared.snapshot(self.home)

        result = self.move_to_release(components="runtime")

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)

    def test_s6_a_successful_activation_replaces_every_shim_and_leaves_no_temporary_link(self):
        """installer-contract/R6: the swap is a rename: the new shims are in, and no half-made link is left beside them."""
        self.install_prior()
        self.release("0.1.0")
        result = self.move_to_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        bin_dir = self.home / ".local" / "bin"
        self.assertEqual(sorted(p.name for p in bin_dir.iterdir()), sorted(SHIMS))
        for shim in SHIMS:
            self.assertIn("v0.1.0", os.readlink(bin_dir / shim), shim)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.1.0")
        self.assertEqual([p.name for p in self.install_home().glob(".*")], [])


class CheckoutTransactionTests(TransactionCase):
    """A local checkout install is activated the same way: whole, or not at all."""

    def test_s6_a_failed_checkout_upgrade_restores_the_prior_version(self):
        """installer-contract/R6: a checkout install that fails after linking puts the prior one back."""
        prior = self.install_prior()
        newer = self.checkout("newer", version="0.2.0")
        before = shared.snapshot(self.home)

        result = self.run_installer(
            "--components",
            "runtime,orchestrator",
            script=newer / "install.sh",
            env={
                **self.environment,
                "AGENT_RUNTIME_RELEASE": "",
                **self.fault("add:orchestrator"),
            },
            path=self.path,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_second_signal_during_a_checkout_rollback_does_not_interrupt_it(self):
        """installer-contract/R6: the same for a checkout install: the prior shims all come back, none dangles."""
        prior = self.install_prior()
        newer = self.checkout("newer", version="0.2.0")
        before = shared.snapshot(self.home)

        result = self.run_installer(
            "--components",
            "runtime,orchestrator",
            script=newer / "install.sh",
            env={
                **self.environment,
                "AGENT_RUNTIME_RELEASE": "",
                **self.fault("add:orchestrator", "term"),
                "FAKE_PYTHON_AGAIN": "1",
            },
            path=self.path,
        )

        self.assertEqual(result.returncode, 128 + signal.SIGTERM, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_failed_legacy_install_restores_the_shims_it_replaced(self):
        """installer-contract/R6: the original no-argument install is activated whole too."""
        prior = self.install_prior(components="runtime")
        newer = self.checkout("newer", version="0.2.0")
        before = shared.snapshot(self.home)

        result = self.run_installer(
            script=newer / "install.sh",
            env={**self.environment, **self.fault("swap:agent-runtime-profile")},
            path=self.path,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)
        self.assert_prior_usable(prior)

    def test_s6_a_refused_conflict_changes_nothing(self):
        """installer-contract/R6: a refusal is a rolled-back run: nothing moved, and the user's file is where it was."""
        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "agent-runtime-profile").write_text("mine\n", encoding="utf-8")
        before = shared.snapshot(self.home)
        checkout = self.checkout("fresh")
        result = self.run_installer("--components", "runtime", script=checkout / "install.sh")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to overwrite", result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)


class FailurePathMatrixTests(TransactionCase):
    """installer-contract/R8: the failure paths under clean and existing HOME values, no network, no credentials."""

    def dotfiles(self, home):
        for name in (".claude/settings.json", ".codex/config.toml", ".pi/agent/mcp.json", ".zshrc"):
            target = home / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export KEY=the-user-key\n", encoding="utf-8")

    def test_s8_a_failed_install_changes_nothing_in_a_clean_and_an_existing_home(self):
        """installer-contract/R8: every injected fault is rolled back, in both homes, and nothing reaches out."""
        self.release("0.1.0")
        clean = self.root / "clean home"
        clean.mkdir()
        existing = self.root / "existing"
        existing.mkdir()
        self.dotfiles(existing)
        for label, home in (("clean", clean), ("existing", existing)):
            for step, mode in (
                ("add:runtime", "fail"),
                ("swap:agent-runtime-hook", "fail"),
                ("add:runtime", "term"),
            ):
                before = shared.snapshot(home)
                result = self.move_to_release(
                    components="runtime,pi", env=self.fault(step, mode), home=home
                )
                self.assertNotEqual(result.returncode, 0, (label, step, mode, result.stderr))
                self.assertEqual(shared.snapshot(home), before, (label, step, mode))
                self.assertNotIn("the-user-key", shared.result_text(result), (label, step, mode))
        self.assert_no_network_or_provider()

    def test_s8_a_rolled_back_install_can_be_run_again(self):
        """installer-contract/R8: the rollback leaves a state the next run installs from, and it succeeds."""
        self.release("0.1.0")
        failed = self.move_to_release(components="runtime", env=self.fault("add:runtime"))
        self.assertEqual(failed.returncode, 1, failed.stderr)
        again = self.move_to_release(components="runtime")
        self.assertEqual(again.returncode, 0, again.stderr)
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.1.0")


if __name__ == "__main__":
    unittest.main()
