#!/bin/sh
# Build the assets of one release of agent-runtime into a directory.
#
# The release artifact contract (what the bootstrap in install.sh downloads and verifies):
#
#   agent-runtime-vX.Y.Z.tar.gz  the payload: every tracked file of the checkout, under one
#                                agent-runtime-vX.Y.Z/ directory
#   release.json                 the release manifest: the release, the payload, the platform and the
#                                version of each component (runtime and orchestrator ship together)
#   install.sh                   the installer, stamped with the release it belongs to
#   SHA256SUMS                   a SHA-256 checksum for every other asset, in `sha256sum -c` format
#
# The build is reproducible: the same checkout gives the same bytes, so a checksum published once is
# one anybody can rebuild. A checksum published with the release only proves the download is the one
# CI published; a user who wants more takes the checksum from somewhere else (the release notes, a
# signed tag, a colleague) and hands it to the installer with --sha256.
#
# usage: build-release.sh --version X.Y.Z [--source DIR] [--out DIR]
# exit codes: 0 built, 1 refused (the checkout cannot make that release), 2 the command line was wrong
set -eu

here=$(cd "$(dirname "$0")" && pwd)
source_dir=$(dirname "$here")
out_dir=dist
version=

usage() {
  echo "usage: build-release.sh --version X.Y.Z [--source DIR] [--out DIR]" >&2
}

while [ $# -gt 0 ]; do
  case $1 in
    --version) shift; [ $# -gt 0 ] || { usage; exit 2; }; version=$1 ;;
    --source) shift; [ $# -gt 0 ] || { usage; exit 2; }; source_dir=$1 ;;
    --out) shift; [ $# -gt 0 ] || { usage; exit 2; }; out_dir=$1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

# A release is a version. A branch name or `latest` would make the artifact depend on what the ref
# points at tomorrow, which is what a pinned release exists to prevent.
version=${version#v}
case $version in
  *[!0-9A-Za-z.-]*|'') bad=1 ;;
  *) bad=0 ;;
esac
if [ "$bad" -eq 1 ] || ! printf '%s\n' "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?$'; then
  echo "refusing to build: '$version' is not a version like 1.2.3, and a release is never a moving ref" >&2
  usage
  exit 2
fi
release=v$version

command -v python3 >/dev/null 2>&1 || { echo "python3 is required to build a release" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "git is required to build a release" >&2; exit 1; }
if ! git -C "$source_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "refusing to build: $source_dir is not a git work tree, and the payload is its tracked files" >&2
  exit 1
fi

member_version() {
  sed -n 's/^version *= *"\(.*\)"$/\1/p' "$1" | head -n 1
}
runtime_version=$(member_version "$source_dir/packages/agent-runtime/pyproject.toml")
orchestrator_version=$(member_version "$source_dir/packages/agent-orchestrator/pyproject.toml")
if [ "$runtime_version" != "$version" ] || [ "$orchestrator_version" != "$version" ]; then
  echo "refusing to build a mixed release: the tag is $release, the runtime is v${runtime_version:-unknown}" \
    "and the orchestrator is v${orchestrator_version:-unknown}" >&2
  echo "  bump the members to $version together, or tag the version they agree on" >&2
  exit 1
fi

if [ -e "$out_dir" ] && [ -n "$(ls -A "$out_dir" 2>/dev/null)" ]; then
  echo "refusing to build into $out_dir: it is not empty" >&2
  exit 1
fi

# Build in a staging directory and move it into place: a failed build leaves no half release.
staging=$(mktemp -d "${TMPDIR:-/tmp}/agent-runtime-release.XXXXXX")
trap 'rm -rf "$staging"' EXIT

python3 - "$source_dir" "$staging" "$release" "$version" <<'PY'
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile

source, staging, release, version = sys.argv[1:5]
listing = subprocess.run(
    ["git", "-C", source, "ls-files", "-z"], capture_output=True, check=True
).stdout.decode("utf-8")
names = sorted(name for name in listing.split("\0") if name)
prefix = "agent-runtime-%s" % release
payload = prefix + ".tar.gz"

# The installer asset names the release it belongs to; the payload carries the unstamped file.
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as archive:
    for name in names:
        path = os.path.join(source, name)
        if os.path.islink(path):
            info = tarfile.TarInfo("%s/%s" % (prefix, name))
            info.type = tarfile.SYMTYPE
            info.linkname = os.readlink(path)
            info.mode = 0o755
        elif os.path.isfile(path):
            info = tarfile.TarInfo("%s/%s" % (prefix, name))
            info.size = os.path.getsize(path)
            info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
        else:
            continue
        info.mtime = 0
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        if info.isreg():
            with open(path, "rb") as handle:
                archive.addfile(info, handle)
        else:
            archive.addfile(info)
with open(os.path.join(staging, payload), "wb") as target:
    with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as zipped:
        zipped.write(raw.getvalue())

with open(os.path.join(source, "install.sh"), encoding="utf-8") as handle:
    lines = handle.read().split("\n")
pins = [index for index, line in enumerate(lines) if line.startswith("release_pin=")]
if len(pins) != 1:
    sys.exit("install.sh must carry exactly one release_pin= line, it has %d" % len(pins))
lines[pins[0]] = "release_pin=%s" % release
with open(os.path.join(staging, "install.sh"), "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines))
os.chmod(os.path.join(staging, "install.sh"), 0o755)

manifest = {
    "contract": 1,
    "release": release,
    "platform": "any",
    "payload": payload,
    "prefix": prefix,
    "components": {"runtime": version, "orchestrator": version},
}
with open(os.path.join(staging, "release.json"), "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
    handle.write("\n")

with open(os.path.join(staging, "SHA256SUMS"), "w", encoding="utf-8") as handle:
    for asset in sorted(os.listdir(staging)):
        if asset == "SHA256SUMS":
            continue
        with open(os.path.join(staging, asset), "rb") as content:
            handle.write("%s  %s\n" % (hashlib.sha256(content.read()).hexdigest(), asset))
PY

mkdir -p "$out_dir"
for asset in "$staging"/*; do
  mv "$asset" "$out_dir/"
done
echo "built $release into $out_dir:"
ls "$out_dir"
