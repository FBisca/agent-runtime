"""A ticket whose Paths can hold no test is refused before it is built: the covers gate would strand its worker
(plan-amend/S1). A scenario that carries no evidence and that only person-owned tickets satisfy is refused too:
nothing can prove it (lead-decision-gates/S4, S5)."""

import unittest
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script, plan_data

pytestmark = pytest.mark.integration

CONFIG_ONLY = [".orchestrate/config.yaml", "pyproject.toml", "uv.lock"]


class PlanLint(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # The Lead runs these commands: a worker's own session must not turn them into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_plan(self, paths, owner=None):
        data = plan_data(("T1",))
        ticket = data["tickets"][0]
        ticket["paths"] = paths
        ticket["satisfies"] = ["S1", "S2"]
        if owner:
            ticket.pop("size")
            ticket.pop("difficulty")
            ticket["owner"] = owner
        path = self.repo / "docs" / "plans" / "lint.plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def plan_cli(self, verb, path):
        return self.cli(
            "plan", verb, str(path), "--repo", str(self.repo), "--state-dir", str(self.state_dir)
        )

    def assert_refused(self, result, path):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        said = result.stdout + result.stderr
        for needle in ("T1", "S1", "S2"):
            self.assertIn(needle, said)
        self.assertIn("paths", said.lower())
        self.assertIn("test", said.lower())
        self.assertNotIn("approval", yaml.safe_load(path.read_text(encoding="utf-8")))

    def test_S1_plan_check_refuses_an_agent_ticket_whose_paths_hold_no_test(self):
        path = self.write_plan(CONFIG_ONLY)
        self.assert_refused(self.plan_cli("check", path), path)

    def test_S1_plan_approve_refuses_it_and_writes_no_approval(self):
        path = self.write_plan(CONFIG_ONLY)
        self.assert_refused(self.plan_cli("approve", path), path)

    def test_S1_a_lone_source_file_cannot_hold_a_test_either(self):
        path = self.write_plan(["src/feature.py"])
        self.assert_refused(self.plan_cli("check", path), path)

    def test_S1_a_test_file_among_the_paths_is_enough(self):
        path = self.write_plan(CONFIG_ONLY + ["packages/x/tests/test_tiers.py"])
        result = self.plan_cli("check", path)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_S1_a_person_ticket_is_not_linted(self):
        path = self.write_plan(["docs/runbook.md"], owner="Felippe")
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                result = self.plan_cli(verb, path)
                self.assertEqual(result.returncode, 0, result.stderr)

    def write_person_plan(self, evidence=None, owner="Felippe", paths=None):
        """A plan whose scenario S2 carries no evidence and is satisfied only by a person-owned ticket."""
        data = plan_data(("T1",))
        scenario = next(s for s in data["scenarios"] if s["id"] == "S2")
        scenario.pop("evidence")
        if evidence is not None:
            scenario["evidence"] = evidence
        ticket = data["tickets"][0]
        ticket["satisfies"] = ["S2"]
        ticket["paths"] = paths if paths is not None else ["docs/"]
        ticket.pop("size")
        ticket.pop("difficulty")
        ticket["owner"] = owner
        path = self.repo / "docs" / "plans" / "person.plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def test_S4_plan_check_refuses_a_scenario_only_person_tickets_satisfy(self):
        path = self.write_person_plan()
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                result = self.plan_cli(verb, path)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                said = result.stdout + result.stderr
                for needle in ("S2", "T1", "human", "agent", "complete --cover"):
                    self.assertIn(needle, said)
                self.assertNotIn("approval", yaml.safe_load(path.read_text(encoding="utf-8")))

    def test_S5_plan_check_accepts_an_agent_ticket_or_human_evidence(self):
        # an agent ticket satisfies the scenario: its report's `covers` names the node
        data = plan_data(("T1",))
        scenario = next(s for s in data["scenarios"] if s["id"] == "S2")
        scenario.pop("evidence")
        ticket = data["tickets"][0]
        ticket["satisfies"] = ["S2"]
        ticket["paths"] = ["packages/x/tests/test_a.py"]
        path = self.repo / "docs" / "plans" / "agent.plan.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.assertEqual(self.plan_cli("check", path).returncode, 0)
        # a person-only scenario that carries `human` evidence: a person checks it
        path = self.write_person_plan(evidence={"human": "confirm"})
        self.assertEqual(self.plan_cli("check", path).returncode, 0)

    def test_control_a_person_ticket_that_holds_no_test_keeps_its_plan_approvable(self):
        # the person's work is not a test: its proof is a node recorded by hand, wherever it lives
        path = self.write_person_plan(paths=["docs/runbook.md"])
        self.assertEqual(self.plan_cli("check", path).returncode, 0)

    def test_control_a_directory_path_can_hold_a_test(self):
        path = self.write_plan(["src/a/"])
        result = self.plan_cli("check", path)
        self.assertEqual(result.returncode, 0, result.stderr)
