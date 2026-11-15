"""A lead that has not read its reports cannot change its team: the server refuses every mutating verb."""

import pytest
import test_event_wait as event_helpers

pytestmark = pytest.mark.integration


class RhythmAuthorityTests(event_helpers.EventRig):
    VERBS = (
        ("spawn", {"kind": "worker", "name": "w3", "task": "work", "cwd": "/tmp"}),
        ("message", {"to": "w1", "text": "hi"}),
        ("resume", {"name": "w1", "task": "again"}),
        ("stop", {"name": "w1"}),
    )

    def answer(self, name, arguments):
        return self.rpc.get(self.rpc.tool(name, arguments))["result"]

    def refused(self, name, arguments):
        result = self.answer(name, arguments)
        self.assertTrue(result["isError"], result)
        return result["content"][0]["text"]

    def test_S6_lead_with_undelivered_reports_is_refused_every_mutating_verb(self):
        self.prepare()
        self.publish(0)  # one report the lead has not read
        started = self.runs("run")
        for name, arguments in self.VERBS:
            with self.subTest(verb=name):
                text = self.refused(name, arguments)
                self.assertIn("1 undelivered report", text)
                self.assertIn("wait(all)", text)
        self.assertEqual(self.runs("run"), started)  # no worker starts
        self.assertEqual(self.runs("kill") + self.runs("rm"), [])  # nor is one stopped

    def test_S6_wait_all_delivers_and_the_verbs_are_allowed_again(self):
        self.prepare()
        self.publish(0)
        batch = self.batch(self.rpc, self.rpc.tool("wait", {"target": "all", "timeout": 0}))
        self.assertEqual([message["kind"] for message in batch["messages"]], ["complete"])
        # The receipt acknowledges on the next read: wait(all) is the way out the refusal names.
        self.batch(
            self.rpc,
            self.rpc.tool("wait", {"target": "all", "timeout": 0, "receipt": batch["receipt"]}),
        )
        sent = self.answer("message", {"to": "w1", "text": "hi"})
        self.assertFalse(sent["isError"], sent)
