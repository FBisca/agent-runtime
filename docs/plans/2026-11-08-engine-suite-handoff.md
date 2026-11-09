# Handoff — after the engine's suite moved into the member

**State:** the action plan `docs/plans/2026-11-08-remaining-work.action-plan.md` is still in flight. Its
**W1** is done on both sides: **PR #65 is merged** (`main` = `d834c7c`), and the cross-repository pull
request that ends the duplication is open as **agent-skills PR #53**. The pre-flights for **W2** and **W3**
are open as **agent-runtime PR #66** and **agent-skills PR #54**. The runs themselves (W2, W3, W4) are
untouched.

**Read this before touching anything.** The engine now exists once: this repository holds the engine and its
suite; `agent-skills` holds a shim that hands the command to the member, and the copy that used to live
there is gone.

---

## 1. What landed

| Piece | Where | State |
|---|---|---|
| The engine's own suite (32 files, ~5.2k lines) | `packages/agent-orchestrator/tests/` | merged (#65) |
| The benchmark waits for the tree it killed | `packages/agent-orchestrator/tests/bench.py` | merged (#65) |
| The ledger: what this repo guarantees about the engine | `docs/specs/orchestrator-engine.md` (R1–R7) | merged (#65) |
| The skill's engine copy, gone; the script is a shim | `agent-skills`: `skills/orchestrate/scripts/orchestrate.py` | **PR #53** |
| The guard: no copy of the engine under `skills/` | `agent-skills`: `skills/orchestrate/tests/test_engine_home.py` | **PR #53** |
| The seam's own ledger | `agent-skills`: `docs/specs/orchestrator-single-engine.md` | **PR #53** |
| P4c's plan, runnable by the driver | `docs/plans/2026-11-07-selective-remote-installer.plan.md` | **PR #66** |
| The routing plan, runnable by the driver | `agent-skills`: `docs/plans/2026-11-07-orchestrator-routing-decoupling.plan.md` | **PR #54** |

**The three gaps the port found** are fixed and pinned by the suite: the runtime seam was not injectable, a
refused runtime call crashed the run, and a route refusal did not name the offending value.

**The seam, as it now stands.** The skill's script resolves the member's CLI in this order:
`$AGENT_RUNTIME_HOME` (the checkout of agent-runtime: its `.venv/bin/agent-orchestrator` when the environment
is synced, else `uv run` inside that checkout) → `agent-orchestrator` on `PATH`. When neither answers it
refuses, names the way out and exits 2. It never falls back to a second engine.

## 2. The evidence

| Check | Result |
|---|---|
| `uv run pytest -n 4` (agent-runtime) | **899 passed**, 4 skipped, 282 subtests (143 s) |
| `uv run ruff check .` | 40 (budget 45) — unchanged |
| `uv run ruff format --check .` | 30 files (budget 31) — unchanged |
| `uv run ty check packages/ tests/test_workspace_isolation.py` | clean |
| `uv run ty check .` | 277 (budget 285) — unchanged |
| `shellcheck install.sh bin/*` | clean |
| `converge.py --final --base main` (the engine-suite plan) | coverage, tagged, scope, spec — all **PASS** |
| `uv run pytest -m "" -n 4` (agent-skills) | **212 passed** |
| `scripts/checks.sh` (agent-skills) | lint, frontmatter, whole suite — green |
| `converge.py --final --base main` (the single-engine plan) | coverage, tagged, scope, spec — all **PASS** |
| `parse_plan` + `check_plan` on the P4c and routing plans | no problem reported — both can be launched |

## 3. What is still open, in order

1. **Merge agent-skills PR #53** (the seam), then **agent-runtime PR #66** and **agent-skills PR #54**.
   The skill is symlinked into `~/.agents/skills`, so this machine already runs the shim; what the merge
   changes is what everyone else gets.
2. **W2 — P4c, the selective remote installer** (`agent-runtime`,
   `docs/plans/2026-11-07-selective-remote-installer.plan.md`). The pre-flight is done in PR #66: the plan
   parses, `check_plan` passes, and the tickets are T1–T5 with the fixed columns. The run is the owner's:
   `launch` the plan, the driver dispatches, the owner merges each ticket's PR. Five sequential tickets
   touch `install.sh`, and the format budget sits one file below its limit — run `uv run ruff format` on
   whatever a ticket adds, before it reports.
3. **W3 — `orchestrator-routing-decoupling` (`agent-skills`)**: one owner ticket (T3, "approve fail-closed
   routing compatibility"). The pre-flight is done in PR #54; the driver can launch the plan. The owner
   answers T3 in the plan (`COMPLETE <date>`), and the driver archives the plan. Whether the archive still
   belongs in `agent-skills` is the owner's call: the plan's scenarios are already answered by the member's
   suite, under `agent-runtime`'s `docs/specs/orchestrator-engine.md`.
4. **W4 — `orchestration-baselines` T4 (`agent-skills`)**: the comparative benchmark, still one
   `regular/complex` ticket. The action plan proposes the split (corpus, harness, runs), and the split is
   still to be edited into the plan's ledger. One question is open before that edit: **where the corpus and
   the harness live now that the engine moved.** The action plan's proposed T5 still names
   `skills/orchestrate/scripts/`, which now holds only the shim — the benchmark harness the suite used is
   in the member (`packages/agent-orchestrator/tests/bench.py`). The corpus does not exist yet: only
   `benchmarks/evidence-manifest.json` (T3's schema) is there.

## 4. Decisions taken

| # | Decision | State |
|---|---|---|
| A1 | The order: D8 → P4c → routing → baselines | applied |
| A2 | Which engine drives the runs | the member, everywhere (its suite is its proof) |
| A3 | What the skill does when the member is missing | **fail closed**, with the install command and `AGENT_RUNTIME_HOME` as the way out |
| A4 | The release artifact contract | a GitHub Release with per-asset SHA-256 from CI; the bootstrap refuses a moving `main`; CI tests run against a local fixture — now decision D7 of the P4c plan |
| A5 | Where the baselines runs happen | corpus and harness in the driver, the runs by hand (W4 still to be split) |
| A6 | The 30-second rule | the worker's first act is a one-line progress report, written into the first ticket's "What it delivers" cell |
| A7 | The seam's resolution order: `$AGENT_RUNTIME_HOME` first, then `PATH` | applied (the checkout the user named is never shadowed by an older install) |
| A8 | The plans that describe the engine keep their scenarios and say where they live; their open tickets stay open | applied to `code-driven-orchestrator`, `orchestrate-lean-loop` and `orchestrator-routing-decoupling` |

## 5. Traps on this machine

- **The driver reads `Status:` more strictly than `converge` does.** `Status: **APPROVED**` — this
  repository's own plan style, as in `docs/plans/2026-11-08-orchestrator-engine-suite.plan.md` — reads as
  *missing*, so the driver cannot launch that plan. `converge.py` accepts it. One regex, one test and one
  requirement would close the class; it is not fixed.
- **The 30 s window.** `UNREPORTED_GRACE_SECONDS = 30` (R100, hard-coded). A worker that has not reported
  within 30 s is flagged `unreported`, and the driver parks the ticket on a person. The run parks rather than
  dies: `<run>/attention.md` names what waits, and the answer goes through the plan's Status cell.
- **Pi's trust prompt** blocks a worker in a directory Pi does not trust:
  `~/.pi/agent/trust.json` must carry the parent of `~/developer/agent-runtime.worktrees/`.
- **`~/.pi/agent/mcp.json`** must keep its `agent-runtime` entry; without it a pi worker cannot call
  `report` and the runtime never sees it finish. It was restored once already.
- **Converge reads a test's id from the docstring's first line**, and it only looks at the line after the
  `def`. A test whose signature is split over several lines (`def test_x(\n    self,\n):`) hides its
  docstring from the gate. Six ported tests were renamed and joined for that reason; keep the signatures on
  one line.
- **The budgets are measured debt**: ruff 40/45, ruff format 30/31, ty 277/285. A change that pushes a
  count over its budget fails CI on purpose. The format budget sits one file below its limit.
- **The suite is load-flaky** in two files (`tests/test_event_wait.py`, `tests/test_stop_failures.py`); run
  the failing test alone before believing it. One more flake was in the member's benchmark harness and is
  fixed (#65): the tree check gave the system two seconds to reap a tree it had just killed, and the
  member's CI runs the suite in parallel on two-core runners.
- **The driver does not strip backticks from a ticket's `Paths`**, so a backticked path reaches the worker
  as `` `install.sh` ``. The plans the driver runs write their paths plain.

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

The driver runs one plan at a time, in the repository that owns it:

```bash
cd ~/developer/agent-runtime
uv run --package agent-orchestrator agent-orchestrator launch \
  docs/plans/2026-11-07-selective-remote-installer.plan.md
```
