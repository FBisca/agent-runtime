"""Runtime-local worker metadata, profile decisions, assignment/session binding and usage.

Metadata lives under $XDG_STATE_HOME (default $HOME/.local/state) and is written atomically
with restrictive modes. CLI session logs are read only: bounded head reads for binding, and
`assignment_usage` for per-assignment token, call and cost figures.
"""

import json
import math
import os
import re
import subprocess
import tempfile
import time
import zlib
from collections import namedtuple
from datetime import datetime
from pathlib import Path

# The Codex profile a worker runs under; AGENT_RUNTIME_CODEX_PROFILE overrides the name.
DEFAULT_CODEX_PROFILE = "worker"
PROFILE_VARIABLE = "AGENT_RUNTIME_CODEX_PROFILE"
# Why the profile was or was not applied: the file is missing, the base config carries a legacy
# [profiles.*] table, or the environment turned the profile off.
APPLIED, MISSING_FILE, LEGACY_TABLE, DISABLED_BY_ENV = (
    "applied",
    "missing_file",
    "legacy_table",
    "disabled_by_env",
)
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


def _write_private(env, path, data, prefix):
    """Write data atomically to path: a 0600 file inside a 0700 directory. False when it cannot."""
    directory = path.parent
    if not _prepare_dir(state_root(env)) or not _prepare_dir(directory):
        return False
    handle, temp = tempfile.mkstemp(dir=str(directory), prefix=prefix)
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
        if not _write_private(env, path, data, ".%s." % worker):
            return None
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
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clean,
        )
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


SESSION_HEAD_BYTES = 256 * 1024
SESSION_CREATION_SLACK_MS = 2000
ASSIGNMENT_MARKER_RE = re.compile(r"\bassignment_id=([A-Za-z0-9][A-Za-z0-9_-]*)")
SESSION_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def claude_project_slug(cwd):
    """Claude Code names a project directory after its cwd, every non-alphanumeric character a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def pi_session_directory(env, cwd):
    """Pi keeps a cwd's sessions in <PI_CODING_AGENT_DIR or ~/.pi/agent>/sessions/--<cwd slug>--/."""
    home = env.get("HOME") or os.path.expanduser("~")
    raw = env.get("PI_CODING_AGENT_DIR") or ""
    if raw.startswith("~"):
        raw = home + raw[1:]
    agent = Path(raw) if raw else Path(home) / ".pi/agent"
    return agent / "sessions" / ("--%s--" % cwd.lstrip("/").replace("/", "-"))


def _message_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def _is_child_thread(meta):
    """True for a Codex thread that is not its session's root (a guardian review, for one)."""
    thread, session = meta.get("id"), meta.get("session_id")
    if isinstance(thread, str) and isinstance(session, str) and thread != session:
        return True
    return meta.get("thread_source") == "guardian_review"


def _session_match(path, metadata):
    """Match the first marked user prompt, skipping injected context and stopping at assistant output."""
    try:
        info = path.stat()
        created_ms = getattr(info, "st_birthtime", info.st_ctime) * 1000
        if created_ms < metadata["spawned_ms"] - SESSION_CREATION_SLACK_MS:
            return None
        with path.open("rb") as stream:
            head = stream.read(SESSION_HEAD_BYTES)
        session_id, session_cwd = None, None
        for line in head.splitlines():
            record = json.loads(line)
            if not isinstance(record, dict):
                return None
            if metadata["cli"] == "codex":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                if record.get("type") == "session_meta":
                    if _is_child_thread(payload):
                        return None  # a child quotes its parent's markers; only the root binds
                    session_id = payload.get("session_id") or payload.get("id")
                    session_cwd = payload.get("cwd")
                    continue
                if (
                    record.get("type") == "response_item" and payload.get("role") == "assistant"
                ) or (record.get("type") == "event_msg" and payload.get("type") == "agent_message"):
                    return None
                if record.get("type") != "response_item" or payload.get("role") != "user":
                    continue
                content = payload.get("content")
            elif metadata["cli"] == "pi":
                if record.get("type") == "session":
                    session_id, session_cwd = record.get("id"), record.get("cwd")
                    continue
                message = record.get("message")
                if record.get("type") != "message" or not isinstance(message, dict):
                    continue
                if message.get("role") == "assistant":
                    return None
                if message.get("role") != "user":
                    continue
                content = message.get("content")
            else:
                if record.get("type") == "assistant":
                    return None
                if record.get("type") != "user" or record.get("isMeta"):
                    continue
                message = record.get("message")
                content = message.get("content") if isinstance(message, dict) else None
            if session_cwd and metadata.get("cwd") and session_cwd != metadata["cwd"]:
                return None
            assignments = ASSIGNMENT_MARKER_RE.findall(_message_text(content))
            if (
                not assignments
            ):  # CLI context and literal assignment_id=<id> placeholders are not prompts.
                continue
            if metadata["assignment_id"] not in assignments:
                return None
            if metadata["cli"] == "codex":
                if not session_id:
                    uuid = SESSION_UUID_RE.search(path.stem)
                    session_id = uuid.group() if uuid else path.stem.rsplit("-", 1)[-1]
            elif metadata["cli"] == "claude":
                session_id = path.stem  # a Pi session's id is its header's, not its file name
            return session_id if isinstance(session_id, str) and session_id else None
    except (OSError, ValueError, TypeError, RecursionError):
        return None
    return None


