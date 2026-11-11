# AGENTS.md

Instructions for agents working in this repo (Claude Code, Codex, Pi alike).

## The spec is the ledger

`docs/specs/agent-runtime.md` and `docs/specs/harness-adapters.md` are the living contracts: numbered
WHEN/THEN requirements, each traced to a scenario and a test. `docs/plans/` holds the plans that
produced them.

- Every behaviour change starts as a scenario in a plan, is written red first, and lands with its
  requirement.
- A plan's tickets carry their status in the plan file. Mark them as they land.
- Fix the class, not the instance: when you fix a defect, search every other place with the same
  pattern.

## Testing

- **Unit:** In-process, isolated, deterministic; mock external dependencies.
- **Integration:** Tests real component boundaries (subprocesses, IPC, databases, network), even when
  dependencies are faked.
- Prefer unit tests for fast feedback; use integration tests to verify contracts.
- Classify tests by behaviour, not filename. Never weaken or reclassify tests just to improve speed.

[uv](https://docs.astral.sh/uv/) and pytest are the testing workflow. `uv sync --frozen --all-packages`
sets the environment up from a fresh checkout; `uv run pytest` is the canonical command.

| Command | What it runs |
|---|---|
| `uv run pytest` | the fast suite: the `unit` tests, in process (under 3 seconds) |
| `uv run pytest -m integration` | the `integration` tests: the real boundaries, fakes behind them |
| `uv run pytest -n 4` | the whole suite, in parallel |
| `uv run pytest --durations=20` | the whole suite, with the slowest tests |

Run the fast suite while you work, and the whole suite before you report a change done. Name a file or
a node id (`uv run pytest tests/test_pi_worker.py`) to run that one; it keeps its own marker. The tests
are `unittest.TestCase` classes, so `python3 -B -m unittest discover -s tests` still runs them, but uv
and pytest are how they are run. A live acceptance run needs a real terminal and is opt-in
(`AGENT_RUNTIME_LIVE=1`).

Each test file carries `pytestmark = pytest.mark.unit` or `pytest.mark.integration`. Classify by what
the test does: a test that starts a process, opens a socket or writes to the filesystem is an
integration test, whatever it is called.

## The hooks and the CLI are the boundary

- The PreToolUse hooks are defence in depth; the CLI and the server are the authority. Never make the
  hook the only thing that refuses something. The one exception: a native in-process subagent cannot be
  told from its Lead outside the host hook, so R74 and R89 stay hook-only. No other refusal is an
  exception.
- A refusal names the reason and the way out. "This is not allowed" is not a refusal.
- Never log a secret, a token or a full environment; the tests check for leaks.

## Style

- Python 3.11, standard library only in `server/` and `hooks/` (the plugin ships without dependencies).
- Keep the server's stdout to protocol JSON only; diagnostics go to stderr.
- Comments say why, not what.
