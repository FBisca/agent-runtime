# Handoff — the uv workspace migration, after the runtime and orchestrator members

**State:** the plan `docs/plans/2026-11-07-uv-workspace-python-standards.plan.md` is APPROVED and in flight.
T1 and T2 are merged. T3, T4 and T5 are in pull request #62, waiting for CI and a merge.
T6 is the owner's ticket and has not started.

**Read this before touching anything.** The work below was done by hand, in one pass, with the
`orchestrate` driver retired halfway through (see "The driver is gone" below).

---

## 1. Where the work stands

| Ticket | What it delivers | State |
|---|---|---|
| T1 | The compatibility baseline and the contract tests | MERGED — PR #56 (`87de50b`) |
| T2 | The uv workspace, the member manifests, the tracked toolchain budget | MERGED — PR #61 (`25e1658`) |
| T3 | The runtime moves into its member, the entry points stay | PR #62 (`4838d16`) |
| T4 | The orchestrator member: typed contracts, the `RuntimeClient` seam | PR #62 (`685e8c4`) |
| T5 | The typed YAML routing policy, the no-SQLite proof | PR #62 (`685e8c4`) |
| T6 | Full CI + smoke sessions on Pi/Claude/Codex, the rollback check | NOT STARTED (owner) |

PR #62 has two commits plus a plan-status commit:

1. `refactor(runtime): the runtime lives in its member; the entry points stay`
2. `feat(orchestrator): the orchestrator member, with typed contracts and the runtime seam`
3. `docs(plan): the status cells of the two pull requests`

**Next step for T3-T5:** wait for CI on #62, merge it (squash), then set the three Status cells to
`COMPLETE <date> (PR #62, squash <sha>)` in the plan and push that as a one-line PR — the same shape
as PR #59 did for T1.

---

## 2. What the two commits do

### The runtime member (T3)

- The sources moved: `server/*.py` -> `packages/agent-runtime/src/agent_runtime/*.py`.
- `server/agent_runtime.py`, `server/mcp_server.py` and `server/profiles.py` stayed behind as the
  entry points: the plugin's `.mcp.json` runs `server/mcp_server.py`, the installer's shims exec
  `server/agent_runtime.py`, and the orchestrator resolves `$AGENT_RUNTIME_HOME/server/agent_runtime.py`.
- The hooks import the package (`hooks/hook_lib.py` puts `packages/agent-runtime/src` on the path).
- The compatibility baseline (`docs/compat/compatibility-baseline.json`) records the new source
  paths, and the contract test that pins them scans the member instead of `server/`.
- Typing: the member is clean under `ty`. Ten optionality sites were narrowed rather than ignored
  (`ps_entry`, `FORCED_GENERATION`, the `store`/`address` locals, the profile launcher).

### The orchestrator member (T4, T5)

- The engine moved from the skill: `agent-skills/skills/orchestrate/scripts/*.py` ->
  `packages/agent-orchestrator/src/agent_orchestrator/`. `orchestrate.py` is now `driver.py`.
- `runtime_client.py` is new: the `RuntimeClient` protocol and `CliRuntimeClient`, the one
  implementation, which calls `agent-runtime call <tool> --lead <id> --json <args>`. The driver's
  `runtime_call` goes through it.
- `contracts.py` and `routes.py` validate with Pydantic v2 (`extra="forbid"`, explicit versions).
  The error messages changed with them: they are Pydantic's now, not the hand-rolled ones.
- The CLI keeps its commands: `agent-orchestrator` and `python -m agent_orchestrator` answer the
  same as the skill's script did.
- `tests/test_orchestrator_contract.py` is new: eleven contracts (the seam, the typed boundary,
  the CLI surface, the no-state rule).

---

## 3. Decisions a reader must know (all in the plan's Decisions table)

