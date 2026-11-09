# Handoff — after the engine's suite moved into the member

**State:** the action plan `docs/plans/2026-11-08-remaining-work.action-plan.md` is in flight. Its
**W1/PR-1** is done and open as **PR #65** (`feat/orchestrator-engine-suite`, commit `03e5e62`).
W1/PR-2, W2, W3 and W4 are untouched.

**Read this before touching anything.** The engine now exists twice still, but only one copy is
canonical: this repository holds the engine and its suite; `agent-skills` holds a stale copy of the
engine and a copy of the tests that were moved.

---

## 1. What landed (PR #65)

| Piece | Where |
|---|---|
| The engine's own suite (32 files, ~5.2k lines) | `packages/agent-orchestrator/tests/` |
| The runtime seam is the process the client speaks through | `packages/agent-orchestrator/src/agent_orchestrator/runtime_client.py` |
| A refusal reaches the driver as a `ValueError` | `packages/agent-orchestrator/src/agent_orchestrator/driver.py` (`runtime_call`) |
| A route refusal names the value and the way out | `packages/agent-orchestrator/src/agent_orchestrator/routes.py` (`_problem`) |
| The ledger: what this repo guarantees about the engine | `docs/specs/orchestrator-engine.md` (R1–R6) |
| The plan of record for the move | `docs/plans/2026-11-08-orchestrator-engine-suite.plan.md` |

The port was **red against the member** until the member spoke like the engine. Three real gaps, all
now fixed and all now pinned by the suite: the runtime seam was not injectable, a refused runtime call
crashed the run, and a route refusal did not name the offending value.

## 2. The evidence

| Check | Result |
|---|---|
| `uv run pytest -n 4` | **899 passed**, 4 skipped, 282 subtests (146 s) |
| `uv run pytest -m integration -n 4` | the integration half of the same suite |
| `uv run ruff check .` | 40 (budget 45) — unchanged |
| `uv run ruff format --check .` | 30 files (budget 31) — unchanged |
| `uv run ty check packages/ tests/test_workspace_isolation.py` | clean |
| `uv run ty check .` | 277 (budget 285) — unchanged |
| `shellcheck install.sh bin/*` | clean |
| `converge.py --final --base main` | coverage, tagged, scope, spec — all **PASS** |

## 3. What is still open, in order

1. **Merge PR #65** (CI green, then merge). The plan's tickets are already `COMPLETE`.
2. **W1/PR-2 — the cross-repository pull request, in `agent-skills`.** This is the one that ends the
   duplication:
   - the skill's `skills/orchestrate/scripts/*.py` copy of the engine is deleted;
   - `skills/orchestrate/scripts/orchestrate.py` becomes a thin delegating shim to the member's CLI
     (`agent-orchestrator`, resolved through `AGENT_RUNTIME_HOME` → the checkout's
     `uv run --package agent-orchestrator agent-orchestrator`, or the `agent-orchestrator` shim on PATH);
   - the engine's internal tests leave the skill's suite — they moved in PR #65;
   - the skill's own suite (frontmatter, portability, repository checks) stays;
   - a guard test fails if a copy of the engine reappears under `skills/`;
   - the skill's `SKILL.md` and `references/` document the seam, the resolution order and the way out
     when the member is not installed (**decision A3: fail closed**, never fall back to a second engine).
   - The agent-skills plans that still describe the engine (`code-driven-orchestrator`,
     `orchestrate-lean-loop`) should archive against the engine's new home; their scenarios now live in
     `agent-runtime`'s `docs/specs/orchestrator-engine.md`.
3. **W2 — P4c, the selective remote installer** (`agent-runtime`,
   `docs/plans/2026-11-07-selective-remote-installer.plan.md`). The plan is approved but **not runnable
   by the driver**: its header is `Status: **APPROVED — READY FOR EXECUTION**` (the driver reads `Status:`
   as missing) and its Tickets table uses `ID | Vertical deliverable | Scenarios | Depends | …` instead of
   the fixed `# | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status`
   columns. Its `T3` is `large`, which the driver refuses (`big`/`large` need chunked dispatch) — split
   it in two. Its living spec `docs/specs/installer-contract.md` does not exist yet.
