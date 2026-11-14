"""The RTK proxy launcher at the report gate: an honest proxied report matches
the required check, and a command behind the same launcher that is not the
required check stays refused."""

import unittest

import pytest
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration


class RtkProxyAcceptance(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        config = self.repo / ".orchestrate" / "config.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "checks:\n  fast: uv run pytest -q\n  full:\n    run: uv run pytest -q\n",
            encoding="utf-8",
        )
        self.set_plan(("T1",))
        self.run_driver()
        self.task = self.ledger()["tasks"]["t1"]

    def report(self, command, outcome="passed"):
        return {
            "acceptance_met": True,
            "remaining": [],
            "review_rounds": 0,
            "usage": "unknown",
            "checks": [{"command": command, "outcome": outcome}],
        }

    def test_control_a_bare_report_passes_and_an_off_list_prefix_is_refused(self):
        """The rule the change must not break: the bare spelling matches the
        required check, and a prefix off the list does not."""
        load_script().require_completable(self.task, self.report("uv run pytest -q"))
        with self.assertRaisesRegex(ValueError, "required checks"):
            load_script().require_completable(self.task, self.report("echo uv run pytest -q"))

    def test_S1_a_proxied_report_matches_the_required_check(self):
        """A worker whose shell rewrites the command reports the proxied
        spelling; the gate accepts that spelling, whatever launcher follows
        the proxy."""
        for command in ("rtk proxy uv run pytest -q", "rtk proxy python3 -m pytest -q"):
            with self.subTest(command=command):
                try:
                    load_script().require_completable(self.task, self.report(command))
                except ValueError as error:
                    self.fail("the gate refused an honest proxied report: %s" % error)

    def test_S2_a_proxied_off_list_prefix_is_still_refused(self):
        """The proxy is a launcher, not a bypass: a command behind it that is
        not the required check stays refused, and the refusal names the check
        that is missing."""
        with self.assertRaises(ValueError) as caught:
            load_script().require_completable(
                self.task, self.report("rtk proxy echo uv run pytest -q")
            )
        message = str(caught.exception)
        self.assertIn("required checks", message)
        self.assertIn("uv run pytest -q", message)

    def test_S4_the_contract_names_the_prefixes_the_strip_list_holds(self):
        """The contract the worker reads names every launcher prefix the
        engine's strip list holds, `rtk proxy` among them."""
        contract = (self.run_dir_of() / "contract.md").read_text(encoding="utf-8")
        for prefix in load_script().CHECK_PREFIXES:
            self.assertIn(" ".join(prefix), contract)
        self.assertIn("rtk proxy", contract)
