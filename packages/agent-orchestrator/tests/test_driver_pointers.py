"""The living pointer contract reaches the readers and its guard."""

import unittest
from pathlib import Path

import pytest
from test_gates import QUESTION, GateFixture

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]


def test_S8_spec_names_the_pointer_rule_and_its_guard():
    spec = (ROOT / "docs/specs/orchestrator-engine.md").read_text()
    requirement = next(line for line in spec.splitlines() if line.startswith("- **R172**"))
    for phrase in (
        "file that exists",
        "file the reader is asked to write",
        "number of files",
        "glob",
        "bare directory",
        "newest",
    ):
        assert phrase in requirement
    row = next(line for line in spec.splitlines() if line.startswith("| R172 |"))
    assert "test_S3_" in row
    import ast
    import re

    for citation in re.findall(r"`([^`]+::[^`]*test_[^`]+)`", row):
        file, *names = citation.split("::")
        nodes = ast.parse((ROOT / file).read_text()).body
        for name in names:
            match = next((node for node in nodes if getattr(node, "name", None) == name), None)
            assert match is not None, citation
            nodes = getattr(match, "body", [])


def test_S9_repository_instructions_name_the_rule(tmp_path):
    instructions = (ROOT / "AGENTS.md").read_text()
    paragraph = next(p for p in instructions.split("\n\n") if "R172" in p)
    for phrase in ("name the file", "find it", "nothing", "several", "test_driver_pointers.py"):
        assert phrase in paragraph.lower()
    from types import SimpleNamespace

    from agent_orchestrator.driver.review import review_brief

    rules = tmp_path / "AGENTS.md"
    rules.write_text(instructions)
    driver = SimpleNamespace(
        run_dir=tmp_path,
        plan_path=tmp_path / "plan.yaml",
        args=SimpleNamespace(base="main"),
        book=SimpleNamespace(task=lambda _: {}),
    )
    packet = {"checkout": str(tmp_path), "unit": "t1", "task_id": "t1"}
    brief = review_brief(driver, packet, tmp_path / "review.json")
    assert str(rules) in brief
    assert "R172" in rules.read_text()


