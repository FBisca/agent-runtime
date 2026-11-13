"""plan/1 for a plan that lives in its own folder: the acceptance, package, negative, domain and archive keys, their
check rules, the digest and snapshot over the acceptance and negative files, and the schema. Legacy plans keep their
keys and digests (S12)."""

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

PROBE = "probe\n"
PATCH = "--- a/x\n+++ b/x\n"
DESTINATION = "pkg/accept/flows/probe.txt"


def folder_contract(**top):
    """The shared contract moved to the folder keys: one check item whose acceptance file is placed at DESTINATION."""
    data = contract()
    data["domain"] = "flows"
    data["scenarios"][0]["evidence"] = {
        "check": "test -f " + DESTINATION,
        "baseline": "pass",
        "acceptance": ["probe.txt"],
        "package": "pkg",
    }
    data.update(top)
    return {k: v for k, v in data.items() if v is not None}


class FolderPlanTests(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.folder = self.repo / "docs" / "plans" / "2026-11-12-demo"
        (self.folder / "acceptance").mkdir(parents=True)
        (self.folder / "negatives").mkdir()
        (self.folder / "acceptance" / "probe.txt").write_text(PROBE)
        (self.folder / "negatives" / "break.patch").write_text(PATCH)
        self.plan = self.folder / "plan.yaml"
        (self.repo / "pkg").mkdir()
        (self.repo / ".orchestrate").mkdir()
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"acceptance": {"dir": "accept"}})
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

    def evidence(self, data):
        return data["scenarios"][0]["evidence"]

    # S3

    def test_S3_a_folder_plan_takes_the_acceptance_keys(self):
        code, message = self.command("check")
        self.assertEqual(code, 0, message)
        data = folder_contract()
        self.evidence(data)["negative"] = "negatives/break.patch"
        self.write(data)
        self.assertEqual(self.command("check")[0], 0)
        for archive in ("promote", "discard"):
            with self.subTest(archive=archive):
                self.write(folder_contract(archive=archive))
                self.assertEqual(self.command("check")[0], 0)

    def test_S3_the_legacy_keys_are_refused_in_a_folder_plan_naming_the_new_key(self):
        for key, value, new in (
            ("oracle", ["probe.txt"], "acceptance"),
            ("place", "pkg/", "package"),
            ("promote", True, "archive"),
        ):
            with self.subTest(key=key):
                data = folder_contract()
                self.evidence(data)[key] = value
                self.refused(data, "scenarios[0].evidence." + key, new)

    def test_S3_domain_is_a_slug_and_archive_a_mode(self):
        for domain in ("Flows", "a b", "a/b", ""):
            with self.subTest(domain=domain):
                self.refused(folder_contract(domain=domain), "domain")
        self.refused(folder_contract(archive="keep"), "archive")

    def test_S3_acceptance_paths_are_literal_and_inside_the_folder(self):
        for name in ("../plan.yaml", "/etc/passwd", "a/../../b"):
            with self.subTest(name=name):
                data = folder_contract()
                self.evidence(data)["acceptance"] = [name]
                self.refused(data, "scenarios[0].evidence.acceptance[0]")

    def test_S3_acceptance_needs_a_package(self):
        data = folder_contract()
        del self.evidence(data)["package"]
        self.refused(data, "scenarios[0].evidence.package", "package")

    def test_S3_the_folder_keys_are_refused_in_a_legacy_plan_naming_the_legacy_key(self):
        legacy = self.folder.parent / "2026-11-10-demo.plan.yaml"
        (self.folder.parent / "2026-11-10-demo.oracle").mkdir()
        data = contract()
        data["domain"] = "flows"
        data["scenarios"][0]["evidence"] = {
            "check": "true",
            "baseline": "pass",
            "acceptance": ["probe.txt"],
            "package": "pkg",
        }
        legacy.write_text(yaml.safe_dump(data, sort_keys=False))
        code, message = self.command("check", legacy)
        self.assertEqual(code, 2, message)
        for needle in ("domain", "scenarios[0].evidence.acceptance", "oracle", "place"):
            self.assertIn(needle, message)

    # S4

    def test_S4_a_promote_plan_needs_a_domain(self):
        message = self.refused(folder_contract(domain=None), "domain")
        self.assertIn("archive: discard", message)  # the way out
        self.refused(folder_contract(domain=None, archive="promote"), "domain")

    def test_S4_the_repo_config_decides_promote_when_the_plan_does_not_say(self):
        (self.repo / ".orchestrate" / "config.yaml").write_text(
            yaml.safe_dump({"acceptance": {"dir": "accept"}, "archive": {"mode": "discard"}})
        )
        self.write(folder_contract(domain=None, spec=None))
        self.assertEqual(self.command("check")[0], 0)
        self.refused(folder_contract(domain=None, archive="promote"), "domain")

    def test_S4_a_promote_plan_needs_a_spec(self):
        self.refused(folder_contract(spec=None), "spec", "archive: discard")

    def test_S4_a_discard_plan_needs_neither_spec_nor_domain(self):
        self.write(folder_contract(archive="discard", domain=None, spec=None))
        code, message = self.command("check")
        self.assertEqual(code, 0, message)

    def test_S4_a_missing_acceptance_or_negative_file_is_named(self):
        data = folder_contract()
        self.evidence(data)["acceptance"] = ["probe.txt", "absent.txt"]
        self.evidence(data)["negative"] = "negatives/absent.patch"
        message = self.refused(
            data, "scenarios[0].evidence.acceptance[1]", "scenarios[0].evidence.negative"
        )
        self.assertIn("absent.txt", message)
        self.assertIn("absent.patch", message)
        self.assertNotIn("acceptance[0]", message)

    def test_S4_a_package_outside_the_repository_is_refused(self):
        (self.repo / "escape").symlink_to(self.root)
        for package in ("../elsewhere", "/tmp", "escape"):
            with self.subTest(package=package):
                data = folder_contract()
                self.evidence(data)["package"] = package
                self.refused(data, "scenarios[0].evidence.package")

    def test_S4_the_package_may_be_the_repository_root(self):
        data = folder_contract()
        self.evidence(data)["package"] = "."
        self.write(data)
        code, message = self.command("check")
        self.assertEqual(code, 0, message)

    def test_S4_an_acceptance_basename_that_matches_a_module_in_the_package_is_refused(self):
        (self.repo / "pkg" / "tests").mkdir()
        (self.repo / "pkg" / "tests" / "probe.txt").write_text("already here")
        message = self.refused(
            folder_contract(), "scenarios[0].evidence.acceptance[0]", "probe.txt"
        )
        self.assertIn("pkg/tests/probe.txt", message)
        # another package's module of that name is no clash
        shutil.rmtree(self.repo / "pkg" / "tests")
        (self.repo / "other").mkdir()
        (self.repo / "other" / "probe.txt").write_text("elsewhere")
        self.write(folder_contract())
        self.assertEqual(self.command("check")[0], 0)

    def test_S4_a_destination_that_exists_is_refused(self):
        destination = self.repo / DESTINATION
        destination.parent.mkdir(parents=True)
        destination.write_text(PROBE)
        message = self.refused(
            folder_contract(), "scenarios[0].evidence.acceptance[0]", DESTINATION
        )
        self.assertIn("domain", message)  # the way out names another domain

    def test_S4_a_discard_plan_is_not_held_to_the_promote_clashes(self):
        destination = self.repo / DESTINATION
        destination.parent.mkdir(parents=True)
        destination.write_text(PROBE)
        self.write(folder_contract(archive="discard"))
        self.assertEqual(self.command("check")[0], 0)

    # The discard guard: a plan that discards is deleted, so its folder must sit directly under plans.dir

    def discarding_copy(self, parent, plans=None):
        """The plan folder copied under `parent`, discarding, with `plans` as the repo's plans.dir when given."""
        target = parent / self.folder.name
        shutil.copytree(self.folder, target)
        (target / "plan.yaml").write_text(
            yaml.safe_dump(folder_contract(archive="discard", domain=None, spec=None), sort_keys=False)
        )
        if plans is not None:
            (self.repo / ".orchestrate" / "config.yaml").write_text(
                yaml.safe_dump({"acceptance": {"dir": "accept"}, "plans": {"dir": str(plans)}})
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
            yaml.safe_dump({"acceptance": {"dir": "accept"}, "plans": {"dir": str(plans)}})
        )
        self.assertEqual(self.command("check")[0], 0)  # the fixture's promote plan, in docs/plans

    # S5

    def test_S5_approve_digests_the_plan_and_every_acceptance_and_negative_file(self):
        (self.folder / "acceptance" / "sub").mkdir()
        (self.folder / "acceptance" / "sub" / "b.txt").write_bytes(b"b\x00")
        approval = self.approve()
        identity = Path(self.git(self.repo, "rev-parse", "--git-common-dir"))
        if not identity.is_absolute():
            identity = self.repo / identity
        canonical = yaml.safe_dump(folder_contract(), sort_keys=True).encode()
        parts = [
            str(identity.resolve()).encode(),
            self.base.encode(),
            canonical,
            b"acceptance/probe.txt",
            PROBE.encode(),
            b"acceptance/sub/b.txt",
            b"b\x00",
            b"negatives/break.patch",
            PATCH.encode(),
        ]
        expected = hashlib.sha256(
            b"".join(len(p).to_bytes(8, "big") + p for p in parts)
        ).hexdigest()
        self.assertEqual(approval["digest"], expected)
        snapshot = self.state_dir / "approvals" / expected
        self.assertEqual((snapshot / "plan.yaml").read_bytes(), canonical)
        self.assertEqual((snapshot / "acceptance" / "probe.txt").read_text(), PROBE)
        self.assertEqual((snapshot / "acceptance" / "sub" / "b.txt").read_bytes(), b"b\x00")
        self.assertEqual((snapshot / "negatives" / "break.patch").read_text(), PATCH)
        self.assertEqual(self.command("check")[0], 0)
        self.assertEqual(self.approve()["digest"], expected)

    def test_S5_a_negative_the_plan_names_outside_negatives_is_digested_too(self):
        from agent_orchestrator import plan

        (self.folder / "custom").mkdir()
        (self.folder / "custom" / "n.patch").write_text(PATCH)
        data = folder_contract()
        self.evidence(data)["negative"] = "custom/n.patch"
        (self.folder / "acceptance" / "__pycache__").mkdir()
        (self.folder / "acceptance" / "__pycache__" / "x.pyc").write_bytes(b"cache")
        self.assertEqual(
            sorted(plan.folder_files(self.plan, data)),
            [
                "acceptance/probe.txt",
                "custom/n.patch",
                "negatives/break.patch",
            ],
        )

    def test_S5_the_baseline_runs_the_acceptance_file_at_its_destination(self):
        # the check is `test -f pkg/accept/flows/probe.txt`: package, config dir and domain place the file
        approval = self.approve()
        receipts = list((self.state_dir.glob("*/demo/receipts/*.json")))
        self.assertTrue(receipts)
        self.assertTrue(
            all(json.loads(r.read_text())["result"] == "PASS" for r in receipts), approval
        )

    def test_S5_an_edit_add_or_removal_after_approval_is_named_and_refused_by_check_and_run(self):
        self.approve()
        for sub, name in (
            ("acceptance", "probe.txt"),
            ("acceptance", "extra.txt"),
            ("negatives", "break.patch"),
            ("negatives", "other.patch"),
        ):
            target = self.folder / sub / name
            for action in ("edit", "remove", "add"):
                if action == "remove":
                    if not target.exists():
                        continue
                    saved = target.read_bytes()
                    target.unlink()
                else:
                    saved = target.read_bytes() if target.exists() else None
                    target.write_text(action)
                with self.subTest(file=sub + "/" + name, action=action):
                    code, message = self.command("check")
                    self.assertEqual(code, 2, message)
                    self.assertIn(str(target), message)
                    self.assertIn("plan approve", message)
                    refusal = self.run_driver("--repo", str(self.repo), expect=2)
                    self.assertIn(str(target), refusal.stderr)
                    self.assertIn("plan approve", refusal.stderr)
                    self.assertFalse(self.calls("spawn"))
                if saved is None:
                    target.unlink()
                else:
                    target.write_bytes(saved)
            self.assertEqual(self.command("check")[0], 0)

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
        for key in ("domain", "archive"):
            self.assertIn(key, schema["properties"])
            self.assertTrue(schema["properties"][key].get("description"), key)
        self.assertNotIn("spec", schema["required"])
        evidence = schema["$defs"]["Evidence"]["properties"]
        for key in ("acceptance", "package", "negative"):
            self.assertTrue(evidence[key].get("description"), key)

    # S12

    def legacy_plan(self):
        data = contract()
        data["scenarios"][0]["evidence"] = {
            "check": "test -f checks/probe.txt",
            "baseline": "pass",
            "oracle": ["probe.txt.oracle"],
            "place": "checks/",
        }
        directory = self.repo / "docs" / "plans"
        (directory / "2026-11-10-demo.oracle").mkdir()
        (directory / "2026-11-10-demo.oracle" / "probe.txt.oracle").write_text(PROBE)
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
            b"probe.txt.oracle",
            PROBE.encode(),
        ]
        expected = hashlib.sha256(
            b"".join(len(p).to_bytes(8, "big") + p for p in parts)
        ).hexdigest()
        self.assertEqual(digests, [expected, expected])
        snapshot = self.state_dir / "approvals" / expected
        self.assertEqual((snapshot / "oracle" / "probe.txt.oracle").read_text(), PROBE)

    def test_S12_a_legacy_plan_still_names_an_edited_oracle(self):
        legacy, _ = self.legacy_plan()
        self.assertEqual(self.command("approve", legacy)[0], 0)
        oracle = legacy.with_name("2026-11-10-demo.oracle") / "probe.txt.oracle"
        oracle.write_text("edited")
        code, message = self.command("check", legacy)
        self.assertEqual(code, 2, message)
        self.assertIn(str(oracle), message)
        self.assertIn("plan approve", message)

    def test_S12_a_legacy_plan_still_needs_its_spec(self):
        legacy, data = self.legacy_plan()
        data2 = copy.deepcopy(data)
        del data2["spec"]
        legacy.write_text(yaml.safe_dump(data2, sort_keys=False))
        code, message = self.command("check", legacy)
        self.assertEqual(code, 2, message)
        self.assertIn("spec", message)
