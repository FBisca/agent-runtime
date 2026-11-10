"""Capture an orchestrate run: the run dir, and the Claude, Codex and Pi sessions of its ticket
worktrees (agent-eval-p0/R216-R218)."""

import gzip
import hashlib
import os

import pytest
from claude_fixture import CHAIN, RESUMED, CaptureCase, write
from harness_fixture import (
    ASSIGNMENT,
    PROMPT,
    claude_session,
    codex_rollout,
    orchestrate_run,
    pi_session,
)

pytestmark = pytest.mark.integration

SLUG = "demo-run"
WT1 = "/work/demo.worktrees/demo-t1"
WT2 = "/work/demo.worktrees/demo-t2"
FIRST, LAST = "2026-11-10T10:00:00+00:00", "2026-11-10T12:00:00+00:00"
INSIDE = "2026-11-10T10:30:00.000Z"
BEFORE = "2026-11-10T09:59:00.000Z"
AFTER = "2026-11-10T12:00:01.000Z"

W_CLAUDE = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
W_CODEX = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
W_PI = "cccccccc-3333-4333-8333-cccccccccccc"
EARLY = "dddddddd-4444-4444-8444-dddddddddddd"
LATE = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee"
STRAY = "ffffffff-6666-4666-8666-ffffffffffff"


class OrchestrateCase(CaptureCase):
    def setUp(self) -> None:
        super().setUp()
        self.state = self.tmp / "orchestrate"
        self.codex = self.tmp / "codex"
        self.pi = self.tmp / "pi"
        self.run_dir = orchestrate_run(self.state, SLUG, {"t1": WT1, "t2": WT2}, FIRST, LAST)

    def run_capture(self, *args: str):
        return self.capture(
            "--orchestrate-run", SLUG, "--orchestrate-state-dir", str(self.state), *args
        )

    def stored(self, slug: str, entry: dict) -> bytes:
        return gzip.decompress((self.case(slug) / entry["file"]).read_bytes())


