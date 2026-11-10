"""Pi's session files: `<PI_CODING_AGENT_DIR>/sessions/<folder>/*.jsonl`.

Read-only. A session's id and cwd are those of the file's first record, its `session` header. The
folder name encodes the cwd lossily and starts with `--`: it is never read back, so a path with a
dash in it cannot be misread.
"""

import os
from pathlib import Path

from agent_eval.sessions import Found, Transcript, epoch, first_record, head, regular, text_of
from agent_eval.store import SLUG


def pi_dir() -> Path:
    return Path(os.environ.get("PI_CODING_AGENT_DIR") or Path.home() / ".pi/agent")


def prompt_of(path: Path) -> str:
    for record in head(path):
        message = record.get("message")
        if (
            record.get("type") == "message"
            and isinstance(message, dict)
            and message.get("role") == "user"
        ):
            text = text_of(message.get("content")).strip()
            if text:
                return text
    return ""


def scan(directory: Path) -> list[Found]:
    found = []
    for path in sorted((directory / "sessions").glob("*/*.jsonl")):
        if not regular(path):
            continue
        first = first_record(path)
        if not first or first.get("type") != "session":
            continue
        sid, cwd, started = first.get("id"), first.get("cwd"), epoch(first.get("timestamp"))
        if (
            isinstance(sid, str)
            and SLUG.match(sid)
            and isinstance(cwd, str)
            and started is not None
        ):
            found.append(Found(sid, "pi", cwd, started, Transcript(sid, path, []), prompt_of(path)))
    return found
