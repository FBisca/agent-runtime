"""Every test starts as a person's Lead session, whatever host the suite itself runs in.

`plan approve` asks the runtime CLI who the caller is whenever a host marker is present, and many tests approve
under a Lead marker; a developer running the suite from an agent session would otherwise be refused by the real
lookup. A test of the refusal replaces this."""

import json
import subprocess

import pytest


def verified_lead_process(argv, timeout=None):
    answer = {"role": "lead", "caller": {"kind": "lead", "verified": True}}
    return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")


@pytest.fixture(autouse=True)
def runtime_verifies_a_lead(monkeypatch):
    from agent_orchestrator import runtime_client

    monkeypatch.setattr(runtime_client, "runtime_process", verified_lead_process)
