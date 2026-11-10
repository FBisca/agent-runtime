"""The code change of a run: `git diff base..head`, pinned so the user's git config cannot change it."""

import os
import subprocess
from pathlib import Path

from agent_eval.errors import CaptureError


def git_env() -> dict[str, str]:
    """The environment git runs in: no inherited GIT_* and none of the user's config files.

    The system and global config (and the XDG attributes file) can rename prefixes, colour the
    output, or run an external differ; none of that may leak into a stored patch.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", XDG_CONFIG_HOME=os.devnull)
    return env


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "color.ui=false", "-c", "color.diff=false", *args],
            env=git_env(),
            capture_output=True,
        )
    except FileNotFoundError:
        raise CaptureError("git is not installed, so --repo cannot be read") from None


def resolve(repo: str, base: str, head: str) -> tuple[Path, str, str]:
    """The repository path and the commit shas `base` and `head` name."""
    path = Path(repo).resolve()
    shas = []
    for rev in (base, head):
        if rev.startswith("-"):
            raise CaptureError(f"revision {rev!r} looks like an option; name a commit", 2)
        found = run_git(path, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        if found.returncode != 0:
            raise CaptureError(f"cannot resolve revision {rev!r} in {path}")
        shas.append(found.stdout.decode().strip())
    return path, shas[0], shas[1]


def diff(repo: Path, base_sha: str, head_sha: str) -> bytes:
    done = run_git(
        repo,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        f"{base_sha}..{head_sha}",
    )
    if done.returncode != 0:
        raise CaptureError(f"git diff failed in {repo}: {done.stderr.decode().strip()}")
    return done.stdout
