"""The Pi backend and the brain tier: which backend each tier of a run spawns on."""

import unittest

import pytest
from test_archive import ARCHIVE, ArchiveFixture

pytestmark = pytest.mark.integration

PI_MODEL = "minimax/MiniMax-M3.1-Flash"
GAPS = "demo-gaps"
REVIEW = "demo-t1-review"
WORKER = "demo-t1"


class PiBackendTests(ArchiveFixture, unittest.TestCase):
    """A run on Pi: the workers run on Pi, the review gates run on the brain backend."""

    def setUp(self):
        super().setUp()
        self.waits(self.ticket_merged() + [self.gaps_done()])

    def spawns(self):
        return {call["args"]["name"]: call["args"] for call in self.calls("spawn")}

    def test_S1_a_pi_run_names_the_pi_family_and_its_model(self):
        """orchestrator-engine/R2: pi-worker-driver/S1 — the worker tier runs on Pi, with the Pi model."""
        self.run_driver("--cli", "pi")
        worker = self.spawns()[WORKER]
        self.assertEqual((worker["cli"], worker["model"]), ("pi", PI_MODEL))
        self.assertEqual(worker["effort"], "low")

    def test_S2_the_models_file_pi_column_wins_when_it_is_there(self):
        """orchestrator-engine/R2: pi-worker-driver/S2 — a Pi column in the models file answers the classes."""
        self.models.write_text(
            "## Model classes\n\n| Class | Claude | Codex | Pi |\n|---|---|---|---|\n"
            '| small | `model: "haiku"` | `gpt-s` | `pi-small` |\n'
            '| medium | `model: "sonnet"` | `gpt-m` | `pi-medium` |\n'
            '| large | `model: "opus"` | `gpt-l` | `pi-large` |\n',
            encoding="utf-8",
        )
        self.run_driver("--cli", "pi")
        worker = self.spawns()[WORKER]
        self.assertEqual(worker["model"], "pi-small")

    def test_S2_without_a_pi_column_the_skill_table_answers(self):
        """orchestrator-engine/R2: pi-worker-driver/S2 — the run starts even when the models file knows no Pi."""
        self.run_driver("--cli", "pi")
        self.assertEqual(self.spawns()[WORKER]["model"], PI_MODEL)

    def test_S3_the_reviewer_spawns_on_the_brain_backend(self):
        """orchestrator-engine/R2: pi-worker-driver/S3 — the merge gate runs on the brain, on the brain's medium model."""
        self.run_driver("--cli", "pi", "--brain-cli", "codex")
        review = self.spawns()[REVIEW]
        self.assertEqual(
            (review["cli"], review["model"], review["effort"]), ("codex", "gpt-m", "high")
        )

    def test_S4_the_gaps_reviewer_spawns_on_the_brain_backend(self):
        """orchestrator-engine/R2: pi-worker-driver/S4 — the gaps review is a gate too."""
        self.run_driver("--cli", "pi", "--brain-cli", "codex")
        gaps = self.spawns()[GAPS]
        self.assertEqual((gaps["cli"], gaps["model"], gaps["effort"]), ("codex", "gpt-m", "medium"))

    def test_S5_the_worker_tiers_stay_on_the_worker_backend(self):
        """orchestrator-engine/R2: pi-worker-driver/S5 — the implementer stays on Pi; the driver archives with no agent."""
        self.run_driver("--cli", "pi", "--brain-cli", "codex")
        spawns = self.spawns()
        self.assertEqual(spawns[WORKER]["cli"], "pi")
        self.assertNotIn(ARCHIVE, spawns)

    def test_S6_a_claude_run_keeps_every_tier_where_it_is(self):
        """orchestrator-engine/R2: pi-worker-driver/S6 — without a brain backend nothing moves."""
        self.run_driver("--cli", "claude")
        spawns = self.spawns()
        self.assertEqual((spawns[REVIEW]["cli"], spawns[REVIEW]["model"]), ("claude", "opus"))
        self.assertEqual((spawns[GAPS]["cli"], spawns[GAPS]["model"]), ("claude", "sonnet"))
        self.assertEqual((spawns[WORKER]["cli"], spawns[WORKER]["model"]), ("claude", "haiku"))

    def test_S6_a_codex_run_keeps_every_tier_where_it_is(self):
        """orchestrator-engine/R2: pi-worker-driver/S6 — the same for the Codex column."""
        self.run_driver("--cli", "codex")
        spawns = self.spawns()
        self.assertEqual((spawns[REVIEW]["cli"], spawns[REVIEW]["model"]), ("codex", "gpt-l"))
        self.assertEqual((spawns[WORKER]["cli"], spawns[WORKER]["model"]), ("codex", "gpt-s"))
