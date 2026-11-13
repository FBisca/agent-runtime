"""Capture a session named by id on any harness: the resolution, its refusals, the root it names, and
the manifest's own fields (agent-eval-named-capture/R228-R229)."""

import gzip

import pytest
from claude_fixture import CHAIN, FORKED, RESUMED, SOLO, CaptureCase, job, write
from harness_fixture import (
    ASSIGNMENT,
    CODEX_INJECTED,
    PROMPT,
    claude_session,
    codex_rollout,
    orchestrate_run,
    pi_session,
)

pytestmark = pytest.mark.integration

SLUG = "demo-run"
FIRST, LAST = "2026-11-10T10:00:00+00:00", "2026-11-10T12:00:00+00:00"
INSIDE = "2026-11-10T10:30:00.000Z"
EARLIER = "2026-11-10T09:00:00Z"

PI_ID = "01a1c5f4-da75-77ff-8635-e123ced4e441"
PI_TWIN = "01a1c5f4-da75-77ff-8635-e123ced4e442"
PI_X = "01a1f00d-14c6-738c-94bc-68202792b3a3"
CODEX_X = "01a1f00d-0965-7c91-9fe8-d32059a2ff75"
SAME = "eeeeeeee-5555-4555-8555-eeeeeeeeeeee"
CODEX_ID = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
WORKER = "cccccccc-3333-4333-8333-cccccccccccc"
PARENT = "dddddddd-4444-4444-8444-dddddddddddd"
CHILD = "ffffffff-6666-4666-8666-ffffffffffff"
WORKER_C = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"


class NamedCase(CaptureCase):
    """A throwaway Claude, Codex and Pi home, and an evals home; the CLI runs as a subprocess."""

    def setUp(self) -> None:
        super().setUp()
        self.codex = self.tmp / "codex"
        self.pi = self.tmp / "pi"
        self.state = self.tmp / "orchestrate"

    def worktree(self) -> str:
        return str(self.tmp / "demo.worktrees" / "t1")

    def named_on(self, line: str, harness: str, sid: str) -> bool:
        """Whether one line of a refusal names that harness and that session."""
        return harness in line and sid in line


