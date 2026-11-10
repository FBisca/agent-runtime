"""Codex's rollout files: `<CODEX_HOME>/sessions/**/rollout-*.jsonl`, grouped by session.

Read-only. A session may span several rollout files; what ties them together is the `id` of their
first record, a `session_meta`, never the file name or the day folder it sits in.
"""

import os
from pathlib import Path

from agent_eval.sessions import Aux, Found, Transcript, epoch, first_record, head, regular, text_of
from agent_eval.store import SLUG

# What Codex injects before the user's own prompt: project instructions and the environment.
INJECTED = ("# AGENTS.md instructions", "<")


def codex_dir() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def prompt_of(path: Path) -> str:
    for record in head(path):
        payload = record.get("payload")
        if record.get("type") != "response_item" or not isinstance(payload, dict):
            continue
        if payload.get("type") != "message" or payload.get("role") != "user":
            continue
        text = text_of(payload.get("content")).strip()
        if text and not text.startswith(INJECTED):
            return text
    return ""


def scan(directory: Path) -> list[Found]:
    """Every session under `sessions/`: its earliest file is its transcript, the others its rollouts."""
    by_id: dict[str, list[tuple[float, Path, str]]] = {}
    for path in sorted((directory / "sessions").rglob("rollout-*.jsonl")):
        if not regular(path):
            continue
        first = first_record(path) or {}
        meta = first.get("payload") if first.get("type") == "session_meta" else None
        if not isinstance(meta, dict):
            continue
        sid, cwd = meta.get("id"), meta.get("cwd")
        started = epoch(meta.get("timestamp"))
        if started is None:
            started = epoch(first.get("timestamp"))
        if (
            not (isinstance(sid, str) and SLUG.match(sid) and isinstance(cwd, str))
            or started is None
        ):
            continue
        by_id.setdefault(sid, []).append((started, path, cwd))
    found = []
    for sid, files in by_id.items():
        files.sort(key=lambda item: (item[0], str(item[1])))
        started, main, cwd = files[0]
        rest = [Aux("rollout", path, path.name) for _, path, _ in files[1:]]
        found.append(
            Found(sid, "codex", cwd, started, Transcript(sid, main, rest), prompt_of(main))
        )
    return found
