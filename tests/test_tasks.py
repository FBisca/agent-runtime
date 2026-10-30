"""Shared durable task tool over fake-hc MCP (dispatch opt-in)."""
import threading

from test_event_wait import Session
import test_event_wait as event_helpers
from test_tools import ToolCase


class TaskTests(ToolCase):
    prepare = event_helpers.EventWaitTests.prepare
    spawn = event_helpers.EventWaitTests.spawn
    publish = event_helpers.EventWaitTests.publish

    def task(self, session, index=None, ok=True, **arguments):
        if index is not None:
            arguments.setdefault("assignment_id", self.assignments[index]["assignment_id"])
        if session is self.lead:  # AsyncRPC owns the lead's stdout
            result = self.rpc.get(self.rpc.tool("task", arguments))["result"]
            text = result["content"][0]["text"]
        else:
            result, text = self.call(session, "task", arguments)
        self.assertEqual(result["isError"], not ok, text)
        return result["structuredContent"] if ok else text

    def test_S16_lead_tasks_with_dependencies_are_identical_for_all_teammates_after_restart(self):
        """agent-runtime-dispatch/S16"""
        self.prepare()
        base = self.task(self.lead, action="create", subject="build")
        after = self.task(self.lead, action="create", subject="test", depends_on=[base["task_id"]])
        self.assertEqual(after["depends_on"], [base["task_id"]])
        self.task(self.workers[0], 0, action="claim", task_id=base["task_id"])
        restarted = Session(dict(self.env, CODEX_THREAD_ID="event-lead"))  # new MCP process, same lead
        self.addCleanup(restarted.close)
        restarted.handshake()
        views = [self.task(s, i, action="list")["tasks"] for s, i in
                 ((restarted, None), (self.workers[0], 0), (self.workers[1], 1))]
        self.assertEqual(views[0], views[1])
        self.assertEqual(views[0], views[2])
        by_id = {t["task_id"]: t for t in views[0]}
        self.assertEqual(by_id[base["task_id"]]["owner"], self.assignments[0]["worker_id"])
        self.assertEqual(by_id[base["task_id"]]["status"], "in_progress")
        self.assertEqual((by_id[after["task_id"]]["status"], by_id[after["task_id"]]["ready"]), ("pending", False))

    def test_S17_one_concurrent_claim_wins_and_bad_dependencies_are_rejected(self):
        """agent-runtime-dispatch/S17"""
        self.prepare()
        ready = self.task(self.lead, action="create", subject="ready")
        blocked = self.task(self.lead, action="create", subject="later", depends_on=[ready["task_id"]])
        outcomes = []

        def claim(index):
            result, _ = self.call(self.workers[index], "task", {
                "action": "claim", "task_id": ready["task_id"],
                "assignment_id": self.assignments[index]["assignment_id"]})
            outcomes.append(result["isError"])
        threads = [threading.Thread(target=claim, args=(i,)) for i in (0, 1)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertIn("dependenc", self.task(self.workers[1], 1, ok=False, action="claim", task_id=blocked["task_id"]))
        self.assertIn("missing", self.task(self.lead, ok=False, action="create", subject="x", depends_on=["nope"]))
        self.assertIn("cycle", self.task(self.lead, ok=False, action="update", task_id=ready["task_id"],
                                        depends_on=[blocked["task_id"]]))
        self.assertIn("cycle", self.task(self.lead, ok=False, action="update", task_id=ready["task_id"],
                                        depends_on=[ready["task_id"]]))

    def test_S18_reports_transition_linked_task_once_and_unlock_dependents(self):
        """agent-runtime-dispatch/S18"""
        self.prepare()
        first = self.task(self.lead, action="create", subject="first")
        second = self.task(self.lead, action="create", subject="second", depends_on=[first["task_id"]])
        self.task(self.workers[0], 0, action="claim", task_id=first["task_id"])
        self.publish(0, kind="blocked", text="stuck")
        tasks = {t["task_id"]: t for t in self.task(self.lead, action="list")["tasks"]}
        self.assertEqual(tasks[first["task_id"]]["status"], "blocked")
        self.assertIn("dependenc", self.task(self.workers[1], 1, ok=False, action="claim", task_id=second["task_id"]))
        # The same task is retried by a fresh assignment, then completed with a duplicate report.
        self.task(self.lead, action="update", task_id=first["task_id"], status="pending")
        resumed = self.rpc.get(self.rpc.tool("resume", {"name": "w1", "task": "retry"}))["result"]["structuredContent"]
        self.assignments[0] = resumed
        self.task(self.workers[0], 0, action="claim", task_id=first["task_id"])
        done = self.call(self.workers[0], "report", {"text": "ok", "kind": "complete", "message_id": "m1",
                                                    "assignment_id": resumed["assignment_id"]})[0]
        self.assertFalse(done["isError"], done)
        before = {t["task_id"]: t for t in self.task(self.lead, action="list")["tasks"]}[first["task_id"]]
        replay = self.call(self.workers[0], "report", {"text": "ok", "kind": "complete", "message_id": "m1",
                                                      "assignment_id": resumed["assignment_id"]})[0]
        self.assertFalse(replay["isError"], replay)
        tasks = {t["task_id"]: t for t in self.task(self.lead, action="list")["tasks"]}
        self.assertEqual(tasks[first["task_id"]]["status"], "complete")
        self.assertEqual(tasks[first["task_id"]], before)  # replay adds no transition
        late = self.call(self.workers[0], "report", {"text": "again", "kind": "complete", "message_id": "m2",
                                                    "assignment_id": resumed["assignment_id"]})[0]
        self.assertTrue(late["isError"])
        self.assertEqual({t["task_id"]: t for t in self.task(self.lead, action="list")["tasks"]}[first["task_id"]], before)
        self.assertTrue(tasks[second["task_id"]]["ready"])
        self.task(self.workers[1], 1, action="claim", task_id=second["task_id"])

    def listed(self):
        return {t["task_id"]: t for t in self.task(self.lead, action="list")["tasks"]}

    def test_S18_matching_complete_after_blocked_or_progress_finishes_linked_task(self):
        """agent-runtime-dispatch/S18"""
        self.prepare()
        first = self.task(self.lead, action="create", subject="first")
        second = self.task(self.lead, action="create", subject="second", depends_on=[first["task_id"]])
        self.task(self.workers[0], 0, action="claim", task_id=first["task_id"])
        self.publish(0, kind="blocked", text="stuck")
        self.assertFalse(self.listed()[second["task_id"]]["ready"])
        self.publish(0, kind="progress", text="unstuck")
        self.publish(0, kind="complete", text="done")
        tasks = self.listed()
        self.assertEqual(tasks[first["task_id"]]["status"], "complete")
        self.assertTrue(tasks[second["task_id"]]["ready"])

    def test_S18_stale_assignment_and_finished_assignment_cannot_mutate_or_claim_tasks(self):
        """agent-runtime-dispatch/S17"""
        self.prepare()
        old = self.task(self.lead, action="create", subject="old")
        other = self.task(self.lead, action="create", subject="other")
        self.task(self.workers[0], 0, action="claim", task_id=old["task_id"])
        resumed = self.rpc.get(self.rpc.tool("resume", {"name": "w1", "task": "next"}))["result"]["structuredContent"]
        before = self.listed()
        # Same worker, fresh assignment: the task still belongs to the previous assignment.
        denied = self.task(self.workers[0], ok=False, action="update", task_id=old["task_id"], status="complete",
                           assignment_id=resumed["assignment_id"])
        self.assertIn("assignment", denied)
        self.assertEqual(self.listed(), before)
        self.task(self.lead, action="update", task_id=old["task_id"], status="pending")  # lead override
        self.assignments[0] = resumed
        self.publish(0, kind="complete", text="no task")
        self.assertIn("unfinished", self.task(self.workers[0], 0, ok=False, action="claim", task_id=other["task_id"]))
        self.assertEqual(self.listed()[other["task_id"]]["status"], "pending")