class NamedSessionTests(NamedCase):
    def test_R228_a_named_pi_session_is_captured_with_its_harness_and_cwd(self):
        """agent-eval-named-capture/R228"""
        source = pi_session(
            self.pi, "--Users-x-demo--", "p", PI_ID, "/work/demo", "2026-11-10T10:00:00Z"
        )
        result = self.capture("--session", PI_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(PI_ID)[PI_ID]
        self.assertEqual(entry["harness"], "pi")
        self.assertEqual(entry["cwd"], "/work/demo")
        self.assertEqual((entry["role"], entry["parent"]), ("root", None))
        self.assertEqual(self.manifest(PI_ID)["case"], PI_ID)
        stored = self.case(PI_ID) / entry["file"]
        self.assertEqual(gzip.decompress(stored.read_bytes()), source.read_bytes())
        self.assertEqual(entry["files"], [])

    def test_R228_a_named_session_brings_no_subagent_and_nothing_else(self):
        """agent-eval-named-capture/R228: a named session is a session, not a run"""
        pi_session(self.pi, "--Users-x-demo--", "p", PI_ID, "/work/demo", "2026-11-10T10:00:00Z")
        result = self.capture("--session", PI_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest(PI_ID)
        self.assertNotIn("subagent", {s["role"] for s in manifest["sessions"]})
        self.assertIsNone(manifest["orchestrate"])
        self.assertFalse((self.case(PI_ID) / "changes.patch").exists())

    def test_R228_a_named_codex_session_is_captured_with_its_later_rollouts(self):
        """agent-eval-named-capture/R228"""
        first = codex_rollout(
            self.codex, "2026/11/10", "a", CODEX_ID, "/work/demo", "2026-11-10T10:00:00Z"
        )
        later = codex_rollout(
            self.codex, "2026/11/10", "b", CODEX_ID, "/work/demo", "2026-11-10T11:00:00Z"
        )
        result = self.capture("--session", CODEX_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(CODEX_ID)[CODEX_ID]
        self.assertEqual(entry["harness"], "codex")
        self.assertEqual(entry["cwd"], "/work/demo")
        self.assertEqual((entry["role"], entry["parent"]), ("root", None))
        self.assertEqual([f["kind"] for f in entry["files"]], ["rollout"])
        self.assertEqual(entry["files"][0]["source"], str(later))
        stored = self.case(CODEX_ID) / entry["file"]
        self.assertEqual(gzip.decompress(stored.read_bytes()), first.read_bytes())
        rollout = self.case(CODEX_ID) / entry["files"][0]["file"]
        self.assertEqual(gzip.decompress(rollout.read_bytes()), later.read_bytes())

    def test_R228_a_named_session_is_captured_under_a_unique_prefix_of_its_id(self):
        """agent-eval-named-capture/R228"""
        pi_session(self.pi, "--Users-x-demo--", "p", PI_ID, "/work/demo", "2026-11-10T10:00:00Z")
        result = self.capture("--session", PI_ID[:8])
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(PI_ID)[PI_ID]
        self.assertEqual(entry["harness"], "pi")
        self.assertEqual(entry["cwd"], "/work/demo")
        self.assertEqual((entry["role"], entry["parent"]), ("root", None))

    def test_R228_a_named_codex_session_is_captured_under_a_unique_prefix_of_its_id(self):
        """agent-eval-named-capture/R228"""
        codex_rollout(self.codex, "2026/11/10", "a", CODEX_ID, "/work/demo", "2026-11-10T10:00:00Z")
        result = self.capture("--session", CODEX_ID[:8])
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(CODEX_ID)[CODEX_ID]
        self.assertEqual(entry["harness"], "codex")
        self.assertEqual(entry["cwd"], "/work/demo")

    def test_R228_the_capture_help_names_every_harness(self):
        """agent-eval-named-capture/R228: the help and the spec agree"""
        result = self.run_cli("capture", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        flag = result.stdout.split("--session")[1].split("--case")[0]
        for harness in ("Claude", "Codex", "Pi"):
            self.assertIn(harness, flag)


class RefusalTests(NamedCase):
    def test_R228_an_unknown_ref_is_refused_naming_every_harness_it_looked_in(self):
        """agent-eval-named-capture/R228"""
        result = self.capture("--session", "99999999-0000-4000-8000-000000000000")
        self.assertEqual(result.returncode, 2)
        for place in (self.claude / "projects", self.codex / "sessions", self.pi / "sessions"):
            self.assertIn(str(place), result.stderr)
        self.assertEqual(self.tree(), [])

    def test_R228_an_ambiguous_prefix_inside_one_harness_is_refused_naming_both(self):
        """agent-eval-named-capture/R228"""
        pi_session(self.pi, "--Users-x-demo--", "a", PI_ID, "/work/demo", "2026-11-10T10:00:00Z")
        pi_session(self.pi, "--Users-x-demo--", "b", PI_TWIN, "/work/demo", "2026-11-10T10:01:00Z")
        result = self.capture("--session", PI_ID[:8])
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous", result.stderr)
        self.assertIn(PI_ID, result.stderr)
        self.assertIn(PI_TWIN, result.stderr)
        self.assertTrue(
            any(
                self.named_on(line, "pi", sid)
                for line in result.stderr.splitlines()
                for sid in (PI_ID, PI_TWIN)
            ),
            result.stderr,
        )
        self.assertEqual(self.tree(), [])

    def test_R228_a_prefix_that_matches_two_harnesses_is_refused_naming_both(self):
        """agent-eval-named-capture/R228"""
        pi_session(self.pi, "--Users-x-demo--", "p", PI_X, "/work/demo", "2026-11-10T10:00:00Z")
        codex_rollout(self.codex, "2026/11/10", "a", CODEX_X, "/work/demo", "2026-11-10T10:00:00Z")
        result = self.capture("--session", PI_X[:8])
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous", result.stderr)
        lines = result.stderr.splitlines()
        self.assertTrue(any(self.named_on(line, "pi", PI_X) for line in lines), result.stderr)
        self.assertTrue(any(self.named_on(line, "codex", CODEX_X) for line in lines), result.stderr)
        self.assertEqual(self.tree(), [])

    def test_R228_the_same_session_under_two_harnesses_is_refused(self):
        """agent-eval-named-capture/R228"""
        pi_file = pi_session(
            self.pi, "--Users-x-demo--", "p", SAME, "/work/demo", "2026-11-10T10:00:00Z"
        )
        codex_file = codex_rollout(
            self.codex, "2026/11/10", "a", SAME, "/work/other", "2026-11-10T10:00:00Z"
        )
        result = self.capture("--session", SAME)
        self.assertEqual(result.returncode, 2)
        self.assertIn("two harnesses", result.stderr)
        self.assertIn(SAME, result.stderr)
        self.assertIn(str(pi_file), result.stderr)
        self.assertIn(str(codex_file), result.stderr)
        self.assertEqual(self.tree(), [])


class RootTests(NamedCase):
    def test_R228_the_first_named_session_is_the_root_and_names_the_case(self):
        """agent-eval-named-capture/R228: the first --session decides, not the oldest session"""
        pi_session(self.pi, "--Users-x-demo--", "p", PI_ID, "/work/demo", INSIDE)
        codex_rollout(self.codex, "2026/11/10", "a", CODEX_ID, "/work/demo", EARLIER)
        result = self.capture("--session", PI_ID, "--session", CODEX_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PI_ID)
        self.assertEqual((entries[PI_ID]["role"], entries[PI_ID]["parent"]), ("root", None))
        self.assertEqual(
            (entries[CODEX_ID]["role"], entries[CODEX_ID]["parent"]), ("session", None)
        )
        self.assertTrue(self.case(PI_ID).is_dir())
        self.assertFalse(self.case(CODEX_ID).exists())

    def test_R228_a_named_session_that_is_also_a_run_worker_joins_once(self):
        """agent-eval-named-capture/R228"""
        worktree = self.worktree()
        orchestrate_run(self.state, SLUG, {"t1": worktree}, FIRST, LAST)
        pi_session(self.pi, "--demo--", "p", PI_ID, worktree, INSIDE)
        claude_session(self.claude, "-wt1", WORKER, worktree, INSIDE)
        result = self.capture(
            "--session",
            PI_ID,
            "--orchestrate-run",
            SLUG,
            "--orchestrate-state-dir",
            str(self.state),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        sessions = self.manifest(PI_ID)["sessions"]
        self.assertEqual([s["id"] for s in sessions].count(PI_ID), 1)
        entries = {s["id"]: s for s in sessions}
        self.assertEqual((entries[PI_ID]["role"], entries[WORKER]["role"]), ("root", "worker"))
        self.assertEqual(self.manifest(PI_ID)["orchestrate"]["slug"], SLUG)

    def test_R228_a_named_session_can_be_excluded_and_is_listed(self):
        """agent-eval-named-capture/R228"""
        pi_session(self.pi, "--Users-x-demo--", "p", PI_ID, "/work/demo", INSIDE)
        codex_rollout(self.codex, "2026/11/10", "a", CODEX_ID, "/work/demo", INSIDE)
        result = self.capture("--session", PI_ID, "--session", CODEX_ID, "--exclude", CODEX_ID[:8])
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest(PI_ID)
        self.assertEqual(manifest["excluded"], [CODEX_ID])
        self.assertEqual({s["id"] for s in manifest["sessions"]}, {PI_ID})


class ManifestFieldTests(NamedCase):
    def test_R229_a_named_session_records_its_cwd_and_assignment_id(self):
        """agent-eval-named-capture/R229"""
        pi_session(
            self.pi,
            "--Users-x-demo--",
            "p",
            PI_ID,
            "/work/demo",
            "2026-11-10T10:00:00Z",
            PROMPT % ASSIGNMENT,
        )
        codex_rollout(
            self.codex, "2026/11/10", "a", CODEX_ID, "/work/x", "2026-11-10T10:00:00Z", "no id here"
        )
        claude_session(
            self.claude, "-app", WORKER_C, "/work/c", "2026-11-10T10:00:00Z", PROMPT % ASSIGNMENT
        )
        result = self.capture("--session", PI_ID, "--session", CODEX_ID, "--session", WORKER_C)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PI_ID)
        self.assertEqual(
            (entries[PI_ID]["cwd"], entries[PI_ID]["assignment_id"]), ("/work/demo", ASSIGNMENT)
        )
        self.assertEqual(
            (entries[CODEX_ID]["cwd"], entries[CODEX_ID]["assignment_id"]), ("/work/x", None)
        )
        self.assertEqual(
            (entries[WORKER_C]["cwd"], entries[WORKER_C]["assignment_id"]), ("/work/c", ASSIGNMENT)
        )

    def test_R229_a_session_captured_by_name_and_as_a_worker_records_the_same_fields(self):
        """agent-eval-named-capture/R229"""
        worktree = self.worktree()
        orchestrate_run(self.state, SLUG, {"t1": worktree}, FIRST, LAST)
        claude_session(self.claude, "-wt", WORKER, worktree, INSIDE, PROMPT % ASSIGNMENT)
        pi_session(self.pi, "--demo--", "p", PI_ID, worktree, INSIDE, PROMPT % ASSIGNMENT)
        codex_rollout(
            self.codex, "2026/11/10", "a", CODEX_ID, worktree, INSIDE, PROMPT % ASSIGNMENT
        )
        by_run = self.capture(
            "--orchestrate-run",
            SLUG,
            "--orchestrate-state-dir",
            str(self.state),
            "--case",
            "run-case",
        )
        self.assertEqual(by_run.returncode, 0, by_run.stderr)
        by_name = self.capture(
            "--case", "by-name", "--session", WORKER, "--session", PI_ID, "--session", CODEX_ID
        )
        self.assertEqual(by_name.returncode, 0, by_name.stderr)
        run_entries, name_entries = self.entries("run-case"), self.entries("by-name")
        for sid in (WORKER, PI_ID, CODEX_ID):
            for field in ("cwd", "assignment_id"):
                self.assertEqual(name_entries[sid][field], run_entries[sid][field], (sid, field))
            self.assertEqual(name_entries[sid]["assignment_id"], ASSIGNMENT)
            self.assertEqual(name_entries[sid]["cwd"], worktree)

    def test_R229_a_subagent_records_its_own_cwd_and_assignment_id(self):
        """agent-eval-named-capture/R229: the subagent road carries the same two fields"""
        claude_session(self.claude, "-app", WORKER, "/work/c", "2026-11-10T10:00:00Z")
        write(
            self.claude / "projects" / "-app" / WORKER / "subagents" / "agent-s1.jsonl",
            '{"type":"user","cwd":"/work/sub","message":{"role":"user","content":"hi"}}\n',
        )
        result = self.capture("--session", WORKER)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(WORKER)["agent-s1"]
        self.assertEqual((entry["cwd"], entry["assignment_id"]), ("/work/sub", None))

    def test_R229_a_chain_member_records_its_own_cwd_and_assignment_id(self):
        """agent-eval-named-capture/R229: the chain road carries the same two fields"""
        claude_session(self.claude, "-chain", PARENT, "/work/parent", EARLIER, "the parent prompt")
        claude_session(self.claude, "-chain", CHILD, "/work/child", INSIDE, PROMPT % ASSIGNMENT)
        job(self.claude, "99999999", sessionId=CHILD, resumeSessionId=PARENT)
        result = self.capture("--session", CHILD)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PARENT)
        self.assertEqual(
            (entries[CHILD]["role"], entries[CHILD]["parent"]), ("continuation", PARENT)
        )
        self.assertEqual(entries[CHILD]["cwd"], "/work/child")
        self.assertEqual(entries[CHILD]["assignment_id"], ASSIGNMENT)
        self.assertEqual(entries[PARENT]["cwd"], "/work/parent")
        self.assertIsNone(entries[PARENT]["assignment_id"])

    def test_R229_a_session_without_a_marker_records_a_null_assignment_id(self):
        """agent-eval-named-capture/R229: the marker is read from the first prompt, not the first record"""
        pi_session(self.pi, "--Users-x-demo--", "p", PI_ID, "/work/demo", "2026-11-10T10:00:00Z")
        codex_rollout(self.codex, "2026/11/10", "a", CODEX_ID, "/work/demo", "2026-11-10T10:00:00Z")
        result = self.capture("--session", PI_ID, "--session", CODEX_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PI_ID)
        self.assertIsNone(entries[PI_ID]["assignment_id"])
        self.assertIsNone(entries[CODEX_ID]["assignment_id"])
        solo = self.capture("--session", SOLO)
        self.assertEqual(solo.returncode, 0, solo.stderr)
        self.assertEqual(
            (self.entries(SOLO)[SOLO]["cwd"], self.entries(SOLO)[SOLO]["assignment_id"]),
            (None, None),
        )

    def test_R237_a_codex_prompt_that_starts_with_a_bracket_keeps_its_assignment_id(self):
        """R237: only Codex's own injected messages are skipped"""
        codex_rollout(
            self.codex,
            "2026/11/10",
            "a",
            CODEX_ID,
            "/work/demo",
            "2026-11-10T10:00:00Z",
            "<task>\n" + PROMPT % ASSIGNMENT,
            injected=CODEX_INJECTED,
        )
        result = self.capture("--session", CODEX_ID)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.entries(CODEX_ID)[CODEX_ID]["assignment_id"], ASSIGNMENT)


class ControlTests(NamedCase):
    def test_control_a_named_claude_session_still_joins_with_its_chain_and_subagents(self):
        """agent-eval-named-capture/R228: today's Claude chain holds"""
        result = self.capture("--session", CHAIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(RESUMED)
        self.assertEqual(set(entries), {RESUMED, CHAIN, FORKED, "agent-a1", "agent-a2"})
        self.assertEqual((entries[RESUMED]["role"], entries[RESUMED]["parent"]), ("root", None))

    def test_control_a_run_capture_still_copies_the_run_directory(self):
        """agent-eval-named-capture/R228: today's run path holds"""
        worktree = self.worktree()
        orchestrate_run(self.state, SLUG, {"t1": worktree}, FIRST, LAST)
        claude_session(self.claude, "-wt", WORKER, worktree, INSIDE)
        result = self.capture("--orchestrate-run", SLUG, "--orchestrate-state-dir", str(self.state))
        self.assertEqual(result.returncode, 0, result.stderr)
        run = self.manifest(SLUG)["orchestrate"]
        self.assertEqual(run["slug"], SLUG)
        self.assertIn("orchestrate/events.jsonl.gz", {f["file"] for f in run["files"]})