4. **W3 — `orchestrator-routing-decoupling` (`agent-skills`)**: one owner ticket (T3, "approve
   fail-closed routing compatibility"). Its header has the same `Status:` problem; the driver cannot run
   it until that is fixed.
5. **W4 — `orchestration-baselines` T4 (`agent-skills`)**: the comparative benchmark. The plan's own
   seam says the task corpus does not exist yet — only `benchmarks/evidence-manifest.json` (T3's schema).
   Split it into corpus, harness and runs before giving it to a worker.

## 4. Decisions taken (from the action plan)

| # | Decision | State |
|---|---|---|
| A1 | The order: D8 → P4c → routing → baselines | applied |
| A2 | Which engine drives the runs | the skill's copy until PR #65 merges; the member afterwards (the member's suite is now its proof) |
| A3 | What the skill does when the member is missing | **fail closed**, with the install command and `AGENT_RUNTIME_HOME` as the way out |
| A4 | The release artifact contract P4c's T3 verifies against | a GitHub Release with per-asset SHA-256 from CI; the bootstrap refuses a moving `main`; CI tests run against a local fixture, so no real release is needed to pass |
| A5 | Where the baselines runs happen | corpus and harness in the driver, the runs by hand |
| A6 | The 30-second rule | the worker's first act is a one-line progress report, written into the first ticket's "What it delivers" cell, which is copied into the worker's `task.md` |

## 5. Traps on this machine

- **The 30 s window.** `UNREPORTED_GRACE_SECONDS = 30` (R100, hard-coded). A worker that has not
  reported within 30 s is flagged `unreported`, and the driver parks the ticket on a person. It has
  already fired once (`escalation … ended without a report: runtime state idle`, 2026-11-07). The run
  parks rather than dies: `<run>/attention.md` names what waits, and the answer goes through the plan's
  Status cell.
- **Pi's trust prompt** blocks a worker in a directory Pi does not trust:
  `~/.pi/agent/trust.json` must carry the parent of `~/developer/agent-runtime.worktrees/`.
- **`~/.pi/agent/mcp.json`** must keep its `agent-runtime` entry; without it a pi worker cannot call
  `report` and the runtime never sees it finish. It was restored once already.
- **Converge reads a test's id from the docstring's first line**, and it only looks at the line after the
  `def`. A test whose signature is split over several lines (`def test_x(\n    self,\n):`) hides its
  docstring from the gate. Six ported tests were renamed and joined for that reason; keep the
  signatures on one line.
- **The budgets are measured debt**: ruff 40/45, ruff format 30/31, ty 277/285. A change that pushes a
  count over its budget fails CI on purpose. The format budget sits one file below its limit.
- **The suite is load-flaky** in two files (`tests/test_event_wait.py`, `tests/test_stop_failures.py`);
  run the failing test alone before believing it.

## 6. What not to redo

- The rollback check is a **test**, not a manual procedure (`tests/test_rollback_contract.py`): if the
  pre-workspace commit moves, the baseline pin and the test fail together.
- The requirement ids of the existing specs are **stable**; the plan's `S<n>` ids live only in the plan
  file and in the spec's `(from <slug>/S<n>)` provenance.
- The ported suite's tests keep the **source scenario ids** they were written against
  (`code-driven-orchestrator/S11`, `orchestrate-lean-loop/S33`, `P4/S3`, `P3.T3/S2`,
  `pi-worker-driver/S1`) next to the local requirement they answer. Do not re-tag them again: the
  mapping is in `docs/specs/orchestrator-engine.md`.

## 7. The commands

```bash
cd ~/developer/agent-runtime
uv sync --frozen --all-packages
uv run pytest -n 4
uv run pytest -m integration -n 4
uv run ruff check . && uv run ruff format --check . && uv run ty check
shellcheck install.sh bin/*
python3 ~/.agents/skills/converge/scripts/converge.py \
  docs/plans/2026-11-08-orchestrator-engine-suite.plan.md --final --base main

cd ~/developer/agent-skills
uv sync && uv run pytest -m "" -n 4 && scripts/checks.sh
```
