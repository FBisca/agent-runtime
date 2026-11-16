"""The spawn tests' shared pieces: the lead's dispatch environment and the spawn arguments they
drive; pytest does not collect this module."""

ENV = {
    "CODEX_THREAD_ID": "lead-session",
    "HC_SCOPE": "lead-token",
    "HC_TERMINAL_SOCKET": "/hc/sock",
    "AGENT_RUNTIME_DISPATCH": "1",
}
ARGS = {"kind": "worker", "name": "w1", "task": "x", "cwd": "/tmp"}
