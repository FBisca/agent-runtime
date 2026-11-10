"""An orchestrate run: its state directory `<state>/<slug>/`, the worktrees of its tickets and its window.

Read-only. The run directory is self-contained: `events.jsonl` says when the run was alive and every
ticket's `task.md` names the checkout its worker was given.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from agent_eval import claude, codex, pi
from agent_eval.errors import CaptureError
from agent_eval.sessions import Found, epoch, regular
from agent_eval.store import check_slug

DEFAULT_STATE = "~/.local/state/orchestrate"
CHECKOUT = re.compile(r"^Checkout:\s+(.+?)\s+on branch\b", re.MULTILINE)


@dataclass
class Run:
    slug: str
    directory: Path
    start: float
    end: float
    worktrees: set[str]
    files: list[Path]  # regular files below the run directory, links not followed


def real(path: str) -> str:
    """A path as the harnesses record it, whatever symlinks (`/tmp`) lead there."""
    return os.path.realpath(path)


def load(slug: str, state_dir: str | None) -> Run:
    check_slug(slug)
    root = Path(state_dir or DEFAULT_STATE).expanduser()
    directory = root / slug
    if not directory.is_dir() or directory.is_symlink():
        raise CaptureError(f"orchestrate run {slug!r} not found under {root}")
    stamps = []
    events = directory / "events.jsonl"
    try:
        for line in events.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            at = epoch(record.get("at")) if isinstance(record, dict) else None
            if at is not None:
                stamps.append(at)
    except (OSError, UnicodeDecodeError):
        pass
    if not stamps:
        raise CaptureError(
            f"orchestrate run {slug!r} has no events with an `at` time in {events}: "
            "the run window is unknown"
        )
    worktrees = set()
    files = []
    for folder, dirs, names in os.walk(directory, followlinks=False):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            if not regular(path):
                continue
            files.append(path)
            if name == "task.md":
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                worktrees.update(real(m) for m in CHECKOUT.findall(text))
    return Run(slug, directory, min(stamps), max(stamps), worktrees, files)


def workers(run: Run) -> list[Found]:
    """Every Claude, Codex or Pi session whose cwd is a ticket worktree and which started in the window."""
    everything = claude.scan(claude.claude_dir())
    everything += codex.scan(codex.codex_dir())
    everything += pi.scan(pi.pi_dir())
    chosen: dict[str, Found] = {}
    for found in everything:
        if real(found.cwd) not in run.worktrees or not run.start <= found.started <= run.end:
            continue
        other = chosen.get(found.id)
        if other and other.harness != found.harness:
            raise CaptureError(
                f"session {found.id} is found under two harnesses: "
                f"{other.transcript.source} and {found.transcript.source}"
            )
        chosen.setdefault(found.id, found)
    return sorted(chosen.values(), key=lambda found: (found.started, found.id))
