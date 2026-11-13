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
    def test_s13_a_checkout_links_the_mod_and_the_layout_without_switching(self):
        """installer-contract/R10: a checkout links the mod and the workbench layout, nothing switches."""
        result = self.install_from_checkout()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            os.readlink(self.mod()), str(self.checkout_dir / "herdr" / "agent-workbench")
        )
        self.assertEqual(
            os.readlink(self.layout()), str(self.checkout_dir / "herdr" / "layouts" / "workbench.json")
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
        self.layout().unlink()
        self.layout().write_text('{"id": "edited"}', encoding="utf-8")
        result = self.run_installer(
            "uninstall", "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kept", result.stdout)
        self.assertEqual(self.layout().read_text(encoding="utf-8"), '{"id": "edited"}')
        self.assertFalse(self.mod().is_symlink())

    def test_s19_a_rerun_keeps_an_edited_layout_and_copy_aside(self):
        """installer-contract/R10: an upgrade never overwrites what the person edited since."""
        self.assertEqual(self.install_from_checkout().returncode, 0)
        self.layout().unlink()
        self.layout().write_text('{"id": "edited"}', encoding="utf-8")
        result = self.run_installer(
            "--components", "herdr", "--dry-run", script=self.checkout_dir / "install.sh"
        )
        self.assertIn("edited", result.stdout)
        self.assertEqual(self.layout().read_text(encoding="utf-8"), '{"id": "edited"}')
        again = self.install_from_checkout()
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("kept your edited", again.stdout)
        asides = list((self.herdr_home() / "layouts").glob("workbench.json.edited-*"))
        self.assertEqual([p.read_text(encoding="utf-8") for p in asides], ['{"id": "edited"}'])
        self.assertEqual(
            self.layout().read_bytes(), (REPO / "herdr/layouts/workbench.json").read_bytes()
        )

    def test_s19_an_edited_release_copy_is_kept_aside_and_uninstall_follows_the_digest(self):
        """installer-contract/R10: a copy is judged by what was installed, not by the release now."""
        self.release("0.1.0")
        args = ("--components", "herdr", "--yes", "--release", "v0.1.0")
        self.assertEqual(self.bootstrap(*args).returncode, 0)
        (self.mod() / "mod.json").write_text('{"edited": true}', encoding="utf-8")
        again = self.bootstrap(*args)
        self.assertEqual(again.returncode, 0, again.stderr)
        asides = list((self.herdr_home() / "mods").glob("agent-workbench.edited-*"))
        self.assertEqual(len(asides), 1)
        self.assertEqual((asides[0] / "mod.json").read_text(encoding="utf-8"), '{"edited": true}')
        self.assertNotEqual(
            (self.mod() / "mod.json").read_text(encoding="utf-8"), '{"edited": true}'
        )
        # Unedited since the rerun: uninstall removes the copy and the layout, and only those.
        result = self.bootstrap("uninstall", "--components", "herdr", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.mod().exists())
        self.assertFalse(self.layout().exists())
        self.assertTrue(asides[0].is_dir())

    def test_s13_a_pull_in_the_checkout_reaches_the_layout(self):
        """installer-contract/R10: the layout is a link, so a change to the checkout's file is what Herdr reads."""
        self.assertEqual(self.install_from_checkout().returncode, 0)
        source = self.checkout_dir / "herdr" / "layouts" / "workbench.json"
        source.write_text('{"id": "pulled"}', encoding="utf-8")
        self.assertEqual(self.layout().read_text(encoding="utf-8"), '{"id": "pulled"}')

    def test_s13_a_reinstall_keeps_the_layout_link(self):
        """installer-contract/R10: a reinstall from the same checkout leaves the layout linked, not copied."""
        self.assertEqual(self.install_from_checkout().returncode, 0)
        again = self.run_installer(
            "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(self.layout().is_symlink())
        self.assertEqual(
            os.readlink(self.layout()), str(self.checkout_dir / "herdr" / "layouts" / "workbench.json")
        )

    def test_s13_uninstall_removes_the_layout_link_and_keeps_the_checkout(self):
        """installer-contract/R10: uninstall removes the layout link; the checkout's file stays."""
        self.assertEqual(self.install_from_checkout().returncode, 0)
        result = self.run_installer(
            "uninstall", "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.layout().is_symlink() or self.layout().exists())
        self.assertTrue((self.checkout_dir / "herdr" / "layouts" / "workbench.json").is_file())

    def test_s13_a_layout_symlink_it_did_not_make_is_refused_with_a_way_out(self):
        """installer-contract/R10: a layout link to a file this install does not own is refused and named."""
        self.checkout_dir = self.checkout()
        foreign = self.home / "elsewhere" / "workbench.json"
        foreign.parent.mkdir(parents=True)
        foreign.write_text('{"id": "theirs"}', encoding="utf-8")
        self.layout().parent.mkdir(parents=True)
        self.layout().symlink_to(foreign)
        before = self.snapshot()
        result = self.run_installer(
            "--components", "herdr", "--yes", script=self.checkout_dir / "install.sh"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(self.layout()), result.stderr)
        self.assertIn("would link", result.stderr)
        self.assertIn("move it away or remove it", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_s13_a_release_copies_the_layout(self):
        """installer-contract/R10: a release install copies the layout; it is never a link into the staged release."""
        self.release("0.1.0")
        result = self.bootstrap("--components", "herdr", "--yes", "--release", "v0.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.layout().is_file())
        self.assertFalse(self.layout().is_symlink())
        self.assertEqual(
            self.layout().read_bytes(), (REPO / "herdr" / "layouts" / "workbench.json").read_bytes()
        )

    def test_s13_the_install_prints_how_to_reload_the_mod_after_a_pull(self):
        """installer-contract/R10: the checkout install names the touch that makes Herdr reload after a pull."""
        result = self.install_from_checkout()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("after a pull", result.stdout)
        self.assertIn(f"touch -h {self.mod()} {self.layout()}", result.stdout)
