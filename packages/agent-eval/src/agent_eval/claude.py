"""Claude's files: job states that link sessions, transcripts, subagents, tool results.

Read-only. A session id read from a job state is checked before it becomes part of a path.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from agent_eval.errors import CaptureError
from agent_eval.store import SLUG


@dataclass(frozen=True)
class Edge:
    parent: str
    child: str
    kind: str  # "continuation" (the job resumed `parent`) or "fork" (forkParentSessionId)
    created: str


@dataclass(frozen=True)
class Aux:
    """A file beside a transcript: a subagent's meta, or a session's tool result."""

    kind: str  # "meta" | "tool-result"
    source: Path
    name: str  # the path below its owner's directory in the case


@dataclass
class Transcript:
    id: str
    source: Path
    aux: list[Aux]


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def regular(path: Path) -> bool:
    """A real file: a link is never followed, so a planted one cannot pull in a file from elsewhere."""
    return path.is_file() and not path.is_symlink()


def jobs(cdir: Path) -> list[dict]:
    """Every job's state.json (unreadable or malformed ones skipped), in directory order."""
    found = []
    for path in sorted((cdir / "jobs").glob("*/state.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(state, dict):
            found.append(state)
    return found


def session_id(value: object) -> str | None:
    return value if isinstance(value, str) and SLUG.match(value) else None


def edges(all_jobs: list[dict]) -> list[Edge]:
    """The links the job states hold, oldest job first.

    A job whose `sessionId` equals its `resumeSessionId` resumed itself: it links nothing, and so
    cannot shadow a later job that resumed the same session. A fork parent counts as the parent
    even when the job also names it as the session it resumed.
    """
    found = []
    for state in all_jobs:
        child = session_id(state.get("sessionId"))
        if child is None:
            continue
        stamp = state.get("createdAt")
        created = stamp if isinstance(stamp, str) else ""
        fork = session_id(state.get("forkParentSessionId"))
        resumed = session_id(state.get("resumeSessionId"))
        if fork and fork != child:
            found.append(Edge(fork, child, "fork", created))
        if resumed and resumed != child and resumed != fork:
            found.append(Edge(resumed, child, "continuation", created))
    return sorted(found, key=lambda edge: edge.created)


def created_at(all_jobs: list[dict]) -> dict[str, str]:
    """When each session's own job started: it orders roots, oldest first."""
    out: dict[str, str] = {}
    for state in all_jobs:
        sid, created = session_id(state.get("sessionId")), state.get("createdAt")
        if sid and isinstance(created, str):
            out[sid] = min(out.get(sid, created), created)
    return out


def chain(all_jobs: list[dict], start: str) -> list[tuple[str, Edge | None]]:
    """Every session reachable from `start` through resume and fork links, roots first.

    The walk is undirected, so naming a fork also finds its parent, and the parent's other
    continuations. Each entry is the session and the first edge that leads into it.
    """
    links = edges(all_jobs)
    component, todo = [start], [start]
    while todo:
        current = todo.pop(0)
        for edge in links:
            for other in (
                (edge.child,)
                if edge.parent == current
                else (edge.parent,)
                if edge.child == current
                else ()
            ):
                if other not in component:
                    component.append(other)
                    todo.append(other)
    incoming: dict[str, Edge] = {}
    for edge in links:
        if edge.parent in component and edge.child not in incoming:
            incoming[edge.child] = edge
    started = created_at(all_jobs)
    roots = sorted(
        (n for n in component if n not in incoming), key=lambda n: (started.get(n, "~"), n)
    )
    ordered: list[tuple[str, Edge | None]] = []
    seen: set[str] = set()
    pending = list(roots)
    while len(seen) < len(component):
        if not pending:  # a cycle of links has no root: start from its first member
            pending.append(next(n for n in component if n not in seen))
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)
        ordered.append((current, incoming.get(current)))
        pending.extend(edge.child for edge in links if edge.parent == current)
    return ordered


def known_ids(cdir: Path) -> set[str]:
    return {p.stem for p in (cdir / "projects").glob("*/*.jsonl")}


def resolve(cdir: Path, ref: str) -> str:
    """A full session id, or a unique prefix of one, read from the transcript files."""
    ids = sorted(i for i in known_ids(cdir) if i.startswith(ref))
    if ref in ids:
        return ref
    if len(ids) > 1:
        raise CaptureError(f"session {ref!r} is ambiguous: {', '.join(ids)}", 2)
    if not ids:
        raise CaptureError(f"session {ref!r} not found under {cdir / 'projects'}")
    return ids[0]


def transcript(cdir: Path, sid: str) -> Transcript | None:
    """The transcript of `sid` with its aux files; None when its file is gone.

    One id under two project directories is refused: which one is the run cannot be guessed.
    """
    found = sorted(p for p in (cdir / "projects").glob(f"*/{sid}.jsonl") if regular(p))
    if len(found) > 1:
        raise CaptureError(
            f"session {sid} is found under two Claude project directories: "
            f"{' and '.join(str(p) for p in found)}; remove one, or capture from a copy of "
            "the Claude directory that has one"
        )
    if not found:
        return None
    return Transcript(sid, found[0], tool_results(found[0].parent / sid))


def tool_results(session_dir: Path) -> list[Aux]:
    root = session_dir / "tool-results"
    if not root.is_dir() or root.is_symlink():
        return []
    found = []
    for folder, dirs, names in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(names):
            path = Path(folder) / name
            if regular(path):
                found.append(Aux("tool-result", path, path.relative_to(root).as_posix()))
    return found


def subagents(main: Transcript) -> list[Transcript]:
    """`<session>/subagents/agent-*.jsonl`, each with its `.meta.json` when there is one."""
    folder = main.source.parent / main.id / "subagents"
    found = []
    for path in sorted(folder.glob("agent-*.jsonl")):
        if not regular(path):
            continue
        meta = path.with_suffix(".meta.json")
        aux = [Aux("meta", meta, meta.name)] if regular(meta) else []
        found.append(Transcript(path.stem, path, aux))
    return found
