"""The `herdr` component: the agent-workbench mod and its opt-in layout, installed into Herdr.

From a checkout the mod is a symlink, so editing the checkout edits the mod; from a release it is a
copy, because a staged release is not something a person edits. The file is an integration test: it
drives a shell, a subprocess and the filesystem.
"""

import json
import os
import shutil

import pytest
import test_release as release_tests

pytestmark = pytest.mark.integration

REPO = release_tests.REPO


class HerdrCase(release_tests.ReleaseCase):
    def herdr_home(self):
        return self.home / ".config" / "herdr-app"

    def mod(self):
        return self.herdr_home() / "mods" / "agent-workbench"

    def layout(self):
        return self.herdr_home() / "layouts" / "workbench.json"

    def snapshot(self):
        """Every path under the home directory with what it is, for a before and after."""
        found = {}
        for path in sorted(self.home.rglob("*")):
            kind = (
                "link:" + os.readlink(path)
                if path.is_symlink()
                else ("dir" if path.is_dir() else path.read_bytes())
            )
            found[str(path.relative_to(self.home))] = kind
        return found

    def install_from_checkout(self, *args):
        if not hasattr(self, "checkout_dir"):
            self.checkout_dir = self.checkout()
        return self.run_installer(
            "--components", "herdr", "--yes", *args, script=self.checkout_dir / "install.sh"
        )


class HerdrComponentTests(HerdrCase):
    def test_s19_a_checkout_symlinks_the_mod_and_adds_the_layout_without_switching(self):
        """installer-contract/R10: a checkout links the mod and adds the workbench layout, nothing switches."""
        result = self.install_from_checkout()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            os.readlink(self.mod()), str(self.checkout_dir / "herdr" / "agent-workbench")
        )
        self.assertEqual(
            json.loads(self.layout().read_text(encoding="utf-8")),
            json.loads((REPO / "herdr" / "layouts" / "workbench.json").read_text(encoding="utf-8")),
        )
        # Opt-in: Herdr's own state (settings, the shown layout) is not written, nor another layout.
        self.assertEqual(sorted(p.name for p in self.herdr_home().iterdir()), ["layouts", "mods"])
        self.assertEqual(
            [p.name for p in (self.herdr_home() / "layouts").iterdir()], ["workbench.json"]
        )
        self.assertEqual(
            sorted(self.manifest()["components"]), ["herdr", "orchestrator", "runtime"]
        )

    def test_s19_a_release_copies_the_mod(self):
        """installer-contract/R10: a staged release is copied, never linked into."""
        self.release("0.1.0")
        result = self.bootstrap("--components", "herdr", "--yes", "--release", "v0.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.mod().is_symlink())
        self.assertTrue((self.mod() / "mod.json").is_file())
        self.assertTrue(self.layout().is_file())
        result = self.bootstrap("uninstall", "--components", "herdr", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.mod().exists())
        self.assertFalse(self.layout().exists())

    def test_s19_dry_run_changes_nothing(self):
        """installer-contract/R10: --dry-run previews the mod and the layout and writes nothing."""
        self.checkout_dir = self.checkout()
        before = self.snapshot()
        result = self.run_installer(
            "--components", "herdr", "--dry-run", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.mod()), result.stdout)
        self.assertIn(str(self.layout()), result.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_s19_a_rerun_is_idempotent(self):
        """installer-contract/R10: the same install twice leaves the same tree and manifest."""
        self.assertEqual(self.install_from_checkout().returncode, 0)
        before = self.snapshot()
        again = self.run_installer(
            "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_s19_a_mod_it_did_not_install_is_refused_with_a_preview(self):
        """installer-contract/R10: a mod or layout of that name that is not ours is kept and named."""
        self.checkout_dir = self.checkout()
        own = self.mod()
        own.mkdir(parents=True)
        (own / "mod.json").write_text('{"mine": true}', encoding="utf-8")
        before = self.snapshot()
        result = self.run_installer(
            "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(own), result.stderr)
        self.assertIn("would link", result.stderr)
        self.assertEqual(
            self.snapshot(), before, "a refused install changes nothing, shims included"
        )
        # The same for a layout of that name.
        shutil.rmtree(own)
        self.layout().parent.mkdir(parents=True)
        self.layout().write_text('{"id": "mine"}', encoding="utf-8")
        result = self.run_installer(
            "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.layout()), result.stderr)
        self.assertEqual(self.layout().read_text(encoding="utf-8"), '{"id": "mine"}')
        # And a dry run says so too.
        result = self.run_installer(
            "--components", "herdr", "--dry-run", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 1)

    def test_s19_uninstall_removes_only_what_it_installed(self):
        """installer-contract/R10: the link and the layout go; other mods and an edited layout stay."""
        self.assertEqual(self.install_from_checkout().returncode, 0)
        other = self.herdr_home() / "mods" / "plans"
        other.mkdir()
        (other / "mod.json").write_text("{}", encoding="utf-8")
        result = self.run_installer(
            "uninstall",
            "--components",
            "herdr,orchestrator,runtime",
            "--yes",
            script=self.checkout_dir / "install.sh",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.mod().is_symlink() or self.mod().exists())
        self.assertFalse(self.layout().exists())
        self.assertTrue((other / "mod.json").is_file())
        self.assertTrue((self.checkout_dir / "herdr" / "agent-workbench" / "mod.json").is_file())
        # A layout the person edited since is theirs.
        self.assertEqual(self.install_from_checkout().returncode, 0)
        self.layout().write_text('{"id": "edited"}', encoding="utf-8")
        result = self.run_installer(
            "uninstall", "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept", result.stdout)
        self.assertEqual(self.layout().read_text(encoding="utf-8"), '{"id": "edited"}')
        self.assertFalse(self.mod().is_symlink())