def resolve_session_log(env, worker):
    """Resolve an assignment's CLI log to a Path, cache its identity, or return None.

    Candidates must be newly created and carry the assignment marker in their first marked
    user prompt, before assistant output. Cached paths are validated too, since worker names
    can be reused. Reads are bounded and failures never make session metrics a precondition
    for runtime operations.
    """
    try:
        metadata = read_worker_metadata(env, worker)
        if not metadata or metadata.get("cli") not in ("codex", "claude", "pi"):
            return None
        assignment = metadata.get("assignment_id")
        spawned = metadata.get("spawned_ms")
        if not isinstance(assignment, str) or not assignment:
            return None
        if not isinstance(spawned, (int, float)) or not math.isfinite(spawned):
            return None

        def bind(path):
            session_id = _session_match(path, metadata)
            if session_id is None:
                return None
            if metadata.get("session_id") != session_id or metadata.get("session_log") != str(path):
                cached = dict(metadata, session_id=session_id, session_log=str(path))
                cached.pop("worker", None)
                write_worker_metadata(env, worker, **cached)
            return path

        cached_path = metadata.get("session_log")
        if isinstance(cached_path, str) and cached_path:
            result = bind(Path(cached_path))
            if result is not None:
                return result
        if metadata["cli"] == "codex":
            candidates = (codex_home(env) / "sessions").glob("*/*/*/rollout-*.jsonl")
        else:
            cwd = metadata.get("cwd")
            if not isinstance(cwd, str) or not cwd:
                return None
            if metadata["cli"] == "pi":
                candidates = pi_session_directory(env, cwd).glob("*.jsonl")
            else:
                home = env.get("HOME") or os.path.expanduser("~")
                directory = Path(home) / ".claude/projects" / claude_project_slug(cwd)
                current = env.get("CLAUDE_CODE_SESSION_ID")
                if current and Path(current).name == current:
                    result = bind(directory / (current + ".jsonl"))
                    if result is not None:
                        return result
                candidates = directory.glob("*.jsonl")
        for path in candidates:
            result = bind(path)
            if result is not None:
                return result
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return None
    return None


# --- Per-assignment usage ---------------------------------------------------------------------
#
# A worker's session log is one file the CLI keeps appending to; `resume` is a prompt sent into it,
# so each assignment is a slice of the log, from the prompt whose last marker is its id to the next
# such prompt. The measurement keeps, per log, the consumed byte offset and the running counters in a
# sidecar, so a later call reads only what was appended. Only numbers, ids and paths are kept.

SIDECAR_VERSION = 1
FINGERPRINT_BYTES = (
    256  # the consumed region's head and tail are checksummed to spot a replaced log
)
READ_BLOCK_BYTES = 4 * 1024 * 1024
CLAUDE_TOKEN_KEYS = (
    ("input_tokens", "input_tokens"),
    ("cache_read_input_tokens", "cached_input_tokens"),
    ("cache_creation_input_tokens", "cache_write_tokens"),
    ("output_tokens", "output_tokens"),
)
CODEX_CALL_TYPES = ("function_call", "custom_tool_call")
PI_TOKEN_KEYS = (
    ("input", "input_tokens"),
    ("cacheRead", "cached_input_tokens"),
    ("cacheWrite", "cache_write_tokens"),
    ("output", "output_tokens"),
    ("reasoning", "reasoning_tokens"),
)


