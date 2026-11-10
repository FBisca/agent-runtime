"""What every harness reader hands to capture: a transcript with the files beside it, and a way to
read the first records of a session file without loading it."""

import datetime
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

HEAD_RECORDS = 400  # a session's first prompt is within its first records; the rest is not read


@dataclass(frozen=True)
class Aux:
    """A file beside a transcript: a subagent's meta, a session's tool result, a later rollout."""

    kind: str  # "meta" | "tool-result" | "rollout"
    source: Path
    name: str  # the path below its owner's directory in the case


@dataclass
class Transcript:
    id: str
    source: Path
    aux: list[Aux]


@dataclass
class Found:
    """A session discovered by where it ran, with what decides whether it belongs to a run."""

    id: str
    harness: str  # "claude" | "codex" | "pi"
    cwd: str
    started: float  # epoch seconds of the session's first record
    transcript: Transcript
    prompt: str = ""


def regular(path: Path) -> bool:
    """A real file: a link is never followed, so a planted one cannot pull in a file from elsewhere."""
    return path.is_file() and not path.is_symlink()


def head(path: Path) -> Iterator[dict]:
    """The first records of a JSONL file that parse as objects; an unreadable file yields none."""
    try:
        with open(path, encoding="utf-8") as handle:
            for number, line in enumerate(handle):
                if number >= HEAD_RECORDS:
                    return
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except (OSError, UnicodeDecodeError):
        return


def first_record(path: Path) -> dict | None:
    """The file's first line as an object, or None: a later record is never taken for it."""
    try:
        with open(path, encoding="utf-8") as handle:
            record = json.loads(handle.readline())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def epoch(stamp: object) -> float | None:
    """An ISO-8601 time as epoch seconds; one without a zone is UTC."""
    if not isinstance(stamp, str):
        return None
    try:
        moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def text_of(content: object) -> str:
    """The text of a message body: a string, or the `text` of its blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict)]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


ASSIGNMENT = re.compile(r"assignment[_ -]?id\W{1,4}([0-9a-fA-F]{32})(?![0-9a-fA-F])")


def assignment_id(prompt: str) -> str | None:
    found = ASSIGNMENT.search(prompt)
    return found.group(1).lower() if found else None
