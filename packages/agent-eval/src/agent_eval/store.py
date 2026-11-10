"""Where a case lives and how it is written: private to the user, outside every git working tree."""

import datetime
import gzip
import hashlib
import os
import re
import subprocess
from pathlib import Path

from agent_eval.errors import CaptureError

# A slug becomes a directory name under `<home>/cases`, so it can never carry a separator or a leading dot.
SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHUNK = 1 << 20


def home() -> Path:
    return Path(os.environ.get("AGENT_EVALS_HOME") or Path.home() / ".local/state/agent-evals")


def inside_git_tree(path: Path) -> bool:
    """True when git says `path` is in a working tree or in a `.git` directory.

    Git decides, not the presence of a `.git` entry: a stray empty `.git` above the home is not a
    repository. A repository git refuses to open for its owner (`dubious ownership`) still counts as
    one, so a case never lands in it. Without git installed, a `.git` entry on the way up counts.
    """
    start = next(p for p in (path, *path.parents) if p.exists())
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        found = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--is-inside-work-tree", "--is-inside-git-dir"],
            env=env,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return any((candidate / ".git").exists() for candidate in (path, *path.parents))
    if found.returncode != 0:
        return "dubious ownership" in found.stderr
    return "true" in found.stdout.split()


def make_private_dirs(path: Path) -> None:
    """`mkdir -p` where every directory this creates is 0700 whatever the umask."""
    missing = [p for p in (path, *path.parents) if not p.exists()]
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            continue
        os.chmod(directory, 0o700)


def write_file(path: Path, data: bytes) -> None:
    make_private_dirs(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)


def write_gzipped(source: Path, dest: Path) -> tuple[str, int]:
    """Store `source` gzipped at `dest`; returns the sha256 and the byte size of the original.

    Streamed, so a transcript of any size costs a chunk of memory; the digest is of the bytes this
    pass read, so the manifest always matches what decompressing gives back.
    """
    make_private_dirs(dest.parent)
    digest, size = hashlib.sha256(), 0
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as packed:
        with open(source, "rb") as original:
            while chunk := original.read(CHUNK):
                digest.update(chunk)
                size += len(chunk)
                packed.write(chunk)
    os.chmod(dest, 0o600)
    return digest.hexdigest(), size


def check_slug(slug: str) -> str:
    if not SLUG.match(slug):
        raise CaptureError(
            f"--case {slug!r} is not a slug: use letters, digits, '.', '_' and '-', "
            "starting with a letter or a digit",
            2,
        )
    return slug


def iso(timestamp: float) -> str:
    moment = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
    return moment.isoformat(timespec="seconds")
