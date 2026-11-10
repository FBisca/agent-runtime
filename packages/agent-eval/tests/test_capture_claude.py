"""Capture a Claude run: chains, subagents, tool results, bytes, case id, exclusion, age, patch,
duplicates and permissions (agent-eval-p0/S1-S7a, S11)."""

import gzip
import hashlib
import json
import os
import stat

import pytest
from claude_fixture import (
    CHAIN,
    FORKED,
    OTHER,
    PROJECT,
    RESUMED,
    SOLO,
    CaptureCase,
    git,
    job,
    make_repo,
    write,
)

pytestmark = pytest.mark.integration

CHAIN_IDS = {RESUMED, CHAIN, FORKED, "agent-a1", "agent-a2"}


class ChainTests(CaptureCase):
    def test_S1_resume_fork_subagents_meta_and_tool_results(self):
        """agent-eval-p0/S1"""
        result = self.capture("--session", CHAIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(RESUMED)
        self.assertEqual(set(entries), CHAIN_IDS)
        for entry in entries.values():
            self.assertEqual(entry["harness"], "claude")
        self.assertEqual((entries[RESUMED]["role"], entries[RESUMED]["parent"]), ("root", None))
        self.assertEqual(
            (entries[CHAIN]["role"], entries[CHAIN]["parent"]), ("continuation", RESUMED)
        )
        self.assertEqual((entries[FORKED]["role"], entries[FORKED]["parent"]), ("fork", RESUMED))
        for agent in ("agent-a1", "agent-a2"):
            self.assertEqual(
                (entries[agent]["role"], entries[agent]["parent"]), ("subagent", CHAIN)
            )
            meta = entries[agent]["files"][0]
            self.assertEqual(meta["kind"], "meta")
            stored = self.case(RESUMED) / meta["file"]
            original = self.transcript(f"{CHAIN}/subagents/{agent}.meta.json").read_bytes()
            self.assertEqual(gzip.decompress(stored.read_bytes()), original)
        results = {
            sid: [f["file"] for f in entries[sid]["files"] if f["kind"] == "tool-result"]
            for sid in (RESUMED, CHAIN, FORKED)
        }
        self.assertEqual([len(v) for v in results.values()], [1, 1, 1])
        stored = self.case(RESUMED) / results[CHAIN][0]
        self.assertEqual(gzip.decompress(stored.read_bytes()), b"the full output of a tool\n")

    def test_S1_a_self_resuming_job_does_not_hide_the_continuation(self):
        """agent-eval-p0/S1: sessionId == resumeSessionId links nothing, and shadows nothing"""
        self.assertEqual(self.capture("--session", RESUMED).returncode, 0)
        self.assertEqual(set(self.entries(RESUMED)), CHAIN_IDS)

    def test_S1_naming_the_fork_captures_its_parent_and_siblings(self):
        """agent-eval-p0/S1: the walk follows forkParentSessionId both ways"""
        self.assertEqual(self.capture("--session", FORKED).returncode, 0)
        self.assertEqual(set(self.entries(RESUMED)), CHAIN_IDS)

    def test_S1_a_fork_with_its_own_resume_link_is_still_a_fork(self):
        """agent-eval-p0/S1: a job carrying both links is one edge from the fork parent"""
        job(
            self.claude, "66666666", sessionId=OTHER, resumeSessionId=SOLO, forkParentSessionId=SOLO
        )
        self.assertEqual(self.capture("--session", OTHER).returncode, 0)
        entries = self.entries(SOLO)
        self.assertEqual((entries[OTHER]["role"], entries[OTHER]["parent"]), ("fork", SOLO))

    def test_S1_unique_id_prefix_is_enough(self):
        """agent-eval-p0/S1"""
        self.assertEqual(self.capture("--session", CHAIN[:8]).returncode, 0)
        self.assertIn(CHAIN, self.entries(RESUMED))

    def test_S1_ambiguous_prefix_is_a_usage_error_and_writes_nothing(self):
        """agent-eval-p0/S1"""
        write(self.transcript("11111111-9999-4999-8999-999999999999.jsonl"), "{}\n")
        result = self.capture("--session", "11111111")
        self.assertEqual(result.returncode, 2)
        self.assertIn("ambiguous", result.stderr)
        self.assertEqual(self.tree(), [])

    def test_S1_unknown_session_fails_and_writes_nothing(self):
        """agent-eval-p0/S1"""
        result = self.capture("--session", "99999999-0000-4000-8000-000000000000")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("99999999", result.stderr)
        self.assertEqual(self.tree(), [])

    def test_S1_a_session_the_job_names_but_whose_file_is_deleted_is_listed_missing(self):
        """agent-eval-p0/S1"""
        gone = "77777777-ffff-4fff-8fff-ffffffffffff"
        job(self.claude, "77777777", sessionId=gone, resumeSessionId=SOLO)
        self.assertEqual(self.capture("--session", SOLO).returncode, 0)
        self.assertEqual(self.manifest(SOLO)["missing"], [gone])

    def test_S1_the_oldest_root_of_a_chain_with_two_roots_is_the_root(self):
        """agent-eval-p0/S4: two sessions resumed into one; the older job's session is the root"""
        job(
            self.claude,
            "88888888",
            sessionId=SOLO,
            resumeSessionId=SOLO,
            createdAt="2026-11-01T00:00:00Z",
        )
        job(
            self.claude,
            "99999999",
            sessionId=OTHER,
            resumeSessionId=OTHER,
            createdAt="2026-11-02T00:00:00Z",
        )
        merged = "aaaaaaaa-1111-4111-8111-111111111111"
        write(self.transcript(f"{merged}.jsonl"), "{}\n")
        job(
            self.claude,
            "aaaaaaaa",
            sessionId=merged,
            resumeSessionId=OTHER,
            forkParentSessionId=SOLO,
        )
        self.assertEqual(self.capture("--session", merged).returncode, 0)
        self.assertTrue(self.case(SOLO).is_dir())
        self.assertEqual(self.entries(SOLO)[SOLO]["role"], "root")

    def test_S2_repeated_session_joins_every_named_session_and_subagents(self):
        """agent-eval-p0/S2"""
        result = self.capture("--session", SOLO, "--session", OTHER)
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = self.entries(SOLO)
        self.assertEqual(set(entries), {SOLO, OTHER, "agent-o1"})
        self.assertEqual(entries["agent-o1"]["parent"], OTHER)
        self.assertEqual(entries[SOLO]["role"], "root")
        self.assertEqual((entries[OTHER]["role"], entries[OTHER]["parent"]), ("session", None))

    def test_S2_a_session_named_twice_or_also_in_the_chain_is_captured_once(self):
        """agent-eval-p0/S2"""
        self.assertEqual(self.capture("--session", CHAIN, "--session", RESUMED).returncode, 0)
        self.assertEqual(len(self.manifest(RESUMED)["sessions"]), len(CHAIN_IDS))

    def test_S1_a_symlink_in_a_session_directory_is_not_followed(self):
        """agent-eval-p0/S11: capture reads regular files only, so a link cannot pull in a secret"""
        secret = self.tmp / "secret.txt"
        secret.write_text("token\n")
        (self.transcript(f"{SOLO}") / "tool-results").mkdir(parents=True)
        (self.transcript(f"{SOLO}") / "tool-results" / "link.txt").symlink_to(secret)
        self.assertEqual(self.capture("--session", SOLO).returncode, 0)
        self.assertEqual(self.entries(SOLO)[SOLO]["files"], [])
        self.assertFalse(
            any(
                "token" in p.read_text(errors="ignore")
                for p in self.case(SOLO).rglob("*")
                if p.is_file() and p.suffix == ".txt"
            )
        )


class BytesTests(CaptureCase):
    def test_S3_stored_gzipped_with_sha256_and_size_of_the_original(self):
        """agent-eval-p0/S3"""
        self.assertEqual(self.capture("--session", CHAIN).returncode, 0)
        entries = self.entries(RESUMED)
        agent = entries["agent-a1"]
        files = [
            (entries[RESUMED], self.transcript(f"{RESUMED}.jsonl")),
            (entries[CHAIN], self.transcript(f"{CHAIN}.jsonl")),
            (agent, self.transcript(f"{CHAIN}/subagents/agent-a1.jsonl")),
            (agent["files"][0], self.transcript(f"{CHAIN}/subagents/agent-a1.meta.json")),
            (entries[CHAIN]["files"][0], self.transcript(f"{CHAIN}/tool-results/r1.txt")),
        ]
        self.assertEqual(len(files), 5)
        for record, source in files:
            original = source.read_bytes()
            self.assertTrue(record["file"].endswith(".gz"), record["file"])
            self.assertEqual(
                gzip.decompress((self.case(RESUMED) / record["file"]).read_bytes()), original
            )
            self.assertEqual(record["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(record["bytes"], len(original))


class CaseIdTests(CaptureCase):
    def test_S4_default_slug_is_the_root_session_id(self):
        """agent-eval-p0/S4"""
        self.assertEqual(self.capture("--session", CHAIN).returncode, 0)
        self.assertEqual(self.manifest(RESUMED)["schema"], "case/1")
        self.assertEqual(self.manifest(RESUMED)["case"], RESUMED)

    def test_S4_default_slug_is_the_root_even_when_the_root_is_excluded(self):
        """agent-eval-p0/S4"""
        result = self.capture("--session", CHAIN, "--exclude", RESUMED)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.case(RESUMED).is_dir())
        self.assertFalse(self.case(CHAIN).exists())

    def test_S4_the_slug_is_the_root_of_the_first_session_chain(self):
        """agent-eval-p0/S4"""
        self.assertEqual(self.capture("--session", SOLO, "--session", CHAIN).returncode, 0)
        self.assertTrue(self.case(SOLO).is_dir())

    def test_S4_case_flag_names_the_directory(self):
        """agent-eval-p0/S4"""
        self.assertEqual(self.capture("--session", CHAIN, "--case", "my-case").returncode, 0)
        self.assertTrue((self.case("my-case") / "manifest.json").is_file())
        self.assertFalse(self.case(RESUMED).exists())

    def test_S4_an_existing_case_is_refused_by_name_and_nothing_is_written(self):
        """agent-eval-p0/S4"""
        self.assertEqual(self.capture("--session", SOLO, "--case", "c1").returncode, 0)
        before = self.tree()
        digest = (self.case("c1") / "manifest.json").read_bytes()
        result = self.capture("--session", OTHER, "--case", "c1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("c1", result.stderr)
        self.assertEqual(self.tree(), before)
        self.assertEqual((self.case("c1") / "manifest.json").read_bytes(), digest)

    def test_S4_an_existing_default_case_is_refused(self):
        """agent-eval-p0/S4"""
        self.assertEqual(self.capture("--session", SOLO).returncode, 0)
        result = self.capture("--session", SOLO)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(SOLO, result.stderr)

    def test_S4_a_slug_cannot_leave_home(self):
        """agent-eval-p0/S4"""
        for bad in ("../escape", "a/b", "/abs", ".hidden", ""):
            result = self.capture("--session", SOLO, "--case", bad)
            self.assertNotEqual(result.returncode, 0, bad)
        self.assertEqual(self.tree(), [])
        self.assertFalse((self.tmp / "escape").exists())


class ExcludeTests(CaptureCase):
    def test_S5_an_excluded_session_is_not_copied_and_is_listed(self):
        """agent-eval-p0/S5"""
        result = self.capture("--session", CHAIN, "--case", "c", "--exclude", RESUMED)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.manifest("c")
        self.assertEqual(manifest["excluded"], [RESUMED])
        self.assertNotIn(RESUMED, self.entries("c"))
        self.assertEqual({s["id"] for s in manifest["sessions"]}, CHAIN_IDS - {RESUMED})
        copied = [p for p in self.case("c").rglob("*") if p.is_file()]
        self.assertFalse(any(RESUMED in str(p) or "r0" in p.name for p in copied))

    def test_S5_an_id_prefix_names_the_session_and_none_is_listed_by_default(self):
        """agent-eval-p0/S5"""
        self.assertEqual(self.capture("--session", SOLO).returncode, 0)
        self.assertEqual(self.manifest(SOLO)["excluded"], [])
        self.assertEqual(
            self.capture("--session", CHAIN, "--case", "p", "--exclude", FORKED[:8]).returncode, 0
        )
        self.assertEqual(self.manifest("p")["excluded"], [FORKED])

    def test_S5_excluding_a_session_outside_the_chain_fails_and_writes_nothing(self):
        """agent-eval-p0/S5"""
        result = self.capture("--session", SOLO, "--exclude", OTHER)
        self.assertEqual(result.returncode, 2)
        self.assertIn(OTHER[:8], result.stderr)
        self.assertEqual(self.tree(), [])


class AgeWarningTests(CaptureCase):
    def test_S6_a_25_5_day_old_session_warns_with_days_left_and_is_still_captured(self):
        """agent-eval-p0/S6"""
        self.age(f"{SOLO}.jsonl", 25.5)
        result = self.capture("--session", SOLO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(SOLO, result.stderr)
        self.assertIn("5 days left", result.stderr)
        self.assertIn(SOLO, self.entries(SOLO))

    def test_S6_a_24_9_day_old_session_does_not_warn(self):
        """agent-eval-p0/S6"""
        self.age(f"{SOLO}.jsonl", 24.9)
        result = self.capture("--session", SOLO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("days left", result.stderr)

    def test_S6_the_days_left_count_down_with_the_age(self):
        """agent-eval-p0/S6"""
        self.age(f"{SOLO}.jsonl", 27)
        result = self.capture("--session", SOLO)
        self.assertIn("27 days old", result.stderr)
        self.assertIn("3 days left", result.stderr)

    def test_S6_only_the_old_session_is_named(self):
        """agent-eval-p0/S6"""
        self.age(f"{RESUMED}.jsonl", 29.5)
        result = self.capture("--session", CHAIN)
        self.assertIn(RESUMED, result.stderr)
        self.assertNotIn(CHAIN, result.stderr)

    def test_S6_past_the_deadline_says_zero_days_left_and_still_captures(self):
        """agent-eval-p0/S6"""
        self.age(f"{SOLO}.jsonl", 31)
        result = self.capture("--session", SOLO)
        self.assertEqual(result.returncode, 0)
        self.assertIn("0 days left", result.stderr)


class GitPatchTests(CaptureCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.base, self.head = make_repo(self.repo)

    def test_S7_the_patch_holds_the_pinned_diff_and_the_manifest_the_resolved_shas(self):
        """agent-eval-p0/S7"""
        result = self.capture(
            "--session", SOLO, "--repo", str(self.repo), "--base", self.base[:10], "--head", "HEAD"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = git(
            self.repo,
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--src-prefix=a/",
            "--dst-prefix=b/",
            f"{self.base}..{self.head}",
        )
        self.assertIn("+two", expected)
        self.assertEqual((self.case(SOLO) / "changes.patch").read_text().strip(), expected)
        self.assertEqual(
            self.manifest(SOLO)["git"],
            {"repo": str(self.repo.resolve()), "base": self.base, "head": self.head},
        )

    def test_S7_the_patch_does_not_depend_on_the_users_diff_config(self):
        """agent-eval-p0/S7"""
        config = self.tmp / "gitconfig"
        config.write_text(
            "[diff]\n\tnoprefix = true\n\tmnemonicPrefix = true\n\texternal = /bin/false\n\trenames = false\n[color]\n\tui = always\n"
        )
        (self.repo / ".gitattributes").write_text("*.txt diff=loud\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "attrs")
        head = git(self.repo, "rev-parse", "HEAD")
        loud = {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"}
        result = self.capture(
            "--session",
            SOLO,
            "--repo",
            str(self.repo),
            "--base",
            self.base,
            "--head",
            head,
            env=loud,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        patch = (self.case(SOLO) / "changes.patch").read_text()
        self.assertIn("--- a/a.txt", patch)
        self.assertIn("+++ b/a.txt", patch)
        self.assertNotIn("\x1b[", patch)

    def test_S7_without_them_the_manifest_records_null_and_no_patch(self):
        """agent-eval-p0/S7"""
        self.assertEqual(self.capture("--session", SOLO).returncode, 0)
        self.assertIsNone(self.manifest(SOLO)["git"])
        self.assertFalse((self.case(SOLO) / "changes.patch").exists())

    def test_S7_partial_arguments_are_a_usage_error_and_write_nothing(self):
        """agent-eval-p0/S7"""
        result = self.capture("--session", SOLO, "--repo", str(self.repo), "--base", "base")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.tree(), [])

    def test_S7_an_unresolvable_revision_fails_and_writes_nothing(self):
        """agent-eval-p0/S7"""
        result = self.capture(
            "--session", SOLO, "--repo", str(self.repo), "--base", "nope", "--head", "HEAD"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("nope", result.stderr)
        self.assertEqual(self.tree(), [])


class DuplicateTests(CaptureCase):
    def test_S7a_one_id_under_two_project_directories_is_refused_naming_both(self):
        """agent-eval-p0/S7a"""
        twin = self.claude / "projects" / "-other-app" / f"{SOLO}.jsonl"
        write(twin, "{}\n")
        result = self.capture("--session", SOLO)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.transcript(f"{SOLO}.jsonl")), result.stderr)
        self.assertIn(str(twin), result.stderr)
        self.assertEqual(self.tree(), [])

    def test_S7a_a_duplicate_reached_through_the_chain_is_refused_too(self):
        """agent-eval-p0/S7a"""
        twin = self.claude / "projects" / "-other-app" / f"{CHAIN}.jsonl"
        write(twin, "{}\n")
        result = self.capture("--session", RESUMED)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(twin), result.stderr)
        self.assertEqual(self.tree(), [])


class PermissionTests(CaptureCase):
    def test_S11_directories_0700_and_files_0600_home_and_parents_included(self):
        """agent-eval-p0/S11"""
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        repo = self.tmp / "repo"
        base, head = make_repo(repo)
        deep = self.tmp / "state" / "nested" / "evals"
        result = self.capture(
            "--session", CHAIN, "--repo", str(repo), "--base", base, "--head", head, home=deep
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((deep / "cases" / RESUMED / "changes.patch").exists())
        created = [self.tmp / "state", self.tmp / "state" / "nested", deep, *deep.rglob("*")]
        self.assertGreater(len(created), 12)
        for path in created:
            self.assertEqual(self.mode(path), 0o700 if path.is_dir() else 0o600, path)

    def test_S11_a_home_inside_a_git_working_tree_is_refused_and_nothing_is_written(self):
        """agent-eval-p0/S11"""
        repo = self.tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q")
        result = self.capture("--session", SOLO, home=repo / "evals" / "deeper")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git", result.stderr)
        self.assertEqual(sorted(p.name for p in repo.iterdir()), [".git"])

    def test_S11_the_default_home_is_under_the_user_state_dir(self):
        """agent-eval-p0/S11"""
        from pathlib import Path

        from agent_eval import store

        old = os.environ.pop("AGENT_EVALS_HOME", None)
        try:
            self.assertEqual(store.home(), Path.home() / ".local/state/agent-evals")
        finally:
            if old is not None:
                os.environ["AGENT_EVALS_HOME"] = old

    def test_S11_nothing_is_left_behind_when_a_write_fails(self):
        """agent-eval-p0/S11: a failed capture removes its staging directory"""
        write(self.transcript(f"{SOLO}.jsonl"), "{}\n")
        self.transcript(f"{SOLO}.jsonl").chmod(0)
        self.addCleanup(self.transcript(f"{SOLO}.jsonl").chmod, stat.S_IRUSR | stat.S_IWUSR)
        if os.access(self.transcript(f"{SOLO}.jsonl"), os.R_OK):
            self.skipTest("running as a user that can read a mode-0 file")
        result = self.capture("--session", SOLO)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([p for p in self.tree() if ".tmp" in p], [])
        self.assertNotIn("Traceback", result.stderr)
