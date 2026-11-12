"""Capture a Codex or Pi session by name, and every captured session's own cwd and assignment id
(agent-eval-named-capture/S1-S4)."""

import gzip

import pytest
from claude_fixture import SOLO, CaptureCase, write
from harness_fixture import (
    ASSIGNMENT,
    PROMPT,
    claude_session,
    codex_rollout,
    orchestrate_run,
    pi_session,
)

pytestmark = pytest.mark.integration

CODEX = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
PI = "cccccccc-3333-4333-8333-cccccccccccc"
PI2 = "cccccccc-9999-4999-8999-cccccccccccc"
SPLIT = "99999999-7777-4777-8777-999999999999"
WORKER = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
WT = "/work/demo.worktrees/demo-t1"
SLUG = "demo-run"
FIRST, LAST = "2026-11-10T10:00:00+00:00", "2026-11-10T12:00:00+00:00"
INSIDE = "2026-11-10T10:30:00.000Z"
OLD = "2026-01-01T10:00:00.000Z"
NEW = "2026-11-10T10:45:00.000Z"


class NamedCase(CaptureCase):
    def runTest(self) -> None:  # lets the case be built outside a collected TestCase
        pass

    def setUp(self) -> None:
        super().setUp()
        self.codex = self.tmp / "codex"
        self.pi = self.tmp / "pi"
        self.state = self.tmp / "orchestrate"

    def stored(self, slug: str, entry: dict) -> bytes:
        return gzip.decompress((self.case(slug) / entry["file"]).read_bytes())

    def run_capture(self, *args: str):
        return self.capture(
            "--orchestrate-run", SLUG, "--orchestrate-state-dir", str(self.state), *args
        )

    def make_run(self) -> None:
        orchestrate_run(self.state, SLUG, {"t1": WT}, FIRST, LAST)


@pytest.fixture
def c():
    case = NamedCase()
    case.setUp()
    yield case
    case.doCleanups()


def test_R228_a_named_pi_session_is_captured_with_its_harness_and_cwd(c):
    source = pi_session(c.pi, "--work-app--", "p", PI, "/work/my-app", OLD, "do it")
    result = c.capture("--session", PI)
    c.assertEqual(result.returncode, 0, result.stderr)
    entry = c.entries(PI)[PI]
    c.assertEqual(
        (entry["harness"], entry["role"], entry["parent"], entry["cwd"]),
        ("pi", "root", None, "/work/my-app"),
    )
    c.assertEqual(c.stored(PI, entry), source.read_bytes())


def test_R228_a_named_session_brings_no_subagent_and_nothing_else(c):
    pi_session(c.pi, "--work-app--", "p", PI, "/work/my-app", OLD, "do it")
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
    result = c.capture("--session", PI, "--session", CODEX)
    c.assertEqual(result.returncode, 0, result.stderr)
    entries = c.entries(PI)
    c.assertEqual(set(entries), {PI, CODEX})
    c.assertEqual(entries[PI]["files"], [])


def test_R228_a_named_codex_session_is_captured_with_its_later_rollouts(c):
    first = codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/app", OLD, "do it")
    later = codex_rollout(c.codex, "2026/11/11", "b", CODEX, "/work/app", NEW)
    result = c.capture("--session", CODEX)
    c.assertEqual(result.returncode, 0, result.stderr)
    entry = c.entries(CODEX)[CODEX]
    c.assertEqual(
        (entry["harness"], entry["role"], entry["parent"], entry["cwd"]),
        ("codex", "root", None, "/work/app"),
    )
    c.assertEqual(c.stored(CODEX, entry), first.read_bytes())
    c.assertEqual([f["kind"] for f in entry["files"]], ["rollout"])
    c.assertEqual(entry["files"][0]["source"], str(later))
    rollout = c.case(CODEX) / entry["files"][0]["file"]
    c.assertEqual(gzip.decompress(rollout.read_bytes()), later.read_bytes())


