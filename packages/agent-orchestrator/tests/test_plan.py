"""plan/1 validation and approval at the CLI boundary."""

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

pytestmark = pytest.mark.integration


def contract():
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "outcome": ["S1"],
        "scenarios": [{"id": "S1", "when": "a", "then": "b", "evidence": {"human": "confirm b"}}],
        "tickets": [
            {
                "id": "T1",
                "delivers": "first",
                "satisfies": ["S1"],
                "paths": ["src/a/"],
                "size": "small",
                "difficulty": "simple",
            }
        ],
    }


class PlanTests(RunFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.plan = self.plan.with_suffix(".yaml")
        self.oracles = self.plan.with_name("demo.oracle")
        self.oracles.mkdir()
        self.write(contract())
        self.env_patch = mock.patch.dict(
            os.environ, {"XDG_STATE_HOME": str(self.root / "xdg"), "HC_TERMINAL_ID": "lead-test"}
        )
        self.env_patch.start()
        self.resolve_patch = mock.patch.object(
            load_script(), "resolve_runtime", return_value={"role": "lead"}
        )
        self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def write(self, data):
        self.plan.write_text("# retained comment\n" + yaml.safe_dump(data, sort_keys=False))

    def command(self, verb, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(
                [
                    "plan",
                    verb,
                    str(self.plan),
                    "--repo",
                    str(self.repo),
                    "--state-dir",
                    str(self.state_dir),
                    *extra,
                ]
            )
        return code, out.getvalue() + err.getvalue()

    def approve(self):
        code, message = self.command("approve")
        self.assertEqual(code, 0, message)
        return yaml.safe_load(self.plan.read_text())["approval"]

    def test_S1_valid_reference_and_schema(self):
        from agent_orchestrator.plan import load

        reference = Path(__file__).resolve().parent / "data" / "reference.plan.yaml"
        # The reference's schema, not its approval: the snapshot lives in the approver's state dir (D26), so a
        # committed approval block cannot be verified on CI or any other machine.
        self.assertEqual(load(reference, verify=False).slug, "yaml-eec")
        self.assertEqual(self.command("check")[0], 0)
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(load_script().run_cli(["plan", "schema"]), 0)
        self.assertFalse(json.loads(out.getvalue())["additionalProperties"])

    def test_S1_names_all_schema_and_graph_defects(self):
        data = contract()
        data["mystery"] = True
        del data["goal"]
        data["scenarios"] += [
            copy.deepcopy(data["scenarios"][0]),
            {"id": "S2", "when": "x", "then": "y", "evidence": {}},
        ]
        data["outcome"] += ["S2", "S9"]
        data["tickets"][0].update(
            size="big", blocked_by=["T9", "T1"], satisfies=["S9"], risk=["state"]
        )
        data["tickets"].append(copy.deepcopy(data["tickets"][0]))
        self.write(data)
        code, message = self.command("check")
        self.assertEqual(code, 2, message)
        for path in (
            "mystery",
            "goal",
            "scenarios[1].id",
            "scenarios[2].evidence",
            "outcome[1]",
            "outcome[2]",
            "tickets[0].size",
            "tickets[0].blocked_by[0]",
            "tickets[0].blocked_by",
            "tickets[0].satisfies[0]",
            "tickets[0].risk",
            "tickets[1].id",
        ):
            self.assertIn(path, message)
        self.assertIn("split it", message)
        before = self.plan.read_bytes()
        self.assertEqual(self.command("approve")[0], 2)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())

    def test_S1_duplicate_key_is_named(self):
        self.plan.write_text(
            self.plan.read_text().replace("size: small", "size: small\n  size: regular")
        )
        code, message = self.command("check")
        self.assertEqual(code, 2, message)
        self.assertIn("tickets[0].size", message)
        self.assertIn("duplicate", message)

    def test_S1_strict_nested_mappings_and_literal_paths(self):
        for change, path in [
            ({"scenarios": [dict(contract()["scenarios"][0], typo=1)]}, "scenarios[0].typo"),
            (
                {"approval": {"by": "a", "at": "b", "base": "c", "digest": "d", "typo": 1}},
                "approval.typo",
            ),
        ]:
            with self.subTest(path=path):
                self.write(dict(contract(), **change))
                code, message = self.command("check")
                self.assertEqual(code, 2)
                self.assertIn(path, message)
        for value in ("../x", "/x", "src/*.py"):
            data = contract()
            data["tickets"][0]["paths"] = [value]
            self.write(data)
            code, message = self.command("check")
            self.assertEqual(code, 2)
            self.assertIn("tickets[0].paths[0]", message)
        data = contract()
        data["tickets"][0].update(repo="elsewhere", max_fix_rounds=-1, extra=True)
        self.write(data)
        code, message = self.command("check")
        self.assertEqual(code, 2)
        for path in ("tickets[0].repo", "tickets[0].max_fix_rounds", "tickets[0].extra"):
            self.assertIn(path, message)

    def test_S17_check_and_approve_refuse_an_env_naming_home_path_or_xdg(self):
        for name in ("HOME", "PATH", "XDG_CACHE_HOME"):
            with self.subTest(name=name):
                data = contract()
                data["scenarios"][0]["evidence"] = {
                    "check": "true",
                    "baseline": "pass",
                    "env": [name],
                }
                self.write(data)
                for verb in ("check", "approve"):
                    code, message = self.command(verb)
                    self.assertEqual(code, 2, message)
                    self.assertIn(name, message)
                    self.assertIn("scratch", message)
                    self.assertIn("drop", message)

    def test_S1_oracle_placement_and_missing_payloads(self):
        for place in ("/tmp/out", "../out", "escape"):
            with self.subTest(place=place):
                if place == "escape":
                    (self.repo / "escape").symlink_to(self.root)
                data = contract()
                data["scenarios"][0]["evidence"] = {
                    "check": "true",
                    "baseline": "pass",
                    "oracle": ["missing.py.oracle"],
                    "negative": "missing.patch.oracle",
                    "place": place,
                }
                self.write(data)
                code, message = self.command("check")
                self.assertEqual(code, 2, message)
                for path in (
                    "scenarios[0].evidence.place",
                    "scenarios[0].evidence.oracle[0]",
                    "scenarios[0].evidence.negative",
                ):
                    self.assertIn(path, message)
        (self.oracles / "x.py.oracle").write_text("x")
        (self.repo / "x.py").write_text("occupied")
        data["scenarios"][0]["evidence"].update(oracle=["x.py.oracle"], negative=None, place=".")
        self.write(data)
        self.assertIn("already exists", self.command("check")[1])

    def test_S24_an_archived_plan_whose_oracle_was_promoted_still_checks(self):
        (self.oracles / "x.py.oracle").write_text("def test_x():\n    assert True\n")
        data = contract()
        data["scenarios"][0]["evidence"] = {
            "check": "true",
            "baseline": "pass",
            "oracle": ["x.py.oracle"],
            "place": ".",
        }
        self.write(data)
        self.approve()
        # the archive promotes the oracle byte for byte onto its place
        shutil.copyfile(self.oracles / "x.py.oracle", self.repo / "x.py")
        code, message = self.command("check")
        self.assertEqual(code, 0, message)
        (self.repo / "x.py").write_text("edited after promotion")
        self.assertIn("already exists", self.command("check")[1])

    def test_S2_digest_snapshot_comments_and_idempotence(self):
        (self.oracles / "z.oracle").write_bytes(b"z\x00")
        (self.oracles / "a.oracle").write_bytes(b"a")
        before = self.plan.read_text()
        approval = self.approve()
        self.assertTrue(self.plan.read_text().startswith(before))
        identity = Path(self.git(self.repo, "rev-parse", "--git-common-dir"))
        if not identity.is_absolute():
            identity = self.repo / identity
        canonical = yaml.safe_dump(contract(), sort_keys=True).encode()
        parts = [
            str(identity.resolve()).encode(),
            self.base.encode(),
            canonical,
            b"a.oracle",
            b"a",
            b"z.oracle",
            b"z\x00",
        ]
        expected = hashlib.sha256(
            b"".join(len(p).to_bytes(8, "big") + p for p in parts)
        ).hexdigest()
        self.assertEqual(approval["digest"], expected)
        snapshot = self.state_dir / "approvals" / expected
        self.assertEqual((snapshot / "plan.yaml").read_bytes(), canonical)
        self.assertEqual((snapshot / "oracle" / "z.oracle").read_bytes(), b"z\x00")
        self.assertEqual(self.command("check")[0], 0)
        self.assertEqual(self.approve()["digest"], expected)
        self.assertEqual(self.plan.read_text().count("\napproval:"), 1)

    def test_S2_changes_missing_snapshot_and_unapproved_run_refuse(self):
        self.run_driver("--repo", str(self.repo), expect=2)
        self.assertFalse(self.calls("spawn"))
        approval = self.approve()
        pristine = self.plan.read_bytes()
        self.plan.write_text(self.plan.read_text().replace("goal: deliver", "goal: changed"))
        code, message = self.command("check")
        self.assertEqual(code, 2)
        self.assertIn(self.plan.name, message)
        self.assertIn("plan approve", message)
        self.run_driver("--repo", str(self.repo), expect=2)
        self.assertFalse(self.calls("spawn"))
        self.plan.write_bytes(pristine)
        (self.state_dir / "approvals" / approval["digest"] / "plan.yaml").unlink()
        code, message = self.command("check")
        self.assertEqual(code, 2)
        self.assertIn("snapshot", message)

    def test_S2_oracle_add_edit_remove_are_named_and_can_be_reapproved(self):
        payload = self.oracles / "p.oracle"
        payload.write_text("first")
        self.approve()
        for action in ("edit", "remove", "add"):
            if action == "remove":
                payload.unlink()
            else:
                payload.write_text(action)
            code, message = self.command("check")
            self.assertEqual(code, 2, message)
            self.assertIn("p.oracle", message)
            self.assertIn("plan approve", message)
            self.run_driver("--repo", str(self.repo), expect=2)
            self.assertFalse(self.calls("spawn"))
            self.approve()

    def test_S2_approve_base_and_repo_identity_are_bound(self):
        self.approve()
        other = self.root / "other"
        self.git(self.root, "clone", "-q", str(self.origin), str(other))
        code, message = self.command("check", "--repo", str(other))
        self.assertEqual(code, 2)
        self.assertIn("repo", message)
        code, message = self.command("approve", "--base", "not-a-ref")
        self.assertEqual(code, 2)
        self.assertIn("base", message)

    def test_S3_runtime_worker_is_refused_before_any_write(self):
        before = self.plan.read_bytes()
        with mock.patch.object(
            load_script(),
            "resolve_runtime",
            return_value={"runtime": "hc", "role": "worker", "parent": "lead-terminal"},
        ):
            code, message = self.command("approve")
        self.assertEqual(code, 2)
        for word in ("worker", "Lead", "lead-terminal", "own session"):
            self.assertIn(word, message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertFalse(self.state_dir.exists())

    def test_S3_lead_unknown_and_failed_resolution_proceed(self):
        for caller in ({"role": "lead"}, {"runtime": "unknown"}):
            with (
                self.subTest(caller=caller),
                mock.patch.object(load_script(), "resolve_runtime", return_value=caller),
                mock.patch.dict(os.environ, {"AGENT_RUNTIME_DISPATCH": "1"}),
            ):
                self.approve()
        self.resolve_patch.stop()
        with mock.patch.object(load_script(), "run", side_effect=ValueError("resolve failed")):
            self.approve()

    def test_S2_missing_snapshot_is_repaired_by_reapproval(self):
        block = self.approve()
        snap = self.state_dir / "approvals" / block["digest"] / "plan.yaml"
        snap.unlink()
        self.assertEqual(self.command("check")[0], 2)
        self.approve()
        self.assertEqual(self.command("check")[0], 0)

    def test_S2_same_contract_under_another_filename_can_be_approved(self):
        self.approve()
        self.plan = self.plan.with_name("another.plan.yaml")
        self.write(contract())
        self.approve()
        self.assertEqual(self.command("check")[0], 0)

    def test_S2_flow_mapping_and_explicit_document_end_remain_valid(self):
        for text in (
            yaml.safe_dump(contract(), default_flow_style=True),
            yaml.safe_dump(contract(), sort_keys=False) + "...\n",
        ):
            with self.subTest(text=text[:30]):
                self.plan.write_text(text)
                self.approve()
                self.assertEqual(self.command("check")[0], 0)
                self.approve()
                self.assertEqual(self.command("check")[0], 0)

    def test_S1_unsafe_oracle_filename_is_a_path_refusal(self):
        data = contract()
        data["scenarios"][0]["evidence"] = {
            "check": "true",
            "baseline": "pass",
            "oracle": ["/outside.py.oracle"],
            "place": ".",
        }
        self.write(data)
        code, message = self.command("check")
        self.assertEqual(code, 2, message)
        self.assertIn("scenarios[0].evidence.oracle[0]", message)

    def test_S1_risk_tagged_baseline_pass_needs_a_negative(self):
        (self.oracles / "x.patch.oracle").write_text("--- a\n+++ b\n")

        def risky(evidence, ticket_extra=None, risk=("state",)):
            data = contract()
            data["scenarios"][0]["evidence"] = evidence
            data["tickets"][0].update(ticket_extra or {})
            if risk:
                data["tickets"][0]["risk"] = list(risk)
            return data

        baseline_pass = {"check": "true", "baseline": "pass"}
        self.write(risky(baseline_pass))
        code, message = self.command("check")
        self.assertEqual(code, 2, message)
        self.assertIn("scenarios[0].evidence", message)
        self.assertIn("negative", message)

        valid = {
            "negative_present": risky(dict(baseline_pass, negative="x.patch.oracle")),
            "baseline_fail": risky({"check": "true", "baseline": "fail"}),
            "human": risky({"human": "confirm b"}, risk=("state",)),
            "untagged": risky(baseline_pass, risk=()),
            "waiver": risky(baseline_pass, ticket_extra={"evidence_waiver": "verified by outcome"}),
        }
        for name, data in valid.items():
            with self.subTest(case=name):
                self.write(data)
                code, message = self.command("check")
                self.assertEqual(code, 0, message)

    def test_S2_plan_schema_is_a_closed_plan_v1_schema(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(["plan", "schema"])
        self.assertEqual(code, 0, err.getvalue())
        schema = json.loads(out.getvalue())
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("scenarios", schema["properties"])
        self.assertIn("tickets", schema["properties"])
