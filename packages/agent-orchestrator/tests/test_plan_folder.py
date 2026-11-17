"""plan/1 for a plan that lives in its own folder: the archive key, its check rules, and the digest and snapshot over
plan.yaml alone. Legacy plans keep their digests (S12)."""

import copy
import hashlib
import io
import json
import os
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import pytest
import yaml
from fixtures import RunFixture, load_script
from test_plan import contract

pytestmark = pytest.mark.integration


def folder_contract(**top):
    """The shared contract in a folder plan, with `top` keys set (None drops one)."""
    data = contract()
    data.update(top)
    return {k: v for k, v in data.items() if v is not None}


class FolderPlanTests(RunFixture, unittest.TestCase):  # noqa: PLR0904
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.folder = self.repo / "docs" / "plans" / "2026-11-12-demo"
        self.folder.mkdir(parents=True)
        (self.folder / "brief.md").write_text("# The plan's narrative\n")
        self.plan = self.folder / "plan.yaml"
        (self.repo / ".orchestrate").mkdir()
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"archive": {"mode": "promote"}})
        )
        self.write(folder_contract())
        patcher = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead-test"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        resolve = mock.patch.object(load_script(), "resolve_runtime", return_value={"role": "lead"})
        resolve.start()
        self.addCleanup(resolve.stop)

    def write(self, data):
        self.plan.write_text("# retained comment\n" + yaml.safe_dump(data, sort_keys=False))

    def command(self, verb, plan=None, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    verb,
                    str(plan or self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                    *extra,
                ]
            )
        return code, out.getvalue() + err.getvalue()

    def refused(self, data, *needles, plan=None):
        self.write(data)
        code, message = self.command("check", plan)
        self.assertEqual(code, 2, message)
        for needle in needles:
            self.assertIn(needle, message)
        return message

    def approve(self):
        code, message = self.command("approve")
        self.assertEqual(code, 0, message)
        return yaml.safe_load(self.plan.read_text())["approval"]

    # S3

    def test_S3_a_folder_plan_takes_the_archive_key(self):
        code, message = self.command("check")
        self.assertEqual(code, 0, message)
        for archive in ("promote", "discard"):
            with self.subTest(archive=archive):
                self.write(folder_contract(archive=archive))
                self.assertEqual(self.command("check")[0], 0)

    def test_S3_archive_is_a_mode(self):
        self.refused(folder_contract(archive="keep"), "archive")

    def test_S3_the_folder_key_is_refused_in_a_legacy_plan(self):
        legacy = self.folder.parent / "2026-11-10-demo.plan.yaml"
        data = contract()
        data["archive"] = "discard"
        legacy.write_text(yaml.safe_dump(data, sort_keys=False))
        code, message = self.command("check", legacy)
        self.assertEqual(code, 2, message)
        for needle in ("archive", "move the plan into a folder"):
            self.assertIn(needle, message)

    # S4

    def test_S4_the_repo_config_decides_promote_when_the_plan_does_not_say(self):
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"archive": {"mode": "discard"}})
        )
        self.write(folder_contract(spec=None))
        self.assertEqual(self.command("check")[0], 0)
        self.refused(folder_contract(spec=None, archive="promote"), "spec")

    def test_S4_a_promote_plan_needs_a_spec(self):
        self.refused(folder_contract(spec=None), "spec", "archive: discard")

    def test_S4_a_discard_plan_needs_no_spec(self):
        self.write(folder_contract(archive="discard", spec=None))
        code, message = self.command("check")
        self.assertEqual(code, 0, message)

    # The discard guard: a plan that discards is deleted, so its folder must sit directly under plans.dir

    def discarding_copy(self, parent, plans=None):
        """The plan folder copied under `parent`, discarding, with `plans` as the repo's plans.dir when given."""
        target = parent / self.folder.name
        shutil.copytree(self.folder, target)
        (target / "plan.yaml").write_text(
            yaml.safe_dump(folder_contract(archive="discard", spec=None), sort_keys=False)
        )
        if plans is not None:
            (self.repo / ".orchestrate" / "config.yaml").write_text(
                yaml.safe_dump({"plans": {"dir": str(plans)}})
            )
        return target / "plan.yaml"

    def refused_by_plans_dir(self, plan, plans):
        for verb in ("check", "approve"):
            code, message = self.command(verb, plan)
            self.assertEqual(code, 2, message)
            for needle in ("plans.dir", str(plans), str(plan.parent), "move"):
                self.assertIn(needle, message)

    def test_S3_a_discard_folder_outside_plans_dir_is_refused_by_check_and_approve(self):
        plans = self.root / "plans"
        plans.mkdir()
        plan = self.discarding_copy(self.root / "elsewhere", plans)
        self.refused_by_plans_dir(plan, plans)

    def test_S3_a_discard_folder_nested_below_plans_dir_is_refused(self):
        plans = self.root / "plans"
        plan = self.discarding_copy(plans / "nested", plans)
        self.refused_by_plans_dir(plan, plans)

    def test_S3_a_discard_folder_reached_through_a_symlink_is_refused(self):
        plans = self.root / "plans"
        plans.mkdir()
        target = self.discarding_copy(self.root / "elsewhere", plans).parent
        (plans / target.name).symlink_to(target)
        self.refused_by_plans_dir(plans / target.name / "plan.yaml", plans)

    def test_S3_a_discard_folder_directly_under_a_plans_dir_outside_the_repository_passes(self):
        plans = self.root / "plans"
        plan = self.discarding_copy(plans, plans)
        code, message = self.command("check", plan)
        self.assertEqual(code, 0, message)

    def test_S3_a_promote_folder_is_not_held_to_plans_dir_at_check(self):
        plans = self.root / "plans"
        plans.mkdir()
        self.discarding_copy(self.root / "elsewhere", plans)
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"plans": {"dir": str(plans)}})
        )
        self.assertEqual(self.command("check")[0], 0)  # the fixture's promote plan, in docs/plans

    # S5

    def test_S5_approve_digests_the_plan_alone(self):
        approval = self.approve()
        identity = Path(self.git(self.repo, "rev-parse", "--git-common-dir"))
        if not identity.is_absolute():
            identity = self.repo / identity
        canonical = yaml.safe_dump(folder_contract(), sort_keys=True).encode()
        parts = [str(identity.resolve()).encode(), self.base.encode(), canonical]
        expected = hashlib.sha256(
            b"".join(len(p).to_bytes(8, "big") + p for p in parts)
        ).hexdigest()
        self.assertEqual(approval["digest"], expected)
        snapshot = self.state_dir / "approvals" / expected
        self.assertEqual((snapshot / "plan.yaml").read_bytes(), canonical)
        self.assertEqual(sorted(p.name for p in snapshot.iterdir()), ["plan.yaml"])
        # A file beside the plan in its folder is no part of the contract: editing it leaves the approval standing.
        (self.folder / "brief.md").write_text("# edited\n")
        (self.folder / "notes").mkdir()
        (self.folder / "notes" / "x.txt").write_text("x")
        self.assertEqual(self.command("check")[0], 0)
        self.assertEqual(self.approve()["digest"], expected)

    def test_S5_a_changed_plan_is_named_and_can_be_approved_again(self):
        self.approve()
        self.plan.write_text(self.plan.read_text().replace("goal: deliver", "goal: changed"))
        code, message = self.command("check")
        self.assertEqual(code, 2)
        self.assertIn(str(self.plan), message)
        self.assertIn("plan approve", message)
        self.approve()
        self.assertEqual(self.command("check")[0], 0)

    def test_S5_the_plan_schema_describes_the_folder_keys(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(load_script().run_cli(["plan", "schema"]), 0)
        schema = json.loads(out.getvalue())
        self.assertTrue(schema["properties"]["archive"].get("description"))
        self.assertNotIn("spec", schema["required"])
        for gone in ("domain", "outcome"):
            self.assertNotIn(gone, schema["properties"])
        self.assertNotIn("Evidence", schema.get("$defs", {}))
        self.assertNotIn("evidence", schema["$defs"]["Scenario"]["properties"])

    # S12

    def legacy_plan(self):
        data = contract()
        directory = self.repo / "docs" / "plans"
        legacy = directory / "2026-11-10-demo.plan.yaml"
        legacy.write_text(yaml.safe_dump(data, sort_keys=False))
        return legacy, data

    def test_S12_a_legacy_plan_digests_as_before_with_or_without_a_repo_config(self):
        legacy, data = self.legacy_plan()
        config = self.repo / ".orchestrate" / "config.yaml"
        digests = []
        for configured in (True, False):
            if not configured:
                config.unlink()
            code, message = self.command("approve", legacy)
            self.assertEqual(code, 0, message)
            digests.append(yaml.safe_load(legacy.read_text())["approval"]["digest"])
            self.assertEqual(self.command("check", legacy)[0], 0)
        identity = Path(self.git(self.repo, "rev-parse", "--git-common-dir"))
        if not identity.is_absolute():
            identity = self.repo / identity
        parts = [
            str(identity.resolve()).encode(),
            self.base.encode(),
            yaml.safe_dump(data, sort_keys=True).encode(),
        ]
        expected = hashlib.sha256(
            b"".join(len(p).to_bytes(8, "big") + p for p in parts)
        ).hexdigest()
        self.assertEqual(digests, [expected, expected])
        snapshot = self.state_dir / "approvals" / expected
        self.assertEqual(sorted(p.name for p in snapshot.iterdir()), ["plan.yaml"])

    def test_S12_a_legacy_plan_still_needs_its_spec(self):
        legacy, data = self.legacy_plan()
        data2 = copy.deepcopy(data)
        del data2["spec"]
        legacy.write_text(yaml.safe_dump(data2, sort_keys=False))
        code, message = self.command("check", legacy)
        self.assertEqual(code, 2, message)
        self.assertIn("spec", message)