def test_R228_a_named_session_is_captured_under_a_unique_prefix_of_its_id(c):
    pi_session(c.pi, "--work-app--", "p", PI, "/work/app", OLD)
    c.assertEqual(c.capture("--session", "cccccccc-3").returncode, 0)
    c.assertEqual(c.entries(PI)[PI]["harness"], "pi")


def test_R228_a_named_codex_session_is_captured_under_a_unique_prefix_of_its_id(c):
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
    c.assertEqual(c.capture("--session", "bbbbbbbb-2").returncode, 0)
    c.assertEqual(c.entries(CODEX)[CODEX]["harness"], "codex")


def test_R228_the_capture_help_names_every_harness(c):
    result = c.capture("--help")
    c.assertEqual(result.returncode, 0, result.stderr)
    for harness in ("Claude", "Codex", "Pi"):
        c.assertIn(harness, result.stdout)


def test_R228_an_unknown_ref_is_refused_naming_every_harness_it_looked_in(c):
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
    result = c.capture("--session", "nosuchsession")
    c.assertEqual(result.returncode, 2)
    for place in (c.claude / "projects", c.codex / "sessions", c.pi / "sessions"):
        c.assertIn(str(place), result.stderr)
    c.assertEqual(c.tree(), [])


def test_R228_an_ambiguous_prefix_inside_one_harness_is_refused_naming_both(c):
    pi_session(c.pi, "--a--", "p1", PI, "/work/app", OLD)
    pi_session(c.pi, "--a--", "p2", PI2, "/work/app", OLD)
    result = c.capture("--session", "cccccccc")
    c.assertEqual(result.returncode, 2)
    c.assertIn("ambiguous", result.stderr)
    c.assertIn(f"pi {PI}", result.stderr)
    c.assertIn(f"pi {PI2}", result.stderr)
    c.assertEqual(c.tree(), [])


def test_R228_a_prefix_that_matches_two_harnesses_is_refused_naming_both(c):
    pi_session(c.pi, "--a--", "p1", PI, "/work/app", OLD)
    other = "cccccccc-5555-4555-8555-cccccccccccc"
    codex_rollout(c.codex, "2026/11/10", "a", other, "/w", OLD)
    result = c.capture("--session", "cccccccc")
    c.assertEqual(result.returncode, 2)
    c.assertIn("ambiguous", result.stderr)
    c.assertIn(f"pi {PI}", result.stderr)
    c.assertIn(f"codex {other}", result.stderr)
    c.assertEqual(c.tree(), [])


def test_R228_the_same_session_under_two_harnesses_is_refused(c):
    codex_file = codex_rollout(c.codex, "2026/11/10", "a", SPLIT, "/work/app", OLD)
    pi_file = pi_session(c.pi, "--a--", "p", SPLIT, "/work/app", OLD)
    result = c.capture("--session", SPLIT)
    c.assertEqual(result.returncode, 2)
    c.assertIn(str(codex_file), result.stderr)
    c.assertIn(str(pi_file), result.stderr)
    c.assertEqual(c.tree(), [])


def test_R228_the_first_named_session_is_the_root_and_names_the_case(c):
    pi_session(c.pi, "--a--", "p", PI, "/work/app", NEW)
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
    result = c.capture("--session", PI, "--session", CODEX)
    c.assertEqual(result.returncode, 0, result.stderr)
    entries = c.entries(PI)
    c.assertEqual((entries[PI]["role"], entries[CODEX]["role"]), ("root", "session"))
    c.assertEqual(entries[CODEX]["harness"], "codex")


def test_R228_a_named_session_that_is_also_a_run_worker_joins_once(c):
    c.make_run()
    pi_session(c.pi, "--a--", "p", PI, WT, INSIDE, "do it")
    claude_session(c.claude, "-wt1", WORKER, WT, INSIDE)
    result = c.run_capture("--session", PI)
    c.assertEqual(result.returncode, 0, result.stderr)
    sessions = c.manifest(PI)["sessions"]
    c.assertEqual([s["id"] for s in sessions].count(PI), 1)
    entries = {s["id"]: s for s in sessions}
    c.assertEqual((entries[PI]["role"], entries[WORKER]["role"]), ("root", "worker"))


