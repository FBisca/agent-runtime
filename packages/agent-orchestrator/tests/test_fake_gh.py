"""The fake PR view fails instead of polling a draft forever."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fake_gh
import pytest

pytestmark = pytest.mark.unit


class FakeGhTests(unittest.TestCase):
    def test_draft_poll_limit_and_per_pr_ready(self):
        """orchestrator-engine/R2"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            plan = home / "plan.md"
            plan.write_text("plan", encoding="utf-8")
            env = {"FAKE_RUNTIME_HOME": directory, "FAKE_PLAN": str(plan)}
            first = "https://github.com/o/r/pull/7"
            second = "https://github.com/o/r/pull/9"
            with mock.patch.dict(os.environ, env, clear=True):
                for _ in range(100):
                    self.assertEqual(
                        json.loads(fake_gh.process(["pr", "view", first]).stdout)["state"], "OPEN"
                    )
                with self.assertRaisesRegex(RuntimeError, "PR 7 stayed draft for 100 polls"):
                    fake_gh.process(["pr", "view", first])
                self.assertEqual(
                    json.loads(fake_gh.process(["pr", "view", second]).stdout)["state"], "OPEN"
                )
                fake_gh.process(["pr", "ready", first])
                self.assertEqual(
                    json.loads(fake_gh.process(["pr", "view", first]).stdout)["state"], "MERGED"
                )

    def test_draft_views_ready_checks_and_a_failing_undo_are_scripted(self):
        """orchestrator-engine/R2"""
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            plan = home / "plan.md"
            plan.write_text("plan", encoding="utf-8")
            dirty = {"state": "OPEN", "mergeable": "CONFLICTING", "headRefOid": "b" * 40}
            (home / "view-draft.json").write_text(json.dumps([dirty, "OPEN"]), encoding="utf-8")
            (home / "checks.json").write_text(json.dumps([[0, "[]", ""]]), encoding="utf-8")
            failed = [[1, json.dumps([{"name": "unit", "bucket": "fail"}]), ""]]
            (home / "checks-ready.json").write_text(json.dumps(failed), encoding="utf-8")
            (home / "undo.json").write_text(json.dumps([[1, "boom"], [0, ""]]), encoding="utf-8")
            env = {"FAKE_RUNTIME_HOME": directory, "FAKE_PLAN": str(plan)}
            url = "https://github.com/o/r/pull/7"

            def view():
                return json.loads(fake_gh.process(["pr", "view", url]).stdout)

            with mock.patch.dict(os.environ, env, clear=True):
                first = view()
                self.assertEqual((first["mergeable"], first["isDraft"]), ("CONFLICTING", True))
                self.assertEqual(first["headRefOid"], "b" * 40)
                self.assertEqual(view()["mergeable"], "MERGEABLE")
                self.assertEqual(fake_gh.process(["pr", "checks", url]).stdout, "[]")
                fake_gh.process(["pr", "ready", url])
                self.assertIn("fail", fake_gh.process(["pr", "checks", url]).stdout)
                self.assertEqual(fake_gh.process(["pr", "ready", url, "--undo"]).returncode, 1)
                self.assertFalse(view()["isDraft"])
                self.assertEqual(fake_gh.process(["pr", "ready", url, "--undo"]).returncode, 0)
                self.assertTrue(view()["isDraft"])

    def test_S1_pr_create_prints_the_url_of_the_new_pull_request(self):
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict(os.environ, {"FAKE_RUNTIME_HOME": home}, clear=True):
                made = fake_gh.process(["pr", "create", "--draft"])
        self.assertEqual((made.returncode, made.stdout), (0, "https://github.com/o/r/pull/9\n"))


if __name__ == "__main__":
    unittest.main()
