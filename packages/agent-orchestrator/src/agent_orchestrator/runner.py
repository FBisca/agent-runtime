"""The whole-suite runner: where `checks.full.run` runs once per verify pass, on the pinned default-branch commit.

The command runs under `bash -euo pipefail` in a disposable detached worktree at the commit, with a temporary HOME
and XDG_STATE_HOME, the real cache directories and no virtualenv on PATH, so the suite sees the commit and not the
driver's environment. Stdlib only.
"""

import os
import shutil
import signal
import subprocess
from pathlib import Path

GIT_TIMEOUT = 120

# Names a tool reads its caches from. They are passed through (or defaulted to the real home's) so `uv run` in the
# worktree builds its own venv from the warm cache instead of the network.
CACHE_NAMES = (
    "XDG_CACHE_HOME",
    "UV_CACHE_DIR",
    "UV_PYTHON_INSTALL_DIR",
    "PIP_CACHE_DIR",
    "npm_config_cache",
    "CARGO_HOME",
    "RUSTUP_HOME",
    "GOCACHE",
    "GOMODCACHE",
    "PRE_COMMIT_HOME",
)


class RunnerError(RuntimeError):
    """The runner could not set a run up (no such commit, no git); never the suite's own failure."""


def git(*args, cwd=None, check=True):
    done = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )
    if check and done.returncode:
        raise RunnerError("git %s failed: %s" % (args[0], done.stderr.strip()[-300:]))
    return done


def is_venv_bin(entry, virtual_env):
    """A PATH entry that belongs to a virtualenv: the active one, or any directory beside a pyvenv.cfg."""
    if not entry:
        return False
    path = Path(entry)
    if virtual_env and (path == Path(virtual_env, "bin") or path.is_relative_to(virtual_env)):
        return True
    return (path.parent / "pyvenv.cfg").is_file()


def clean_path(path, virtual_env):
    return os.pathsep.join(
        entry for entry in (path or "").split(os.pathsep) if not is_venv_bin(entry, virtual_env)
    )


def real_defaults(home):
    """Cache locations a tool would use under the real HOME, for the names the caller did not set."""
    home = Path(home)
    found = {"XDG_CACHE_HOME": home / ".cache"}
    for name, candidate in (
        ("PIP_CACHE_DIR", home / "Library" / "Caches" / "pip"),
        ("UV_PYTHON_INSTALL_DIR", home / ".local" / "share" / "uv" / "python"),
        ("CARGO_HOME", home / ".cargo"),
        ("RUSTUP_HOME", home / ".rustup"),
        ("npm_config_cache", home / ".npm"),
    ):
        if candidate.is_dir():
            found[name] = candidate
    return {name: str(path) for name, path in found.items()}


def suite_env(base, scratch):
    """The environment the whole suite runs in: `base` without its virtualenv, a scratch HOME and state."""
    home = base.get("HOME") or os.path.expanduser("~")
    env = {"PATH": clean_path(base.get("PATH", ""), base.get("VIRTUAL_ENV"))}
    for name in ("LANG", "LC_ALL", "TERM", "TMPDIR"):
        if name in base:
            env[name] = base[name]
    env.update(real_defaults(home))
    env.update({name: base[name] for name in CACHE_NAMES if name in base})
    # Last, so nothing passed through can point the suite's HOME or state at the real ones.
    env["HOME"] = str(scratch / "home")
    env["XDG_STATE_HOME"] = str(scratch / "state")
    for name in ("HOME", "XDG_STATE_HOME"):
        os.makedirs(env[name], exist_ok=True)
    return env


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def execute(check, cwd, env, timeout, log_path):
    """(exit code or None, timed out) of `check` under bash -euo pipefail in its own process group."""
    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            ["bash", "-euo", "pipefail", "-c", check],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_group(process)
            process.wait()
            code, timed_out = None, True
        else:
            kill_group(process)  # a check's background children must not outlive it
    return code, timed_out


def worktree(repo, commit, scratch, name):
    path = scratch / name
    git("-C", str(repo), "worktree", "add", "--detach", "--quiet", str(path), commit)
    return path


def drop_worktree(repo, path):
    git("-C", str(repo), "worktree", "remove", "--force", str(path), check=False)
    shutil.rmtree(path, ignore_errors=True)
    git("-C", str(repo), "worktree", "prune", check=False)