class RunTests(OrchestrateCase):
    def test_R216_the_run_dir_is_copied_into_the_case(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.manifest(SLUG)["orchestrate"]
        self.assertEqual(run["slug"], SLUG)
        by_name = {f["file"]: f for f in run["files"]}
        self.assertEqual(
            set(by_name),
            {
                "orchestrate/events.jsonl.gz",
                "orchestrate/contract.md.gz",
                "orchestrate/t1/task.md.gz",
                "orchestrate/t2/task.md.gz",
            },
        )
        original = (self.run_dir / "events.jsonl").read_bytes()
        entry = by_name["orchestrate/events.jsonl.gz"]
        self.assertEqual(entry["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(entry["bytes"], len(original))
        self.assertEqual(gzip.decompress((self.case(SLUG) / entry["file"]).read_bytes()), original)
        self.assertEqual(self.mode(self.case(SLUG) / entry["file"]), 0o600)
        self.assertEqual(run["window"], {"start": FIRST, "end": LAST})

    def test_R216_claude_codex_and_pi_sessions_of_the_ticket_worktrees_join_as_workers(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE, PROMPT % ASSIGNMENT)
        write(self.claude / "projects" / "-wt1" / W_CLAUDE / "subagents" / "agent-w1.jsonl", "{}\n")
        write(self.claude / "projects" / "-wt1" / W_CLAUDE / "tool-results" / "r.txt", "out\n")
        codex_rollout(self.codex, "2026/11/10", "t", W_CODEX, WT2, INSIDE, PROMPT % ASSIGNMENT)
        pi_session(self.pi, "--wt2--", "p", W_PI, WT2, INSIDE, "no assignment here")
        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(SLUG)
        self.assertEqual(set(entries), {W_CLAUDE, "agent-w1", W_CODEX, W_PI})
        for sid, harness in ((W_CLAUDE, "claude"), (W_CODEX, "codex"), (W_PI, "pi")):
            self.assertEqual((entries[sid]["harness"], entries[sid]["role"]), (harness, "worker"))
            self.assertIsNone(entries[sid]["parent"])
        self.assertEqual(entries[W_CLAUDE]["assignment_id"], ASSIGNMENT)
        self.assertEqual(entries[W_CODEX]["assignment_id"], ASSIGNMENT)
        self.assertIsNone(entries[W_PI]["assignment_id"])
        self.assertEqual(
            (entries["agent-w1"]["role"], entries["agent-w1"]["parent"]), ("subagent", W_CLAUDE)
        )
        self.assertEqual([f["kind"] for f in entries[W_CLAUDE]["files"]], ["tool-result"])
        for sid, source in (
            (W_CLAUDE, self.claude / "projects" / "-wt1" / f"{W_CLAUDE}.jsonl"),
            (W_CODEX, next((self.codex / "sessions").rglob("*.jsonl"))),
            (W_PI, self.pi / "sessions" / "--wt2--" / "p.jsonl"),
        ):
            self.assertEqual(self.stored(SLUG, entries[sid]), source.read_bytes())
            self.assertEqual(
                entries[sid]["sha256"], hashlib.sha256(source.read_bytes()).hexdigest()
            )

    def test_R216_a_session_outside_the_worktrees_or_the_run_window_stays_out(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        claude_session(self.claude, "-wt1", EARLY, WT1, BEFORE)
        claude_session(self.claude, "-wt1", LATE, WT1, AFTER)
        claude_session(self.claude, "-x", STRAY, "/work/demo", INSIDE)
        codex_rollout(
            self.codex, "2026/11/10", "t", "99999999-0000-4000-8000-000000000001", WT1, BEFORE
        )
        pi_session(
            self.pi, "--wt1--", "p", "99999999-0000-4000-8000-000000000002", "/work/other", INSIDE
        )
        self.assertEqual(self.run_capture().returncode, 0)
        self.assertEqual(set(self.entries(SLUG)), {W_CLAUDE})

    def test_R216_the_window_is_inclusive_at_both_ends(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", EARLY, WT1, "2026-11-10T10:00:00Z")
        claude_session(self.claude, "-wt1", LATE, WT1, "2026-11-10T12:00:00Z")
        self.assertEqual(self.run_capture().returncode, 0)
        self.assertEqual(set(self.entries(SLUG)), {EARLY, LATE})

    def test_R216_the_case_slug_defaults_to_the_run_and_session_flags_add_to_it(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.run_capture("--session", CHAIN).returncode, 0)
        entries = self.entries(RESUMED)
        self.assertIn(W_CLAUDE, entries)
        self.assertIn(CHAIN, entries)
        self.assertEqual(entries[W_CLAUDE]["role"], "worker")
        self.assertEqual(self.manifest(RESUMED)["orchestrate"]["slug"], SLUG)
        self.assertFalse(self.case(SLUG).exists())

    def test_R216_a_worker_the_chain_already_holds_is_captured_once(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.run_capture("--session", W_CLAUDE).returncode, 0)
        sessions = self.manifest(W_CLAUDE)["sessions"]
        self.assertEqual([s["id"] for s in sessions], [W_CLAUDE])

    def test_R216_exclude_leaves_a_worker_out_and_lists_it(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        pi_session(self.pi, "--wt2--", "p", W_PI, WT2, INSIDE)
        self.assertEqual(self.run_capture("--exclude", W_PI[:8]).returncode, 0)
        self.assertEqual(set(self.entries(SLUG)), {W_CLAUDE})
        self.assertEqual(self.manifest(SLUG)["excluded"], [W_PI])

    def test_R216_without_an_orchestrate_run_no_worker_joins_and_the_manifest_says_null(self):
        """agent-eval-p0/R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.capture("--session", CHAIN).returncode, 0)
        manifest = self.manifest(RESUMED)
        self.assertIsNone(manifest["orchestrate"])
        self.assertNotIn(W_CLAUDE, self.entries(RESUMED))

    def test_R216_an_unknown_run_fails_naming_it_and_writes_nothing(self):
        """agent-eval-p0/R216"""
        result = self.capture(
            "--orchestrate-run", "nope", "--orchestrate-state-dir", str(self.state)
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nope", result.stderr)
        self.assertIn("not found", result.stderr)
        self.assertEqual(self.tree(), [])

    def test_R216_a_run_with_no_session_to_capture_fails_and_writes_nothing(self):
        """agent-eval-p0/R216"""
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nothing to capture", result.stderr)
        self.assertEqual(self.tree(), [])

    def test_R216_a_run_slug_cannot_leave_the_state_dir(self):
        """agent-eval-p0/R216"""
        result = self.capture(
            "--orchestrate-run", "../orchestrate", "--orchestrate-state-dir", str(self.state)
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.tree(), [])

    def test_R216_a_symlink_in_the_run_dir_is_not_followed(self):
        """agent-eval-p0/R216"""
        secret = write(self.tmp / "secret.txt", "private\n")
        os.symlink(secret, self.run_dir / "leak.txt")
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.run_capture().returncode, 0)
        names = {f["file"] for f in self.manifest(SLUG)["orchestrate"]["files"]}
        self.assertNotIn("orchestrate/leak.txt.gz", names)

    def test_R216_a_claude_worker_found_under_two_project_directories_is_refused(self):
        """agent-eval-p0/R214, R216"""
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        claude_session(self.claude, "-wt1-copy", W_CLAUDE, WT1, INSIDE)
        result = self.run_capture()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("two Claude project directories", result.stderr)
        self.assertEqual(self.tree(), [])


class CodexTests(OrchestrateCase):
    def test_R217_every_rollout_file_of_a_session_is_captured_under_it(self):
        """agent-eval-p0/R217"""
        first = codex_rollout(
            self.codex,
            "2026/11/10",
            "2026-11-10T10-30-00",
            W_CODEX,
            WT2,
            INSIDE,
            PROMPT % ASSIGNMENT,
        )
        later = codex_rollout(
            self.codex,
            "2026/11/10",
            "2026-11-10T11-30-00",
            W_CODEX,
            WT2,
            "2026-11-10T11:30:00.000Z",
        )
        third = codex_rollout(
            self.codex,
            "2026/11/11",
            "2026-11-11T08-00-00",
            W_CODEX,
            WT2,
            "2026-11-11T08:00:00.000Z",
        )
        codex_rollout(
            self.codex,
            "2026/11/10",
            "2026-11-10T10-45-00",
            "99999999-0000-4000-8000-000000000003",
            WT2,
            INSIDE,
        )
        self.assertEqual(self.run_capture().returncode, 0)
        entries = self.entries(SLUG)
        self.assertIn(W_CODEX, entries)
        mine = entries[W_CODEX]
        self.assertEqual(self.stored(SLUG, mine), first.read_bytes())
        self.assertEqual(mine["assignment_id"], ASSIGNMENT)
        extra = {f["source"]: f for f in mine["files"]}
        self.assertEqual(set(extra), {str(later), str(third)})
        for path, entry in extra.items():
            self.assertEqual(entry["kind"], "rollout")
            original = open(path, "rb").read()
            self.assertEqual(
                gzip.decompress((self.case(SLUG) / entry["file"]).read_bytes()), original
            )
            self.assertEqual(entry["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(entry["bytes"], len(original))
        self.assertEqual(len([s for s in entries.values() if s["harness"] == "codex"]), 2)

    def test_R217_rollout_files_with_the_same_id_in_other_directories_still_join(self):
        """agent-eval-p0/R217: the id comes from session_meta, not from the day folder or the file name"""
        codex_rollout(self.codex, "2026/11/10", "a", W_CODEX, WT2, INSIDE)
        odd = write(
            self.codex / "sessions" / "archive" / "rollout-renamed.jsonl",
            (self.codex / "sessions" / "2026/11/10" / f"rollout-a-{W_CODEX}.jsonl").read_text(),
        )
        self.assertEqual(self.run_capture().returncode, 0)
        extra = self.entries(SLUG)[W_CODEX]["files"]
        self.assertEqual([f["source"] for f in extra], [str(odd)])

    def test_R217_a_later_rollout_does_not_pull_in_a_session_that_started_outside_the_window(self):
        """agent-eval-p0/R217: the window is the session's start, the first file's"""
        codex_rollout(self.codex, "2026/11/09", "a", W_CODEX, WT2, BEFORE)
        codex_rollout(self.codex, "2026/11/10", "b", W_CODEX, WT2, INSIDE)
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.run_capture().returncode, 0)
        self.assertEqual(set(self.entries(SLUG)), {W_CLAUDE})


class PiTests(OrchestrateCase):
    def test_R218_id_and_cwd_come_from_the_first_record_in_a_dash_dash_directory(self):
        """agent-eval-p0/R218"""
        name = f"2026-11-10T10-30-00-000Z_{STRAY}"
        pi_session(
            self.pi,
            "--Users-x-demo.worktrees-demo-t1--",
            name,
            W_PI,
            WT1,
            INSIDE,
            PROMPT % ASSIGNMENT,
        )
        self.assertEqual(self.run_capture().returncode, 0)
        entry = self.entries(SLUG)[W_PI]
        self.assertEqual((entry["harness"], entry["role"], entry["cwd"]), ("pi", "worker", WT1))
        self.assertEqual(entry["assignment_id"], ASSIGNMENT)
        self.assertNotIn(STRAY, self.entries(SLUG))
        self.assertNotIn("575ca184", self.entries(SLUG))

    def test_R218_a_directory_name_that_looks_like_the_worktree_does_not_decide_the_cwd(self):
        """agent-eval-p0/R218"""
        pi_session(self.pi, "--work-demo.worktrees-demo-t1--", "p", W_PI, "/somewhere/else", INSIDE)
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.run_capture().returncode, 0)
        self.assertEqual(set(self.entries(SLUG)), {W_CLAUDE})

    def test_R218_a_file_whose_first_record_is_not_a_session_header_is_skipped(self):
        """agent-eval-p0/R218"""
        write(
            self.pi / "sessions" / "--wt1--" / "bad.jsonl",
            '{"type":"message","id":"zz","cwd":"%s"}\n' % WT1,
        )
        write(self.pi / "sessions" / "--wt1--" / "junk.jsonl", "not json\n")
        claude_session(self.claude, "-wt1", W_CLAUDE, WT1, INSIDE)
        self.assertEqual(self.run_capture().returncode, 0)
        self.assertEqual(set(self.entries(SLUG)), {W_CLAUDE})
