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
from agent_orchestrator import runtime_client
from fixtures import RunFixture, load_script

pytestmark = pytest.mark.integration


class Done:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def contract():
    return {
        "schema": "plan/1",
        "slug": "demo",
        "goal": "deliver",
        "spec": "docs/specs/demo.md",
        "scenarios": [{"id": "S1", "when": "a", "then": "b"}],
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


class PlanTests(RunFixture, unittest.TestCase):  # noqa: PLR0904
    def setUp(self):
        super().setUp()
        shutil.rmtree(self.state_dir)  # the fixture's own approval is not this test's
        self.plan = self.plan.with_suffix(".yaml")
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
        self.asked = []
        self.answer = lambda argv: Done(
            0, json.dumps({"role": "lead", "caller": {"kind": "lead", "verified": True}})
        )
        process_patch = mock.patch.object(runtime_client, "runtime_process", self.ask_runtime)
        process_patch.start()
        self.addCleanup(process_patch.stop)

    def ask_runtime(self, argv, timeout=None):
        self.asked.append(argv)
        return self.answer(argv)

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
        self.assertEqual(load(reference, repo=self.repo, verify=False).slug, "yaml-eec")
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
            "tickets[0].size",
            "tickets[0].blocked_by[0]",
            "tickets[0].blocked_by",
            "tickets[0].satisfies[0]",
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

    def refused_approve(self, reason):
        code, message = self.command("approve")
        self.assertEqual(code, 2, message)
        self.assertIn(reason, message)
        self.assertIn("ask the Lead", message)
        self.assertNotIn("approval", yaml.safe_load(self.plan.read_text()))
        self.assertFalse((self.state_dir / "approvals").exists())

    def test_S20_a_worker_the_runtime_names_is_refused_and_nothing_is_written(self):
        for caller in ({"kind": "worker", "verified": True}, {"kind": "worker", "verified": False}):
            with self.subTest(caller=caller):
                self.answer = lambda argv, caller=caller: Done(
                    0, json.dumps({"role": "worker", "parent": "t1", "caller": caller})
                )
                self.refused_approve("worker")

    def test_S20_an_unverified_or_unknown_caller_inside_a_host_is_refused(self):
        for answer in (
            {"role": "unknown", "caller": {"kind": "unknown", "verified": False}},
            {"role": "lead", "caller": {"kind": "lead", "verified": False}},
            {"role": "lead"},
        ):
            with self.subTest(answer=answer):
                self.answer = lambda argv, answer=answer: Done(0, json.dumps(answer))
                self.refused_approve("verif")

    def test_S20_a_failed_lookup_with_agent_markers_is_refused(self):
        for answer in (
            lambda argv: Done(1, "", "boom"),
            lambda argv: Done(0, "not json"),
            lambda argv: (_ for _ in ()).throw(OSError("no agent-runtime")),
        ):
            with self.subTest(answer=answer):
                self.answer = answer
                self.refused_approve("lookup")

    def without_host_markers(self):
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for name in runtime_client.HOST_MARKERS:
            os.environ.pop(name, None)

    def test_S21_a_plain_terminal_or_ci_with_no_host_markers_approves_when_the_runtime_cannot_place_it(
        self,
    ):
        for answer in (
            lambda argv: (_ for _ in ()).throw(OSError("no agent-runtime")),
            lambda argv: Done(1, "", "boom"),
            lambda argv: Done(0, json.dumps({"role": "unknown", "caller": {"kind": "unknown"}})),
        ):
            with self.subTest(answer=answer):
                self.without_host_markers()
                self.answer = answer
                self.assertIn("digest", self.approve())

    def test_S20_a_worker_the_runtime_verifies_by_ancestry_is_refused_with_no_host_markers(self):
        self.without_host_markers()
        self.answer = lambda argv: Done(
            0, json.dumps({"role": "lead", "caller": {"kind": "worker", "verified": True}})
        )
        self.refused_approve("worker")

    def test_S20_plan_amend_refuses_a_worker_the_runtime_verifies_by_ancestry_too(self):
        block = self.approve()
        self.without_host_markers()
        self.answer = lambda argv: Done(
            0, json.dumps({"role": "lead", "caller": {"kind": "worker", "verified": True}})
        )
        before = self.plan.read_bytes()
        code, message = self.command("amend", "--reason", "retitle")
        self.assertEqual(code, 2, message)
        self.assertIn("worker", message)
        self.assertIn("amend", message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(
            yaml.safe_load(self.plan.read_text())["approval"]["digest"], block["digest"]
        )

    def test_S20_the_way_out_names_both_a_worker_and_a_lead(self):
        self.answer = lambda argv: Done(
            0, json.dumps({"role": "unknown", "caller": {"kind": "unknown"}})
        )
        code, message = self.command("approve")
        self.assertEqual(code, 2, message)
        self.assertIn("ask the Lead", message)
        self.assertIn("from a terminal with no agent host", message)
        self.assertIn("hc Lead terminal", message)

    def test_S21_a_lead_the_runtime_verifies_approves(self):
        self.assertIn("digest", self.approve())
        self.assertEqual(self.asked[0][-2:], ["resolve", "--json"])

    def test_S2_digest_snapshot_comments_and_idempotence(self):
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
        ]
        expected = hashlib.sha256(
            b"".join(len(p).to_bytes(8, "big") + p for p in parts)
        ).hexdigest()
        self.assertEqual(approval["digest"], expected)
        snapshot = self.state_dir / "approvals" / expected
        self.assertEqual((snapshot / "plan.yaml").read_bytes(), canonical)
        # The plan alone is the contract: the snapshot holds plan.yaml and nothing beside it.
        self.assertEqual(sorted(p.name for p in snapshot.iterdir()), ["plan.yaml"])
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

    def test_S1_a_scenario_evidence_block_is_refused_naming_the_way_out(self):
        data = contract()
        data["scenarios"][0]["evidence"] = {"check": "pytest", "baseline": "fail"}
        self.write(data)
        before = self.plan.read_bytes()
        for verb in ("check", "approve"):
            with self.subTest(verb=verb):
                code, message = self.command(verb)
                self.assertEqual(code, 2, message)
                # The refusal is the last line, as JSON: read it back so its dash is a dash.
                message = json.loads(message.strip().splitlines()[-1])["error"]
                self.assertIn(
                    "scenarios[0].evidence: scenario evidence is gone; the ticket's tests are its "
                    "evidence — delete the block",
                    message,
                )
                self.assertNotIn("Extra inputs are not permitted", message)
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertFalse((self.state_dir / "approvals").exists())

    def test_S2_approve_runs_nothing_and_spawns_no_worktree(self):
        """A stripped plan (scenarios and tickets only) is approved by writing its block and its snapshot: no check
        runs at the base, so no scratch worktree is ever added to the repository."""
        approval = self.approve()
        self.assertEqual(sorted(approval), ["at", "base", "by", "digest"])
        snapshot = self.state_dir / "approvals" / approval["digest"]
        self.assertEqual(sorted(p.name for p in snapshot.iterdir()), ["plan.yaml"])
        listed = self.git(self.repo, "worktree", "list", "--porcelain")
        trees = [
            line[len("worktree ") :] for line in listed.splitlines() if line.startswith("worktree ")
        ]
        self.assertEqual([Path(t).resolve() for t in trees], [self.repo.resolve()])
        self.assertEqual(list(self.state_dir.rglob("receipts")), [])
        self.assertEqual(self.command("check")[0], 0)

    def test_S2_plan_schema_is_a_closed_plan_v1_schema(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = load_script().run_cli(["plan", "schema"])
        self.assertEqual(code, 0, err.getvalue())
        schema = json.loads(out.getvalue())
        self.assertIs(schema["additionalProperties"], False)
        self.assertIn("scenarios", schema["properties"])
        self.assertIn("tickets", schema["properties"])