def test_S7_unresolvable_pointers_are_refused(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from agent_orchestrator import driver

    notices = []
    reader = SimpleNamespace(warn_sender=lambda *args: notices.append(args))
    monkeypatch.setattr(driver, "payload_waits", lambda paths: any(Path(p).exists() for p in paths))
    expected = tmp_path / "report.json"
    other = tmp_path / "alternate.json"
    other.write_text("other")
    for alternatives in ((), (other,)):
        notices.clear()
        result = driver.Driver.read_payload(
            reader, "t1", "report", expected, Path.read_text, alternatives
        )
        assert result is None
        assert str(expected) in str(notices)
        assert "file" in str(notices).lower()
        assert "write" in str(notices).lower() or "repair" in str(notices).lower()
    expected.write_text("expected")
    notices.clear()
    assert (
        driver.Driver.read_payload(reader, "t1", "report", expected, Path.read_text, (other,))
        is None
    )
    assert str(expected) in str(notices) and str(other) in str(notices)
    expected.unlink()
    from agent_orchestrator.driver.review import review_brief

    book = SimpleNamespace(task=lambda _: {"report_path": str(expected), "report": {}})
    stub = SimpleNamespace(
        run_dir=tmp_path,
        plan_path=tmp_path / "plan.yaml",
        args=SimpleNamespace(base="main"),
        book=book,
    )
    packet = {"checkout": str(tmp_path), "unit": "t1", "task_id": "t1"}
    with pytest.raises(ValueError, match="report.json.*expected.*report"):
        review_brief(stub, packet, tmp_path / "review.json")
    monkeypatch.setenv(driver.TDD_SKILL_ENV, str(expected))
    ticket = {"#": "T1", "What it delivers": "work"}
    packet.update({"checks": [], "owns": [], "worker": "worker"})
    stub.plan_path = tmp_path / "plan.yaml"
    stub.args.base = "origin/main"
    with pytest.raises(ValueError, match="report.json.*expected.*(restore|choose)"):
        driver.Driver.task_text(stub, None, ticket, packet, [], tmp_path)


class PointerGuard:
    def __init__(self, checkout):
        self.checkout = checkout

    def invalid(self, token, content):
        import re

        token = token.rstrip(".:/")
        if any(c in token for c in "*?[]"):
            return token
        path = Path(token)
        if not path.is_absolute():
            path = self.checkout / token.split("::")[0]
        counted = re.search(r"\b\d+ files in\s+" + re.escape(token) + r"(?:\s|$|[.)])", content)
        written = re.search(
            r"\b(?:Report:|[Ww]rite(?: [^.\n]*? to)?|written to)\s+" + re.escape(token), content
        )
        if path.is_dir() and not counted:
            return token
        if not path.is_file() and not (counted or written):
            return token
        return None

    def violations(self, content):
        import re

        problems = []
        pattern = r"(?<![\w:/.-])/[^\s`\"',;()]+|(?<![\w/.*-])[\w.-]*\*[\w.-]+(?![\w*.-])|(?<![\w/.-])[\w-]+\*(?![\w/.*-])"
        for line in content.splitlines():
            match = re.search(r"\b(?:newest|latest|most recent)\b", line, re.I)
            if match:
                problems.append(match.group())
            for token in re.findall(pattern, line):
                invalid = self.invalid(token, content)
                if invalid:
                    problems.append(invalid)
        return problems


class PointerRun(GateFixture, unittest.TestCase):
    __test__ = False

    def runTest(self):
        (self.home / "checks.json").write_text('[[2, "", "gh is down"]]')
        self.waits(
            [
                self.worker_done(),
                self.worker_done("t1r1", tests=True),
                self.reviewer_done(self.review2(items=[QUESTION])),
                self.skipped(),
            ]
        )
        self.run_driver("--idle-minutes", "0")
        artifacts = list(self.run_dir.rglob("task.md"))
        assert {"t1", "t1r1", "t1-review", "t1a1"} <= {p.parent.name for p in artifacts}
        rendered = [(str(path), path.read_text()) for path in artifacts]
        rendered.extend(self.other_outputs())
        guard = PointerGuard(self.worktree)
        for artifact, content in rendered:
            assert not guard.violations(content), (artifact, guard.violations(content))
            for token in (
                str(self.run_dir / "unwritten.txt"),
                str(self.run_dir / "unwritten"),
                str(self.run_dir / "*.json"),
                "*.json",
                "report*",
                str(self.run_dir / "t1"),
                "newest",
            ):
                found = guard.violations(content + "\nRead " + token)
                assert token in found, (artifact, token, found)

    def handover_output(self):
        from contextlib import contextmanager
        from types import SimpleNamespace

        from agent_orchestrator.driver.spawn import Spawning

        @contextmanager
        def book():
            yield self.ledger()

        trail = (
            "",
            str(self.run_dir / "t1r1" / "findings.json"),
            str(self.run_dir / "t1r1" / "report.json"),
        )
        sender = Spawning(
            SimpleNamespace(
                run_dir=self.run_dir,
                args=SimpleNamespace(base="origin/main"),
                book=book,
                unit_trail=lambda unit: trail,
            )
        )
        return sender.handoff("old", "t1")

    def other_outputs(self):
        from types import SimpleNamespace

        from agent_orchestrator.driver import Driver

        current = self.task("t1r1")
        (self.run_dir / "t1r2").mkdir(exist_ok=True)
        (self.run_dir / "t1r2" / "findings.json").write_text("{}")
        stub = SimpleNamespace(run_dir=self.run_dir)
        rebase = Driver.rebase_text(stub, current, "t1r2", self.run_dir / "t1r2", {"items": []})
        current["event"] = {"reason": "needs an answer"}
        answer = Driver.answer_text(stub, current, {"reason": "continue", "answer": "continue"})
        retry = Driver.redo_text(
            stub, current, "t1r2", self.run_dir / "t1r2", {"reason": "continue"}
        )
        status = self.cli("status", str(self.plan), "--state-dir", str(self.state_dir))
        assert status.returncode == 0, status.stderr
        self.use_yaml_plan()
        verify = self.cli(
            "verify", str(self.plan), "--repo", str(self.repo), "--state-dir", str(self.state_dir)
        )
        assert verify.stdout, verify.stderr
        return [
            ("handover", self.handover_output()),
            ("rebase", rebase),
            ("answer", answer),
            ("retry", retry),
            ("status", status.stdout),
            ("verify", verify.stdout),
        ]


class TestRenderedPointers:
    def test_S3_rendered_instructions_name_their_files(self, tmp_path):
        from agent_orchestrator.driver.pointers import reference

        existing = tmp_path / "README"
        existing.write_text("read me")
        assert reference(tmp_path, "README", "read") == str(existing)
        assert reference(tmp_path, "newfile", "write") == str(tmp_path / "newfile")
        assert reference(tmp_path, "READ*", "pattern") == str(existing)
        guard = PointerGuard(tmp_path)
        assert not guard.violations("Write %s" % reference(tmp_path, "newfile", "write"))
        assert not guard.violations("Read %s" % reference(tmp_path, "README", "read"))
        for missing in ("missing", str(tmp_path / "missing")):
            with pytest.raises(ValueError, match="missing.*expected.*(restore|choose)"):
                reference(tmp_path, missing, "read")
        for invalid in ("*", "."):
            with pytest.raises(ValueError, match="expected"):
                reference(tmp_path, invalid, "read")
        with pytest.raises(ValueError, match="README.*expected.*(restore|choose)"):
            reference(tmp_path, ["README", "README"], "read")
        case = PointerRun()
        case.setUp()
        try:
            case.runTest()
        finally:
            case.doCleanups()
        archive_output(tmp_path)


def archive_output(tmp_path):
    from types import SimpleNamespace

    from agent_orchestrator.driver.archive import Archive

    plan = tmp_path / "plan"
    plan.write_text("plan")
    spec = tmp_path / "spec"
    spec.write_text("spec")
    (tmp_path / "archive").mkdir()
    calls = []

    def gh(argv, **kwargs):
        calls.append(argv)
        return 0, "https://example.com/o/r/pull/1", ""

    driver = SimpleNamespace(
        repo=tmp_path,
        run_dir=tmp_path,
        plan_path=plan,
        slug="demo",
        args=SimpleNamespace(base="origin/main"),
        layout=SimpleNamespace(is_folder=False, plan_place="plan"),
        plan_model=lambda: SimpleNamespace(spec="spec"),
        task=lambda _: {"packet": {"checkout": str(tmp_path), "worker": "demo-archive"}},
        pr_of=lambda _: None,
        gh=gh,
        mark=lambda *a, **k: None,
        log=lambda *a, **k: None,
    )
    archive = Archive(driver)
    archive.archive_missing = lambda: []
    archive._archive_mode = lambda: "promote"
    archive._archive_branch = lambda _: "commit"
    archive._archive_owns = lambda: ["spec"]
    archive.build_archive("archive")
    (call,) = calls
    body = call[call.index("--body") + 1]
    assert not PointerGuard(tmp_path).violations(body), body
    assert (tmp_path / "archive" / "report.json").is_file()
