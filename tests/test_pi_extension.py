"""The Pi rhythm extension, driven through node: one hook answer translated into Pi's own shape.

`extensions/agent-runtime/handlers.mjs` is the pure translation and `index.js` wires it to Pi's events,
so both are driven here rather than through a live Pi session: a failing case fails on the translation
or on the wiring, not on a model. The hook's own decision is `tests/test_lead_rhythm.py`'s; what is
proven here is that the extension asks the documented entry, carries the hook's reason word for word,
pushes a wake once, and refuses a long sleep naming `wait(all)`.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "agent-runtime"
HANDLERS = EXTENSION / "handlers.mjs"
INDEX = EXTENSION / "index.js"
HOOK = ROOT / "bin" / "agent-runtime-hook"
REASON = "agent-runtime: 2 unfinished, 1 undelivered — call wait(all) before ending the turn"
WORKER_REASON = (
    "agent-runtime: assignment a1 is still open — end this turn with `report complete`, or "
    "`report blocked` if you cannot finish"
)


def turn_end(entries=()):
    """A completed turn whose earlier handlers already proposed these drafts."""
    return (
        "for (const fn of handlers.turn_end || []) {"
        '  result = (await fn({outcome: "completed", toolResults: [], entries: %s}, {})) ?? null;'
        "}" % json.dumps(list(entries))
    )


TURN_END = turn_end()
SESSION_START = (
    "for (const fn of handlers.session_start || []) { await fn({}, {}); }"
    "for (const fn of handlers.session_shutdown || []) { await fn({}, {}); }"
    "result = sent.map((entry) => entry.message.content);"
)


def node(script, env=None, timeout=120):
    """Run one ES module in node and parse the JSON it prints on stdout."""
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or dict(os.environ),
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def handler(name, payload):
    """One pure function of the translation, with the acceptance oracle's own call shape."""
    return node(
        "const m = await import(%s);"
        "process.stdout.write(JSON.stringify(m[%s](%s) ?? null));"
        % (json.dumps(HANDLERS.as_uri()), json.dumps(name), json.dumps(payload))
    )


def session(body, env):
    """The extension installed against a fake Pi: one session's work, then what it proposed and sent."""
    script = (
        "const mod = await import(%s);"
        "const handlers = {};"
        "const sent = [];"
        "const pi = {"
        "  on(name, fn) {(handlers[name] ||= []).push(fn);},"
        "  sendMessage(message, options) {sent.push({message, options});},"
        "};"
        "mod.default(pi);"
        "let result = null;"
        "%s"
        "process.stdout.write(JSON.stringify({registered: Object.keys(handlers), result, sent}));"
    ) % (json.dumps(INDEX.as_uri()), body)
    return node(script, env=env)


def stub_hook(directory, answer):
    """A stand-in for the documented entry: it records argv and stdin, then prints `answer`."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "answer.json").write_text(json.dumps(answer), encoding="utf-8")
    script = directory / "agent-runtime-hook"
    script.write_text(
        "#!/bin/sh\n"
        'here=$(dirname "$0")\n'
        'printf "%s\\n" "$@" > "$here/argv"\n'
        'cat > "$here/stdin.json"\n'
        'cat "$here/answer.json"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


class ExtensionCase(unittest.TestCase):
    """A temp home and state root: the extension reads both, and nothing outside them."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.state),
        }

    def with_hook(self, answer):
        script = stub_hook(self.root / "hook", answer)
        self.env["AGENT_RUNTIME_HOOK"] = str(script)
        return script


