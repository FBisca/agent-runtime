# Plan: Orchestrator audit fixes

Slug: `orchestrator-audit-fixes`. Living spec: `docs/specs/orchestrator-engine.md`. Status: AWAITING APPROVAL.

Contract: [`2026-11-11-orchestrator-audit-fixes.plan.yaml`](2026-11-11-orchestrator-audit-fixes.plan.yaml) (scenarios,
tickets, evidence). Oracle: [`2026-11-11-orchestrator-audit-fixes.oracle/`](2026-11-11-orchestrator-audit-fixes.oracle/).
This file is the narrative and is not covered by the approval digest.

**Approval waits on the runtime seams.** PRs #150 and #154, which both edit `driver.py`, merged on 2026-11-11
(`b5ff683`). Approval still waits on two tickets of [`runtime-audit-fixes`](2026-11-11-runtime-audit-fixes.plan.md):
T5 (operation keys) and T6 (verified caller).

The driver can't express a blocker in another plan, so `plan approve` runs on main after both merge. Re-run the oracle
red before approving.

## Why now

The 2026-11-11 static audit found orchestrator defects that strand a run or repeat its side effects:
- **A Lead command contending for the ledger kills the drive.** The non-blocking ledger lock raises, the loop has no
  recovery, and `await` then polls forever.
- **A silent mutation reviewer hangs the plan in VERIFYING.** The four #116 follow-ups are still open.
- **A restart can resume a worker twice or count a gate round twice.** `start_answers` and `chase_responses` resume
  before booking their marker, and `fail_gate` splits its booking around git and gh calls.
- **The evidence runner can be pointed outside its scratch tree.** A declared HOME wins, and oracle writes follow
  symlinks a patch adds.
- **`plan approve` is refused only by the hook.**

`driver.py` took 19 of its 34 commits in three days as fixes. These fixes come before any split of that file (audit
D-C).

## Sources

