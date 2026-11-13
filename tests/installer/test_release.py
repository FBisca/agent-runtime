"""The release artifact and the pinned, checksum-verified download.

A release is a set of assets built by `scripts/build-release.sh`; the bootstrap fetches the payload of
one pinned release, verifies it before anything runs, and refuses a moving ref. Every test runs against
a local fixture: the release is built into a temporary directory and a fake `curl` serves it, so no
network and no credentials are involved. The file is an integration test: it drives a shell, a
subprocess and the filesystem.
"""

import atexit
import hashlib
import io
import json
import os
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

import pytest
import test_install as shared

pytestmark = pytest.mark.integration

REPO = shared.REPO
INSTALLER = shared.INSTALLER
SHELL = shared.SHELL
SHIMS = shared.SHIMS
BUILD = REPO / "scripts" / "build-release.sh"
BASE = "https://releases.test/download"

# A stand-in for curl: it serves the release fixture for the https URL it is given, logs every call,
# and can fail the way a real download does (a truncated body, a killed shell).
FAKE_CURL = """#!/bin/sh
echo "curl $*" >> "$FAKE_CALLS"
out=
for arg in "$@"; do last=$arg; done
while [ $# -gt 0 ]; do
  case $1 in
    --output) out=$2; shift ;;
    --proto|--proto-redir|--connect-timeout|--max-time) shift ;;
  esac
  shift
done
rel=${last#"$FAKE_BASE/"}
served=$FAKE_RELEASES/$rel
[ -f "$served" ] || { echo "curl: (22) the requested URL returned error: 404 $last" >&2; exit 22; }
case ${FAKE_CURL_MODE:-} in
  partial:"${rel##*/}") head -c 100 "$served" > "$out"; exit 18 ;;
  kill:"${rel##*/}") head -c 100 "$served" > "$out"; kill -TERM "$PPID"; exit 15 ;;
esac
cp "$served" "$out"
"""


# A release is built once per session and shared read-only: building one costs a shell, a git work
# tree and a tarball, and most tests only read it. `BUILDS` counts the builds, per version.
SHARED = {}
BUILDS = {}
_SHARED_ROOT = []


def _writable(path, writable):
    """Flip a tree between read-only (shared) and writable (a copy, or the cleanup)."""
    for entry in [path, *Path(path).rglob("*")]:
        mode = entry.stat().st_mode
        entry.chmod(mode | stat.S_IWUSR if writable else mode & ~0o222)


def _drop_shared():
    for directory in SHARED.values():
        _writable(directory, True)
    for root in _SHARED_ROOT:
        shutil.rmtree(root, ignore_errors=True)


