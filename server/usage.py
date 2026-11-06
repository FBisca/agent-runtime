"""Runtime-local worker state: the Codex worker profile decision and the per-worker metadata.

Everything here lives under $XDG_STATE_HOME (default $HOME/.local/state) and is written atomically
with restrictive modes. Nothing outside the state root and the repo is touched.
"""

import json
import os
import re
import subprocess
import tempfile
import time
from collections import namedtuple
from pathlib import Path

# The Codex profile a worker runs under; AGENT_RUNTIME_CODEX_PROFILE overrides the name.
DEFAULT_CODEX_PROFILE = "worker"
PROFILE_VARIABLE = "AGENT_RUNTIME_CODEX_PROFILE"
# Why the profile was or was not applied: the file is missing, the base config carries a legacy
# [profiles.*] table, or the environment turned the profile off.
APPLIED, MISSING_FILE, LEGACY_TABLE, DISABLED_BY_ENV = ("applied", "missing_file", "legacy_table",
                                                        "disabled_by_env")
# A profile name names a file inside CODEX_HOME: keep it to a plain file name.
PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
# Worker names are terminal names; this mirrors the runtime's own NAME_RE so a name can never
# become a path.
WORKER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
GIT_HASH_RE = re.compile(r"[0-9a-f]{40,64}")
LEGACY_TABLE_RE = re.compile(r"^\s*\[\s*profiles\s*\.[^\]]*\]", re.M)
# Git's own environment can point the command at another repository.
GIT_ENVIRONMENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR")

CodexProfile = namedtuple("CodexProfile", "name applied reason")


def state_root(env):
    """The runtime's own state directory: $XDG_STATE_HOME/agent-runtime."""
    home = env.get("HOME") or os.path.expanduser("~")
    root = env.get("XDG_STATE_HOME") or str(Path(home) / ".local/state")
    return Path(root) / "agent-runtime"


def worktrees_dir(env):
    """Where per-worker metadata lives."""
    return state_root(env) / "worktrees"


def worker_metadata_path(env, worker):
    """The metadata file of one worker. The name is a terminal name: nothing else becomes a path."""
    if not WORKER_NAME_RE.fullmatch(worker):
        raise ValueError("invalid worker name %r" % worker)
    return worktrees_dir(env) / ("%s.json" % worker)


def _prepare_dir(path):
    """Create path and any missing parent with mode 0700; refuse a link or a foreign directory."""
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        return False
    path.chmod(0o700)
    return True


def write_worker_metadata(env, worker, **fields):
    """Write one worker's metadata atomically: a 0600 file inside a 0700 directory.

    Never raises: metadata is a record, not a precondition for the worker.
    """
    try:
        path = worker_metadata_path(env, worker)
        payload = dict(fields)
        payload["worker"] = worker
        data = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
        directory = path.parent
        if not _prepare_dir(state_root(env)) or not _prepare_dir(directory):
            return None
        handle, temp = tempfile.mkstemp(dir=str(directory), prefix=".%s." % worker)
        try:
            os.fchmod(handle, 0o600)
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                handle = None
                stream.write(data)
            try:
                os.replace(temp, path)
            except OSError:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
                raise
        finally:
            if handle is not None:
                os.close(handle)
    except (OSError, ValueError, TypeError):
        return None
    return path


def read_worker_metadata(env, worker):
    """The worker's metadata, or None when it is missing, invalid or unreadable."""
    try:
        with worker_metadata_path(env, worker).open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def base_commit(cwd, env=None, timeout=5):
    """The commit the worker starts from; None when it cannot be read."""
    if not cwd or not os.path.isdir(cwd):
        return None
    clean = dict(os.environ if env is None else env)
    for key in GIT_ENVIRONMENT:
        clean.pop(key, None)  # git's own environment can point the command at another repository
    try:
        proc = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=timeout, env=clean)
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip() if proc.returncode == 0 else ""
    return value if GIT_HASH_RE.fullmatch(value) else None


def codex_home(env):
    """$CODEX_HOME, defaulting to ~/.codex."""
    home = env.get("HOME") or os.path.expanduser("~")
    raw = env.get("CODEX_HOME") or ""
    if raw.startswith("~"):
        raw = home + raw[1:]
    return Path(raw) if raw else Path(home) / ".codex"


def legacy_profiles_table(path):
    """True when a config carries a legacy [profiles.*] table, which makes `-p <name>` fail outright."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        tomllib = None
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
        except ValueError:
            data = None
        if data is not None:
            profiles = data.get("profiles")
            return isinstance(profiles, dict) and bool(profiles)
    return bool(LEGACY_TABLE_RE.search(text))


def resolve_codex_profile(env):
    """The Codex profile a worker may run under: its name, whether it is usable, and why not.

    The name defaults to `worker`; AGENT_RUNTIME_CODEX_PROFILE overrides it, and an empty or
    unusable override turns the profile off. The profile is used only when its file exists and the
    base config carries no legacy [profiles.*] table.
    """
    override = env.get(PROFILE_VARIABLE)
    if override is not None and not override.strip():
        return CodexProfile(None, False, DISABLED_BY_ENV)
    name = override.strip() if override else DEFAULT_CODEX_PROFILE
    if not PROFILE_NAME_RE.fullmatch(name):
        return CodexProfile(None, False, DISABLED_BY_ENV)  # a name that is not a plain file name
    directory = codex_home(env)
    if not (directory / ("%s.config.toml" % name)).is_file():
        return CodexProfile(name, False, MISSING_FILE)
    if legacy_profiles_table(directory / "config.toml"):
        return CodexProfile(name, False, LEGACY_TABLE)
    return CodexProfile(name, True, APPLIED)


def now_ms():
    """Wall clock in milliseconds."""
    return int(time.time() * 1000)