class _Reset(Exception):
    """A tracked log shrank, changed identity or vanished: the counters no longer describe it."""


def usage_sidecar_path(env, worker):
    """The incremental-parse state of one worker, beside its metadata."""
    if not WORKER_NAME_RE.fullmatch(worker):
        raise ValueError("invalid worker name %r" % worker)
    return worktrees_dir(env) / ("%s.usage.json" % worker)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _parse_time(value):
    """Seconds since the epoch of an ISO timestamp, or None."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _last_marker(text):
    markers = ASSIGNMENT_MARKER_RE.findall(text)
    return markers[-1] if markers else None


def _new_state(cli, root):
    return {
        "version": SIDECAR_VERSION,
        "cli": cli,
        "root": str(root),
        "current": None,
        "marks": [],  # [timestamp, assignment] of every marked prompt, for crediting other threads
        "cost_total": None,
        "totals": {},
        "files": {},
    }


def _slot(state, assignment):
    return state["totals"].setdefault(assignment, {"model_calls": 0, "tool_calls": 0})


def _add(slot, key, value):
    """Sum a figure the record carries; a figure no record carries never appears."""
    if _number(value):
        slot[key] = slot.get(key, 0) + value


def _credited(state, role, at):
    """The assignment a record belongs to: the active slice in the root log, else by time."""
    if role == "root":
        return state["current"]
    best = None
    for mark_at, assignment in state["marks"]:
        if at is not None and mark_at <= at and (best is None or mark_at >= best[0]):
            best = (mark_at, assignment)
    return best[1] if best else None


def _start_slice(state, entry, at, assignment):
    state["current"] = assignment
    state["marks"].append([at if at is not None else entry.get("at", 0.0), assignment])
    _slot(state, assignment)


def _claude_record(state, entry, role, record):
    at = _parse_time(record.get("timestamp"))
    if at is not None:
        entry["at"] = at
    else:
        at = entry.get("at")
    kind = record.get("type")
    message = record.get("message")
    if kind == "user" and role == "root" and isinstance(message, dict):
        content = message.get("content")
        if (
            record.get("isMeta")
            or record.get("isCompactSummary")
            or record.get("toolUseResult") is not None
            or (
                isinstance(content, list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            )
        ):
            return
        assignment = _last_marker(_message_text(content))
        if assignment:
            _start_slice(state, entry, at, assignment)
    elif kind == "cost-state" and role == "root":
        total = record.get("totalCostUSD")
        if not _number(total):
            return
        before = state["cost_total"]
        # The counter only grows within a session; a smaller total is a fresh counter.
        increase = total - before if before is not None and total >= before else total
        state["cost_total"] = total
        assignment = state["current"]
        if assignment:
            _add(_slot(state, assignment), "cost_usd", increase)
    elif kind == "assistant" and isinstance(message, dict):
        if message.get("model") == "<synthetic>":
            return
        assignment = _credited(state, role, at)
        if not assignment:
            return
        slot = _slot(state, assignment)
        message_id = message.get("id")
        repeat = False
        if isinstance(message_id, str) and message_id:
            seen = entry.setdefault("seen", [])
            if "seen_set" not in entry:  # rebuilt from the sidecar's list, once per call
                entry["seen_set"] = set(seen)
            repeat = message_id in entry["seen_set"]
            if not repeat:
                entry["seen_set"].add(message_id)
                seen.append(message_id)
        if not repeat:
            slot["model_calls"] += 1
            fields = message.get("usage")
            if isinstance(fields, dict):
                for source, target in CLAUDE_TOKEN_KEYS:
                    _add(slot, target, fields.get(source))
        content = message.get("content")
        if isinstance(content, list):
            slot["tool_calls"] += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )


def _codex_record(state, entry, role, record):
    at = _parse_time(record.get("timestamp"))
    if at is not None:
        entry["at"] = at
    else:
        at = entry.get("at")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return
    kind = record.get("type")
    if kind == "response_item":
        if payload.get("type") == "message" and payload.get("role") == "user":
            if role != "root":
                return  # a child thread's prompt quotes its parent's markers and starts no slice
            assignment = _last_marker(_message_text(payload.get("content")))
            if assignment:
                _start_slice(state, entry, at, assignment)
        elif payload.get("type") in CODEX_CALL_TYPES:
            assignment = _credited(state, role, at)
            if assignment:
                _slot(state, assignment)["tool_calls"] += 1
    elif kind == "event_msg" and payload.get("type") == "token_count":
        info = payload.get("info")
        total = info.get("total_token_usage") if isinstance(info, dict) else None
        if not isinstance(total, dict):
            return
        total = {key: value for key, value in total.items() if _number(value)}
        before = entry.get("total") or {}
        if total == before:
            return  # the CLI repeats a total it already reported
        if any(total[key] < before[key] for key in total if key in before):
            before = {}  # the cumulative total restarted
        entry["total"] = total
        assignment = _credited(state, role, at)
        if not assignment:
            return
        slot = _slot(state, assignment)
        slot["model_calls"] += 1
        change = {key: value - before.get(key, 0) for key, value in total.items()}
        if "input_tokens" in change:
            # Codex's input includes its cached part.
            _add(
                slot, "input_tokens", change["input_tokens"] - change.get("cached_input_tokens", 0)
            )
        for source, target in (
            ("cached_input_tokens", "cached_input_tokens"),
            ("cache_write_input_tokens", "cache_write_tokens"),
            ("output_tokens", "output_tokens"),
            ("reasoning_output_tokens", "reasoning_tokens"),
        ):
            _add(slot, target, change.get(source))


def _pi_record(state, entry, role, record):
    """A Pi message: a user message starts a slice, an assistant message is one model call."""
    message = record.get("message")
    if record.get("type") != "message" or not isinstance(message, dict):
        return
    at = _parse_time(record.get("timestamp"))
    if at is not None:
        entry["at"] = at
    else:
        at = entry.get("at")
    if message.get("role") == "user":
        assignment = _last_marker(_message_text(message.get("content")))
        if assignment:
            _start_slice(state, entry, at, assignment)
    elif message.get("role") == "assistant":
        assignment = _credited(state, role, at)
        if not assignment:
            return
        slot = _slot(state, assignment)
        slot["model_calls"] += 1
        fields = message.get("usage")
        if isinstance(fields, dict):
            for source, target in PI_TOKEN_KEYS:
                _add(slot, target, fields.get(source))
            cost = fields.get("cost")
            if isinstance(cost, dict):
                _add(slot, "cost_usd", cost.get("total"))
        content = message.get("content")
        if isinstance(content, list):
            slot["tool_calls"] += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") == "toolCall"
            )


def _no_side_logs(env, root, metadata):
    """Pi keeps no child log: a session is one file."""
    return []


def _crc(stream, start, length):
    stream.seek(start)
    return zlib.crc32(stream.read(length))


def _fingerprint(stream, offset):
    span = min(offset, FINGERPRINT_BYTES)
    return [_crc(stream, 0, span), _crc(stream, offset - span, span)]


def _check_tracked(path, entry):
    """Raise _Reset unless the log is still the file, and still holds the bytes, already consumed."""
    try:
        info = os.stat(path)
        offset = entry["offset"]
        if info.st_ino != entry["ino"] or info.st_size < offset:
            raise _Reset()
        with open(path, "rb") as stream:
            if _fingerprint(stream, offset) != entry["check"]:
                raise _Reset()
    except FileNotFoundError:
        raise _Reset() from None


def _consume(path, entry, handle):
    """Feed every complete new line to handle; the offset moves past them, never past a partial line."""
    with open(path, "rb") as stream:
        info = os.fstat(stream.fileno())
        position = entry.get("offset", 0)
        stream.seek(position)
        pending = b""
        while True:
            block = stream.read(READ_BLOCK_BYTES)
            if not block:
                break
            pending += block
            cut = pending.rfind(b"\n") + 1
            if not cut:
                continue
            lines, pending = pending[:cut].split(b"\n")[:-1], pending[cut:]
            position += cut
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    handle(record)
        entry.update(offset=position, ino=info.st_ino, check=_fingerprint(stream, position))


def _first_record(path):
    """The first line of a log as a dict, or None."""
    try:
        with open(path, "rb") as stream:
            record = json.loads(stream.readline(SESSION_HEAD_BYTES))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _claude_side_logs(env, root, metadata):
    """The Task subagents' own transcripts: <session>/subagents/*.jsonl beside the session's."""
    return sorted((root.parent / root.stem / "subagents").glob("*.jsonl"))


def _codex_side_logs(env, root, metadata):
    """The session's child threads, each in its own rollout file whose totals start from zero."""
    first = _first_record(root)
    meta = first.get("payload") if first and first.get("type") == "session_meta" else None
    session = (meta.get("session_id") or meta.get("id")) if isinstance(meta, dict) else None
    if not isinstance(session, str):
        return []
    floor = (metadata.get("spawned_ms") or 0) / 1000 - SESSION_CREATION_SLACK_MS / 1000
    found = []
    for path in (codex_home(env) / "sessions").glob("*/*/*/rollout-*.jsonl"):
        try:
            if path == root or path.stat().st_mtime < floor:
                continue
        except OSError:
            continue
        head = _first_record(path)
        payload = head.get("payload") if head and head.get("type") == "session_meta" else None
        if (
            isinstance(payload, dict)
            and payload.get("session_id") == session
            and payload.get("id") != session
        ):
            found.append(path)
    return sorted(found)


