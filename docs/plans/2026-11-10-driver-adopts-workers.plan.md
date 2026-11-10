# Plan: A worker outlives the driver, and the next drive adopts it

Slug: `driver-adopts-workers`. Living spec: `docs/specs/agent-runtime.md`. Status: APPROVED 2026-11-10.

The driver-side amendments this plan makes land in `docs/specs/orchestrator-engine.md` (R51, R54, R56 in
place), because they change that engine's behaviour; the runtime's contracts, which the adoption rests
on, live in the Living spec above. New runtime requirements start at **R173** (172 exist).

## Why now

`drive-restart-recovery` recovered the *unit* of a worker a restart killed, and deferred the worker
itself: D1 of that plan records why `--outlive-parent` alone is worse than today. A relaunched driver
has a new `HC_TERMINAL_ID` and generation, so `lead_store` gives it a new team, `wait` sees only its
own team's members, and `require_child` refuses a terminal whose parent is the dead driver — the four
verbs the driver would need (wait, resume, message, stop) all refuse, and hc's live-name uniqueness
blocks re-dispatching the unit. So a restart costs the worker's transcript, every time.

The gap that plan left is now named as a ticket with no owner behind it, and nothing in the runtime
states a worker's lifetime at all (0 of 172 requirements), so today's "dies with the driver" is hc's
default and not a decision anyone wrote down.

## Sources

