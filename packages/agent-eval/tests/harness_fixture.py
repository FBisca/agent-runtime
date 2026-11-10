"""Throwaway Claude, Codex and Pi session files and an orchestrate run dir, for the run-capture tests."""

import json
from pathlib import Path

from claude_fixture import write

ASSIGNMENT = "e5b3c96ccdd74683a1110e73236ee901"
PROMPT = "Read task.md and follow it.\n\nInclude assignment_id=%s on your first runtime call."


def lines(records: list[dict]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def claude_session(
    claude: Path, project: str, sid: str, cwd: str, at: str, prompt: str = "do the task"
) -> Path:
    """A Claude transcript: bookkeeping records without a cwd, then the first prompt."""
    return write(
        claude / "projects" / project / f"{sid}.jsonl",
        lines(
            [
                {"type": "last-prompt"},
                {
                    "type": "user",
                    "message": {"role": "user", "content": prompt},
                    "timestamp": at,
                    "cwd": cwd,
                    "sessionId": sid,
                },
                {"type": "assistant", "timestamp": at, "cwd": "/elsewhere", "sessionId": sid},
            ]
        ),
    )


def codex_rollout(
    codex: Path, day: str, stamp: str, sid: str, cwd: str, at: str, prompt: str | None = None
) -> Path:
    """One rollout file: `session_meta` first, an injected AGENTS.md context, then the prompt."""
    meta = {
        "type": "session_meta",
        "payload": {"session_id": sid, "id": sid, "cwd": cwd, "timestamp": at},
    }
    context = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "# AGENTS.md instructions for /repo\n\nassignment_id=" + "0" * 32,
                }
            ],
        },
    }
    records = [meta, context]
    if prompt is not None:
        records.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            }
        )
    return write(codex / "sessions" / day / f"rollout-{stamp}-{sid}.jsonl", lines(records))


def pi_session(
    pi: Path, folder: str, name: str, sid: str, cwd: str, at: str, prompt: str | None = None
) -> Path:
    """A Pi session: the first record is the session header; later records carry ids of their own."""
    records = [
        {"type": "session", "version": 3, "id": sid, "timestamp": at, "cwd": cwd},
        {"type": "model_change", "id": "575ca184", "parentId": None, "timestamp": at, "cwd": "/no"},
    ]
    if prompt is not None:
        records.append(
            {
                "type": "message",
                "id": "49bc6c64",
                "parentId": "575ca184",
                "timestamp": at,
                "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
            }
        )
    return write(pi / "sessions" / folder / f"{name}.jsonl", lines(records))


def orchestrate_run(state: Path, slug: str, tickets: dict[str, str], first: str, last: str) -> Path:
    """A run dir: `events.jsonl` spanning [first, last] and one `<task>/task.md` per ticket worktree."""
    run = state / slug
    write(
        run / "events.jsonl",
        lines(
            [
                {"at": first, "kind": "start"},
                {"at": "2026-11-10T11:00:00+00:00", "kind": "idle"},
                {"at": last, "kind": "stop"},
            ]
        ),
    )
    write(run / "contract.md", "# Contract\n")
    for task, checkout in tickets.items():
        write(
            run / task / "task.md",
            f"Contract: x\n\n# {task}\n\nCheckout: {checkout} on branch feat/{task} (new, from origin/main).\n",
        )
    return run
