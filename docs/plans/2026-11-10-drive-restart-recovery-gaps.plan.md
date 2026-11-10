# Plan: Close the drive-restart-recovery gaps

Slug: `drive-restart-recovery-gaps`. Living spec: `docs/specs/orchestrator-engine.md`. Status: ARCHIVED 2026-11-10.

`orchestrator-silent-idle`'s T1 has already merged (#104, `67f7405`) and its requirement is in the
spec, so nothing here is externally blocked. Nothing in this plan takes a new requirement number; the
tickets amend R51, R57, R59 and R60 in place, and add rows to the coverage table.

## Why now

The `drive-restart-recovery` plan is archived and its requirements are in the spec as R50–R61 (#103),
but the convergence audit that followed (`gaps.md`) found the code and the contract disagreeing in six
places, all re-verified against current main (`943b7a3`):

- `decide … answer` accepts an id naming a stale **reopen** attempt (`t1p1`) — the refusal pattern's
  letter class `[rdo]` misses the `p` the code itself mints — and is case-sensitive, so `T1` passes;
- the shipped `reconcile(finishing=True)` strands a live-but-unbound terminal and moves an ended turn
  with a landed report to `reported` — correct, tested behaviour the plan never asked for, so R51 does
  not describe it;
- the not-dispatched retry requires a cell that reads **exactly** `TO-DO`, but the driver itself writes
  `TO-DO · not dispatched: …` into that cell, and a Lead who answers `TO-DO · retry` is silently ignored
  until a restart — unpinned by any test;
- `working_since` keeps its old start across a `requested`/`awaiting-merge` skip, so a unit that returns
  to `working` is escalated at once without a fresh `--working-minutes` window;
- D1's promise of a `driver-adopts-workers` plan is owned by nobody — no such file exists;
- the Lead-facing orchestrate skill mentions none of the new wakes, the `orphaned` event,
  `--working-minutes` or the exact-`TO-DO` rule.

All of these sit in the four files `orchestrator-silent-idle` is about to rewrite, so they must land
after it, not beside it.

## Sources

| Source | What it settles |
|---|---|
| `~/.local/state/orchestrate/drive-restart-recovery/gaps.md` | The six advisory gaps (partial/unrequested/missing), taken against origin/main 3cf9fed |
| `~/.local/state/orchestrate/drive-restart-recovery/deferred.md` | The reviewers' deferred findings: T4 c4 (foreign_attempt miss and false positives), T3 Q1 (the TO-DO prefix must stay; a distinct prefix is a follow-up), T5 C1–C3 (the working clock's start, its 2-tick delay, and the uncleared skip) |
| `~/.local/state/orchestrate/drive-restart-recovery/gather-result.json` (fresh-context gather, 2026-11-10) | Every gap re-verified on main `943b7a3`: the `foreign_attempt` pattern (`driver.py:4875-4880`), the reopen mint (`4529-4531`), `reconcile`'s finishing path (4374–4437), the not-dispatched rewrite and exact-match retry (4297–4311, 4237), `ready()`'s TO-DO prefix (638–643), `working_since`'s three sites (1192/1419/1421), the idle-tick skips (1379–1383), and the pinning tests file by file |
| `docs/plans/2026-11-09-drive-restart-recovery.plan.md` (ARCHIVED) | D1–D11, S2–S8a, the Out-of-scope promise of `driver-adopts-workers` (line 41, 74), the Lead's hand-archive note |
| `docs/specs/orchestrator-engine.md` @ `7456440` | R50–R61 as landed (S2–S8a); R57's current TO-DO wording; R59 and R60 as amended by this plan |
| `packages/agent-orchestrator/tests/` — `test_wake.py:291-306`, `test_run.py:332-371` (RecoveryTests), `test_run.py:586-646` (S5), `test_pr.py:625-731` (IdleTests), `test_reopen.py:42-58`, `test_agents.py:89-99` (S33) | The tests that pin each behaviour: the four refusal rows, the S2a strand and report-landed tests (S10/S11 are already satisfied), the S5 retry, the S8 idle windows |
| `docs/plans/2026-11-10-orchestrator-silent-idle.plan.md` / `.plan.yaml` | File ownership and R22 reservation; its Migration note "in-memory idle clocks reset on restart as today" |
| `~/.agents/skills/orchestrate/` — `SKILL.md:42-80`, `references/routing.md:75` | The Lead-facing contract: "TO-DO: retry", "any other edit leaves the ticket paused" — and zero mentions of stranded, not-dispatched, orphaned or working-minutes |
| `AGENTS.md` | The spec is the ledger: a behaviour change lands with its requirement |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `foreign_attempt` widens only: the letter class gains `p`, and matching is case-insensitive; test rows add `t1p1`, `T1`, `T1R1` (refused) and an uppercase correct id (accepted) | Reworking the matcher to require a distinct suffix, fixing the false positives (it refuses answers citing `.../t1/task.md`) | A missed stale id instructs the wrong attempt; a false positive only costs the Lead one reworded answer. The rework risks refusing real answers for a lesser evil | `driver.py:4875-4880`; deferred.md T4 c4 |
| D2 | The live-but-unbound strand and the ended-turn-with-landed-report path are adopted into the contract: R51 grows both clauses, the coverage table names the two existing RecoveryTests | Dropping the code (deleting tested, merged behaviour) | The behaviour is the double-dispatch protection D2 of the old plan feared, already shipped in T2's merge and pinned by tests the audit confirmed; the Lead contract cannot explain a `stranded` wake without it | `driver.py:4374-4437`; `test_run.py:332-371` |
| D3 | The retry keeps the exact-`TO-DO` match and the trap is closed with words: the `not-dispatched` wake's way out says "set the ticket's Status cell back to exactly `TO-DO`", R57 names the exact form, a test pins that `TO-DO · retry` does not retry within the run | `startswith("TO-DO")` (the driver's own `TO-DO · not dispatched: …` cell would clear itself and re-dispatch in a loop — pinned by `test_S5_without_the_lead_s_todo…`); the `PAUSED ·` cell vocabulary (a follow-up that must also change `ready()` and `test_agents.py` S33) | The loop is the worse failure; the exact form is one sentence the Lead already reads in the wake | `driver.py:4237, 638-643`; `test_run.py:602-646`; deferred.md T3 Q1 |
| D4 | The idle tick clears `working_since` for every unit it skips (gate `requested`, `awaiting-merge` or parked), so a unit returning to `working` gets a fresh `--working-minutes` window; R60 also states the window is in-memory and starts empty at each start (a restart resets it) | Calling the stale carry-over intended | The field's own comment promises "unbroken": the Lead just resolved a request for that unit, so the window should start again. The skipped-parked case is the same class (a parked unit is re-dispatched as a new attempt anyway) | `driver.py:1369-1422`; deferred.md T5 C3; silent-idle plan Migration note |
| D5 | D1's dangling promise gets an owner: a person-owned ticket in which Felippe opens the `driver-adopts-workers` plan (a real feature needing its own gather and grill — it changes the runtime team model, `hc_tools.lead_store`, `require_child` under agent-runtime R48) | Planning it inside this plan; dropping the promise (a restart still kills the workers, undocumented as intended) | The prerequisites are already named in the archived plan's Out of scope; scoping it here would double this plan's size | `docs/plans/` listing (no such file exists); archived plan lines 41, 74 |
| D6 | R57, R59 and R60 are amended by the code tickets that change the behaviour (the spec is the ledger: a requirement lands with its behaviour); the R51 adoption and its coverage rows are the spec-only ticket; the orchestrate skill edit is a person-owned step whose exact lines this plan lists (no ticket can own a path outside the repository — `check_plan` rejects it) | One doc ticket amending R51/R57/R60 after the code merges (the spec would disagree with the code between merges) | `AGENTS.md`: every behaviour change lands with its requirement; the payload-race T1 precedent owns the spec in a ticket | `AGENTS.md`; `check_plan` path rule |
| D7 | Order after `orchestrator-silent-idle`'s T1, which merged as #104 before this plan was written; no new requirement numbers are taken (all amendments are in place) | Merging in parallel (conflicts in `driver.py`, `test_wake.py`, `test_pr.py` and the spec); taking numbers before R22's owner lands | The silent-idle plan reserved R22 and owned the same files; stacked branches break under squash merge | `2026-11-10-orchestrator-silent-idle.plan.yaml`; git log `67f7405` |
| D8 | (mid-run, outside the tickets) The two S7 answer tests' docstring ids and the `test_wake.py` ported-suite row trace to R59, not R31: corrected in its own PR (#117) | Leaving it to T2's spec ticket, or leaving the wrong id in place | The test named a requirement about merged PR reports, so the ledger lied about the behaviour it pins; T2's Paths hold only the spec, and `check_plan` keeps a ticket inside its Paths | `test_wake.py:303,318`; #117 |
| D9 | (mid-run, outside this plan) The repo-wide `ruff format` gate is repaired on main before any code ticket can pass it: the one unformatted file (#113) is formatted, and the CI budget pattern gains its optional plural so a single file fails the build, pinned by a new R3 test (#122) | Letting T2 or T3 edit a file outside their Paths; counting only changed files; waiving the gate | The gate refused both T2 and T3 for a defect neither owned, and R3 promises CI fails when the tracked diagnostics grow — it did not for a growth of one, because `ruff` prints `1 file`, singular | `.github/workflows/ci.yml`; `test_engine_home.py`; #122 |

## Scenarios

- **S9** WHEN `decide … answer` is given text naming an attempt id of the unit other than the request's
  `task` — including a reopen attempt `t<N>p<n>`, and any uppercase form such as `T1` or `T1R1` — THEN
  it exits 2, writes no decision, and names the id it expects. An uppercase form of the expected id is
  accepted. (amends R59)
- **S10** WHEN the loop would finish while an open, unparked task is held by a live terminal this drive
  did not bind, THEN a `stranded` wake names the task and the runtime's answer, and the run exits
  non-zero instead of logging `finish`. (extends R51; satisfied today by
  `test_S2a_a_terminal_at_work_that_wait_does_not_list_is_stranded_not_finished`)
- **S11** WHEN the loop would finish while an open task's agent has ended its turn (`idle` or `done`)
  and its report has landed for that attempt, THEN the gate moves to `reported`, nothing is spawned,
  and the run does not finish over it. (extends R51; satisfied today by
  `test_S2a_a_finished_turn_whose_report_landed_goes_to_the_gates_not_to_finish`)
- **S12** WHEN a ticket is in the not-dispatched state and the Lead edits its cell to anything other
  than exactly `TO-DO` (for example `TO-DO · retry`), THEN the same run does not dispatch it again.
  (amends R57)
- **S12a** WHEN a dispatch fails, THEN the `not-dispatched` wake names the ticket, the reason, and the
  way out "set the ticket's Status cell back to exactly `TO-DO`", and R57 names the exact form.
- **S13** WHEN a unit the idle tick skipped (its gate was `requested`, `awaiting-merge` or parked)
  returns to `working`, THEN its working window starts again at that tick, and the idle tick does not
  count it until another `--working-minutes` have passed. (amends R60)
- **S13a** WHEN a drive starts, THEN the working window starts empty: `--working-minutes` is measured
  from the first tick that sees the agent `working`, and a restart resets it. (stated in R60)

## Out of scope

- The `driver-adopts-workers` feature itself (a restart still kills the driver's workers); it gets its
  own plan, opened by T5.
- The false-positive rework of `foreign_attempt` (D1): an answer citing the brief path `.../t1/task.md`
  stays refused for now.
- The `PAUSED ·` cell-vocabulary rework of the not-dispatched retry (D3).
- The merge-time and report-time COMPLETE rules (the `merged-unit-completes` plan).
- The `plan-invalid` wake on an ARCHIVED plan: the relaunch refusal the Lead saw on 2026-11-10 is
  correct behaviour, predates this plan, and needs no change.

## Risks

- **Security:** n/a. No new input is trusted; the widened pattern only refuses more answers.
- **Performance:** n/a. One `dict.pop` per skipped unit per idle tick; no new reads.
- **Migration:** none. R51, R57, R59 and R60 are amended in place by the same PRs that change the
  behaviour; the wake-text change is one string; no schema, ledger or plan-format change.
- **Rollback:** `driver.py`, the three test files and the spec; revert the pull requests in reverse
  ticket order and restart any drive that picked them up.

## Seams

- `decide` / `foreign_attempt` for S9 (`tests/test_wake.py:291-306` — the existing refusal rows and the
  accepted-text companion test).
- `Driver.reconcile(finishing=True)` and `strand` for S10/S11 (`tests/test_run.py:332-371`, RecoveryTests
  — already green; the ticket only makes the contract name them).
- `Driver.not_dispatched`, `dispatch_ready`'s retry condition and the wake text for S12/S12a
  (`tests/test_run.py:586-646`, the S5 tests; the trap test extends
  `test_S5_without_the_lead_s_todo_the_same_run_does_not_try_again`).
- `Driver.idle_ticks` / `working_minutes` for S13/S13a (`tests/test_pr.py:625-731`, IdleTests; `wait`
  steps already carry `runtime_state`).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | `foreign_attempt` refuses reopen and uppercase attempt ids: the letter class gains `p`, matching is case-insensitive, refusal rows `t1p1`, `T1`, `T1R1` and an accepted uppercase row land in the S7 tests, and R59 names the reopen and uppercase forms | S9 | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_wake.py, docs/specs/orchestrator-engine.md | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/114 (564ffe6) |
| T2 | The live-but-unbound strand and the ended-turn-with-landed-report path enter the contract: R51 grows both clauses and the coverage table names the two existing RecoveryTests that already pin them | S10, S11 | T1 | docs/specs/orchestrator-engine.md | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/118 (69fe1d8) |
| T3 | The not-dispatched trap is closed in words and pinned in code: the wake's way out says "set the ticket's Status cell back to exactly `TO-DO`", a test pins that `TO-DO · retry` does not retry within the run, and R57 names the exact form | S12, S12a | T1 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_run.py, docs/specs/orchestrator-engine.md | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/123 (5af1617) |
| T4 | The working window restarts after a skip: `idle_ticks` clears `working_since` for every unit it skips (requested, awaiting-merge, parked), a test covers the requested→working transition, and R60 states the clear and the restart reset | S13, S13a | T3 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_pr.py, docs/specs/orchestrator-engine.md | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/131 (44bbcce) |
| T5 | The `driver-adopts-workers` plan is opened: Felippe runs `/start-planning` for the feature the archived plan deferred (prerequisites already named: `hc_tools.lead_store`, `require_child`, agent-runtime R48), giving D1's promise an owner | — | none | docs/plans/ | | | Felippe | needs you: The `driver-adopts-workers` plan is opened: Felippe runs `/start-planning` for the feature the archived plan deferred (prerequisites already named: `hc_tools.l… |
| T6 | The Lead-facing skill names the new contract: `~/.agents/skills/orchestrate/SKILL.md` ("When the run pauses") and `references/routing.md` gain the `stranded` and `not-dispatched` wake meanings, the exactly-`TO-DO` retry rule, the `orphaned` event and `--working-minutes`; applied by hand, outside the repository | — | T3 | | | | Felippe | COMPLETE 2026-11-10 |

T2 and T3 branch after T1, and T4 after T3, because every code ticket touches `driver.py` and the
spec. T5 and T6 have no code and can run any time.
