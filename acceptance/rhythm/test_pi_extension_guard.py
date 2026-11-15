"""Outcome oracle for the pi-lead-rhythm plan's Pi adapter.

The extension decides nothing: the stop decision comes from `agent-runtime-hook`, and what the adapter
owns is the translation into Pi's dialect — one more provider request at a turn boundary, and a push
into an idle session. `extensions/agent-runtime/handlers.mjs` is pure for that reason and is driven
here through node, so the test fails on the translation and not on a Pi session.

The control test holds today and must keep holding: it proves node runs an ES module from a file URL,
so a failing case fails because of the defect, not the harness.
"""

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
HANDLERS = ROOT / "extensions" / "agent-runtime" / "handlers.mjs"
REASON = "agent-runtime: 2 unfinished, 1 undelivered — call wait(all) before ending the turn"


def call(name, payload):
    """Import the adapter's handler module and call one pure function with `payload`."""
    script = (
        "const m = await import(%s);"
        "process.stdout.write(JSON.stringify(m[%s](%s) ?? null));"
        % (json.dumps(HANDLERS.as_uri()), json.dumps(name), json.dumps(payload))
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_control_node_runs_an_es_module_with_a_top_level_import():
    """The control must pass at base, before the adapter exists."""
    script = (
        "const {basename} = await import('node:path');"
        "process.stdout.write(basename('/a/handlers.mjs'));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "handlers.mjs"


class TestTurnEnd:
    def test_S3_a_block_becomes_one_more_provider_request_with_the_reason(self):
        answer = call("toTurnEnd", {"block": True, "reason": REASON})
        assert answer == {
            "continue": True,
            "entries": [{"type": "custom_message", "content": REASON}],
        }

    def test_S3_an_allowed_stop_adds_nothing(self):
        assert call("toTurnEnd", {"block": False, "reason": ""}) is None


class TestWakePush:
    def test_S7_an_unseen_wake_is_pushed_with_its_text(self):
        answer = call("toWake", {"line": "t2 reported complete", "seen": ""})
        assert answer == {"content": "t2 reported complete", "seen": "t2 reported complete"}

    def test_S7_the_same_wake_is_pushed_once(self):
        line = "t3 needs attention"
        assert call("toWake", {"line": line, "seen": line}) is None
        assert call("toWake", {"line": "", "seen": ""}) is None