atexit.register(_drop_shared)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReleaseCase(shared.StreamedInstallCase):
    """A release built into a fixture directory, and a fake curl that serves it."""

    def setUp(self):
        super().setUp()
        self.releases = self.root / "releases"
        self.releases.mkdir()
        self.calls = self.root / "calls.log"
        fake = self.root / "fake-bin"
        fake.mkdir()
        curl = fake / "curl"
        curl.write_text(FAKE_CURL, encoding="utf-8")
        curl.chmod(0o755)
        self.path = f"{fake}:{os.environ.get('PATH', '/usr/bin:/bin')}"
        self.environment = {
            "FAKE_CALLS": str(self.calls),
            "FAKE_BASE": BASE,
            "FAKE_RELEASES": str(self.releases),
            "AGENT_RUNTIME_RELEASE_URL": BASE,
        }

    def source(self, version="0.1.0", orchestrator_version=None):
        """A checkout to build a release from: a git work tree, as CI has at a tag."""
        self.sources = getattr(self, "sources", 0) + 1
        source = self.checkout(f"source-{version}-{self.sources}", version, orchestrator_version)
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "add", "-A"], cwd=source, check=True)
        return source

    def build(self, version="0.1.0", orchestrator_version=None, extra=()):
        """Build the release of a version with the release script, into the fixture."""
        source = self.source(version, orchestrator_version)
        out = self.releases / f"v{version}"
        result = subprocess.run(
            [
                SHELL,
                str(BUILD),
                "--version",
                version,
                "--source",
                str(source),
                "--out",
                str(out),
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result, out

    def release(self, version="0.1.0"):
        """The release of a version, shared read-only: a test that changes it takes `release_copy`."""
        shared = self.shared_release(version)
        out = self.releases / f"v{version}"
        if not out.is_symlink():
            out.symlink_to(shared)
        return out

    def release_copy(self, version="0.1.0"):
        """A private, writable copy of the release, for a test that tampers with its assets."""
        out = self.releases / f"v{version}"
        if out.is_symlink():
            out.unlink()
        shutil.copytree(self.shared_release(version), out)
        _writable(out, True)
        return out

    def shared_release(self, version):
        if version not in SHARED:
            result, built = self.build(version)
            self.assertEqual(result.returncode, 0, result.stderr)
            if not _SHARED_ROOT:
                _SHARED_ROOT.append(tempfile.mkdtemp(prefix="agent-runtime-release-"))
            kept = Path(_SHARED_ROOT[0]) / f"v{version}"
            shutil.copytree(built, kept)
            _writable(kept, False)
            SHARED[version] = kept
            BUILDS[version] = BUILDS.get(version, 0) + 1
            shutil.rmtree(built)
        return SHARED[version]

    def seal(self, directory):
        """Rewrite SHA256SUMS for what the directory holds now: a publisher that tampers does this."""
        directory = Path(directory)
        lines = [
            f"{sha256(asset)}  {asset.name}"
            for asset in sorted(directory.iterdir())
            if asset.name != "SHA256SUMS"
        ]
        (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def tarball(self, directory):
        return next(Path(directory).glob("agent-runtime-v*.tar.gz"))

    def bootstrap(self, *args, extra=None, **keywords):
        """Stream the installer with the fake curl, the fixture URL and no real network."""
        environment = dict(self.environment)
        environment.update(extra or {})
        keywords.setdefault("env", {}).update(environment)
        return self.stream(*args, path=self.path, **keywords)

    def curl_calls(self):
        if not self.calls.exists():
            return []
        return self.calls.read_text(encoding="utf-8").splitlines()

    def install_home(self):
        return self.home / ".local" / "share" / "agent-runtime"

    def clean(self, result):
        """Nothing was installed: the home holds nothing, not even an empty directory."""
        self.assertEqual(shared.snapshot(self.home), {}, result.stderr)


class ReleaseBuildTests(ReleaseCase):
    """scripts/build-release.sh: the artifact contract, one version and a checksum per asset."""

    def test_s3_a_release_is_one_version_with_a_checksum_for_every_asset(self):
        """installer-contract/R3: the payload, its manifest and the installer are published with SHA-256 checksums."""
        out = self.release("0.1.0")
        self.assertEqual(
            sorted(asset.name for asset in out.iterdir()),
            ["SHA256SUMS", "agent-runtime-v0.1.0.tar.gz", "install.sh", "release.json"],
        )
        sums = {}
        for line in (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ")
            sums[name] = digest
        self.assertEqual(
            sorted(sums), ["agent-runtime-v0.1.0.tar.gz", "install.sh", "release.json"]
        )
        for name, digest in sums.items():
            self.assertEqual(sha256(out / name), digest, name)

        manifest = json.loads((out / "release.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["contract"], 1)
        self.assertEqual(manifest["release"], "v0.1.0")
        self.assertEqual(manifest["platform"], "any")
        self.assertEqual(manifest["payload"], "agent-runtime-v0.1.0.tar.gz")
        self.assertEqual(manifest["components"], {"runtime": "0.1.0", "orchestrator": "0.1.0"})

        with tarfile.open(self.tarball(out)) as archive:
            names = archive.getnames()
        self.assertIn("agent-runtime-v0.1.0/install.sh", names)
        self.assertIn("agent-runtime-v0.1.0/bin/agent-runtime", names)
        self.assertTrue(all(name.startswith("agent-runtime-v0.1.0") for name in names), names)

    def test_s3_the_published_installer_names_the_release_it_belongs_to(self):
        """installer-contract/R3: the installer asset carries its release, so a bootstrap needs no moving URL."""
        out = self.release("0.1.0")
        stamped = (out / "install.sh").read_text(encoding="utf-8")
        self.assertIn("\nrelease_pin=v0.1.0\n", stamped)
        # The source installer is not stamped: only a built release names itself.
        self.assertIn("\nrelease_pin=\n", INSTALLER.read_text(encoding="utf-8"))
        version = subprocess.run(
            [SHELL, str(out / "install.sh"), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(version.stdout.strip(), "agent-runtime installer v0.1.0")

    def test_s3_a_release_whose_members_disagree_is_not_built(self):
        """installer-contract/R3: the runtime and the orchestrator ship at one version, or the release is not built."""
        result, out = self.build("0.1.0", orchestrator_version="0.2.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to build a mixed release", result.stderr)
        self.assertIn("0.2.0", result.stderr)
        self.assertFalse(out.exists())

    def test_s3_a_release_is_built_from_a_version_and_never_from_a_moving_ref(self):
        """installer-contract/R3: a tag that is not a version, or is not the checkout's version, builds nothing."""
        for version in ("main", "latest", "HEAD", "1.0", "feature/x", "0.1.0;touch x", ""):
            result, out = self.build(version)
            self.assertEqual(result.returncode, 2, (version, result.stderr))
            self.assertFalse(out.exists(), version)
        source = self.source("0.3.0")
        mismatch = subprocess.run(
            [
                SHELL,
                str(BUILD),
                "--version",
                "0.4.0",
                "--source",
                str(source),
                "--out",
                str(self.root / "x"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(mismatch.returncode, 1)
        self.assertIn("v0.4.0", mismatch.stderr)
        self.assertIn("0.3.0", mismatch.stderr)
        self.assertFalse((self.root / "x").exists())

    def test_s6_the_same_release_builds_to_the_same_bytes(self):
        """installer-contract/R6: a reproducible artifact: a checksum published once is a checksum anyone can rebuild."""
        first = self.release("0.1.0")
        source = self.source("0.1.0")
        again = self.root / "again"
        subprocess.run(
            [SHELL, str(BUILD), "--version", "0.1.0", "--source", str(source), "--out", str(again)],
            check=True,
            capture_output=True,
        )
        for name in ("agent-runtime-v0.1.0.tar.gz", "SHA256SUMS", "release.json", "install.sh"):
            self.assertEqual((first / name).read_bytes(), (again / name).read_bytes(), name)

    def test_s8_a_release_is_not_built_from_a_directory_that_is_no_checkout(self):
        """installer-contract/R8: the build names its way out when the source is not a git work tree."""
        plain = self.root / "plain"
        plain.mkdir()
        result = subprocess.run(
            [
                SHELL,
                str(BUILD),
                "--version",
                "0.1.0",
                "--source",
                str(plain),
                "--out",
                str(self.root / "o"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("git", result.stderr)
        self.assertFalse((self.root / "o").exists())


class PinnedBootstrapTests(ReleaseCase):
    """The bootstrap installs one pinned, verified release, and nothing else."""

    def test_s2_a_streamed_bootstrap_installs_the_payload_of_its_pinned_release(self):
        """installer-contract/R2: streamed, pinned, verified: the shims point into the staged release payload."""
        self.release("0.1.0")
        result = self.bootstrap(
            "--components", "runtime,orchestrator", "--yes", "--release", "v0.1.0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.install_home() / "releases" / "v0.1.0"
        for shim in SHIMS:
            self.assertEqual(os.readlink(self.shim(shim)), str(payload / "bin" / shim))
        manifest = self.manifest()
        self.assertEqual(sorted(manifest["components"]), ["orchestrator", "runtime"])
        self.assertEqual(
            {entry["release"] for entry in manifest["components"].values()}, {"v0.1.0"}
        )
        # Every download is https, from the release the bootstrap was pinned to.
        calls = self.curl_calls()
        self.assertEqual(len(calls), 3, calls)
        for call in calls:
            self.assertIn("--proto =https", call)
            self.assertIn(f"{BASE}/v0.1.0/", call)
        # The staging area is gone: only the release is left beside the manifest.
        self.assertEqual(
            sorted(path.name for path in self.install_home().iterdir()),
            ["install-manifest.json", "releases"],
        )

    def test_s2_the_published_installer_streamed_needs_no_release_flag(self):
        """installer-contract/R2: the installer asset names its release, so the curl | sh line carries no ref to get wrong."""
        out = self.release("0.1.0")
        result = self.bootstrap("--components", "runtime", "--yes", script=out / "install.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.1.0")

    def test_s2_the_terminal_menu_runs_before_the_download_and_stdin_is_never_an_answer(self):
        """installer-contract/R2: the menu is on the terminal; the confirmation is asked before anything is fetched."""
        self.release("0.1.0")
        declined = self.bootstrap("--release", "v0.1.0", answers=["pi", "n"])
        self.assertEqual(declined.returncode, 1)
        self.assertIn("release: v0.1.0", declined.stdout)
        self.assertEqual(self.curl_calls(), [])
        self.clean(declined)

        accepted = self.bootstrap("--release", "v0.1.0", answers=["pi", "y"])
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(sorted(self.manifest()["components"]), ["pi", "runtime"])
        self.assertIn("~/.pi/agent/mcp.json", accepted.stdout)

    def test_s2_a_streamed_install_without_a_release_is_a_usage_error(self):
        """installer-contract/R2: no release named, none stamped: the bootstrap says how to pin one and fetches nothing."""
        self.release("0.1.0")
        result = self.bootstrap("--components", "runtime", "--yes")
        self.assertEqual(result.returncode, 2)
        self.assertIn("names no release", result.stderr)
        self.assertIn("--release v", result.stderr)
        self.assertIn("usage: install.sh", result.stderr)
        self.assertEqual(self.curl_calls(), [])
        self.clean(result)

    def test_s3_a_moving_ref_is_refused_before_anything_is_fetched(self):
        """installer-contract/R3: main, a branch, latest, a sha or a half version are refused with the way out."""
        self.release("0.1.0")
        marker = self.root / "injected"
        for ref in (
            "main",
            "master",
            "HEAD",
            "latest",
            "refs/heads/main",
            "feat/branch",
            "0123456789abcdef0123456789abcdef01234567",
            "v1",
            "v1.2",
            "v1.2.3/../../x",
            "v1.2.3 main",
            "v1.2.3\nmain",
            f"v1.2.3;touch {marker}",
            "$(touch x)",
        ):
            result = self.bootstrap("--components", "runtime", "--yes", "--release", ref)
            self.assertEqual(result.returncode, 1, (ref, result.stderr))
            self.assertIn("moving", result.stderr, ref)
            self.assertIn("vX.Y.Z", result.stderr, ref)
        for variable in ("AGENT_RUNTIME_RELEASE",):
            result = self.bootstrap("--components", "runtime", "--yes", extra={variable: "main"})
            self.assertEqual(result.returncode, 1)
            self.assertIn("moving", result.stderr)
        # A dry run asks the same question: its plan never promises a moving ref.
        dry = self.bootstrap("--components", "runtime", "--dry-run", "--release", "main")
        self.assertEqual(dry.returncode, 1)
        self.assertEqual(self.curl_calls(), [])
        self.assertFalse(marker.exists())
        self.clean(dry)

    def test_s3_two_different_releases_named_are_refused(self):
        """installer-contract/R3: a stamped installer, a flag and the environment must agree on one release."""
        out = self.release("0.1.0")
        stamped = self.bootstrap(
            "--components", "runtime", "--yes", "--release", "v0.2.0", script=out / "install.sh"
        )
        self.assertEqual(stamped.returncode, 1)
        self.assertIn("v0.1.0", stamped.stderr)
        self.assertIn("v0.2.0", stamped.stderr)
        both = self.bootstrap(
            "--components",
            "runtime",
            "--yes",
            "--release",
            "v0.1.0",
            extra={"AGENT_RUNTIME_RELEASE": "v0.2.0"},
        )
        self.assertEqual(both.returncode, 1)
        self.assertIn("two releases", both.stderr)
        self.assertEqual(self.curl_calls(), [])
        # The same release, spelled with or without its v, is one release.
        agreeing = self.bootstrap(
            "--components",
            "runtime",
            "--yes",
            "--release",
            "v0.1.0",
            extra={"AGENT_RUNTIME_RELEASE": "0.1.0"},
        )
        self.assertEqual(agreeing.returncode, 0, agreeing.stderr)

    def test_s3_a_release_manifest_that_mixes_versions_is_refused(self):
        """installer-contract/R3: the release manifest is checked: components of two versions are a mixture."""
        out = self.release_copy("0.1.0")
        manifest = json.loads((out / "release.json").read_text(encoding="utf-8"))
        manifest["components"]["orchestrator"] = "0.2.0"
        (out / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.seal(out)
        result = self.bootstrap("--components", "orchestrator", "--yes", "--release", "v0.1.0")
        self.assertEqual(result.returncode, 1)
        self.assertIn("refusing to mix releases", result.stderr)
        self.clean(result)

    def test_s3_the_orchestrator_installs_with_the_runtime_of_the_same_release(self):
        """installer-contract/R3: the requirement is explicit in the plan, and both are installed from one release."""
        self.release("0.1.0")
        result = self.bootstrap("--components", "orchestrator", "--yes", "--release", "v0.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("runtime: required by orchestrator", result.stdout)
        releases = {entry["release"] for entry in self.manifest()["components"].values()}
        self.assertEqual(releases, {"v0.1.0"})
        self.assertEqual(sorted(self.manifest()["components"]), ["orchestrator", "runtime"])

    def test_s3_an_installed_release_is_not_mixed_with_another_one(self):
        """installer-contract/R3: components of another installed release are refused before the download."""
        self.release("0.1.0")
        self.release("0.2.0")
        first = self.bootstrap("--components", "claude", "--yes", "--release", "v0.1.0")
        self.assertEqual(first.returncode, 0, first.stderr)
        before = len(self.curl_calls())
        second = self.bootstrap("--components", "pi", "--yes", "--release", "v0.2.0")
        self.assertEqual(second.returncode, 1)
        self.assertIn("refusing to mix releases", second.stderr)
        self.assertEqual(len(self.curl_calls()), before)

    def test_s5_a_pinned_dry_run_names_what_it_would_fetch_and_fetches_nothing(self):
        """installer-contract/R5: the plan lists the assets, the checksum check and the staging path; no curl is run."""
        self.release("0.1.0")
        result = self.bootstrap(
            "--components", "runtime,orchestrator", "--dry-run", "--release", "v0.1.0"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release: v0.1.0", result.stdout)
        for asset in ("SHA256SUMS", "release.json", "agent-runtime-v0.1.0.tar.gz"):
            self.assertIn(f"fetch: {BASE}/v0.1.0/{asset}", result.stdout)
        self.assertIn("verify: sha256", result.stdout)
        staged = self.install_home() / "releases" / "v0.1.0"
        self.assertIn(f"stage: {staged}", result.stdout)
        self.assertIn(f"-> {staged}/bin/agent-runtime", result.stdout)
        self.assertEqual(self.curl_calls(), [])
        self.clean(result)

    def test_s8_the_download_is_https_only(self):
        """installer-contract/R8: a plain http (or any other) release URL is refused: it can be rewritten in transit."""
        self.release("0.1.0")
        for url in ("http://releases.test/download", "file:///tmp/x", "ftp://x/y", "releases.test"):
            result = self.bootstrap(
                "--components",
                "runtime",
                "--yes",
                "--release",
                "v0.1.0",
                extra={"AGENT_RUNTIME_RELEASE_URL": url},
            )
            self.assertEqual(result.returncode, 1, (url, result.stderr))
            self.assertIn("https", result.stderr)
        self.assertEqual(self.curl_calls(), [])

    def test_s8_without_curl_the_bootstrap_says_so(self):
        """installer-contract/R8: the bootstrap needs curl to fetch; without it nothing is attempted."""
        self.release("0.1.0")
        bare = self.root / "bare"
        bare.mkdir()
        for tool in (
            "sh",
            "python3",
            "mktemp",
            "rm",
            "mkdir",
            "dirname",
            "cat",
            "sed",
            "tr",
            "grep",
            "head",
            "ln",
            "mv",
            "readlink",
            "printf",
            "uname",
        ):
            found = shutil.which(tool)
            if found and not (bare / tool).exists():
                (bare / tool).symlink_to(found)
        # Drive the installer directly with a PATH that has no curl.
        completed = subprocess.run(
            [SHELL, "-s", "--", "--components", "runtime", "--yes", "--release", "v0.1.0"],
            input=INSTALLER.read_bytes(),
            env={"HOME": str(self.home), "PATH": str(bare), "LANG": "C", **self.environment},
            capture_output=True,
            check=False,
            start_new_session=True,
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn(b"curl", completed.stderr)
        self.clean(completed)


class VerificationTests(ReleaseCase):
    """A payload that fails verification, or never finishes downloading, leaves nothing behind."""

    def install_prior(self):
        """A prior version, installed from a local checkout: it has to stay usable."""
        prior = self.checkout("prior", version="0.0.9")
        result = self.run_installer(
            "--components",
            "runtime",
            script=prior / "install.sh",
            env={"AGENT_RUNTIME_RELEASE": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return prior

    def refused(self, *extra_arguments, mode=None, extra=None):
        environment = dict(extra or {})
        if mode:
            environment["FAKE_CURL_MODE"] = mode
        return self.bootstrap(
            "--components",
            "runtime",
            "--yes",
            "--release",
            "v0.1.0",
            *extra_arguments,
            extra=environment,
        )

    def test_s6_a_tampered_payload_is_refused_and_nothing_is_left(self):
        """installer-contract/R6: the tarball no longer matches its checksum: nothing runs and nothing stays."""
        out = self.release_copy("0.1.0")
        tarball = self.tarball(out)
        tarball.write_bytes(tarball.read_bytes() + b"tampered")
        result = self.refused()
        self.assertEqual(result.returncode, 1)
        self.assertIn("checksum", result.stderr)
        self.assertIn(tarball.name, result.stderr)
        self.clean(result)

    def test_s6_a_failed_verification_leaves_the_prior_version_usable(self):
        """installer-contract/R6: the prior install, its manifest and its shims are exactly as they were."""
        self.install_prior()
        out = self.release_copy("0.1.0")
        self.tarball(out).write_bytes(b"not a tarball")
        before = shared.snapshot(self.home)
        for mode in (None, "partial:agent-runtime-v0.1.0.tar.gz"):
            result = self.refused(mode=mode)
            self.assertEqual(result.returncode, 1, (mode, result.stderr))
            self.assertEqual(shared.snapshot(self.home), before, mode)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.0.9")
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
            self.assertTrue(self.shim(shim).resolve().is_file(), shim)

    def test_s6_an_interrupted_download_leaves_nothing_behind(self):
        """installer-contract/R6: a download that stops half way (curl fails) installs nothing and cleans its staging."""
        out = self.release("0.1.0")
        for asset in ("agent-runtime-v0.1.0.tar.gz", "release.json", "SHA256SUMS"):
            result = self.refused(mode=f"partial:{asset}")
            self.assertEqual(result.returncode, 1, (asset, result.stderr))
            self.assertIn("download", result.stderr, asset)
            self.assertIn(asset, result.stderr)
            self.clean(result)
        self.assertTrue(out.exists())

    def test_s6_a_signal_during_the_download_cleans_up_and_keeps_the_prior_install(self):
        """installer-contract/R6: the shell is killed while a download is running; the staging directory goes with it."""
        self.install_prior()
        self.release("0.1.0")
        before = shared.snapshot(self.home)
        result = self.refused(mode="kill:agent-runtime-v0.1.0.tar.gz")
        self.assertEqual(result.returncode, 128 + signal.SIGTERM, result.stderr)
        self.assertEqual(shared.snapshot(self.home), before)

    def test_s6_a_missing_asset_is_refused_with_the_asset_named(self):
        """installer-contract/R6: a release with no checksums, no manifest or no payload is not installed."""
        out = self.release_copy("0.1.0")
        for name in ("SHA256SUMS", "release.json", "agent-runtime-v0.1.0.tar.gz"):
            hidden = self.root / f"hidden-{name}"
            (out / name).rename(hidden)
            result = self.refused()
            self.assertEqual(result.returncode, 1, (name, result.stderr))
            self.assertIn(name, result.stderr)
            self.clean(result)
            hidden.rename(out / name)

    def test_s6_a_checksum_file_without_the_payload_entry_is_refused(self):
        """installer-contract/R6: no checksum for the asset means nothing to verify it against: refuse."""
        out = self.release_copy("0.1.0")
        sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        kept = [line for line in sums if "tar.gz" not in line]
        (out / "SHA256SUMS").write_text("\n".join(kept) + "\n", encoding="utf-8")
        result = self.refused()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no checksum", result.stderr)
        self.clean(result)

        (out / "SHA256SUMS").write_text("zz  agent-runtime-v0.1.0.tar.gz\n", encoding="utf-8")
        garbled = self.refused()
        self.assertEqual(garbled.returncode, 1)
        self.clean(garbled)

    def test_s6_an_independent_checksum_outranks_the_one_published_with_the_release(self):
        """installer-contract/R6: a tamperer who rewrites the release's checksums cannot rewrite the user's own."""
        out = self.release_copy("0.1.0")
        trusted = sha256(self.tarball(out))
        # The publisher swaps the payload and republishes consistent checksums.
        with tarfile.open(self.tarball(out), "w:gz") as archive:
            info = tarfile.TarInfo("agent-runtime-v0.1.0/install.sh")
            data = b"#!/bin/sh\necho owned\n"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        self.seal(out)

        result = self.refused("--sha256", trusted)
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the checksum you gave", result.stderr)
        self.clean(result)

        also = self.refused(extra={"AGENT_RUNTIME_SHA256": trusted})
        self.assertEqual(also.returncode, 1)
        self.clean(also)

        malformed = self.refused("--sha256", "not-a-checksum")
        self.assertEqual(malformed.returncode, 2)
        self.assertIn("64 hexadecimal", malformed.stderr)

    def test_s6_an_independent_checksum_that_matches_installs_and_says_how_it_was_checked(self):
        """installer-contract/R6: the assurance is stated: independent when --sha256 is given, same-origin when not."""
        out = self.release("0.1.0")
        trusted = sha256(self.tarball(out))
        verified = self.refused("--sha256", trusted.upper())
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("checksum you gave", verified.stdout)
        shutil.rmtree(self.home / ".local")
        convenience = self.refused()
        self.assertEqual(convenience.returncode, 0, convenience.stderr)
        self.assertIn("published with the release", convenience.stdout)
        self.assertIn("--sha256", convenience.stdout)

    def test_s6_the_checksum_in_the_environment_installs_like_the_flag(self):
        """installer-contract/R6: AGENT_RUNTIME_SHA256 is the same as --sha256: it must not break the hand-over."""
        out = self.release("0.1.0")
        trusted = sha256(self.tarball(out))
        result = self.refused(extra={"AGENT_RUNTIME_SHA256": trusted})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checksum you gave", result.stdout)
        for shim in SHIMS:
            self.assertTrue(self.shim(shim).is_symlink(), shim)
        self.assertEqual(self.manifest()["components"]["runtime"]["release"], "v0.1.0")

    def test_s6_a_release_manifest_for_another_release_or_platform_is_refused(self):
        """installer-contract/R6: a manifest that names another release, payload or platform is a mismatch."""
        out = self.release_copy("0.1.0")
        original = (out / "release.json").read_text(encoding="utf-8")
        for field, value in (
            ("release", "v0.2.0"),
            ("platform", "plan9"),
            ("payload", "agent-runtime-v0.2.0.tar.gz"),
            ("contract", 2),
        ):
            manifest = json.loads(original)
            manifest[field] = value
            (out / "release.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.seal(out)
            result = self.refused()
            self.assertEqual(result.returncode, 1, (field, result.stderr))
            self.assertIn("release manifest", result.stderr, field)
            self.clean(result)
        (out / "release.json").write_text("{not json", encoding="utf-8")
        self.seal(out)
        broken = self.refused()
        self.assertEqual(broken.returncode, 1)
        self.assertIn("release manifest", broken.stderr)
        self.clean(broken)

    def test_s6_a_payload_that_escapes_its_directory_is_refused(self):
        """installer-contract/R6: a tarball with traversal, an absolute path or an escaping link extracts nothing."""
        out = self.release_copy("0.1.0")
        escapes = self.root / "escaped"
        cases = {
            "traversal": ("agent-runtime-v0.1.0/../../escaped", tarfile.REGTYPE, ""),
            "absolute": (str(escapes), tarfile.REGTYPE, ""),
            "outside": ("elsewhere/file", tarfile.REGTYPE, ""),
            "link": ("agent-runtime-v0.1.0/link", tarfile.SYMTYPE, "../../../../etc/passwd"),
            "hardlink": (
                "agent-runtime-v0.1.0/hard",
                tarfile.LNKTYPE,
                "agent-runtime-v0.1.0/install.sh",
            ),
        }
        for label, (name, kind, target) in cases.items():
            with tarfile.open(self.tarball(out), "w:gz") as archive:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = target
                data = b"owned\n" if kind == tarfile.REGTYPE else b""
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            self.seal(out)
            result = self.refused()
            self.assertEqual(result.returncode, 1, (label, result.stderr))
            self.assertIn("unsafe", result.stderr, label)
            self.assertFalse(escapes.exists(), label)
            self.clean(result)

    def test_s6_a_payload_that_is_not_a_release_checkout_is_refused(self):
        """installer-contract/R6: a verified tarball that lacks the installer or the shims is no release payload."""
        out = self.release_copy("0.1.0")
        with tarfile.open(self.tarball(out), "w:gz") as archive:
            info = tarfile.TarInfo("agent-runtime-v0.1.0/README.md")
            info.size = 2
            archive.addfile(info, io.BytesIO(b"x\n"))
        self.seal(out)
        result = self.refused()
        self.assertEqual(result.returncode, 1)
        self.assertIn("not a release payload", result.stderr)
        self.clean(result)

    def test_s6_a_staged_release_is_reused_and_a_different_payload_for_it_is_refused(self):
        """installer-contract/R6: the staged release stays what it was verified to be; a rerun does not replace it."""
        out = self.release_copy("0.1.0")
        first = self.refused()
        self.assertEqual(first.returncode, 0, first.stderr)
        again = self.refused()
        self.assertEqual(again.returncode, 0, again.stderr)
        # Another payload published under the same release is refused, and the staged one stays.
        staged = self.install_home() / "releases" / "v0.1.0"
        before = shared.snapshot(staged)
        with tarfile.open(self.tarball(out), "w:gz") as archive:
            for name, body in (
                ("agent-runtime-v0.1.0/install.sh", b"#!/bin/sh\n"),
                ("agent-runtime-v0.1.0/bin/agent-runtime", b"#!/bin/sh\n"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
        self.seal(out)
        different = self.refused()
        self.assertEqual(different.returncode, 1)
        self.assertIn("already staged", different.stderr)
        self.assertEqual(shared.snapshot(staged), before)

    def test_s6_a_symlinked_release_directory_is_refused(self):
        """installer-contract/R6: a releases directory that is a link is not written through."""
        self.release("0.1.0")
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.install_home().mkdir(parents=True)
        (self.install_home() / "releases").symlink_to(elsewhere)
        result = self.refused()
        self.assertEqual(result.returncode, 1)
        self.assertIn("symlink", result.stderr)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_s6_the_uninstall_of_a_streamed_run_needs_no_download(self):
        """installer-contract/R6: an uninstall works from the manifest alone: no release, no network."""
        self.release("0.1.0")
        installed = self.refused()
        self.assertEqual(installed.returncode, 0, installed.stderr)
        calls = len(self.curl_calls())
        removed = self.bootstrap("uninstall", "--components", "runtime", "--yes")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(len(self.curl_calls()), calls)
        self.assertNotIn("runtime", self.manifest()["components"])


class ReleaseEnvironmentTests(ReleaseCase):
    """The matrix: clean and existing homes, no network and no credentials."""

    def test_s8_the_pinned_bootstrap_runs_in_a_clean_and_an_existing_home(self):
        """installer-contract/R8: dotfiles are left alone, no credential is printed, and the fake curl is the only fetch."""
        self.release("0.1.0")
        existing = self.root / "existing"
        existing.mkdir()
        for name in (".claude/settings.json", ".codex/config.toml", ".pi/agent/mcp.json", ".zshrc"):
            target = existing / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("export KEY=the-user-key\n", encoding="utf-8")
        dotfiles = {k: v for k, v in shared.snapshot(existing).items() if v[0] == "file"}
        clean = self.root / "clean home"
        clean.mkdir()
        for kind, home in (("clean", clean), ("existing", existing)):
            environment = {**self.environment, "HOME": str(home)}
            result = self.stream(
                "--components",
                "runtime,pi",
                "--yes",
                "--release",
                "v0.1.0",
                path=self.path,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, (kind, result.stderr))
            self.assertNotIn("the-user-key", shared.result_text(result))
            self.assertTrue((home / ".local" / "bin" / "agent-runtime").is_symlink(), kind)
        for name, entry in dotfiles.items():
            self.assertEqual((existing / name).read_bytes(), entry[1], name)

    def test_s8_shellcheck_is_happy_with_the_installer_and_the_release_scripts(self):
        """installer-contract/R8: the installer and every script under scripts/ are shellcheck clean."""
        if shutil.which("shellcheck") is None:
            self.skipTest("shellcheck is not installed")
        scripts = sorted((REPO / "scripts").glob("*.sh"))
        self.assertIn(BUILD, scripts)
        result = subprocess.run(
            ["shellcheck", str(INSTALLER), *map(str, scripts)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_s8_the_release_workflow_publishes_the_assets_of_a_version_tag(self):
        """installer-contract/R8: CI builds the release from a version tag with the same script the tests run."""
        workflow = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/build-release.sh", workflow)
        self.assertIn("v*.*.*", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("contents: write", workflow)
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/*", ci)


if __name__ == "__main__":
    unittest.main()
