"""`wait`'s own verdict on an attempt has one home: `driver/verdict.py`.

The runtime decides an attempt needs a person before the idle tick has watched it long enough to ask: a resume whose
worker produced nothing, or a turn with no progress for a quarter of an hour. The drive reads that verdict where it
reads every other `wait` fact, so the module holds the mapping from the payload to what the Lead is told — and only
the verdict the idle tick must defer to, since One idle watchdog keeps a running turn waiting `--working-minutes`.
"""

import unittest

import pytest
from agent_orchestrator.driver.verdict import ATTENTION, attention

pytestmark = pytest.mark.unit

NOT_STARTED_NEXT = (
    "resume has not started: collect the worker's screen or stop it if no longer needed; "
    "the runtime never resends the task"
)


def assignment(**fields):
    entry = {"assignment_id": "r1-demo-t1", "state": "working", "worker_id": "w-demo-t1"}
    return dict(entry, **fields)


class VerdictTests(unittest.TestCase):
    def rows(self, *assignments):
        return list(attention(assignments))

    @pytest.mark.spec("Not-started attempts reach the Lead")
    def test_a_resumed_attempt_the_runtime_says_never_started_is_named_with_its_way_out(self):
        rows = self.rows(
            assignment(runtime_state="idle", attention="not_started", next=NOT_STARTED_NEXT)
        )
        self.assertEqual(
            rows,
            [("r1-demo-t1", "not_started", NOT_STARTED_NEXT, False)],
            "the verdict was dropped",
        )
        self.assertIn("look at its terminal", ATTENTION["not_started"])
        self.assertIn("fresh worker", ATTENTION["not_started"])

    @pytest.mark.spec("Not-started attempts reach the Lead")
    def test_a_stalled_turn_is_left_to_the_idle_ticks_working_minutes(self):
        self.assertEqual(
            self.rows(assignment(runtime_state="working", attention="stalled", next="no progress")),
            [],
            "One idle watchdog keeps a running turn the runtime finds quiet waiting --working-minutes",
        )

    @pytest.mark.spec("Not-started attempts reach the Lead")
    def test_the_states_the_drive_already_read_are_unchanged(self):
        self.assertEqual(
            self.rows(
                assignment(state="blocked"),
                assignment(state="decision"),
                assignment(state="failed"),
                assignment(runtime_state="idle"),
                assignment(runtime_state="unreported"),
                assignment(state="complete"),
            ),
            [
                ("r1-demo-t1", "blocked", "", False),
                ("r1-demo-t1", "decision", "", False),
                ("r1-demo-t1", "exited", "", False),
                ("r1-demo-t1", "exited", "runtime state idle", True),
                ("r1-demo-t1", "exited", "runtime state unreported", False),
            ],
        )

    @pytest.mark.spec("Not-started attempts reach the Lead")
    def test_an_assignment_with_no_id_is_not_named(self):
        self.assertEqual(self.rows({"state": "working", "attention": "not_started"}), [])
