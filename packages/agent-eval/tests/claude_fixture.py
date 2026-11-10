"""A throwaway Claude config directory and an evals home, shared by the capture tests."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

RESUMED = (
    "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa"  # a self-resuming job's session, and CHAIN's parent
)
CHAIN = "22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb"  # a job that resumed RESUMED; subagents a1 and a2
OTHER = "33333333-cccc-4ccc-8ccc-cccccccccccc"  # no job; subagent o1
SOLO = "44444444-dddd-4ddd-8ddd-dddddddddddd"  # no job, no subagents
FORKED = "55555555-eeee-4eee-8eee-eeeeeeeeeeee"  # a job forked from RESUMED (forkParentSessionId)
PROJECT = "-work-app"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def job(claude: Path, short: str, **state: str) -> None:
    write(claude / "jobs" / short / "state.json", json.dumps({"state": "done", **state}))


def build_claude(claude: Path) -> None:
    """The fixture tree: a self-resuming job, a resume, a fork, and sessions no job links."""
    project = claude / "projects" / PROJECT
    for sid in (RESUMED, CHAIN, OTHER, SOLO, FORKED):
        write(project / f"{sid}.jsonl", json.dumps({"sessionId": sid, "type": "user"}) + "\n")
    job(
        claude,
        "11111111",
        sessionId=RESUMED,
        resumeSessionId=RESUMED,
        createdAt="2026-11-08T10:00:00Z",
    )
    job(
        claude,
        "22222222",
        sessionId=CHAIN,
        resumeSessionId=RESUMED,
        createdAt="2026-11-09T10:00:00Z",
    )
    job(
        claude,
        "55555555",
        sessionId=FORKED,
        resumeSessionId=FORKED,
        forkParentSessionId=RESUMED,
        createdAt="2026-11-09T11:00:00Z",
    )
    write(project / CHAIN / "subagents" / "agent-a1.jsonl", '{"agent":"a1"}\n')
    write(project / CHAIN / "subagents" / "agent-a1.meta.json", '{"agentType":"general-purpose"}')
    write(project / CHAIN / "subagents" / "agent-a2.jsonl", '{"agent":"a2"}\n')
    write(project / CHAIN / "subagents" / "agent-a2.meta.json", '{"agentType":"Explore"}')
    write(project / CHAIN / "tool-results" / "r1.txt", "the full output of a tool\n")
    write(project / RESUMED / "tool-results" / "r0.txt", "an older output\n")
    write(project / FORKED / "tool-results" / "r5.txt", "a fork's output\n")
    write(project / OTHER / "subagents" / "agent-o1.jsonl", '{"agent":"o1"}\n')
    write(project / OTHER / "subagents" / "agent-o1.meta.json", '{"agentType":"Plan"}')


def git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()


def make_repo(repo: Path) -> tuple[str, str]:
    """A two-commit repository; returns (base sha, head sha)."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "first")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a.txt").write_text("one\ntwo\n")
    git(repo, "commit", "-qam", "second")
    return base, git(repo, "rev-parse", "HEAD")


class CaptureCase(unittest.TestCase):
    """Every test gets its own Claude directory and evals home; the CLI runs as a subprocess."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.claude = self.tmp / "claude"
        build_claude(self.claude)
        self.home = self.tmp / "evals"

    def run_cli(
        self, *args: str, home: Path | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        full = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(self.claude),
            "AGENT_EVALS_HOME": str(home or self.home),
            **(env or {}),
        }
        return subprocess.run(
            [sys.executable, "-m", "agent_eval", *args], env=full, capture_output=True, text=True
        )

    def capture(
        self, *args: str, home: Path | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self.run_cli("capture", *args, home=home, env=env)

    def case(self, slug: str) -> Path:
        return self.home / "cases" / slug

    def manifest(self, slug: str) -> dict:
        return json.loads((self.case(slug) / "manifest.json").read_text())

    def entries(self, slug: str) -> dict[str, dict]:
        return {s["id"]: s for s in self.manifest(slug)["sessions"]}

    def transcript(self, name: str) -> Path:
        return self.claude / "projects" / PROJECT / name

    def tree(self) -> list[str]:
        if not self.home.exists():
            return []
        return sorted(str(p.relative_to(self.tmp)) for p in self.home.rglob("*"))

    def age(self, name: str, days: float) -> None:
        stamp = time.time() - days * 86400
        os.utime(self.transcript(name), (stamp, stamp))

    @staticmethod
    def mode(path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)