| Source | What it settles |
|---|---|
| `docs/plans/2026-11-09-drive-restart-recovery.plan.md` D1, Out of scope | Outliving ships only together with adoption; `--outlive-parent` alone is rejected; the prerequisites are the team key and `require_child`; the unit's worktree keeps the dead worker's files, so recovery costs only its transcript |
| `docs/plans/2026-11-10-drive-restart-recovery-gaps.plan.md` D5, T5 | This plan's owner and its scope: a real feature needing its own gather and grill, not a gap in that plan |
| `docs/specs/agent-runtime.md` (172 requirements) | R48 (an unrelated team, a forged parent, a mutation by a foreign caller fails before publishing or changing state); R165 (the team cannot fork within one generation); R118, R124, R156 (superseded and dead generations already have vocabulary: "no needs", or a refusal naming the live generation); R96 (spawn's argv) |
| `docs/specs/orchestrator-engine.md` | R17 (one live driver per plan, refused before any spawn), R50–R61 (restart reconciliation: orphaned, `stranded`, re-dispatch, `not-dispatched`), R62 |
| `packages/agent-runtime/src/agent_runtime/hc_tools.py` | `lead_store` keys a team on `(scope, HC_TERMINAL_ID, generation)` (6 call sites); `require_child` compares `hc get <name>.parent` to the caller (4 call sites: send, resume, legacy stop, diagnostics); the structured `stop` already reports "not your child; terminal untouched" instead of raising |
| `packages/agent-runtime/src/agent_runtime/dispatch_store.py` | Teams are `UNIQUE(scope, lead, generation)`; `authorize` accepts only the newest team row for `(scope, lead)`; `_bind` refuses a stale assignment or generation; 11 tables, 7 indexes, `user_version=2`; `workers` and `assignments` and `messages` each carry `team` |
| `packages/agent-runtime/src/agent_runtime/dispatch_wait.py` | `wait` snapshots members of the caller's own team; a worker of another team is invisible |
| `packages/agent-runtime/src/agent_runtime/agent_runtime.py` | `status` never raises on a runtime failure: `exited`, or `unknown` with hc's `not_found`, is the only proof an agent is gone |
| `packages/agent-orchestrator/src/agent_orchestrator/driver.py` | The runtime is reached only through `runtime_call` (8 sites, 5 tools: spawn, resume, wait, status, stop); the ledger's `runtime_id` is the hc address and `spawn`'s `team_id` is discarded; `bound_here` is `name in self.touched`; a live terminal that `wait` does not list is stranded (R51) |
| `packages/agent-orchestrator/tests/test_run.py` | The strand path is already pinned by 5 tests, including the one this plan changes |
| `tests/` | `test_tools.py` pins the `hc run --bg --name …` argv shape, so a lifetime flag is a pinned contract, not a free change |
| the request | An orchestrate worker outlives the driver; a relaunched driver adopts it; the two prerequisites are the team key and `require_child` |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | A worker always outlives its driver: `spawn` passes hc's `--outlive-parent` | An opt-in `--adopt-workers` flag or route; outliving only for implementers | Adoption is the flag's only consumer, and an opt-in doubles the lifetimes to test, leaving the flag-off path as the one nobody exercises | Q1; `R96`, 0 of 172 requirements state a lifetime |
| D2 | Adoption **re-binds** the surviving assignment into the new driver's team; one live team per lead | Reading the old team across generations with `authorize` relaxed to a newer caller | `authorize`, `_pin` and `_bind`'s newer-generation rules stay one-directional, and `wait` needs no change because membership follows the move | Q2; `dispatch_store.py` `authorize`, `_bind` |
| D3 | A new `adopt` runtime tool, which `require_child` then honours for that terminal | Accepting any terminal whose parent is dead; rebinding the worker's hc parent | "Dead parent" is every survivor by definition, so any process could claim one; rebinding parents reaches into `hc`, which this plan keeps out of scope | Q3; `hc_tools.py` `require_child`, R48 |
| D4 | R54's same worker name stays; a live name that cannot be adopted is a **strand**, not a re-dispatch | Minting a name per attempt (`t1o1`) | hc's name uniqueness is the double-dispatch backstop the archived D2 relied on; deleting it needs a different backstop, which this plan does not have | Q4; R54, R56, hc name rule |
| D5 | Implementers only, and one `adopted` event naming the unit, the worker and the generation it came from | Adopting reviewers and answerers too | A lost reviewer costs a re-review; an implementer holds the branch's unpushed work. R53 already routes reviewers and answerers onto the exited path | Q5; R53, R54 |
| D6 | `adopt` is addressed by the ledger's `runtime_id` (the hc address) and the plan's scope; the store resolves the rest from its own rows | Adding `team_id`/generation to the ledger | The store already knows a worker's team; a ledger field would be a schema change for a fact another table owns | gather: `spawn` returns `team_id`, the driver drops it; `bind` (244) |
| D7 | No schema change: adoption updates `workers.team`, `assignments.team` and the rows addressed to the worker, and inserts the new `callers` row; `user_version` stays 2 | A `team_handovers` table | The columns already carry `team`; a new table would be a third place to keep consistent, and an older server must still read the database | gather: 11 tables, `user_version=2`; D9 of the archived plan |
| D8 | The old team is left as history: nothing is deleted, and it cannot be authorized again because the new generation is the newest row | Deleting or merging the old team's rows | The audit trail is the only record of what the failed drive did; `authorize` already refuses a non-newest team | D2; gather: `authorize` |
| D9 | The run lock is the fence: adoption only arises over a dead holder, which is exactly what R17 leaves behind | An adoption token or lease in the ledger | R17 refuses a second live driver of the plan before any spawn, so two drivers cannot adopt the same worker | D2; R17 |
| D10 | `adopt` is its own verb, and `resume` keeps its child check | Relaxing `resume` to accept a foreign terminal | A relaxed `resume` would silently accept any terminal; the adoption is a named act with a record behind it | D3; `require_child`'s 4 sites |
| D11 | Reconciliation runs before adoption: an attempt whose report has landed goes to the gates, and nothing is adopted for it | Adopting first and reconciling after | R51 already owns that case and its tests pin it; adoption would only add a second path to the same state | gather: report-landed path; R51 |
| D12 | An adopted worker whose turn has ended is resumed through the existing resume path once the adoption is recorded | A separate "re-awaken" verb | After the adoption the boundaries that refused the call are satisfied, so the verb that already exists does the work | D3; `ensure_agent`'s re-attach path |
| D13 | `adopt` refuses anything `status` does not prove alive at work: `exited`, or `unknown` with `not_found`, belongs to R50's orphan path | Adopting a terminal the runtime will not vouch for | R50 already owns "the agent is gone"; two owners for one answer is how the two paths drift apart | R50; `agent_runtime.py` `status` |
| D14 | Archive and gaps workers keep today's lifetime; adoption covers units of the plan | Adopting them too | They are spawned per task and their work is a report on disk; the feature's value is the unit's transcript | gather: roles; D5 |

## Scenarios

- **S1** WHEN a drive spawns a worker, THEN its terminal outlives the driver's terminal, and the
  ledger records the attempt exactly as today.
- **S2** WHEN a drive starts and the ledger holds an open implementer attempt whose agent is alive at
  work in another team, THEN an `adopted` event names the unit, the worker and the generation it came
  from, and the attempt's rows are bound in this drive's team.
- **S3** WHEN an attempt has been adopted, THEN `wait` in this drive's team lists it as unfinished,
  with no `spawn` and no `resume` in between.
- **S4** WHEN an attempt has been adopted, THEN `resume`, `message` and `stop` reach its worker; a
  terminal no adoption names is still refused, and the refusal names the adoption it lacks.
- **S5** WHEN `adopt` is asked for a terminal whose store row is not at work for an open unit of this
  plan, THEN it refuses before binding, releasing or spawning anything, and names why.
- **S6** WHEN a live terminal holds the worker name of an attempt that cannot be adopted, THEN a
  `stranded` wake names it and the run exits non-zero; the unit is not re-dispatched.
- **S7** WHEN an attempt's report has landed for that attempt, THEN the run reconciles it to the gates
  and adopts nothing for it.
- **S8** WHEN a drive ends and its worker survives, and a later drive starts, THEN the surviving
  assignment's rows carry the new team and the old team is no longer the newest for its lead.
- **S9** WHEN an adoption moves an assignment, THEN the old team's rows are unchanged and its
  assignments cannot be authorized again.
- **S10** WHEN a reviewer's or an answerer's agent is gone, THEN the existing exited path applies and
  nothing is adopted.
- **S11** WHEN `adopt` is asked for a worker that `status` answers `exited` (or `unknown` with
  `not_found`) for, THEN it refuses, and the orphan path owns what happens next.

## Out of scope

- Adopting reviewers, answerers, archive workers or gaps workers (D5, D14).
- Any change to `hc` itself: `--outlive-parent` semantics, rebinding a terminal's parent, and name
  uniqueness stay hc's. Cross-machine adoption (hc machines) is not covered.
- A store schema change or a `user_version` bump (D7).
- Reusing an adopted worker for a different plan or repository: adoption is per plan and per scope.
- The merge-time and report-time COMPLETE rules, and the recovery behaviours R50–R61 themselves.

## Risks

- **Security:** adoption is the one place a caller reaches a terminal it did not spawn, which is what
  R48 forbids. It is fenced three ways: `adopt` needs the store to prove the terminal is at work for
  an open unit of the caller's own plan scope, the adoption is a recorded row, and `require_child`
  accepts a foreign terminal only while that record stands. A forged address, a foreign scope or a
  call from an unrelated team still fails before any state changes.
- **Performance:** one store read and at most one write per open attempt at drive start, on the path
  that already reconciles every open task. No per-tick or per-call cost: the adoption is resolved once
  and `wait`'s snapshot then includes the assignment as any other.
- **Migration:** none. The columns that carry `team` already exist, `user_version` stays 2, and the
  writes are `UPDATE`s plus one `callers` insert, so an older server reading the same database sees
  the same shape it wrote.
- **Rollback:** revert the driver ticket first — it is the one that turns `--outlive-parent` on — and a
  survivor becomes what it is today: a live terminal `wait` does not list, which R51 strands and exits
  non-zero over. Then revert the tool ticket, then the store ticket. No data needs undoing: adoption
  only moved rows into a team that is already the newest.

## Seams

- `dispatch_store` over a temporary SQLite file: the adopt path is a store operation, so the test needs
  no runtime and no terminal (S2, S8, S9).
- `hc_tools.adopt` and `require_child` in process, against the same store: the tool contract, the
  refusal and the post-adoption verbs (S3, S4, S5, S11).
- `driver.reconcile` and `ensure_agent` through the existing `runtime_process` seam, with the fake
  runtime answering for two teams: the `adopted` event, the strand, and the report-landed ordering
  (S1, S6, S7, S10).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The store adopts an assignment into another team: `adopt` moves the worker, its assignments and the rows addressed to it, inserts the new caller row, leaves the old team as history, and refuses a row that is not at work for the unit it was asked about | S2, S5, S8, S9, S11 | none | packages/agent-runtime/src/agent_runtime/dispatch_store.py, packages/agent-runtime/tests/ | regular | complex | agent | TO-DO |
| T2 | The runtime tool surface: an `adopt` tool that resolves the caller's team, calls the store, and is honoured by `require_child` for that terminal only; `resume`, `message` and `stop` then reach the adopted worker, and a terminal no adoption names is refused with the reason and the way out | S3, S4, S5, S11 | T1 | packages/agent-runtime/src/agent_runtime/hc_tools.py, docs/specs/agent-runtime.md, packages/agent-runtime/tests/, tests/ | regular | complex | agent | TO-DO |
| T3 | The driver: `spawn` passes the lifetime flag, and a starting drive adopts each open implementer attempt it can prove alive at work, waking `adopted`; an attempt it cannot adopt is stranded and the run exits non-zero; reconciliation keeps its report-landed ordering | S1, S2, S6, S7, S10 | T2 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/, docs/specs/orchestrator-engine.md | regular | complex | agent | TO-DO |

T1 changes no behaviour on its own: the verb has no caller until T2, and T2's adoption is inert until
T3 turns the lifetime flag on — which is why T3, and not T1, is the ticket that must not ship first.
Outliving and adoption land together, as the archived D1 requires.
