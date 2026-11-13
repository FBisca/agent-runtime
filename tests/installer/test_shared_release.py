"""The v0.1.0 release is built once per session, and the fakes wait on no clock.

A test that only reads the release shares it; a test that changes it takes a copy. The fakes that
signal the installer return at once: the signal is the event the test asserts, not a pause.
"""

import os
import unittest

import pytest
import test_release as release
import test_transaction as transaction

pytestmark = pytest.mark.integration


class SharedReleaseTests(release.ReleaseCase):
    def test_S9_the_release_is_built_once_and_a_reader_shares_it(self):
        first = self.release("0.1.0")
        builds = release.BUILDS.get("0.1.0")
        again = self.release("0.1.0")
        self.assertEqual(first.resolve(), again.resolve())
        self.assertEqual(release.BUILDS.get("0.1.0"), builds)
        self.assertEqual(builds, 1)
        self.assertTrue((self.releases / "v0.1.0" / "install.sh").is_file())

    def test_S9_a_test_that_changes_the_release_takes_a_copy(self):
        shared = self.shared_release("0.1.0")
        copy = self.release_copy("0.1.0")
        self.assertNotEqual(copy.resolve(), shared.resolve())
        self.assertEqual(
            sorted(p.name for p in copy.iterdir()), sorted(p.name for p in shared.iterdir())
        )
        self.tarball(copy).write_bytes(b"changed")
        self.assertNotEqual(self.tarball(copy).read_bytes(), self.tarball(shared).read_bytes())

    def test_S9_a_write_to_the_shared_release_is_refused(self):
        shared = self.release("0.1.0")
        with self.assertRaises(OSError):
            (shared / "release.json").write_text("{}", encoding="utf-8")
        self.assertTrue(os.access(shared / "release.json", os.R_OK))


class NoClockTests(unittest.TestCase):
    def test_S7_the_fakes_signal_the_installer_and_return_without_a_sleep(self):
        self.assertNotIn("sleep", release.FAKE_CURL)
        self.assertNotIn("sleep", transaction.FAKE_PYTHON)


if __name__ == "__main__":
    unittest.main()
