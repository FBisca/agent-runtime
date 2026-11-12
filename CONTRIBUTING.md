# Contributing

Thanks for helping. This page covers how the project is built and how to run the tests. Agents working
in the repo also follow [`AGENTS.md`](AGENTS.md).

## How it is built

Spec- and plan-driven. [`docs/specs/agent-runtime.md`](docs/specs/agent-runtime.md) is the living contract: numbered WHEN/THEN requirements, each traced to a scenario and a test (requirements about a consuming repo's config are marked as moved there). [`docs/specs/harness-adapters.md`](docs/specs/harness-adapters.md) covers the harnesses, [`docs/specs/orchestrator-engine.md`](docs/specs/orchestrator-engine.md) the orchestrator, and [`docs/specs/python-workspace-contracts.md`](docs/specs/python-workspace-contracts.md) the workspace packaging. [`docs/plans/`](docs/plans/) holds the archived plans that produced them, with their decisions and scenarios.

Every behavior change starts as a scenario, is written red first, and lands with its requirement.

The code is one uv workspace with two installable members and one lockfile: `packages/agent-runtime` (the standard-library-only runtime) and `packages/agent-orchestrator` (the typed engine, with Pydantic and PyYAML). The documented commands still live in `server/` and `bin/`, so the plugin, the installer's shims and the hooks are unchanged; `uv sync --frozen --all-packages` installs both members.

## Tests

[uv](https://docs.astral.sh/uv/) and pytest are the project's testing workflow. A fresh checkout needs
nothing but uv:

```sh
uv sync --frozen --all-packages  # the environment (pytest, pytest-xdist) and both members
uv run pytest           # the fast suite: the unit tests, in process (under 3 seconds)
uv run pytest -m integration   # the tests that drive a real boundary (a subprocess, a socket)
uv run pytest -n auto   # the whole suite, one worker per core
uv run pytest --durations=20  # the whole suite, with the slowest tests
```

The tests are `unittest.TestCase` classes, so `python3 -B -m unittest discover -s tests` still works;
pytest is how they are run. Every test file carries a marker that says what it does: `unit` for the
in-process, isolated, deterministic tests (no subprocess, no server) and `integration` for the tests
that drive a real component boundary - a subprocess, an IPC pipe, the filesystem - even when the
dependency behind that boundary is faked. `uv run pytest` runs the unit tests; the integration tests run
with `-m integration`, and the whole suite runs with `-n auto` (or any `-n`). The suite drives the server
over stdio against a fake `hc`. The live mixed-CLI acceptance run is opt-in (`AGENT_RUNTIME_LIVE=1`).

CI runs the suite on Python 3.11 to 3.13 on Linux and macOS, and runs shellcheck on `install.sh` and
`bin/`.