@pytest.mark.spec("Pi lead stop continuation")
class StopDecisionTests(ExtensionCase):
    """S3: the lead's stop-guard answer becomes one more provider request, or nothing."""

    def test_S3_a_block_becomes_one_more_provider_request_with_the_hook_reason(self):
        self.with_hook({"block": True, "reason": REASON})
        answer = session(TURN_END, self.env)
        self.assertIn("turn_end", answer["registered"])
        self.assertEqual(
            answer["result"],
            {
                "continue": True,
                "entries": [
                    {
                        "type": "custom_message",
                        "content": REASON,
                        "customType": "agent-runtime-stop",
                        "display": False,
                    }
                ],
            },
        )

    def test_S3_the_decision_is_asked_of_the_documented_hook_entry(self):
        self.with_hook({"block": True, "reason": REASON})
        session(TURN_END, self.env)
        argv = (self.root / "hook" / "argv").read_text(encoding="utf-8").split()
        self.assertEqual(argv, ["stop", "--cli", "pi", "--dialect", "json"])
        payload = json.loads((self.root / "hook" / "stdin.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["hook_event_name"], "Stop")

    def test_S3_an_allowed_stop_adds_nothing(self):
        self.with_hook({"block": False, "reason": ""})
        answer = session(TURN_END, self.env)
        self.assertIsNone(answer["result"], "an allowed stop proposes no continuation")
        self.assertEqual(answer["sent"], [], "and pushes nothing")

    def test_S3_a_hook_that_cannot_answer_allows_the_stop(self):
        """No override and no identity: the real entry fails open, so the turn ends as Pi asked."""
        answer = session(TURN_END, self.env)
        self.assertIsNone(answer["result"])
        self.assertEqual(answer["sent"], [])

    def test_S3_a_draft_another_turn_end_handler_proposed_survives_the_block(self):
        """Returning `entries` replaces the field, so the guard appends after the other handlers."""
        self.with_hook({"block": True, "reason": REASON})
        earlier = {"type": "custom", "customType": "someone-else", "data": {"note": "kept"}}
        answer = session(turn_end([earlier]), self.env)
        entries = answer["result"]["entries"]
        self.assertEqual(entries[0], earlier, "another handler's draft is not dropped")
        self.assertEqual(entries[1]["content"], REASON)
        self.assertEqual(len(entries), 2)

    def test_S3_without_an_override_the_entry_is_this_checkouts_own(self):
        self.env.pop("AGENT_RUNTIME_HOOK", None)
        resolved = session("result = mod.hookPath({});", self.env)
        self.assertEqual(Path(resolved["result"]), HOOK)
        self.assertTrue(os.access(HOOK, os.X_OK), "the entry the extension runs is executable")


class WorkerStopTests(ExtensionCase):
    """S4: a worker's unreported assignment reaches the next request with both ways out intact.

    The naming half is the hook's, not this adapter's: `hooks/stop_guard.py` is what decides that a
    worker's turn may not end while its own assignment is unreported, and `tests/test_hooks_stop_guard.py` is
    where that answer is proved to name `report complete` and `report blocked`. This node pins the
    other half — the extension asks once, and carries that answer into exactly one more request
    without paraphrasing or dropping it — so it can fail when the reason is mangled, and it cannot
    stand in for a hook that refuses nothing (an allowed stop adds nothing, as S3 shows).
    """

    @pytest.mark.spec("Pi worker stop continuation")
    def test_S4_an_unreported_assignment_asks_for_one_more_request_naming_both_ways_out(self):
        self.with_hook({"block": True, "reason": WORKER_REASON})
        answer = session(TURN_END, self.env)
        result = answer["result"]
        self.assertTrue(result["continue"], result)
        self.assertEqual(len(result["entries"]), 1, "exactly one more request is asked for")
        content = result["entries"][0]["content"]
        self.assertIn("report complete", content)
        self.assertIn("report blocked", content)
        # The words are the hook's, not the extension's: it must carry them unchanged.
        self.assertEqual(content, WORKER_REASON)


@pytest.mark.spec("Pi wake push")
class WakePushTests(ExtensionCase):
    """S7: an unseen wake is pushed with its text, in order, into the session that leads the run."""

    LEAD = "lead-pane"
    WORKER = "worker-pane"
    PANE = "pane"

    def setUp(self):
        super().setUp()
        self.wakes = self.state / "orchestrate" / "repo" / "plan" / "wakes.jsonl"
        self.terminals()
        self.env["HC_TERMINAL_ID"] = self.LEAD

    def terminals(self, rows=None):
        """A stand-in for `hc ls --all --json`, which lists every terminal, the polling one included.

        The machine this test runs on: the lead's own terminal (no parent), the driver's pane
        `orchestrate-plan` under it, and a Pi worker's terminal under the lead as well.
        """
        if rows is None:
            rows = [
                {"name": "pi-lead", "address": self.LEAD, "parent": None},
                {"name": "orchestrate-plan", "address": self.PANE, "parent": self.LEAD},
                {"name": "pi-worker", "address": self.WORKER, "parent": self.LEAD},
            ]
        binary = self.root / "bin" / "hc"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text(
            "#!/bin/sh\ncat <<'EOF'\n%s\nEOF\n" % json.dumps({"terminals": rows}),
            encoding="utf-8",
        )
        binary.chmod(0o755)
        self.env["PATH"] = "%s:%s" % (binary.parent, os.environ["PATH"])

    def append(self, *lines):
        self.wakes.parent.mkdir(parents=True, exist_ok=True)
        with self.wakes.open("a", encoding="utf-8") as log:
            for line in lines:
                log.write(line + "\n")

    def wake(self, text, kind="wake"):
        return json.dumps({"kind": kind, "unit": "t1", "text": text})

    def poll(self, session_terminal=None):
        """One Pi session's poll; a fresh node process is a restart, the cursor is on disk."""
        if session_terminal is not None:
            self.env["HC_TERMINAL_ID"] = session_terminal
        return session("result = mod.pollWakes(pi, {env: process.env});", self.env)

    def cursors(self):
        path = self.state / "agent-runtime" / "pi-wakes.json"
        return json.loads(path.read_text(encoding="utf-8"))["sessions"]

    def test_S7_an_unseen_wake_is_pushed_with_its_text(self):
        self.append(self.wake("t2 reported complete"))
        answer = self.poll()
        self.assertEqual(answer["result"], ["t2 reported complete"])
        self.assertEqual(
            answer["sent"],
            [
                {
                    "message": {
                        "customType": "agent-runtime-wake",
                        "content": "t2 reported complete",
                        "display": True,
                    },
                    "options": {"triggerTurn": True},
                }
            ],
        )

    def test_S7_a_wake_already_seen_is_pushed_once_even_after_a_restart(self):
        self.append(self.wake("t3 needs attention"))
        self.assertEqual(self.poll()["result"], ["t3 needs attention"])
        again = self.poll()
        self.assertEqual(again["result"], [])
        self.assertEqual(again["sent"], [])

    def test_S7_wakes_appended_before_one_poll_are_pushed_in_the_order_the_driver_booked_them(self):
        # One advance() round books a wake per asking unit, milliseconds apart: two lines, one poll.
        self.append(self.wake("t1 asked for a decision"), self.wake("t2 needs attention"))
        answer = self.poll()
        self.assertEqual(answer["result"], ["t1 asked for a decision", "t2 needs attention"])
        self.assertEqual(len(answer["sent"]), 2)
        self.assertEqual(self.poll()["result"], [], "and a third poll pushes nothing")
        # A later wake resumes where the cursor stands: the lines already pushed are not replayed.
        self.append(self.wake("t3 reported blocked"))
        self.assertEqual(self.poll()["result"], ["t3 reported blocked"])
        self.assertEqual(self.poll()["result"], [])

    def test_S7_a_worker_session_pushes_nothing_and_does_not_spend_the_leads_wake(self):
        self.append(self.wake("t3 needs attention"))
        # A Pi worker is another session on the same machine, with its own terminal and one state file.
        worker = self.poll(self.WORKER)
        self.assertEqual(worker["result"], [])
        self.assertEqual(worker["sent"], [])
        self.assertNotIn(self.WORKER, self.cursors(), "a worker does not spend the lead's cursor")
        lead = self.poll(self.LEAD)
        self.assertEqual(lead["result"], ["t3 needs attention"])
        self.assertEqual(self.poll(self.WORKER)["result"], [])

    def test_S7_a_pane_whose_address_is_this_terminal_of_its_own_is_this_runs(self):
        # A driver started inside the lead's own terminal is that terminal: its address is the claim.
        self.append(self.wake("t2 needs attention"))
        self.terminals(
            [{"name": "orchestrate-plan", "address": self.LEAD, "parent": "somewhere-else"}]
        )
        self.assertEqual(self.poll()["result"], ["t2 needs attention"])

    def test_S7_a_session_does_not_push_another_runs_wake(self):
        self.append(self.wake("t3 needs attention"))
        other = self.state / "orchestrate" / "repo" / "other-plan" / "wakes.jsonl"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(self.wake("someone else's plan") + "\n", encoding="utf-8")
        self.terminals(
            [
                {"name": "pi-lead", "address": self.LEAD, "parent": None},
                {"name": "orchestrate-plan", "address": self.PANE, "parent": self.LEAD},
                {"name": "orchestrate-other-plan", "address": "pane2", "parent": "another"},
            ]
        )
        answer = self.poll()
        self.assertEqual(answer["result"], ["t3 needs attention"])

    def test_S7_a_sibling_run_whose_pane_shares_a_prefix_stays_with_its_own_lead(self):
        # `plan` and `plan-long` are two runs: a pane name is the whole name, never a prefix of it.
        self.append(self.wake("t1 asked for a decision"))
        sibling = self.state / "orchestrate" / "repo" / "plan-long" / "wakes.jsonl"
        sibling.parent.mkdir(parents=True, exist_ok=True)
        sibling.write_text(self.wake("the sibling plan") + "\n", encoding="utf-8")
        self.terminals(
            [
                {"name": "pi-lead", "address": self.LEAD, "parent": None},
                {"name": "orchestrate-plan", "address": self.PANE, "parent": self.LEAD},
                {"name": "orchestrate-plan-long", "address": "pane2", "parent": "another"},
            ]
        )
        self.assertEqual(self.poll()["result"], ["t1 asked for a decision"])

    def test_S7_a_run_whose_pane_name_the_driver_truncated_is_still_its_own(self):
        # hc allows 32 characters, so the driver cuts the pane's name and strips the hyphens the cut
        # leaves; the run is still this session's, matched by the name the driver built.
        slug = "pi-lead-rhythm-long-extra"
        self.assertGreater(len("orchestrate-" + slug), 32, "the name does not fit hc's limit")
        pane = ("orchestrate-" + slug)[:32].rstrip("-")
        self.assertEqual(
            pane, "orchestrate-pi-lead-rhythm-long", "the cut ends in a stripped hyphen"
        )
        run = self.state / "orchestrate" / "repo" / slug / "wakes.jsonl"
        run.parent.mkdir(parents=True, exist_ok=True)
        run.write_text(self.wake("t2 needs attention") + "\n", encoding="utf-8")
        self.terminals(
            [
                {"name": "pi-lead", "address": self.LEAD, "parent": None},
                {"name": pane, "address": self.PANE, "parent": self.LEAD},
            ]
        )
        self.assertEqual(self.poll()["result"], ["t2 needs attention"])

    def test_S7_an_explicitly_named_wake_file_still_belongs_to_a_lead(self):
        # Naming a file is not a claim to it: a worker inherits the lead's environment and must not
        # take the wakes — its own terminal has a parent — while the lead that named it receives them.
        self.append(self.wake("t2 reported complete"))
        self.env["AGENT_RUNTIME_WAKES"] = str(self.wakes)
        worker = self.poll(self.WORKER)
        self.assertEqual(worker["result"], [])
        self.assertEqual(self.poll(self.LEAD)["result"], ["t2 reported complete"])

    def test_S7_a_session_start_pushes_what_the_run_already_wrote(self):
        self.append(self.wake("t2 reported complete"))
        answer = session(SESSION_START, self.env)
        self.assertIn("session_start", answer["registered"])
        self.assertIn("session_shutdown", answer["registered"])
        self.assertEqual(answer["result"], ["t2 reported complete"])

    def test_S7_an_empty_line_pushes_nothing(self):
        self.append(self.wake(""))
        answer = self.poll()
        self.assertEqual(answer["result"], [])
        self.assertEqual(answer["sent"], [])

    def test_S7_a_new_wake_after_a_seen_one_is_pushed(self):
        self.append(self.wake("t2 reported complete"))
        self.poll()
        self.append(self.wake("t4 needs attention"))
        self.assertEqual(self.poll()["result"], ["t4 needs attention"])

    def test_S7_a_line_a_crash_cut_short_pushes_nothing(self):
        self.append('{"kind": "wake", "unit": "t1", "te')
        answer = self.poll()
        self.assertEqual(answer["result"], [])
        self.assertEqual(answer["sent"], [])


@pytest.mark.spec("Pi long sleep refused")
class LongSleepTests(unittest.TestCase):
    """S8: a sleep of 60 seconds or more is refused naming `wait(all)`; anything shorter is untouched."""

    def tool_call(self, command):
        return session(
            "for (const fn of handlers.tool_call || []) {"
            '  result = (await fn({toolName: "bash", input: {command: %s}}, {})) ?? null;'
            "}" % json.dumps(command),
            {"PATH": os.environ["PATH"], "HOME": tempfile.gettempdir()},
        )

    def test_S8_a_sleep_of_a_minute_or_more_is_refused_naming_wait_all(self):
        answer = self.tool_call("sleep 90")
        self.assertTrue(answer["result"]["block"], answer)
        self.assertIn("wait(all)", answer["result"]["reason"])
        self.assertIn("90", answer["result"]["reason"], "the refusal names how long it waits")

    def test_S8_a_sleep_shorter_than_a_minute_is_untouched(self):
        answer = self.tool_call("sleep 59")
        self.assertIsNone(answer["result"])

    def test_S8_the_shapes_of_a_long_sleep_are_all_refused(self):
        for command in (
            "sleep 60",
            "sleep 1m",
            "sleep 1.5m && echo ready",
            "sleep 30 30",
            "time sleep 61",
            "FOO=1 sudo sleep 90",
            "echo hi; sleep 120",
            "sleep 2h",
            "sleep 1h30m",
            "sleep infinity",
        ):
            self.assertTrue(
                handler("toToolCall", {"toolName": "bash", "input": {"command": command}}), command
            )

    def test_S8_the_shapes_of_a_short_or_absent_sleep_are_all_untouched(self):
        for command in (
            "sleep 30",
            "sleep 0.9m",
            "sleep 0.5m && echo ready",
            "printenv sleep 300",
            "echo sleep 300",
            "sleep 10 && sleep 20",
            "grep -c sleep 60 README.md",
        ):
            self.assertIsNone(
                handler("toToolCall", {"toolName": "bash", "input": {"command": command}}), command
            )

    def test_S8_another_tool_is_never_refused(self):
        for tool in ("read", "write", "edit", "grep"):
            self.assertIsNone(
                handler("toToolCall", {"toolName": tool, "input": {"command": "sleep 600"}}), tool
            )
