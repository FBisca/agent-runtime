"""The event-wait rig: a test starts only the sessions it talks to, and a plain ToolCase can borrow it."""

import sys

from test_event_wait import (
    EventRig,
    HcCase,
)
from test_mcp_server import RUNTIME_SRC
from tools_harness import ToolCase

sys.path.insert(0, str(RUNTIME_SRC))
import pytest

pytestmark = pytest.mark.integration


class EventHarnessTests(EventRig):
    def test_S11_a_test_starts_only_the_sessions_it_talks_to(self):
        """S11: no server for a rig that drives the tool handlers in process; the lead alone for a test that only
        waits on it; a worker's own session once the test talks to that worker."""
        started = self.count_sessions()
        self.resume_rig()
        self.assertEqual(started, [], "the resume rig calls the handlers in process")
        self.prepare()
        self.assertEqual(started, ["t0"], "prepare starts the lead, no worker")
        self.workers[1]
        self.assertEqual(started, ["t0", "t2"], "the second worker's session starts on first use")
        self.workers[0]
        self.assertEqual(started, ["t0", "t2", "t1"])
        self.workers[0]
        self.assertEqual(started, ["t0", "t2", "t1"], "a session is started once")

    def test_closing_the_rpc_reader_ends_it_on_the_sessions_eof_not_on_its_join_budget(self):
        """The reader blocks on the server's stdout, so a close that leaves the server running can only wait out
        its join budget (2s a test); closing the session's input ends the server and the reader with it."""
        self.prepare()
        self.rpc.close()
        self.assertFalse(self.rpc.thread.is_alive())


class BorrowedRig(ToolCase):
    """What test_budgets, test_diagnostics and test_tasks do: borrow the rig's helpers on a plain ToolCase."""

    prepare = EventRig.prepare
    spawn = EventRig.spawn
    publish = EventRig.publish

    def test_a_plain_tool_case_that_borrows_prepare_gets_the_lead_and_lazy_workers(self):
        started = self.count_sessions_borrowed()
        self.prepare()
        self.assertEqual(started, ["t0"])
        self.publish(0)
        self.assertEqual(started, ["t0", "t1"])

    def count_sessions_borrowed(self):
        return HcCase.count_sessions(self)
