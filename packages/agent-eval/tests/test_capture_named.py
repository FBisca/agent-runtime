"""Capture a Codex or Pi session by name, and every captured session's own cwd and assignment id
(agent-eval-named-capture/S1-S4)."""

import gzip

import pytest
from claude_fixture import CHAIN, RESUMED, SOLO, CaptureCase, write
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


class NamedSessionTests(NamedCase):
    def test_S1_a_named_codex_session_joins_with_its_rollouts(self):
        first = codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/app", OLD, "do it")
        later = codex_rollout(self.codex, "2026/11/11", "b", CODEX, "/work/app", NEW)
        result = self.capture("--session", CODEX)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(CODEX)
        self.assertEqual(set(entries), {CODEX})
        entry = entries[CODEX]
        self.assertEqual(
            (entry["harness"], entry["role"], entry["parent"], entry["cwd"]),
            ("codex", "root", None, "/work/app"),
        )
        self.assertEqual(self.stored(CODEX, entry), first.read_bytes())
        self.assertEqual([f["kind"] for f in entry["files"]], ["rollout"])
        self.assertEqual(entry["files"][0]["source"], str(later))
        rollout = self.case(CODEX) / entry["files"][0]["file"]
        self.assertEqual(gzip.decompress(rollout.read_bytes()), later.read_bytes())

    def test_S1_a_named_pi_session_joins_with_its_cwd_and_no_aux_file(self):
        source = pi_session(self.pi, "--work-app--", "p", PI, "/work/my-app", OLD, "do it")
        result = self.capture("--session", PI)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PI)
        self.assertEqual(set(entries), {PI})
        entry = entries[PI]
        self.assertEqual(
            (entry["harness"], entry["role"], entry["parent"], entry["cwd"], entry["files"]),
            ("pi", "root", None, "/work/my-app", []),
        )
        self.assertEqual(self.stored(PI, entry), source.read_bytes())

    def test_S1_a_unique_prefix_names_a_codex_or_pi_session(self):
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
        pi_session(self.pi, "--work-app--", "p", PI, "/work/app", OLD)
        self.assertEqual(self.capture("--session", "bbbbbbbb-2").returncode, 0)
        self.assertEqual(self.capture("--session", "cccccccc-3").returncode, 0)
        self.assertEqual(self.entries(CODEX)[CODEX]["harness"], "codex")
        self.assertEqual(self.entries(PI)[PI]["harness"], "pi")


class RefusalTests(NamedCase):
    def test_S2_an_unknown_ref_names_the_directory_looked_in_for_each_harness(self):
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
        result = self.capture("--session", "nosuchsession")
        self.assertEqual(result.returncode, 2)
        for place in (self.claude / "projects", self.codex / "sessions", self.pi / "sessions"):
            self.assertIn(str(place), result.stderr)
        self.assertEqual(self.tree(), [])

    def test_S2_an_ambiguous_ref_names_every_candidate_with_its_harness(self):
        pi_session(self.pi, "--a--", "p1", PI, "/work/app", OLD)
        pi_session(self.pi, "--a--", "p2", PI2, "/work/app", OLD)
        codex_rollout(
            self.codex, "2026/11/10", "a", "cccccccc-5555-4555-8555-cccccccccccc", "/w", OLD
        )
        result = self.capture("--session", "cccccccc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous", result.stderr)
        self.assertIn(f"pi {PI}", result.stderr)
        self.assertIn(f"pi {PI2}", result.stderr)
        self.assertIn("codex cccccccc-5555-4555-8555-cccccccccccc", result.stderr)
        self.assertEqual(self.tree(), [])

    def test_S2_a_session_under_two_harnesses_names_the_two_places(self):
        codex_file = codex_rollout(self.codex, "2026/11/10", "a", SPLIT, "/work/app", OLD)
        pi_file = pi_session(self.pi, "--a--", "p", SPLIT, "/work/app", OLD)
        result = self.capture("--session", SPLIT)
        self.assertEqual(result.returncode, 2)
        self.assertIn(str(codex_file), result.stderr)
        self.assertIn(str(pi_file), result.stderr)
        self.assertEqual(self.tree(), [])

    def test_S2_a_bad_ref_beside_a_good_one_writes_nothing(self):
        pi_session(self.pi, "--a--", "p", PI, "/work/app", OLD)
        result = self.capture("--session", PI, "--session", "nosuchsession")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.tree(), [])


