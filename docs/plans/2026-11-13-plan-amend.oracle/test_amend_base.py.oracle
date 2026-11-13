"""`plan amend` baselines at the approval's own base, so work merged since cannot fail the re-approval the way a
fresh `plan approve` at `origin/main` does (plan-amend/S8)."""

import unittest
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration

GUARD = {"check": "test ! -f src/flag", "baseline": "pass"}


def guarded(data):
    """S1 must keep holding: `src/flag` is absent at base. A merged ticket will add it."""
    data["scenarios"][0]["evidence"] = dict(GUARD)


class AmendBase(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        # The Lead runs these commands: a worker's own session must not turn them into the worker refusal.
        patcher = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.set_plan(("T1",), mutate=guarded)
        self.approval = yaml.safe_load(self.plan.read_text(encoding="utf-8"))["approval"]
        # A ticket merged since the approval: main now carries the file the baseline checks guard against.
        (self.repo / "src").mkdir(exist_ok=True)
        (self.repo / "src" / "flag").write_text("merged\n", encoding="utf-8")
        self.git(self.repo, "add", "src/flag")
        self.git(self.repo, "commit", "-q", "-m", "a merged ticket")
        self.git(self.repo, "push", "-q", "origin", "main")
        # The edit adds T3 and gives its S3 a new check item: that baseline must run, and only passes at base.
        data = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        data["scenarios"][2]["evidence"] = dict(GUARD)
        data["tickets"].append(
            dict(
                id="T3",
                delivers="third",
                satisfies=["S3"],
                paths=["src/c/"],
                size="regular",
                difficulty="simple",
            )
        )
        self.plan.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def plan_cli(self, *args):
        return self.cli("plan", *args, "--repo", str(self.repo), "--state-dir", str(self.state_dir))

    def test_S8_amend_baselines_the_new_item_at_the_approval_base_and_succeeds(self):
        result = self.plan_cli("amend", str(self.plan), "--reason", "add T3")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        self.assertEqual(data["approval"]["base"], self.approval["base"])
        self.assertNotEqual(data["approval"]["digest"], self.approval["digest"])
        entry = data["amendments"][-1]
        self.assertEqual(entry["prev_digest"], self.approval["digest"])
        self.assertEqual(entry["digest"], data["approval"]["digest"])

    def test_control_a_fresh_approve_at_origin_main_refuses_the_same_edit(self):
        result = self.plan_cli("approve", str(self.plan))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("baseline", (result.stdout + result.stderr).lower())
        data = yaml.safe_load(self.plan.read_text(encoding="utf-8"))
        self.assertEqual(data["approval"]["digest"], self.approval["digest"])
