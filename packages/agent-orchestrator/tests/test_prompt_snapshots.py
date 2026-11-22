"""Every brief the driver hands an agent, rendered from one fixed fixture and compared with a committed file.

A prompt change is reviewed as a diff of `tests/snapshots/<brief>[-variant].md`. Rewrite the files with
`UPDATE_SNAPSHOTS=1 uv run pytest packages/agent-orchestrator/tests/test_prompt_snapshots.py`, then read the diff.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_orchestrator import driver
from agent_orchestrator.driver import Driver
from agent_orchestrator.driver.review import rereview_text, review_brief

pytestmark = pytest.mark.integration

SNAPSHOTS = Path(__file__).parent / "snapshots"
STAMP = 1_700_000_000  # 2023-11-14T22:13:20Z

BLOCKING = {
    "id": "R1",
    "kind": "blocking",
    "source": "review",
    "file": "src/a/one.py",
    "line": 12,
    "quote": "while True:",
    "claim": "the retry loop never ends",
    "rule": "A failure surfaces as an error a caller can act on.",
}
PLAIN = {
    "id": "R2",
    "kind": "blocking",
    "source": "review",
    "file": "src/a/two.py",
    "line": 3,
    "quote": "cache = {}",
    "claim": "the cache is never cleared",
}
CI = {
    "id": "CI1",
    "kind": "blocking",
    "source": "ci",
    "check": "lint",
    "excerpt": "E501 line too long",
    "exit": 1,
    "command": "ruff check",
    "log_path": "/logs/ci.log",
}
CONFLICT = dict(CI, source="conflict", check="merge", excerpt="CONFLICT in src/a/one.py")


def assert_snapshot(name, text, root):
    """Compare with the committed file; the temp root is written as `<ROOT>` so the file is stable."""
    for form in sorted({str(root), str(root.resolve())}, key=len, reverse=True):
        text = text.replace(form, "<ROOT>")
    path = SNAPSHOTS / (name + ".md")
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == text, "snapshot %s differs" % path.name


class World:
    """A checkout, a run directory and a driver, all under one temp root."""

    def __init__(self, root, monkeypatch, skill=True):
        self.root = root
        self.checkout = root / "work"
        (self.checkout / "src" / "a").mkdir(parents=True)
        (self.checkout / "src" / "a" / "one.py").write_text("one")
        (self.checkout / "src" / "a" / "two.py").write_text("two")
        self.run_dir = root / "run"
        self.run_dir.mkdir()
        skill_file = root / "skill" / "SKILL.md"
        skill_file.parent.mkdir()
        skill_file.write_text("skill")
        (skill_file.parent / "rules.md").write_text("rules")
        monkeypatch.setattr(driver, "tdd_skill_path", lambda: str(skill_file) if skill else None)
        self.driver = object.__new__(Driver)
        self.driver.run_dir = self.run_dir
        self.driver.plan_path = root / "plan.md"
        self.driver.repo = root / "repo"
        (self.driver.repo / ".orchestrate").mkdir(parents=True)
        self.driver.args = SimpleNamespace(base="origin/main", gate=[])
        self.driver.hand_over = lambda *args, **kwargs: (None, "assignment-1")
        self.driver.approved_digest = lambda: "0123456789abcdef0123"

    def packet(self, checks=("uv run pytest -m unit",), **more):
        return {
            "task_id": "t1",
            "unit": "t1",
            "worker": "demo-t1",
            "checkout": str(self.checkout),
            "owns": ["src/a/", "docs/note.md"],
            "checks": list(checks),
            **more,
        }

    def task(self, **more):
        return {
            "packet": self.packet(),
            "event": {"reason": "the worker asked which flag to use"},
            "checkout_at_claim": {"branch": "feat/demo-t1"},
            **more,
        }


@pytest.fixture
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def test_task(world, tmp_path):
    ticket = {"#": "T1", "What it delivers": "a retry loop that ends"}
    scenarios = ["- **S1** WHEN it fails THEN it stops", "- **S2** WHEN it works THEN it returns"]
    packet = world.packet(["uv run ruff check", "uv run pytest src/a/"])
    text = world.driver.task_text(None, ticket, packet, scenarios, world.run_dir / "t1")
    assert_snapshot("task", text, tmp_path)


def test_task_without_checks_with_a_full_suite_and_no_tdd_skill(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, skill=False)
    ticket = {"#": "T2", "What it delivers": "a quiet log"}
    packet = world.packet([], full="uv run pytest -n auto")
    scenarios = ["- **S1** WHEN x THEN y"]
    text = world.driver.task_text(None, ticket, packet, scenarios, world.run_dir / "t1")
    assert_snapshot("task-no-checks", text, tmp_path)


def folder_plan(world, brief=True):
    folder = world.root / "plan-folder"
    folder.mkdir()
    (folder / "plan.yaml").write_text("schema: plan/2")
    if brief:
        (folder / "brief.md").write_text("brief")
    world.driver.plan_path = folder / "plan.yaml"


@pytest.mark.spec("Ticket dispatch")
def test_task_names_the_brief_of_a_folder_plan(world, tmp_path):
    folder_plan(world)
    ticket = {"#": "T1", "What it delivers": "a retry loop that ends"}
    packet = world.packet(["uv run ruff check"])
    text = world.driver.task_text(None, ticket, packet, ["- **S1** WHEN x THEN y"], world.run_dir)
    assert_snapshot("task-folder-plan", text, tmp_path)


@pytest.mark.spec("Ticket dispatch")
def test_task_of_a_folder_plan_without_a_brief_names_no_brief(world):
    folder_plan(world, brief=False)
    ticket = {"#": "T1", "What it delivers": "a retry loop that ends"}
    text = world.driver.task_text(None, ticket, world.packet(), ["- **S1** x"], world.run_dir)
    assert "brief.md" not in text and "Decisions and Seams" not in text


def test_task_amended_note(world, tmp_path):
    note = world.driver._amended_note(["- Paths: src/a/ -> src/b/", "- Size: small -> big"])
    assert_snapshot("amended-note", note, tmp_path)


def reviewer_world(tmp_path, monkeypatch, rules, rulebook=False):
    world = World(tmp_path, monkeypatch)
    if rules:
        (world.checkout / "AGENTS.md").write_text("rules")
        (world.checkout / "CLAUDE.md").write_text("rules")
    if rulebook:
        (world.driver.repo / ".orchestrate" / "config.yaml").write_text(
            "rulebook: docs/rulebook.md\n"
        )
        (world.checkout / "docs").mkdir()
        (world.checkout / "docs" / "rulebook.md").write_text("rules")
    task = {"report": {"head": "def5678", "task_id": "t1", "status": "COMPLETE"}}
    world.driver.book = SimpleNamespace(task=lambda _: task)
    return world


def test_review_first(tmp_path, monkeypatch):
    world = reviewer_world(tmp_path, monkeypatch, rules=False)
    assert_snapshot("review", review_brief(world.driver, world.packet()), tmp_path)


@pytest.mark.spec("Ticket dispatch")
def test_review_names_the_brief_of_a_folder_plan(tmp_path, monkeypatch):
    world = reviewer_world(tmp_path, monkeypatch, rules=False)
    folder_plan(world)
    assert_snapshot("review-folder-plan", review_brief(world.driver, world.packet()), tmp_path)


def test_review_with_a_rulebook_and_rules_files(tmp_path, monkeypatch):
    world = reviewer_world(tmp_path, monkeypatch, rules=True, rulebook=True)
    assert_snapshot("review-rulebook", review_brief(world.driver, world.packet()), tmp_path)


def test_rereview_after_a_block(tmp_path, monkeypatch):
    world = reviewer_world(tmp_path, monkeypatch, rules=False)
    delta = {
        "from": "abc1234",
        "findings": str(world.run_dir / "t1r1" / "findings.json"),
        "blocked": True,
    }
    assert_snapshot(
        "rereview",
        rereview_text(world.driver, world.packet(), delta, world.run_dir / "t1-review" / "task.md"),
        tmp_path,
    )


def test_rereview_after_a_pass(tmp_path, monkeypatch):
    world = reviewer_world(tmp_path, monkeypatch, rules=False)
    text = rereview_text(
        world.driver,
        world.packet(),
        {"from": "abc1234", "blocked": False},
        world.run_dir / "t1-review" / "task.md",
    )
    assert_snapshot("rereview-after-pass", text, tmp_path)


def test_answer(world, tmp_path):
    decision = {"reason": "continue", "answer": "use the second flag"}
    assert_snapshot("answer", world.driver.answer_text(world.task(), decision), tmp_path)


def test_answer_with_open_findings_and_no_checks(world, tmp_path):
    task = world.task(fix={"items": [BLOCKING, PLAIN]})
    task["packet"]["checks"] = []
    text = world.driver.answer_text(task, {"reason": "continue", "answer": "use the second flag"})
    assert_snapshot("answer-open-findings", text, tmp_path)


def test_redo_with_an_answer(world, tmp_path):
    redo = {"reason": "start over", "answer": "use the second flag"}
    text = world.driver.redo_text(world.task(), "t1d1", world.run_dir / "t1d1", redo)
    assert_snapshot("redo", text, tmp_path)


def test_redo_without_an_answer_or_checks(world, tmp_path):
    task = world.task()
    task["packet"]["checks"] = []
    text = world.driver.redo_text(task, "t1d1", world.run_dir / "t1d1", {"reason": "start over"})
    assert_snapshot("redo-bare", text, tmp_path)


def test_fix_with_review_and_ci_findings_and_a_lead_answer(world, tmp_path):
    findings = {"source": "review", "attempt": 2, "items": [BLOCKING, PLAIN, CI]}
    task = world.task(fix={"answer": "keep the public name"})
    text = world.driver.fix_text(task, "t1r2", world.run_dir / "t1r2", findings)
    assert_snapshot("fix", text, tmp_path)


def test_fix_plain(world, tmp_path):
    findings = {"source": "ci", "attempt": 1, "items": [CI]}
    task = world.task(fix={})
    task["packet"]["checks"] = []
    text = world.driver.fix_text(task, "t1r1", world.run_dir / "t1r1", findings)
    assert_snapshot("fix-plain", text, tmp_path)


def test_fix_of_a_conflict_is_the_rebase_brief(world, tmp_path):
    findings = {"source": "conflict", "attempt": 1, "items": [CONFLICT]}
    task, new_dir = world.task(fix={}), world.run_dir / "t1r1"
    text = world.driver.fix_text(task, "t1r1", new_dir, findings)
    assert text == world.driver.rebase_text(task, "t1r1", new_dir, findings)
    assert_snapshot("rebase", text, tmp_path)


@pytest.mark.spec("The report contract matches Report")
def test_the_contract_says_how_to_ask_the_lead_and_what_red_is():
    from agent_orchestrator.driver.ticket_dispatch import CONTRACT

    assert "plain message" in CONTRACT and "BLOCKED report with `blocker`" in CONTRACT
    assert "minimal compiling skeleton" in CONTRACT and "assertion failure" in CONTRACT
