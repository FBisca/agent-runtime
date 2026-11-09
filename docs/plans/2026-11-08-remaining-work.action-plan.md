# Action plan — the work left after PR #64

**Status: AWAITING APPROVAL.** Nothing here has been started; every step waits for the owner's word.
**Date:** 2026-11-08 · **Repos:** `agent-runtime` (`~/developer/agent-runtime`) and `agent-skills`
(`~/developer/agent-skills`)

This is the coordination ledger for the three things left on the table: the `agent-skills`
cross-repository pull request (D8), the selective remote installer (P4c), and the two `agent-skills`
plans. It is not a driver plan: the driver works in one repository, so each step names the plan it
runs and the repository it runs in.

---

## 0. Where we stand (measured today, not remembered)

| Fact | Evidence |
|---|---|
| PR #64 is merged and fast-forwarded | `main` = `266aef0`, tree clean, no open PR in either repo |
| The engine exists twice, and the copies are not equal | the member (`packages/agent-orchestrator/`, typed, Pydantic) vs the skill (`~/.agents/skills/orchestrate/scripts/`, hand-rolled): `contracts.py` differs by 435 lines, `routes.py` by 187, `evidence.py` by 101, `events.py` by 44; the member adds `converge.py` and `runtime_client.py` |
| The member is already usable as the engine | `uv run --package agent-orchestrator agent-orchestrator inspect` answers, and resolves the runtime (`hc`) |
| The two drivers agree on the plan format | the member's `parse_plan`/`check_plan` return the same verdict as the skill's, on all three plans |
| P4c is **not runnable** by the driver as written | `plan Status is missing, not APPROVED; plan has no Tickets table with the fixed columns (# \| What it delivers \| Satisfies \| Blocked by \| Paths \| Size \| Difficulty \| Owner \| Status)` |
| `orchestration-baselines` is runnable; `orchestrator-routing-decoupling` is not | the routing plan: `plan Status is missing, not APPROVED` (its only open ticket, T3, is owner `user`) |
| The benchmark corpus does not exist yet | `benchmarks/` holds only `evidence-manifest.json` (T3's schema); T4's "small fixed corpus of real, permission-safe tasks" is still to be written |
| The skill's suite does **not** pass against the member | probe: the tests patch the old `runtime_process`/`gh_process` seams; the member routes through `CliRuntimeClient`, which calls the real `agent-runtime` CLI. The first failure is `test_S33_a_refused_spawn_leaves_no_open_intent` (`name_taken`), and the suite does not finish. **D8's first PR is a port, not a copy.** |
| The budgets have almost no headroom | measured today: ruff check 40/45, ruff format 30/31, ty 277/285. The format budget is **one file** below its limit |
| The 30-second trap is live | `UNREPORTED_GRACE_SECONDS = 30` (R100, hard-coded); the driver treats `unreported` as a turn that ended (`TURN_ENDED`). The `orchestration-baselines` run recorded `escalation … ended without a report: runtime state idle` and then idled ~10 hours |

---

## 1. The order, and why

**W1 (D8, one engine) → W2 (P4c, the installer) → W3 (routing, the owner's call) → W4 (baselines, the comparison).**

1. **D8 first.** The duplicate engine is the live risk, and it is the risk to every later run: `/orchestrate`
   runs the *skill's* copy, which is the older, untyped one, while every fix lands in the member. D8 also
   gives the member the engine's own test suite, which is what proves the canonical driver before it is
   asked to carry P4c's five tickets.
2. **P4c second.** It is the biggest piece (five tickets, a new contract, shell work) and it needs a
   canonical driver to carry it. It also needs a decision from the owner about the release artifact
   contract (A4), which is cheaper to settle while nothing is in flight.
3. **Routing third.** Only one ticket is left and it is the owner's to make; it needs no worker at all.
4. **Baselines last.** The comparison must run against the canonical engine, and its corpus has to be
   written first.

---

## 2. W1 — One engine (D8): two PRs, two repositories, hand-driven

The driver does not work across repositories (`orchestrate/references/cross-repository.md`: one ledger,
one checkout assignment per worker, verify the dependency in the consumer's checkout before unblocking).
So this is a hand-driven sequence with an agreed interface, not a driver run.

### PR-1 — `agent-runtime`: the engine's test suite moves to the engine

New plan `docs/plans/2026-11-08-orchestrator-engine-suite.plan.md` (slug `orchestrator-engine-suite`),
living spec `docs/specs/orchestrator-engine-suite.md`.

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The engine's test suite lands in the member, re-pointed at `agent_orchestrator.driver`, and the seams the suite patches are the member's seams. | S1, S2 | none | `packages/agent-orchestrator/tests/`, `packages/agent-orchestrator/src/agent_orchestrator/runtime_client.py` | regular | complex | agent | TO-DO |
| T2 | The suite is green against the member, the contract tests still pass, and the budgets do not move (ruff format has one file of headroom). | S2, S3 | T1 | `packages/agent-orchestrator/tests/`, `.github/workflows/ci.yml` | regular | complex | agent | TO-DO |

- **S1** WHEN the engine's own tests run, THEN they exercise the member's driver, not a copy of it.
- **S2** WHEN the member's driver is exercised by the ported suite, THEN the suite's behaviour is the
  member's behaviour — a test that passes only against the old copy fails here.
- **S3** WHEN the ported suite lands, THEN the tracked lint and type budgets do not grow.

### PR-2 — `agent-skills`: the skill stops carrying the engine

New plan `docs/plans/2026-11-08-orchestrator-single-engine.plan.md` (slug `orchestrator-single-engine`),
living spec `docs/specs/orchestrator-single-engine.md`.

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The skill's engine copy is gone; `skills/orchestrate/scripts/orchestrate.py` delegates to the member's CLI, with the resolution order `agent-orchestrator` on PATH → `$AGENT_RUNTIME_HOME` → the checkout's `uv run --package agent-orchestrator`. | S1, S2 | none | `skills/orchestrate/scripts/`, `skills/orchestrate/SKILL.md` | regular | complex | agent | TO-DO |
| T2 | The engine's internal tests leave the skill's suite (they moved with the engine), and the skill's own suite — frontmatter, portability, repo — stays green. | S3 | none | `skills/orchestrate/tests/`, `tests/` | regular | simple | agent | TO-DO |
| T3 | The delegation is tested with a fake member (hermetic, no network, no second checkout), and the skill's documents name the seam and the way out when the member is missing. | S4, S5 | T1 | `skills/orchestrate/tests/`, `skills/orchestrate/references/`, `README.md` | regular | simple | agent | TO-DO |

- **S1** WHEN the orchestrate skill runs, THEN it drives the member, and the skill's tree carries no copy
  of the engine.
- **S2** WHEN the member is not installed, THEN the skill refuses with an actionable message naming the
  install command and the `AGENT_RUNTIME_HOME` escape hatch — it never falls back to a second engine.
- **S3** WHEN the skill's suite runs, THEN it holds the skill's own behaviour, and the engine's internals
  are tested where the engine lives.
- **S4** WHEN the delegation is exercised, THEN the member's CLI answers with the same commands, exit
  codes and ledger as the copy it replaces.
- **S5** WHEN the skill's documents are read, THEN the seam, the resolution order and the way out are
  named, and `AGENT_RUNTIME_HOME` keeps its documented meaning.

**The seam already exists.** `agent-skills`' README documents `AGENT_RUNTIME_HOME` for this skill
("Checkout of agent-runtime; falls back to `agent-runtime` on PATH"), and the member's CLI answers
`inspect`/`run`/`launch`/`metrics`/`evidence`/`await`/`decide` — the same surface the skill's copy has.

**Decisions for the owner: A2 (which engine drives the runs until D8 lands) and A3 (what the skill does
when the member is missing).**

---

## 3. W2 — P4c, the selective remote installer (`agent-runtime`, driver-run)

The plan `docs/plans/2026-11-07-selective-remote-installer.plan.md` is approved by the owner and is the
right scope; it cannot be launched because the driver's plan format is stricter than the plan's. The
pre-flight is a plan edit, not a behaviour change:

1. `Status:` → `APPROVED` (parseable). Today the line reads `**APPROVED — READY FOR EXECUTION**`, which
   the driver reads as *missing*.
2. `Living spec:` → `` `docs/specs/installer-contract.md` `` (backticked, so the archive step writes the
   spec the plan names).
3. The Tickets table → the driver's fixed columns. The current table (`ID | Vertical deliverable |
   Scenarios | Depends | Paths | Size | Difficulty | Status`) is not parseable, and `T3` is `large`, which
   the driver refuses (`big`/`large` tickets need chunked dispatch, which `run` does not do) — so T3 is
   split in two.
4. `Blocked by` → only `T<n>` ids of this plan. The plan's own dependencies (`P4b package interface`,
   `release artifact contract`, `P2 for Pi runtime support`) move to prose: P4b landed in PR #56 and Pi
   worker support landed in PR #55/#58, so both are satisfied; the release artifact contract becomes
   decision **A4**.
5. `Owner` → `agent` for every ticket; the first real release stays out of scope (the plan already says so).

The resulting table (this is the whole scope of the run):

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The legacy `install.sh` behaviour is snapshotted in regression tests, and the component closure (`orchestrator → runtime`, adapters → runtime) and the install manifest are defined. | S1, S3, S4, S7, S8 | none | `install.sh`, `tests/installer/` | regular | complex | agent | TO-DO |
| T2 | The component menu on `/dev/tty`, the headless `--components`/`--yes` path, and `--dry-run`, with the no-TTY, clean-HOME and existing-HOME matrix tests. | S1, S2, S5, S8 | T1 | `install.sh`, `tests/installer/` | regular | complex | agent | TO-DO |
| T3 | The release artifact contract and the pinned, checksum-verified download: the payload is fetched from a pinned ref, verified against a checksum before anything runs, and a moving ref is refused. | S2, S3, S6, S8 | T2 | `install.sh`, `scripts/`, `.github/workflows/`, `tests/installer/` | regular | complex | agent | TO-DO |
| T4 | The install is transactional: the payload is verified before it is activated, an interrupted or failed install leaves no partial state, and the prior version stays usable. | S6, S8 | T3 | `install.sh`, `tests/installer/` | regular | complex | agent | TO-DO |
| T5 | The Claude, Codex and Pi adapters are wired without reading or writing a secret, and install, upgrade and selective uninstall keep the documented `agent-runtime*` shims and the other components intact. | S4, S7, S9 | T4 | `install.sh`, `hooks/`, `tests/installer/`, `README.md` | regular | complex | agent | TO-DO |

**Gate:** the owner merges each ticket's PR. The driver never merges; it marks the ticket COMPLETE and
unblocks the next one. The run ends with the archive into `docs/specs/installer-contract.md` (the plan's
S1–S9 become requirements, the tests' `S<n>` names become `installer-contract/R<m>`).

**Risk to name now:** five sequential tickets that all touch `install.sh`, and the budgets' one file of
format headroom. `uv run ruff format` on whatever a ticket adds, before it reports.

---

## 4. W3 — `orchestrator-routing-decoupling` (`agent-skills`): the owner's ticket, then the archive

- One line of plan edit: `Status:` → `APPROVED` (today the driver reads it as missing).
- T3 is owner `user`: *"Approve fail-closed routing compatibility without changing the existing
  size/class matrix; defer outcome-driven model-route tuning to P5."* The owner answers it in the plan
  (`COMPLETE <date>`), and the driver archives the plan into `docs/specs/orchestration-routing.md`.
- No worker is needed for T3 itself.

## 5. W4 — `orchestration-baselines` T4 (`agent-skills`): the comparison, split

T4 today is one `regular/complex` ticket that compares at least three real tasks per candidate route,
single-agent against orchestrated, with pinned revisions and a report. As one ticket it is too big for
one worker (the driver refuses `big` tickets, and the task needs a corpus, a harness and a measurement
run). The corpus does not exist yet — only T3's schema is there.

Proposed split (the plan's own ledger, edited by the owner):

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T4 | The task corpus: a small set of real, permission-safe tasks in a manifest, each with a pinned commit and an acceptance check. | S1, S3 | T3 | `benchmarks/`, `docs/` | regular | simple | agent | TO-DO |
| T5 | The measurement harness: one task runs on one route and records schema-versioned evidence (verified completion, elapsed time, interventions, retries; unknown costs `null`, never `0`). | S2, S3 | T4 | `benchmarks/`, `skills/orchestrate/scripts/` | regular | complex | agent | TO-DO |
| T6 | The runs and the report: the same tasks on both routes, with a read-only recommendation that says which graph or context change has evidence behind it. | S1, S4 | T5 | `benchmarks/`, `docs/` | regular | complex | agent | TO-DO |

The runs use the loop's own model (`pi` with `minimax`), so no provider spend beyond what the owner has
already approved. **Decision A5 asks whether the runs happen inside the driver or by hand.**

---

## 6. The decisions this plan needs from the owner

| # | Decision | Recommendation | Why it matters |
|---|---|---|---|
| A1 | The order: D8 → P4c → routing → baselines | **as listed** | The duplicate engine is the live risk, and it is the risk to every later run |
| A2 | Which engine drives the runs until D8 lands: the skill's proven copy, or the member's typed copy | **the skill's copy until PR-1 lands, then the member** | The skill's copy has a proven run behind it (T1–T6, fix rounds, real PRs); the member is canonical but its suite is only ported after PR-1 |
| A3 | What the skill does when the member is missing | **fail closed, with the install command and the `AGENT_RUNTIME_HOME` way out** | A fallback copy is the drift the whole workstream is removing |
| A4 | The release artifact contract P4c's T3 verifies against | **a GitHub Release with per-asset SHA-256 checksums published by CI; the bootstrap refuses a moving `main`; CI tests run against a local fixture, so no real release is needed to pass** | The plan's own caveat: an integrity check inside the downloaded script cannot authenticate the script |
| A5 | Whether the baselines runs happen inside the driver or by hand | **by hand for the runs, in the driver for the corpus and the harness** | The runs are long and judgement-heavy; a parked driver ticket costs a human the same attention with less visibility |
| A6 | The 30-second rule: every worker's first act is a one-line progress report | **yes, written into the first ticket's "What it delivers" cell of each run** | The cell is copied verbatim into the worker's `task.md`; without it a worker that reads the plan for 40 seconds is flagged `unreported` and the ticket parks on the owner |

---

## 7. Risks, and what this plan does about each

| Risk | Mitigation |
|---|---|
| A worker is flagged `unreported` after 30 s and the ticket parks | A6; the run parks rather than dies, and `~/.local/state/orchestrate/<slug>/attention.md` names what waits. The owner answers through the plan's Status |
| Pi's trust prompt blocks a worker in a directory Pi does not trust | `~/.pi/agent/trust.json` must carry the parent of `~/developer/agent-runtime.worktrees/` before any run |
| `~/.pi/agent/mcp.json` loses its `agent-runtime` entry (it did once) | Verify before every run; a worker that cannot call `report` never finishes |
| The ported suite pushes the lint or type budgets | The format budget has one file of headroom: `uv run ruff format` on everything a ticket adds, before it reports |
| The suite is load-flaky | `tests/test_event_wait.py` and `tests/test_stop_failures.py` flake under parallel load; run the failing test alone before believing it |
| The driver never merges | The owner merges each PR; the driver marks the ticket COMPLETE and unblocks the next. Every step in this plan names its merge gate |
| A ticket is too big for one worker | The driver refuses `big`/`large`; P4c's T3 is split, baselines' T4 is split into three |

---

## 8. What not to redo (from the handoffs)

- The rollback check is **not** a manual procedure any more: `tests/test_rollback_contract.py` materialises
  the pre-workspace tree and drives it. If that commit moves, the baseline pin and the test fail together.
- The requirement ids are **stable**: do not re-tag the scenarios. The plan's `S<n>` ids live only in the
  plan file and in the spec's `(from <slug>/S<n>)` provenance.
- The lint and type budgets are **measured debt**, not round numbers: ruff 40/45, ruff format 30/31,
  ty 277/285. A change that pushes a count over its budget fails CI on purpose.
- The rollback tree's shims and MCP server answer; a store written by either tree opens in the other.

---

## 9. The commands

```bash
cd ~/developer/agent-runtime
uv sync --frozen --all-packages
uv run pytest -n 4
uv run pytest -m integration -n 4
uv run ruff check . && uv run ruff format --check . && uv run ty check
shellcheck install.sh bin/*

cd ~/developer/agent-skills
uv sync
uv run pytest                      # the fast suite
uv run pytest -m "" -n 4          # the whole suite
scripts/checks.sh                 # lint, frontmatter, full suite (what CI runs)
```

The driver runs one plan at a time, in the repository that owns it:

```bash
python3 ~/.agents/skills/orchestrate/scripts/orchestrate.py launch <plan-path>
# or, once D8's PR-1 has landed:
uv run --package agent-orchestrator agent-orchestrator launch <plan-path>
```
