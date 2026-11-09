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


if __name__ == "__main__":
    unittest.main()
