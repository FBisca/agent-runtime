"""The cases on this machine and the label stored beside each: `<home>/cases/<slug>/`.

Read-only, and only of a case's own record: `manifest.json` and `labels/<kind>.json`. A transcript is
never opened, so listing a case costs what its directory does, whatever the run behind it weighed.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from agent_eval.label import KINDS
from agent_eval.sessions import epoch
from agent_eval.store import SLUG, home


@dataclass(frozen=True)
class Row:
    """One case as the index shows it: what capture recorded, and what a judge said."""

    case: str
    captured: str
    sessions: int
    verdict: str | None = None
    score: float | None = None
    judge: str | None = None

    def as_dict(self) -> dict:
        return {
            "case": self.case,
            "captured": self.captured,
            "sessions": self.sessions,
            "verdict": self.verdict,
            "score": self.score,
            "judge": self.judge,
        }


def read_json(path: Path) -> object | None:
    """The file as JSON, or None: an index reports one broken file rather than failing the listing."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def case_dirs(root: Path) -> list[Path]:
    """Every directory under `cases/` that holds a real `manifest.json`, links not followed."""
    directory = root / "cases"
    if not directory.is_dir() or directory.is_symlink():
        return []
    found = []
    for path in sorted(directory.iterdir()):
        manifest = path / "manifest.json"
        if (
            path.is_dir()
            and not path.is_symlink()
            and manifest.is_file()
            and not manifest.is_symlink()
        ):
            found.append(path)
    return found


def label_of(directory: Path) -> tuple[str | None, float | None, str | None]:
    """The verdict, score and judging model of the case's label, the first kind that has one."""
    for kind in KINDS:
        data = read_json(directory / "labels" / f"{kind}.json")
        if not isinstance(data, dict):
            continue
        verdict, score = data.get("verdict"), data.get("score")
        judge = data.get("judge")
        model = judge.get("model") if isinstance(judge, dict) else None
        numeric = isinstance(score, (int, float)) and not isinstance(score, bool)
        return (
            verdict if isinstance(verdict, str) else None,
            score if numeric else None,  # type: ignore[return-value]
            model if isinstance(model, str) else None,
        )
    return None, None, None


def rows(root: Path) -> list[Row]:
    """Every case, newest first by its capture time, then by slug."""
    found = []
    for directory in case_dirs(root):
        data = read_json(directory / "manifest.json")
        if not isinstance(data, dict):
            continue
        name = data.get("case")
        slug = name if isinstance(name, str) and SLUG.match(name) else directory.name
        stamp = data.get("captured")
        captured = stamp if isinstance(stamp, str) else ""
        sessions = data.get("sessions")
        count = len(sessions) if isinstance(sessions, list) else 0
        verdict, score, judge = label_of(directory)
        found.append((epoch(captured) or 0.0, Row(slug, captured, count, verdict, score, judge)))
    found.sort(key=lambda pair: (-pair[0], pair[1].case))
    return [row for _, row in found]


def render(row: Row, width: int = 0) -> str:
    """One aligned line: a person reads this, and a script that wants exact fields asks for --json."""
    verdict = row.verdict or "-"
    score = f"{row.score:.2f}" if row.score is not None else "-"
    judge = row.judge or "-"
    return (
        f"{row.case:<{max(width, 1)}}  {row.captured[:10] or '-':<10}  {row.sessions:>3}  "
        f"{verdict:<10}  {score:>5}  {judge}"
    )


def listing(as_json: bool, root: Path | None = None) -> int:
    """Print the index; text by default, one JSON array with `as_json`."""
    found = rows(root or home())
    if as_json:
        print(json.dumps([row.as_dict() for row in found], indent=1))
    else:
        width = max((len(row.case) for row in found), default=0)
        for row in found:
            print(render(row, width))
    return 0