class RootTests(NamedCase):
    def test_S3_the_first_named_session_is_the_root_whatever_the_age(self):
        pi_session(self.pi, "--a--", "p", PI, "/work/app", NEW)
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
        result = self.capture("--session", PI, "--session", CODEX)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PI)
        self.assertEqual((entries[PI]["role"], entries[CODEX]["role"]), ("root", "session"))
        self.assertEqual(entries[CODEX]["harness"], "codex")

    def test_S3_a_codex_session_beside_a_claude_chain_is_a_later_session(self):
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
        result = self.capture("--session", CHAIN, "--session", CODEX)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(RESUMED)
        self.assertEqual(entries[RESUMED]["role"], "root")
        self.assertEqual(entries[CODEX]["role"], "session")

    def test_S3_a_named_session_that_is_also_a_worker_joins_once_with_its_role(self):
        self.make_run()
        pi_session(self.pi, "--a--", "p", PI, WT, INSIDE, "do it")
        claude_session(self.claude, "-wt1", WORKER, WT, INSIDE)
        result = self.run_capture("--session", PI)
        self.assertEqual(result.returncode, 0, result.stderr)
        sessions = self.manifest(PI)["sessions"]
        self.assertEqual([s["id"] for s in sessions].count(PI), 1)
        entries = {s["id"]: s for s in sessions}
        self.assertEqual((entries[PI]["role"], entries[WORKER]["role"]), ("root", "worker"))

    def test_S3_exclude_leaves_a_named_session_out_and_lists_it(self):
        pi_session(self.pi, "--a--", "p", PI, "/work/app", OLD)
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/app", OLD)
        result = self.capture("--session", PI, "--session", CODEX, "--exclude", "cccccccc")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest(PI)
        self.assertEqual(manifest["excluded"], [PI])
        self.assertEqual([s["id"] for s in manifest["sessions"]], [CODEX])

    def test_S3_a_named_session_alone_with_a_run_names_the_case_after_it(self):
        self.make_run()
        pi_session(self.pi, "--a--", "p", PI, "/work/app", OLD)
        result = self.run_capture("--session", PI)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.entries(PI)[PI]["role"], "root")


class ManifestFieldTests(NamedCase):
    def test_S4_a_named_session_records_its_cwd_and_assignment_id(self):
        pi_session(self.pi, "--a--", "p", PI, "/work/app", OLD, PROMPT % ASSIGNMENT)
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, "/work/x", OLD, "no id here")
        claude_session(self.claude, "-app", WORKER, "/work/c", OLD, PROMPT % ASSIGNMENT)
        result = self.capture("--session", PI, "--session", CODEX, "--session", WORKER)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(PI)
        self.assertEqual(
            (entries[PI]["cwd"], entries[PI]["assignment_id"]), ("/work/app", ASSIGNMENT)
        )
        self.assertEqual(
            (entries[CODEX]["cwd"], entries[CODEX]["assignment_id"]), ("/work/x", None)
        )
        self.assertEqual(
            (entries[WORKER]["cwd"], entries[WORKER]["assignment_id"]), ("/work/c", ASSIGNMENT)
        )

    def test_S4_a_session_without_a_cwd_in_its_records_records_null(self):
        result = self.capture("--session", SOLO)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(SOLO)[SOLO]
        self.assertEqual((entry["cwd"], entry["assignment_id"]), (None, None))

    def test_S4_the_same_session_by_name_and_as_a_worker_records_the_same_fields(self):
        self.make_run()
        claude_session(self.claude, "-wt1", WORKER, WT, INSIDE, PROMPT % ASSIGNMENT)
        pi_session(self.pi, "--a--", "p", PI, WT, INSIDE, PROMPT % ASSIGNMENT)
        codex_rollout(self.codex, "2026/11/10", "a", CODEX, WT, INSIDE, PROMPT % ASSIGNMENT)
        by_run = self.run_capture("--case", "by-run")
        self.assertEqual(by_run.returncode, 0, by_run.stderr)
        by_name = self.capture(
            "--case", "by-name", "--session", WORKER, "--session", PI, "--session", CODEX
        )
        self.assertEqual(by_name.returncode, 0, by_name.stderr)
        run_entries, name_entries = self.entries("by-run"), self.entries("by-name")
        for sid in (WORKER, PI, CODEX):
            self.assertEqual(name_entries[sid]["role"] != "worker", True)
            for field in ("cwd", "assignment_id"):
                self.assertEqual(name_entries[sid][field], run_entries[sid][field], (sid, field))
            self.assertEqual(name_entries[sid]["assignment_id"], ASSIGNMENT)
            self.assertEqual(name_entries[sid]["cwd"], WT)

    def test_S4_a_subagent_records_the_cwd_and_assignment_id_of_its_own_records(self):
        claude_session(self.claude, "-app", WORKER, "/work/c", OLD)
        write(
            self.claude / "projects" / "-app" / WORKER / "subagents" / "agent-s1.jsonl",
            '{"type":"user","cwd":"/work/sub","message":{"role":"user","content":"hi"}}\n',
        )
        result = self.capture("--session", WORKER)
        self.assertEqual(result.returncode, 0, result.stderr)
        entry = self.entries(WORKER)["agent-s1"]
        self.assertEqual((entry["cwd"], entry["assignment_id"]), ("/work/sub", None))


class ControlTests(NamedCase):
    """What already held: these pass before the change and must after it."""

    def test_S1_S2_S3_S4_a_claude_session_is_still_captured_by_name(self):
        result = self.capture("--session", CHAIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(RESUMED)
        self.assertEqual(entries[RESUMED]["harness"], "claude")
        self.assertEqual(entries[CHAIN]["role"], "continuation")

    def test_S1_S2_S3_S4_a_claude_ref_that_is_ambiguous_is_still_refused(self):
        claude_session(self.claude, "-app", "11111111-ffff-4fff-8fff-ffffffffffff", "/w", OLD)
        result = self.capture("--session", "11111111")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous", result.stderr)
        self.assertEqual(self.tree(), [])