- **D7** — the ralph PoC is retired for this plan; T2 started from its unreviewed branch.
- **D8** — the `agent-skills` shim is a **separate cross-repository pull request** and is still
  open. The skill's own copy of the engine still works; the copy in this repository is the one
  that will be canonical. Nothing has switched the skill over yet.
- **D11** — the engine's own test suite stays with the skill until D8's pull request moves it with
  it. This repository carries the contracts, not the engine's internals.
- **D12** — the moved engine is type-clean; the legacy budget covers the rest of the tree.

---

## 4. What is still open, and how to pick it up

1. **Merge PR #62** (CI green, then squash) and set T3/T4/T5's Status cells.
2. **T6 (the owner's ticket)**: full CI on the matrix, smoke sessions on Pi, Claude and Codex, and
   the rollback check (revert the two members' commits and the legacy shims must still work with no
   state migration). The commands are in `AGENTS.md`:
   `uv sync --frozen && uv run pytest -n 4`, `uv run pytest -m integration`, `shellcheck install.sh bin/*`.
3. **The archive**: the plan's living spec is `docs/specs/python-workspace-contracts.md` and it does
   **not exist yet**. When T6 is done, the scenarios S1-S8 become requirements in that file, the
   tests' `S<n>` names become `<area>/R<m>`, and the plan's Status becomes `ARCHIVED <date>`.
4. **D8's cross-repository pull request** (in `agent-skills`): point the skill at the installed
   member (`uv run --package agent-orchestrator agent-orchestrator ...`) and delete the skill's copy
   of the engine. Until then the engine exists twice, and the two copies drift.
5. **The next plans in the queue**: `docs/plans/2026-11-07-selective-remote-installer.plan.md`
   (P4c) and, in `agent-skills`, the orchestration-baselines and orchestrator-routing plans.

---

## 5. The traps on this machine

- **The ralph PoC is dead.** `~/developer/ralph-poc` was the overnight stand-in for the real
  driver; it was killed mid-P3-g3 on 2026-11-08 and its state is intact
  (`state/state.json`), so it can be resumed, but it is retired for this plan. Its P4b-g2 branch
  (`ralph/P4b-g2`) is the implementation T2 started from.
- **The orchestrate driver was tried and abandoned.** It burned tokens without delivering: every
  spawn escalates within 30 seconds because the runtime flags a worker that has not reported as
  `unreported` (agent-runtime spec R100) and the driver's contract asks for one report at the end.
  The plan records the rule in D10; the runtime's window is still 30 s, so a slow first turn still
  parks a ticket on a person.
- **Pi's trust prompt blocks a worker.** A worker in a directory pi does not trust waits on a
  modal prompt and never starts. `~/.pi/agent/trust.json` must carry the parent folder of the
  worktrees (`~/developer/agent-runtime.worktrees`), or every spawn dies silently.
- **`~/.pi/agent/mcp.json` had lost its `agent-runtime` entry.** It was restored from
  `mcp.json.bak-20261108112035`; without it a pi worker cannot call `report` and the runtime never
  sees it finish.
- **The suite is load-flaky.** `tests/test_event_wait.py` and `tests/test_stop_failures.py` fail
  under parallel load; PR #60 repaired most of it, but a timing test can still flake
  (`test_S13_fast_mid_turn_progress_keeps_the_resume_baseline`). Run the failing test alone before
  believing it.
- **The lint and type budgets are measured debt**, not round numbers: ruff 45, ruff format 31,
  ty 285. A change that pushes a count over its budget fails CI on purpose.

---

## 6. The commands

```bash
cd ~/developer/agent-runtime                     # the checkout
uv sync --frozen --all-packages                  # both members
uv run pytest -n 4                               # the whole suite
uv run pytest -m integration -n 4                # the integration half
uv run ruff check . && uv run ruff format --check . && uv run ty check
shellcheck install.sh bin/*
```

The branches: `feat/uv-workspace` (T2, merged), `feat/runtime-package` (T3-T5, PR #62).
The worktrees live under `~/developer/agent-runtime.worktrees/`.
