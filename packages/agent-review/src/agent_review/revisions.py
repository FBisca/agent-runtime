"""The two Git revisions a review is made over, pinned so the user's git config cannot change the change."""

import os
import subprocess
from pathlib import Path

from agent_review.errors import ReviewError


def git_env() -> dict[str, str]:
    """The environment git runs in: no inherited GIT_* and none of the user's config files.

    A global config can rename prefixes, colour the output or run an external differ; none of that may
    leak into the change a review is made over, and no pager may wait on a terminal.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        XDG_CONFIG_HOME=os.devnull,
        GIT_PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "-c", "color.ui=false", "-c", "color.diff=false", *args],
            env=git_env(),
            capture_output=True,
        )
    except FileNotFoundError:
        raise ReviewError("git is not installed, so no revision can be read") from None


def resolve(repo: Path, base: str, head: str) -> tuple[str, str]:
    """The commit shas `base` and `head` name.

    A revision that does not resolve, or that is spelled like an option, is refused naming it: a review
    screens commits, not paths (D33), and a refusal writes nothing.
    """
    shas = []
    for rev in (base, head):
        if rev.startswith("-"):
            raise ReviewError(f"revision {rev!r} looks like an option; name a commit")
        found = run_git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        if found.returncode != 0:
            raise ReviewError(
                f"cannot resolve revision {rev!r} in {repo}; name a commit this repository has"
            )
        shas.append(found.stdout.decode().strip())
    return shas[0], shas[1]


def diff(repo: Path, base_sha: str, head_sha: str) -> str:
    """`git diff base..head`, independent of the user's diff config, as the frozen change (D33)."""
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
        raise ReviewError(f"git diff failed in {repo}: {done.stderr.decode().strip()}")
    return done.stdout.decode("utf-8", "replace")
