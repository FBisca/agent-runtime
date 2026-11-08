"""The host policy in process: what a Pi lead and a Pi worker may do.

Pure logic: no subprocess, no server, no store. The boundary tests live in `test_pi_lead.py` and
`test_pi_worker.py`.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME_SRC = Path(__file__).resolve().parents[1] / "packages" / "agent-runtime" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))
import pytest  # noqa: E402
from agent_runtime import host_policy  # noqa: E402

pytestmark = pytest.mark.unit


class PolicyFileTests(unittest.TestCase):
    """The operator's file: read strictly, and never echoed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / "agent-runtime").mkdir()
        self.env = {"HOME": str(self.dir), "XDG_CONFIG_HOME": str(self.dir)}

    def write(self, data):
        path = self.dir / "agent-runtime" / "hosts.json"
        path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
        return path

    def test_no_policy_means_no_rules(self):
        """agent-runtime/R177"""
        self.assertEqual(host_policy.rules_for(self.env, "pi"), {})

    def test_a_policy_is_read(self):
        """agent-runtime/R187"""
        self.write({"hosts": {"pi": {"mutations": True, "backends": ["pi"], "paths": ["/tmp"]}}})
        self.assertEqual(host_policy.rules_for(self.env, "pi")["backends"], ["pi"])

    def test_a_broken_policy_is_refused_without_echoing_it(self):
        """agent-runtime/R177"""
        path = self.write("{")
        with self.assertRaises(host_policy.Refused) as error:
            host_policy.load(self.env)
        self.assertNotIn("{", str(error.exception))
        self.assertIn(str(path), str(error.exception))

    def test_a_relative_path_is_refused(self):
        """agent-runtime/R187: the paths are directories, and they are absolute."""
        self.write({"hosts": {"pi": {"mutations": True, "backends": [], "paths": ["relative"]}}})
        with self.assertRaises(host_policy.Refused) as error:
            host_policy.load(self.env)
        self.assertIn("absolute", str(error.exception))

    def test_a_mutations_flag_must_be_a_boolean(self):
        """agent-runtime/R187"""
        self.write({"hosts": {"pi": {"mutations": "yes"}}})
        with self.assertRaises(host_policy.Refused) as error:
            host_policy.load(self.env)
        self.assertIn("true or false", str(error.exception))


class WorkerToolTests(unittest.TestCase):
    """A Pi worker reports and reads its own team; it starts no worker and ends no worker."""

    def allowed(self, tool, arguments=None):
        try:
            host_policy.check_worker("pi", "worker", tool, arguments or {})
        except host_policy.Refused as error:
            return str(error)
        return None

    def test_it_may_report_and_read_its_team(self):
        """agent-runtime/R188"""
        for tool in ("report", "message", "wait", "status", "collect", "list"):
            self.assertIsNone(self.allowed(tool), tool)

    def test_it_may_list_the_tasks(self):
        """agent-runtime/R188: a read-only task list is a read."""
        self.assertIsNone(self.allowed("task", {"action": "list"}))

    def test_it_may_not_start_or_end_a_worker(self):
        """agent-runtime/R189"""
        for tool in ("spawn", "stop", "resume"):
            refusal = self.allowed(tool)
            self.assertIsNotNone(refusal, tool)
            self.assertIn("it starts no worker and ends no worker", refusal)

    def test_it_may_not_create_a_task(self):
        """agent-runtime/R189: a task mutates the team's shared plan."""
        self.assertIsNotNone(self.allowed("task", {"action": "create"}))


class ScopeTests(unittest.TestCase):
    """Where a worker may run, and which calls change durable state."""

    def test_a_path_below_an_authorised_root_is_inside_it(self):
        """agent-runtime/R174"""
        roots = [os.path.realpath("/tmp/checkout")]
        self.assertTrue(host_policy.within("/tmp/checkout/src/a.py", roots))
        self.assertTrue(host_policy.within("/tmp/checkout", roots))

    def test_a_path_outside_the_authorised_roots_is_outside_them(self):
        """agent-runtime/R174"""
        roots = [os.path.realpath("/tmp/checkout")]
        self.assertFalse(host_policy.within("/tmp/elsewhere", roots))
        self.assertFalse(host_policy.within("/tmp/checkout-other", roots))

    def test_the_mutating_tools_are_the_ones_that_change_state(self):
        """agent-runtime/R171"""
        for tool in ("spawn", "message", "resume", "stop", "report"):
            self.assertTrue(host_policy.mutating(tool, {}), tool)
        self.assertFalse(host_policy.mutating("task", {"action": "list"}))
        self.assertTrue(host_policy.mutating("task", {"action": "create"}))

    def test_only_pi_is_a_host_the_server_authorizes_itself(self):
        """agent-runtime/R187"""
        self.assertTrue(host_policy.restricted("pi"))
        for host in ("claude", "codex"):
            self.assertFalse(host_policy.restricted(host), host)


if __name__ == "__main__":
    unittest.main()
