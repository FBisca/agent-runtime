"""The yaml-eec-gaps plan's outcome: the gaps the yaml-eec review left open are closed.

It drives the engine through the suite's existing seams and its CLI contract only:

  run <plan> ...                    -> the driver loop; <run_dir>/attention.md lists what needs the person

The class carries a `control` test that holds whatever the change, so a selection by class always
has a passing control beside its cases.
"""

import unittest

import pytest
from test_decider import DeciderFixture

pytestmark = pytest.mark.integration


class AttentionRefresh(DeciderFixture, unittest.TestCase):
    """S4: attention.md lists only what the ledger still holds as needing the person."""

    def setUp(self):
        super().setUp()
        self.set_plan(("T1", "T4"))

    @pytest.mark.spec("Person tickets")
    def test_control_a_waiting_person_ticket_is_listed_once(self):
        self.run_driver()
        self.run_driver(expect=1)
        self.assertEqual(self.attention_md().count("sign-off"), 1)
        self.assertIn("T4", self.attention_md())

    @pytest.mark.spec("Person tickets")
    def test_a_person_ticket_set_complete_leaves_the_attention_file(self):
        self.run_driver()
        self.assertIn("sign-off", self.attention_md())
        self.set_ticket_status("T4", "COMPLETE 2026-11-12")
        self.run_driver(expect=1)
        self.assertEqual(self.ticket_status("T4"), "COMPLETE 2026-11-12")
        self.assertNotIn(
            "sign-off", self.attention_md(), "a completed person ticket still reads needs you"
        )
        self.assertNotIn("T4", self.attention_md())
        self.assertIn(
            "# Needs you", self.attention_md(), "nothing waits, so the file keeps its header only"
        )

    @pytest.mark.spec("Person tickets")
    def test_a_person_ticket_set_complete_during_a_run_leaves_the_file_in_that_run(self):
        self.run_driver()
        self.assertIn("sign-off", self.attention_md())
        self.waits([self.lead_sets(T4="COMPLETE 2026-11-12")])
        self.run_driver(expect=1)
        self.assertNotIn(
            "sign-off", self.attention_md(), "the file was not rebuilt on a later tick"
        )


if __name__ == "__main__":
    unittest.main()