HARNESSES = {
    "claude": (_claude_record, _claude_side_logs),
    "codex": (_codex_record, _codex_side_logs),
    "pi": (_pi_record, _no_side_logs),
}


def _advance(env, state, root, metadata):
    """Read what each log gained since the state was written; raises _Reset when one changed."""
    record_handler, side_logs = HARNESSES[state["cli"]]
    for path, entry in state["files"].items():
        _check_tracked(path, entry)
    entry = state["files"].setdefault(str(root), {"role": "root"})
    _consume(root, entry, lambda record: record_handler(state, entry, "root", record))
    for path in side_logs(env, root, metadata):
        entry = state["files"].setdefault(str(path), {"role": "side"})
        try:
            _consume(
                path,
                entry,
                lambda record, entry=entry: record_handler(state, entry, "side", record),
            )
        except OSError:
            if "offset" not in entry:
                del state["files"][str(path)]  # never read: nothing of it is in the counters
            continue  # an unreadable side log leaves its own figures out, not the worker's


def _load_state(path, cli, root):
    try:
        with open(path, encoding="utf-8") as stream:
            state = json.load(stream)
    except (OSError, ValueError):
        return None
    if (
        not isinstance(state, dict)
        or state.get("version") != SIDECAR_VERSION
        or state.get("cli") != cli
        or state.get("root") != str(root)
    ):
        return None
    return state


