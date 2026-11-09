# Plan: The Engine's Own Suite Lives With the Engine

Slug: `orchestrator-engine-suite`. Living spec: `docs/specs/orchestrator-engine.md`. Status: **APPROVED**
(owner approved the action plan `2026-11-08-remaining-work.action-plan.md`, W1/PR-1, on 2026-11-08).

## Why

The engine exists twice: the copy in `agent-skills` (`skills/orchestrate/scripts/`) and the copy in
this repository's member (`packages/agent-orchestrator/`). The member is the canonical one; the skill's
copy is the older, hand-rolled one, and the two drift. The engine's own test suite still lives with the
skill, so the member is shipped without the suite that proves it.

This plan moves the suite to the member and points it at the member's driver. It does not delete the
skill's copy — that is the cross-repository pull request, a separate change in the other repository.

## Sources

- `agent-skills`: `skills/orchestrate/tests/` (32 files, the suite), `skills/orchestrate/scripts/` (the
  copy this repository's member supersedes).
- `docs/plans/2026-11-08-remaining-work.action-plan.md` (W1, PR-1) — the decision and its ordering.
- `docs/specs/python-workspace-contracts.md` — the workspace the member lives in (R192–R205).

## Decisions

| # | Choice | Rejected | Why | Status |
|---|---|---|---|---|
| D1 | The suite lands in `packages/agent-orchestrator/tests/` and imports the member by name. | Keeping it in `agent-skills` and running it from there. | The engine's tests belong with the engine; a suite that runs against a copy tests the copy. | Requested |
| D2 | The suite imports `agent_orchestrator.driver` and calls the CLI as `python -m agent_orchestrator`. | Loading the driver from a file path, as the skill did. | The member is a package; the path trick breaks under a package install. | Requested |
| D3 | The runtime seam stays the process: the client takes the process it speaks through, so the suite answers the runtime in process and a run starts no process per call. | A second seam, or a fake runtime inside the member. | One seam, in one place; the fake stays in the test suite. | Requested |
| D4 | A refused runtime call reaches the driver as a `ValueError`, the driver's own word for it. | A second error type the driver must learn. | The driver catches one kind of failure; the client speaks another. | Requested |
| D5 | A route refusal names the value and the way out. | The typed validator's own message. | A refusal names the reason and the way out (AGENTS.md). | Requested |
| D6 | The ported tests keep the source id of the scenario they came from, next to the local requirement they now answer. | Re-tagging them with the local id only. | The provenance survives the move; the local id says what the move established. | Requested |
| D7 | The moved suite is a move, not a rewrite: the behaviour is preserved and the existing tests are the contract. | Porting the tests to the new style as they move. | A move that rewrites its tests proves nothing about the move. | Requested |
| D8 | The harness the port brought waits, bounded, for the tree it killed before it calls the tree alive. | Reporting the tree as alive after a fixed short poll. | The tree is dead the moment the signal lands; the group only empties when the system reaps it, and under load that takes longer than a moment. | Requested |

## Scenarios

- **S1** WHEN the engine's own test suite runs, THEN it exercises the member's driver, not a copy of it.
- **S2** WHEN the member's driver is exercised by the ported suite, THEN the suite's behaviour is the
  member's behaviour: a test that passes only against the old copy fails here.
- **S3** WHEN the ported suite lands, THEN the tracked lint and type budgets do not grow, and the member
  is type-clean by contract.
- **S4** WHEN a route config names something the driver cannot run, THEN the refusal names the value and
  the way out.
- **S5** WHEN the runtime refuses a call, THEN the driver sees a `ValueError` and the run reports the
  refusal instead of crashing.
- **S6** WHEN `launch` starts a run, THEN the pane runs the member's CLI.
- **S7** WHEN a run is terminated for exceeding its budget, THEN the harness waits, within a bounded
  window, for the process group to empty before it decides the tree survived, and a tree that really
  outlived the run still fails the check.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The engine's suite lands in the member, re-pointed at the member's driver and CLI, with the runtime seam the suite patches being the member's seam. | S1, S2 | none | `packages/agent-orchestrator/tests/`, `packages/agent-orchestrator/src/agent_orchestrator/runtime_client.py`, `packages/agent-orchestrator/src/agent_orchestrator/driver.py`, `pyproject.toml` | regular | complex | agent | COMPLETE 2026-11-08 |
| T2 | The member keeps the driver's own words: a refusal is a `ValueError`, a route refusal names the value, and `launch` runs the member's CLI. | S2, S4, S5, S6 | T1 | `packages/agent-orchestrator/src/agent_orchestrator/routes.py`, `packages/agent-orchestrator/src/agent_orchestrator/driver.py`, `packages/agent-orchestrator/tests/` | regular | complex | agent | COMPLETE 2026-11-08 |
| T3 | The suite is green against the member, the tracked budgets do not grow, and the member's behaviour is the ledger: the local spec names what the move established. | S2, S3 | T2 | `packages/agent-orchestrator/tests/`, `docs/specs/orchestrator-engine.md`, `docs/specs/python-workspace-contracts.md`, `docs/plans/` | regular | simple | agent | COMPLETE 2026-11-08 |
| T4 | The benchmark harness the port brought waits for the tree it killed, bounded, so the member's CI stops measuring the machine's reaping speed. | S7 | T1 | `packages/agent-orchestrator/tests/bench.py`, `packages/agent-orchestrator/tests/test_baselines.py`, `docs/specs/orchestrator-engine.md` | small | simple | agent | COMPLETE 2026-11-09 |

## Out of scope

- Deleting the skill's copy of the engine: that is the cross-repository pull request, in `agent-skills`.
- The skill's own suite (frontmatter, portability, repository checks): it stays in `agent-skills`.
- Publishing the member or changing the installer: that is P4c.

## Risks / rollback

- The ported suite is the member's only proof. If it is wrong, the member is wrong: the suite is
  authoritative until the skill's copy is deleted.
- The budgets are measured debt. A moved file that does not fit the house style grows the debt: run
  `uv run ruff format` and `uv run ruff check --fix` on what the move adds, before it is reported.
- The harness the port brought was the skill's, and the skill's CI was lighter: the member's CI runs the
  suite in parallel on two-core runners, and the tree check gave up before the system reaped the tree it
  had killed. T4 repairs it.
- Rollback is one revert: the suite was added, nothing else changed its shape.
