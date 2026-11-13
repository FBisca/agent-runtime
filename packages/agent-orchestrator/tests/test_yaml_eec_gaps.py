"""Outcome oracle for the yaml-eec-gaps plan: the gaps the yaml-eec review left open are closed.

Frozen at approval; placed into packages/agent-orchestrator/tests/ by the runner; never edited by a ticket.
It drives the engine through the suite's existing seams and its CLI contract only:

  plan check <plan> --repo <repo>   -> exit 0 valid; exit 2 naming each defect by YAML path and its fix
  verify <plan> --state-dir <d>     -> exit 0 iff acceptance holds on the receipts; else non-zero naming the reason
  run <plan> ...                    -> the driver loop; <run_dir>/attention.md lists what needs the person

Each class carries control cases that pass before and after the implementation, so a selection by class always
has a passing control beside the case that is red at base.
"""

import io
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script
from test_decider import DeciderFixture

pytestmark = pytest.mark.integration


def risk_contract(evidence, **ticket):
    """One scenario with `evidence`, satisfied by one ticket tagged `security` unless `ticket` says otherwise."""
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "outcome": ["S1"],
        "scenarios": [{"id": "S1", "when": "a", "then": "b", "evidence": evidence}],
        "tickets": [
            dict(
                {
                    "id": "T1",
                    "delivers": "first",
                    "satisfies": ["S1"],
                    "paths": ["src/a/"],
                    "size": "small",
                    "difficulty": "simple",
                    "risk": ["security"],
                },
                **ticket,
            )
        ],
    }


KEEPS = {"check": "true", "baseline": "pass"}
RED = {"check": "false", "baseline": "fail"}


def two_ticket_contract(first, second):
    """S1 (red at base) satisfied by T1, S2 (passing at base) satisfied by T2; `first` and `second` update T1 and T2."""
    data = risk_contract(dict(RED), **first)
    data["scenarios"].append({"id": "S2", "when": "c", "then": "d", "evidence": dict(KEEPS)})
    data["tickets"].append(
        dict(
            {
                "id": "T2",
                "delivers": "second",
                "satisfies": ["S2"],
                "paths": ["src/b/"],
                "size": "small",
                "difficulty": "simple",
            },
            **second,
        )
    )
    return data


class NegativeRule(RunFixture, unittest.TestCase):
    """S1: risk-tagged evidence that already passes at base needs a negative control (D14)."""

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)
        self.plan = self.plan.with_suffix(".yaml")
        self.oracles = self.plan.with_name("demo.oracle")
        self.oracles.mkdir()
        (self.oracles / "break.patch").write_text("", encoding="utf-8")
        patches = [
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "xdg")}),
            mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def check(self, data):
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    "check",
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                ]
            )
        return code, out.getvalue() + err.getvalue()

    def test_risk_evidence_passing_at_base_without_a_negative_is_named(self):
        code, message = self.check(risk_contract(dict(KEEPS)))
        self.assertEqual(
            code, 2, "a risk-tagged baseline-pass item with no negative was accepted: " + message
        )
        self.assertIn("scenarios[0].evidence", message)
        self.assertIn("negative", message)
        self.assertIn("patch", message, "the refusal does not name the fix")

    def test_the_refusal_names_the_scenario_its_risk_tagged_ticket_satisfies(self):
        """A waiver on another ticket exempts only that ticket; the path is the scenario's index, not the ticket's."""
        code, message = self.check(
            two_ticket_contract(
                {"evidence_waiver": "verified in the hook suite"}, {"risk": ["security"]}
            )
        )
        self.assertEqual(
            code, 2, "T2's baseline-pass evidence with no negative was accepted: " + message
        )
        self.assertIn("scenarios[1].evidence", message)
        self.assertNotIn("scenarios[0].evidence", message)

    def test_control_a_risk_tag_elsewhere_does_not_reach_an_untagged_tickets_scenario(self):
        code, message = self.check(two_ticket_contract({}, {}))
        self.assertEqual(code, 0, message)

    def test_control_risk_evidence_with_a_negative_is_valid(self):
        code, message = self.check(risk_contract(dict(KEEPS, negative="break.patch")))
        self.assertEqual(code, 0, message)

    def test_control_risk_evidence_red_at_base_needs_no_negative(self):
        code, message = self.check(risk_contract({"check": "false", "baseline": "fail"}))
        self.assertEqual(code, 0, message)

    def test_control_untagged_ticket_needs_no_negative(self):
        code, message = self.check(risk_contract(dict(KEEPS), risk=[]))
        self.assertEqual(code, 0, message)

    def test_control_human_evidence_needs_no_negative(self):
        code, message = self.check(risk_contract({"human": "a person confirms b"}))
        self.assertEqual(code, 0, message)

    def test_control_a_waived_ticket_needs_no_negative(self):
        code, message = self.check(
            risk_contract(dict(KEEPS), evidence_waiver="verified in the hook suite")
        )
        self.assertEqual(code, 0, message)


class AttentionRefresh(DeciderFixture, unittest.TestCase):
    """S4: attention.md lists only what the ledger still holds as needing the person."""

    def setUp(self):
        super().setUp()
        self.set_plan(("T1", "T4"))

    def test_control_a_waiting_person_ticket_is_listed_once(self):
        self.run_driver()
        self.run_driver(expect=1)
        self.assertEqual(self.attention_md().count("sign-off"), 1)
        self.assertIn("T4", self.attention_md())

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
