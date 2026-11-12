"""The driver archives with no agent: the section it writes, the questions it asks, the refusal of an `archive` route."""

import json
import re
import unittest
from types import SimpleNamespace
from unittest import mock

import pytest
from fixtures import load_script
from test_archive import ARCHIVE, ArchiveFixture

pytestmark = pytest.mark.integration


def driver_with(scenarios, covers, slug="demo"):
    """A Driver that knows its plan's scenarios and the tickets' covers, nothing else (no run, no ledger)."""
    driver = load_script().Driver.__new__(load_script().Driver)
    driver.slug = slug
    driver.model = SimpleNamespace(scenarios=scenarios)
    driver.ticket_covers = lambda: covers
    return driver


def scenario(sid, when="a", then="b", human=None, place=None, oracle=()):
    evidence = SimpleNamespace(human=human, promote=True, place=place, oracle=list(oracle))
    return SimpleNamespace(
        id=sid, when=when, then=then, evidence=evidence if (human or place) else None
    )


class SpecSectionUnit(unittest.TestCase):
    def test_S2_a_scenario_the_spec_lacks_is_a_requirement_and_a_coverage_row(self):
        driver = driver_with([scenario("S1", "a  b", "c\nd")], {"S1": ["t.py::t"]})
        text = driver.spec_section("- **R4** x", {})
        self.assertEqual(
            text,
            "## Requirements: demo\n\n- **R5** WHEN a b, THEN c d (from demo/S1)\n\n"
            "## Coverage: demo\n\n| Requirement | Scenario | Test nodes |\n|---|---|---|\n| R5 | S1 | `t.py::t` |\n",
        )

    def test_S2_a_held_scenario_keeps_its_number_and_adds_no_requirement(self):
        driver = driver_with([scenario("S1")], {"S1": ["t.py::t"]})
        text = driver.spec_section("", {"S1": "R9"})
        self.assertNotIn("## Requirements", text)
        self.assertIn("| R9 | S1 | `t.py::t` |", text)

    def test_S2_the_nodes_are_the_covers_then_the_placed_oracle_joined_by_br(self):
        driver = driver_with(
            [scenario("S1", place="src/", oracle=["o.py.oracle"])], {"S1": ["a::b", "c::d"]}
        )
        self.assertIn("| `a::b`<br>`c::d`<br>`src/o.py` |", driver.spec_section("", {}))

    def test_S3_a_human_scenario_reads_its_evidence_and_needs_no_node(self):
        driver = driver_with([scenario("S1", human="check | by hand")], {})
        self.assertIn("| S1 | human: check \\| by hand |", driver.spec_section("", {}))
        self.assertEqual(driver.archive_missing(), [])

    def test_S4_a_scenario_with_no_node_and_no_oracle_is_missing(self):
        driver = driver_with(
            [scenario("S1"), scenario("S2", human="x"), scenario("S3")], {"S1": ["a::b"]}
        )
        self.assertEqual(driver.archive_missing(), ["S3"])


class DriverArchive(ArchiveFixture, unittest.TestCase):
    def test_S1_the_archive_is_committed_pushed_and_opened_by_the_driver_with_no_trailer(self):
        self.waits(self.ticket_merged() + [self.gaps_done()])
        self.run_driver()
        self.assertNotIn(ARCHIVE, self.spawned())
        (create,) = self.gh_calls("pr", "create")
        self.assertIn("--draft", create)
        self.assertNotIn("Co-Authored-By", " ".join(create))
        message = self.git(self.origin, "log", "-1", "--format=%B", "feat/demo-archive")
        self.assertNotRegex(message, r"(?i)co-authored|generated with")

    def test_S4_a_missing_node_opens_no_pr_and_asks_the_lead(self):
        driver = driver_with([scenario("S1")], {})
        driver.request = mock.Mock()
        driver.archive_missing = load_script().Driver.archive_missing.__get__(driver)
        driver.scenario_nodes = load_script().Driver.scenario_nodes.__get__(driver)
        driver.build_archive = load_script().Driver.build_archive.__get__(driver)
        driver.build_archive("archive")
        task, kind, reason = driver.request.call_args.args
        self.assertEqual((task, kind), ("archive", "archive"))
        self.assertRegex(reason, r"S1.*`retry`.*`skip`")

    def failing_once(self):
        real, calls = load_script().Driver.archive_evidence, []

        def evidence(driver, task_id, packet):
            calls.append(task_id)
            if len(calls) == 1:
                return "FAIL", [
                    {
                        "id": "E1",
                        "kind": "blocking",
                        "source": "verify",
                        "check": "evidence S1",
                        "command": "x",
                        "exit": 1,
                        "excerpt": "S1 boom",
                        "log_path": "/nowhere",
                    }
                ]
            return real(driver, task_id, packet)

        return mock.patch.object(load_script().Driver, "archive_evidence", evidence)

    def test_S5_a_failing_outcome_asks_the_lead_with_retry_and_skip_and_spawns_no_worker(self):
        self.waits(self.ticket_merged() + [self.gaps_done(), self.decide_step("archive", "skip")])
        with self.failing_once():
            self.run_driver()
        self.assertNotIn(ARCHIVE, self.spawned())
        request = self.request("archive")
        self.assertEqual(request["menu"], ["retry", "skip"])
        self.assertIn("S1 boom", request["reason"])
        self.assertEqual(self.task("archive")["gate"], "skipped")
        self.assertEqual(self.task("archive")["status"], "RELEASED")

    def test_S5_retry_rebuilds_the_branch_from_the_base_and_the_plan_is_archived(self):
        self.waits(self.ticket_merged() + [self.gaps_done(), self.decide_step("archive", "retry")])
        with self.failing_once():
            self.run_driver()
        self.assertEqual(len(self.gh_calls("pr", "create")), 1)  # the open pull request is reused
        self.assertEqual([h["state"] for h in self.ledger()["plan"]["history"]][-1], "ARCHIVED")
        self.assertEqual(
            self.git(self.origin, "rev-list", "--count", "main..feat/demo-archive"), "1"
        )


class ArchiveRouteRefusal(ArchiveFixture, unittest.TestCase):
    def test_S7_a_route_config_naming_the_archive_step_is_refused_before_any_spawn(self):
        path = self.root / "routes.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "routes": [{"class": "small", "backend": "claude", "model": "haiku"}],
                    "steps": [{"step": "archive", "backend": "claude", "model": "haiku"}],
                }
            ),
            encoding="utf-8",
        )
        result = self.run_driver("--routes-file", str(path), expect=2)
        self.assertRegex(result.stderr, r"archive.*no agent.*remove the `archive` route")
        self.assertEqual(self.calls("spawn"), [])

    def test_S7_a_step_override_naming_the_archive_step_is_refused_before_any_spawn(self):
        result = self.run_driver("--step", "archive=claude/haiku", expect=2)
        self.assertRegex(result.stderr, r"archive.*no agent.*remove the `archive` route")
        self.assertEqual(self.calls("spawn"), [])

    def test_S7_the_other_step_routes_still_work(self):
        routes = load_script().routes
        self.assertEqual(routes.step_override("gaps=claude/haiku").step, "gaps")
        self.assertIsNone(re.search("archive", ",".join(routes.STEPS)))
