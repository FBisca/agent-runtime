"""The driver archives with no agent: the advisory gaps reviewer starts only once the archive PR has merged."""

import json
import unittest

import fake_gh
import pytest
from test_archive import ArchiveFixture

pytestmark = pytest.mark.integration

GAPS = "demo-gaps"


class GapsAfterArchiveTests(ArchiveFixture, unittest.TestCase):
    def test_S6_before_the_archive_merges_no_gaps_reviewer_exists(self):
        """orchestrator-engine/R2: deterministic-archive/S6 — the archive PR is closed unmerged, so nothing gaps."""
        real = self.gh_process

        def closed_archive(argv, timeout=None, cwd=None):
            if argv[:3] == ["pr", "view", "https://github.com/o/r/pull/9"]:
                row = {"state": "CLOSED", "isDraft": False, "mergeStateStatus": "UNKNOWN"}
                row.update(mergeable="UNKNOWN", mergeCommit=None)
                return fake_gh.Completed(argv, 0, json.dumps(row))
            return real(argv, timeout=timeout, cwd=cwd)

        self.gh_process = closed_archive
        self.waits(self.ticket_merged() + [{}, {}, self.decide_step("archive", "skip")])
        self.run_driver()
        self.assertNotIn(GAPS, self.spawned())
        self.assertFalse(self.task("archive").get("merge_commit"))

    def test_S6_once_the_archive_merges_the_gaps_reviewer_starts_and_is_the_only_one(self):
        """orchestrator-engine/R2: deterministic-archive/S6 — the merged archive spawns the reviewer exactly once."""
        self.waits(self.ticket_merged())
        self.run_driver()
        self.assertEqual(self.task("archive")["gate"], "done")
        self.assertEqual(self.spawned().count(GAPS), 1)

    def test_S6_the_run_ends_while_the_gaps_reviewer_is_still_open(self):
        """orchestrator-engine/R2: deterministic-archive/S6 — its assignment stays open in the runtime; the run ends."""
        held = {"agents": [GAPS]}  # the reviewer holds its assignment open and never reports
        self.waits(self.ticket_merged() + [held, held, held])
        self.run_driver()
        self.assertEqual(self.spawned().count(GAPS), 1)
        self.assertEqual(self.task("archive")["gate"], "done")
        self.assertFalse(self.ledger()["gaps"]["ended"])