def test_R228_a_named_session_can_be_excluded_and_is_listed(c):
    pi_session(c.pi, "--a--", "p", PI, "/work/app", OLD)
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
    result = c.capture("--session", PI, "--session", CODEX, "--exclude", "cccccccc")
    c.assertEqual(result.returncode, 0, result.stderr)
    manifest = c.manifest(PI)
    c.assertEqual(manifest["excluded"], [PI])
    c.assertEqual([s["id"] for s in manifest["sessions"]], [CODEX])


def test_R229_a_named_session_records_its_cwd_and_assignment_id(c):
    pi_session(c.pi, "--a--", "p", PI, "/work/app", OLD, PROMPT % ASSIGNMENT)
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, "/work/x", OLD, "no id here")
    claude_session(c.claude, "-app", WORKER, "/work/c", OLD, PROMPT % ASSIGNMENT)
    result = c.capture("--session", PI, "--session", CODEX, "--session", WORKER)
    c.assertEqual(result.returncode, 0, result.stderr)
    entries = c.entries(PI)
    c.assertEqual((entries[PI]["cwd"], entries[PI]["assignment_id"]), ("/work/app", ASSIGNMENT))
    c.assertEqual((entries[CODEX]["cwd"], entries[CODEX]["assignment_id"]), ("/work/x", None))
    c.assertEqual(
        (entries[WORKER]["cwd"], entries[WORKER]["assignment_id"]), ("/work/c", ASSIGNMENT)
    )


def test_R229_a_session_captured_by_name_and_as_a_worker_records_the_same_fields(c):
    c.make_run()
    claude_session(c.claude, "-wt1", WORKER, WT, INSIDE, PROMPT % ASSIGNMENT)
    pi_session(c.pi, "--a--", "p", PI, WT, INSIDE, PROMPT % ASSIGNMENT)
    codex_rollout(c.codex, "2026/11/10", "a", CODEX, WT, INSIDE, PROMPT % ASSIGNMENT)
    by_run = c.run_capture("--case", "by-run")
    c.assertEqual(by_run.returncode, 0, by_run.stderr)
    by_name = c.capture(
        "--case", "by-name", "--session", WORKER, "--session", PI, "--session", CODEX
    )
    c.assertEqual(by_name.returncode, 0, by_name.stderr)
    run_entries, name_entries = c.entries("by-run"), c.entries("by-name")
    for sid in (WORKER, PI, CODEX):
        for field in ("cwd", "assignment_id"):
            c.assertEqual(name_entries[sid][field], run_entries[sid][field], (sid, field))
        c.assertEqual(name_entries[sid]["assignment_id"], ASSIGNMENT)
        c.assertEqual(name_entries[sid]["cwd"], WT)


def test_R229_a_chain_member_records_its_own_cwd_and_assignment_id(c):
    claude_session(c.claude, "-app", WORKER, "/work/c", OLD)
    write(
        c.claude / "projects" / "-app" / WORKER / "subagents" / "agent-s1.jsonl",
        '{"type":"user","cwd":"/work/sub","message":{"role":"user","content":"hi"}}\n',
    )
    result = c.capture("--session", WORKER)
    c.assertEqual(result.returncode, 0, result.stderr)
    entry = c.entries(WORKER)["agent-s1"]
    c.assertEqual((entry["cwd"], entry["assignment_id"]), ("/work/sub", None))


def test_R229_a_session_without_a_marker_records_a_null_assignment_id(c):
    result = c.capture("--session", SOLO)
    c.assertEqual(result.returncode, 0, result.stderr)
    entry = c.entries(SOLO)[SOLO]
    c.assertEqual((entry["cwd"], entry["assignment_id"]), (None, None))