def _result(state):
    """The per-assignment dicts, with Claude's cost-state rule: a slice before a later checkpoint is 0."""
    order = list(state["totals"])
    result = {}
    for index, assignment in enumerate(order):
        slot = dict(state["totals"][assignment])
        if state["cli"] == "claude" and "cost_usd" not in slot:
            if any("cost_usd" in state["totals"][later] for later in order[index + 1 :]):
                slot["cost_usd"] = 0
        if "cost_usd" in slot:
            slot["cost_usd"] = round(slot["cost_usd"], 9)
        result[assignment] = slot
    return result


def assignment_usage(env, worker):
    """{assignment_id: usage dict} measured from the worker's session log; {} when nothing is.

    Fails open: a missing worker, log or sidecar, an unreadable or truncated log, never raises. The
    sidecar `<worker>.usage.json` makes a later call read only the bytes appended since the last.
    """
    try:
        root = resolve_session_log(env, worker)
        metadata = read_worker_metadata(env, worker)
        if root is None or not metadata or metadata.get("cli") not in HARNESSES:
            return {}
        cli = metadata["cli"]
        sidecar = usage_sidecar_path(env, worker)
        state = _load_state(sidecar, cli, root)
        for attempt in (0, 1):
            if state is None:
                state = _new_state(cli, root)
            try:
                _advance(env, state, root, metadata)
                break
            except (_Reset, KeyError, TypeError, ValueError):
                state = None  # a log that changed under the counters: read it again from the start
                if attempt:
                    return {}
        try:
            _write_private(
                env,
                sidecar,
                json.dumps(_serializable(state), separators=(",", ":")) + "\n",
                ".%s.usage." % worker,
            )
        except (OSError, ValueError, TypeError):
            pass  # the next call re-reads from the offsets it last kept, or from the start
        return _result(state)
    except Exception:  # measuring usage is never a precondition for the runtime
        return {}


def _serializable(state):
    """The state without in-memory helpers."""
    files = {
        path: {key: value for key, value in entry.items() if key != "seen_set"}
        for path, entry in state["files"].items()
    }
    return dict(state, files=files)