| Source | What it settles |
|---|---|
| Static audit, 2026-11-11: FINDINGS.md, ISSUES.md 1, 2, 3, 5 and 10, and E_orchestrator.md (session scratchpad) | The defects with file:line, failure scenarios and a proposed driver split |
| Independent Codex review (CODEX_REVIEW.md) | F1, F7, F8, F22 confirmed. F11 (stuck gate) refuted. M1 (oracle symlinks) and M2 (declared HOME) added. D17: split only incrementally, after these fixes |
| Fresh-context gather at f4e500b (GATHER.md) | Every cited site still present. orchestrator-engine is at R69. Conflicts with PRs #150 and #154 and with driver-adopts-workers T3. #116 C1 contradicts the silent-idle plan's S13 |
| GitHub #116 | C1, C2, C3 and C5, with their done-when ("each item a WHEN/THEN scenario, red first, recorded in the engine spec") |
| `docs/plans/2026-11-10-orchestrator-silent-idle.plan.yaml` S13, R62 | A late reviewer `complete` after the idle request is not acted on, which this plan supersedes |
| `docs/specs/python-workspace-contracts.md` R200, R204 | The orchestrator reaches the runtime only through its public CLI |
| runtime-audit-fixes T5 and T6 | The `operation_id` seam with `call operation`, and `resolve --json` caller identity |
| Grill, rounds 1-3, delegated by the owner to the Codex reviewer (`audit-critic`) | The decisions marked "grill" below |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | One ledger module shared by driver and runner. A distinct `LedgerBusy` is retried with jittered backoff (0.2 s to 5 s) under one monotonic 60 s deadline that includes the waits | Fail fast (today); unbounded wait; nested timeouts | The runner already waits; nested timeouts could exceed 60 s | grill Q2, Q21 |
| D2 | Lead reads take an unlocked snapshot, and any write revalidates under the lock | Every Lead command takes the write lock | Reads should not contend with the driver, and a stale snapshot must not authorize a write | grill Q2 |
| D3 | A crash guard covers startup, loop and finalization, and writes `crashed` plus a wake without taking the ledger lock | Guard only the loop | A crash must not depend on the busy resource | grill Q2, Q21 |
| D4 | `await` tells a crashed driver from one gone without a log, and keeps waiting on a run whose driver hasn't started yet | "No run lock means crashed" | SIGKILL leaves no crash log; `test_wake.py` S2 already pins waiting for a driver that starts later | grill Q2; oracle calibration |
| D5 | An escalated reviewer's late `complete` is honored while its request is undecided and ignored after the Lead decides. This supersedes silent-idle S13 | Keep S13 | The review that S13 drops is usually finished work (#116 C1) | grill Q3 |
| D6 | One idle watchdog over task, mutation and gaps agents. Output for the current attempt plus an ended turn counts as done, and normal validation still decides | Separate per-kind watchdogs | The same silent-idle defect recurred per kind (#94, #116 C2) | grill Q4 |
| D7 | One attempt-id pattern, including the `a` suffix | Patch `foreign_attempt` alone | Fix the class: several sites enumerate suffixes | gather G11 |
| D8 | Every spawn and resume books an intent holding a fresh UUID `operation_id`, with the readable path as metadata, before calling the runtime with it. No operation support means no unkeyed send and a Lead request instead | Reuse `ensure_agent` as is; key by (unit, attempt, operation) | `ensure_agent` resumes live agents on recovery, and composite keys collide across plans and repeated resumes | grill Q5, Q15, Q19 |
| D9 | On restart: absent → act; bound, working, blocked, decision, complete or failed → adopt; pending or unknown → Lead request, never a silent re-send | Re-send when unsure | An intent cannot make delivery exactly-once across a non-idempotent boundary | grill Q15 |
| D10 | `fail_gate` books the round, the next phase and the pending external actions in one write before git or gh | Two bookings (today) | A crash in between counts the round twice and escalates early | grill Q5 |
| D11 | `plan check` and `plan approve` refuse evidence env naming HOME, PATH or XDG_*. The runner applies the scratch values last. `place_oracles` refuses targets outside scratch or behind a symlink | Only one of the layers | Approval-time checks see the author's tree, not the patched one; defence in depth | grill Q6 |
| D12 | `plan approve` asks `agent-runtime resolve --json` for the verified caller. It refuses a worker, or unknown inside a managed host or with agent markers; plain terminal and CI are allowed | Import the runtime's ancestry gate; fail open when the CLI is absent | R200/R204 forbid the import, and CLI absence must not override detected agent identity | grill Q9, Q16 |
| D13 | The ledger keeps `version: 1` and gains `"kind": "agent-orchestrator/ledger"`, written on create and on the next locked write of a legacy ledger, before any worker-visible output | A second `schema` field | One authoritative version field; the marker lets the runtime hook find custom roots | grill Q17, Q20 |
| D14 | New requirement ids continue from R70; tests cite `orchestrator-engine/R<n>` | — | — | grill Q13 |
| D15 | This plan follows #150, #154 and runtime T5/T6; driver-adopts-workers rebases on it | Run before the open PRs | Both PRs edit `driver.py` now | grill Q14 |

## Scenarios

The WHEN/THEN text lives in the contract. In summary:
- **Ledger:**
  - **S1:** a busy ledger is waited out under the deadline.
  - **S2:** past the deadline, the driver logs a crash.
  - **S3:** any unhandled exception logs a crash and writes a wake.
  - **S4:** `await` reports a crashed or vanished driver instead of polling, and still waits for one that hasn't started.
  - **S5:** Lead reads use a snapshot, and writes revalidate.
  - **S6:** driver and runner share one lock.
  - **S7:** the ledger kind marker.
- **Supervision (#116):**
  - **S8:** a silent reviewer with output counts as done.
  - **S9:** a silent reviewer with no output gets an idle request.
  - **S10:** a late `complete` before the Lead decides (C1).
  - **S11:** the right file owed (C3).
  - **S12:** answers survive unrelated decisions (C5).
  - **S13:** the attempt-id pattern.
- **Intents:**
  - **S14:** a keyed intent is booked before the call.
  - **S15:** restart reconciliation.
  - **S16:** `fail_gate`'s single booking.
- **Runner:**
  - **S17:** reserved env names are refused.
  - **S18:** the scratch HOME wins.
  - **S19:** oracle placement is confined to scratch.
- **Approve gate:**
  - **S20:** workers and unverified agents are refused.
  - **S21:** plain terminal and CI still approve.
- **Outcome:** **S22:** the end-to-end oracle.

## Out of scope

- Splitting `driver.py` (audit D-C). A later plan does it in incremental, move-only steps once these land. T1's
  ledger module is the only extraction here, because the fix needs it.
- The silent stuck gate (audit F11). Codex refuted it, since the idle watchdog still reaches the unit. Immediate
  notification when a gate parks is not planned.
- Repairing the orchestrator-engine Coverage table (audit fix 9). That belongs to the ledger-repair plan.
- Git timeouts sized to the tree beyond R13, `ensure_draft` ignoring a failed undo, non-atomic brief writes and the
  `merged_early` bookkeeping. These are lower-severity findings for a follow-up.
- Moving the orchestrator's hook rules behind installer gating (audit D-B step 2).

## Risks

- **Security:**
  - Runner containment (S17-S19) and the approve gate (S20) close escapes. The gate fails closed for an unknown caller inside a managed host.
  - The ledger marker is protected by the runtime hook (runtime-audit-fixes S6).
- **Performance:**
  - A bounded lock wait of up to 60 s replaces an immediate exit.
  - Each spawn or resume makes one extra runtime lookup, and only on restart.
- **Migration:**
  - Ledgers gain the `kind` marker on their next locked write; version 1 is unchanged.
  - Intents are new ledger records. A ledger without them reconciles as it does today.
- **Rollback:**
  - Each ticket is one revertable PR.
  - Reverting T3 restores today's intent-less recovery.
  - Reverting T1 removes the marker writer. The runtime hook then falls back to protecting the default root only (runtime-audit-fixes D5).

## Seams

- **The real driver with fakes:** `fake_runtime.py` and `fake_gh.py` from packages/agent-orchestrator/tests/fixtures.py, as test_reviewer_idle_outcome and test_workbench_outcome already do.
- **The orchestrator CLI:** `drive`, `await`, `accept`, `plan check`, `plan approve`.
- **The evidence runner:** a scratch worktree (test_runner.py).
- **The fake runtime:** gains `call operation` and `resolve --json` caller answers, mirroring runtime-audit-fixes T5 and T6.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | Shared ledger module (bounded lock, snapshots, kind marker), crash guard with a lock-free wake, and an `await` that reports a crashed or vanished driver | S1–S7 | none | ledger.py, driver.py, runner.py, tests/test_ledger.py, tests/test_await.py, spec | regular | complex | agent | TO-DO |
| T2 | One watchdog over every agent kind; #116 C1, C2, C3 and C5; the shared attempt-id pattern | S8–S13 | T1 | driver.py, tests/test_silent_idle.py, tests/test_answers.py, spec | regular | complex | agent | TO-DO |
| T3 | Operation-keyed intents for every spawn and resume, restart reconciliation, `fail_gate`'s single booking, and the outcome oracle | S14, S15, S16, S22 | T2, T4, T5 | driver.py, runtime_client.py, tests/test_intents.py, tests/test_orchestrator_audit_outcome.py, tests/fixtures.py, spec | regular | complex | agent | TO-DO |
| T4 | Runner containment: reserved env names, scratch values applied last, confined oracle placement | S17, S18, S19 | T1 | runner.py, plan.py, tests/test_runner.py, tests/test_plan.py, spec | small | complex | agent | TO-DO |
| T5 | `plan approve` gated on the runtime's verified caller | S20, S21 | T4 | plan.py, runtime_client.py, tests/test_plan.py, spec | small | complex | agent | TO-DO |
