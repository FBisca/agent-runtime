# Plan: A restarted drive recovers, or says why it cannot

Slug: `drive-restart-recovery`. Living spec: `docs/specs/orchestrator-engine.md`. Status: ARCHIVED 2026-11-10. The drive's driver died before the archive merged; the Lead carried the archive by hand.

## Why now

The `selective-remote-installer` drive (2026-11-09) hit this on its second ticket. The driver pane was
killed to pick up a newly merged engine fix; `hc` ends a terminal's children with it, so the worker
pane died too. The ledger still said `t2: ACTIVE, runtime_id t1nd`. Every relaunch afterwards started,
slept 30 seconds, printed `finish` and spawned nothing — three times in a row — while the plan cell
read `IN PROGRESS`. The drive was only recoverable by hand-editing the ledger and the plan cell.

The same drive also produced three smaller dead ends and one false alarm:

- re-dispatch after the repair failed with `assignment is closed` (a RELEASED attempt id cannot be
  claimed again), and the driver logged `T2 not dispatched: …` **without a wake**;
- `answer` renames the attempt (`t5` → `t5d1` → `t5d2`) on every decision, so a worker told to fix
  `task_id` wrote the id of the *previous* attempt, and the runtime then refused its second report with
  `assignment already complete` — a loop only the operator could break;
- the idle tick escalated a worker that was mid-turn for 29 minutes and running a long test.

The engine defect that caused the original crash (a worker's report not naming its attempt) is already
fixed on main (`1b9bba8`, `ee4440a`). What is missing is recovery: a drive must never be able to go
silently finished with work outstanding, and a restart must cost at most the dead attempt's transcript:
its files stay in the unit's worktree and branch.

## Sources

| Source | What it settles |
|---|---|
| `~/.local/state/orchestrate/selective-remote-installer/events.jsonl` 13:34–13:47, 17:15–17:30 | The silent `finish`, the `assignment is closed` log line, the `t5 → t5d1 → t5d2` cascade, and the operator's manual repair |
| `packages/agent-orchestrator/src/agent_orchestrator/driver.py` @ origin/main | `dispatch_ready` (skips a held task with any `runtime_id`; `self.failed` is permanent for the run), `loop` (`idle` from `wait`'s `unfinished`; `awaiting` counts only ready-for-ci/awaiting-merge/requested), `ready` (TO-DO only), `dispatch` (spawns, then `bind` refuses a closed id with `assignment is closed`, then `set_status … IN PROGRESS`), `step_decided` (a held `complete` with a valid report already goes to `reported`), `step_redo` (new attempt id `<unit>d<n>`), `idle_ticks` (signature over gate/report/ci and the agents' state and runtime_state) |
| `packages/agent-runtime/src/agent_runtime/hc_tools.py` @ origin/main | `spawn` builds `hc run --bg --name … --cwd … --parent <caller> --json --no-notify`, so a worker ends with the driver's terminal; `lead_store` keys the team on the caller terminal and `require_child` refuses a worker parented elsewhere, so a relaunched driver cannot reach a surviving worker (why D1 is deferred) |
| `docs/specs/orchestrator-engine.md` | R5 (a refusal is reported), R14 (wakes reach the Lead's terminal), R15 (awaiting-merge is never idle), R16 (the brief names the task id), R17 (one driver per plan): the requirements the new ones sit beside |
| Owner note, 2026-11-09 ("the drive has entered into an idle state unrecoverable") | The failure is a class, not one instance |

## Decisions

| # | Choice | Rejected | Why | Status |
|---|---|---|---|---|
| D1 | Deferred to its own plan (`driver-adopts-workers`): a worker keeps hc's default lifetime and ends with the driver's terminal. Outliving the driver ships only together with a way for a relaunched driver to reach the worker. | `--outlive-parent` without adoption | With `--outlive-parent` alone, a relaunched driver (new HC_TERMINAL_ID, so a new runtime team: hc_tools.lead_store) can neither wait on, resume, message nor stop the surviving worker (`require_child`, agent-runtime R48), and the live worker's name blocks re-dispatch (hc names are unique among live terminals). That is worse than today. The unit's worktree keeps the dead worker's files, so recovery (D2/D3) costs only its transcript. | Accepted (deferred) |
| D2 | At startup and whenever the loop would finish, the driver reconciles every open, unparked ledger assignment (implementer, reviewer, answerer) that `wait` does not list as unfinished. It asks `status` by the agent's name. The assignment is orphaned only when `status` answers `exited`/`finished`, or `unknown` with hc's `not_found` in `native.error`. A raised call, or `unknown` with any other error, means "cannot tell": nothing is released or spawned, and a `stranded` wake names the unit and the error. | Trusting the ledger's `runtime_id` | `status` never raises on hc read failures; it answers `unknown` (agent_runtime.py status). `runtime_call` turns refusals and CLI failures alike into ValueError. So only the shape of the answer tells "forgot" from "failed". `pane_alive` swallows errors to False and must not decide this. | Accepted |
| D3 | An orphaned implementer attempt whose report has landed for that attempt (`report_landed`) moves to `reported`. Otherwise it is RELEASED with reason `orphaned: <status answer>` and dispatched again as a new attempt `t<N>o<k>` of the same unit, on the same worktree and branch, under the same worker name, and the run keeps going. An orphaned reviewer or answerer takes today's `exited` path (`on_agent_attention`). `dispatch` refuses a closed task id before it spawns anything. | Ending the run; reusing the released attempt id | The work is on the branch and in the worktree; only the attempt died. The ledger's `bind` refuses a RELEASED id (`assignment is closed`, driver.py:243), and today it does so only after `ensure_agent` has spawned a worker (13:45:12 in the incident log), so the attempt must be new and the check must come first. The same worker name makes hc refuse a second live terminal (`name_taken`), which is the double-dispatch backstop. | Accepted |
| D4 | A ticket whose cell reads `IN PROGRESS` is dispatchable again only when the ledger holds at least one attempt of its unit, every attempt is RELEASED (none COMPLETE, none at awaiting-merge/cleanup/done, none parked), and `status` for the unit's worker name answers definitively not live (D2's test). A cell with no ledger attempt stays a lock. | Leaving `IN PROGRESS` as a one-way door | `ready` accepts TO-DO only, so a crashed attempt makes the ticket undispatchable. Requiring an attempt in the ledger stops a run under another `--state-dir` (empty ledger) from taking a ticket it never owned; R17 already refuses a second live driver of the plan. | Accepted |
| D5 | Every dispatch that cannot happen raises a `not-dispatched` wake naming the ticket, the reason and the way out, keyed by ticket and reason so a repeat does not spam. That covers `dispatch_ready`'s `not dispatched`, `reopen_parked`'s `not re-dispatched` and the D3 re-dispatch. Setting the ticket's cell to TO-DO takes it out of `self.failed`, so the next round tries it again. | Logging it to stderr only | A drive that cannot dispatch must ask, not idle. Today the reason goes only to stderr, and `self.failed` makes the failure permanent for the life of the run. | Accepted |
| D6 | An `answer` decision keeps the current attempt: the unit's worker is resumed on the same task id with the answer, the gate returns to waiting for that attempt's report, and a report older than the answer is not this round's news. Only `retry`, `fresh worker` and `retry one class up` create a new attempt. | Renaming on every decision | In the 2026-11-09 log the t5 request came from a report contract error (`report task_id 'T5' is not t5`), not from a held complete, which `step_decided` already acts on. Each `answer` went through `step_redo` and minted t5d1, then t5d2, so the id named in the answer was stale by the time it arrived. | Accepted |
| D7 | `decide … answer` refuses an answer whose text names an attempt id of the unit other than the request's `task` (request.json already carries it), and names the id it expects. | Trusting the answer's wording | The brief already names its id (`redo_text`, R16). The operator wrote a correct instruction for the wrong id, and the request file knew the right one. Refusing at `decide` keeps the wrong text out of the run, with no schema change. | Accepted |
| D8 | The idle tick does not count a unit whose agent `wait` reports with `runtime_state: working`, until it has been `working` for `--working-minutes` (default 120). The request then says how long. Every other unchanged signature counts as today, and R15's awaiting-merge skip stands. | Counting any unchanged signature; keying on the turn counter | A long test is one turn. hc's `turn` only moves when the status leaves `working`, and `wait` drops `turn` from its assignments (dispatch_wait.py:109), so 'turn advanced' would still have escalated the 29-minute test. `runtime_state` is already in the signature; it counted because it did not change. | Accepted |
| D9 | No schema change; recovery is expressed in the existing `tasks`, `agents` and `events` records | A new `orphaned` field | A field would need migration and every reader changed | Accepted |
| D10 | A ticket merged before its unit finished is completed by hand: the plan's cell says `COMPLETE`, the reason says why, and the archive is unblocked. The ledger's refusal is reported as a defect, not worked around silently | Re-dispatching a merged unit (the merged-early path completes it with a report that carries no checks, so the ledger refuses it again, and each answer mints a new attempt that cannot settle) | PR #83 merged while its unit was still reporting. `merged_early` completes the unit with a synthetic report whose `checks` are empty, and the ledger requires the gate to be among the passed checks, so the completion is refused; the running driver still mints a new attempt per answer (T4's fix is merged in #84 but the live driver predates it), so each answer restarts the same refusal. The code is on main and green, and the deferred findings are in `deferred.md`; the cell is set by hand, and the two defects go to the `merged-unit-completes` drive (its T2 owns the merge-time COMPLETE rules) | Accepted |
| D11 | T2's Paths grow to the two restart test files the strand rule invalidates (`test_agents.py`, `test_decider.py`): a drive restarted over a worker left live at idle or done with no report now strands that unit instead of finishing over it, and those two tests asserted the old outcome | Narrowing the strand rule to keep the old tests passing; leaving the files out of scope | `converge`'s scope check refused the ticket ("FAIL scope: ..."), and it was right to: the ledger still said the ticket did not own them. The two tests are the same shape as the one the ticket already changed (`test_run.py` S14), so the class is fixed in every place with the same pattern; the third (`test_compat.py` S34) needed no edit, because the driver now tracks a worker it resumed for a fix round as touched and does not mistake it for one a dead drive left behind | Accepted |

## Scenarios

- **S2** WHEN a drive starts and an open, unparked ledger task's assignment is not in `wait`'s `unfinished`, and `status` for its agent answers `exited`, or `unknown` with hc's `not_found`, THEN the task is orphaned and an `orphaned` event names the task and the status answer.
- **S2a** WHEN the loop would finish while an open, unparked ledger task's assignment is not in `wait`'s `unfinished`, THEN it reconciles that task first; a task it cannot settle raises a `stranded` wake, and the run exits non-zero instead of logging `finish`.
- **S2b** WHEN `status` for an open task's agent fails (the call raises, or answers `unknown` with any error other than `not_found`), THEN nothing is released or spawned, and a `stranded` wake names the unit and the error.
- **S2c** WHEN the orphaned agent is a reviewer or an answerer, THEN it takes the `exited` path it takes today when the runtime reports the exit.
- **S3** WHEN an orphaned implementer attempt's report has not landed, THEN the attempt is RELEASED with reason `orphaned: <answer>`, the unit is dispatched again as `t<N>o<k>` on the same worktree and branch under the same worker name, and the plan cell reads `IN PROGRESS`; WHEN its report has landed for that attempt, THEN the gate moves to `reported` and nothing is spawned.
- **S3a** WHEN `dispatch` is given a task id the ledger holds as COMPLETE or RELEASED, THEN it refuses before any `spawn` or `resume` call.
- **S4** WHEN a ticket's cell reads `IN PROGRESS`, the ledger holds at least one attempt of its unit, every attempt is RELEASED, and `status` for its worker answers definitively not live, THEN the ticket is dispatchable again; WHEN the ledger holds no attempt of the unit, THEN it is not dispatched.
- **S5** WHEN a dispatch or re-dispatch fails for any reason, THEN a `not-dispatched` wake names the ticket, the reason and the way out, once per ticket and reason; WHEN the Lead then sets that ticket's cell to TO-DO, THEN the next round tries it again.
- **S6** WHEN the Lead answers a unit's request with `answer`, THEN the worker is resumed on the same task id with the answer, no new attempt is created, and a report written after the answer under that id passes the gate.
- **S7** WHEN `decide … answer` is given text naming an attempt id of the unit other than the request's `task`, THEN it exits 2, writes no decision, and names the id it expects.
- **S8** WHEN `wait` reports a unit's agent with `runtime_state: working`, THEN the idle tick does not count it and no request is raised, until it has been `working` for `--working-minutes`, when the request says how long.
- **S8a** WHEN a unit's signature is unchanged for two ticks and its agent is not reported `working` (CI waiting, or no runtime observation), THEN the Lead is asked, as today.

## Out of scope

- The `merged-unit-completes` plan (`docs/plans/2026-11-09-merged-unit-completes.plan.md`), which owns
  the merge-time and report-time COMPLETE rules. This plan owns restart and liveness only.
- A `drive`-level watchdog process, and any change to `hc` itself.
- Re-dispatching a unit whose branch was already merged: a merge is final and `merged_early` owns it.
- A worker that outlives the driver (`hc run --outlive-parent`) and its adoption by a relaunched driver: a follow-up plan, `driver-adopts-workers`, which must first change the runtime's team key (`hc_tools.lead_store`) and `require_child` under agent-runtime R48. Until then a restart kills the driver's workers and this plan recovers their units.

## Risks / rollback

- **Security:** none. No new input is trusted; the runtime's own answers decide what is orphaned.
- **Performance:** one `status`/`wait` read per open unit at startup, which the driver already makes
  per round.
- **Double dispatch:** the real risk. A live worker must never be duplicated, so recovery re-attaches by
  name first (`ensure_agent` does, within one driver terminal), treats a unit as orphaned only on a definite `status` answer (D2), never on a failed lookup, refuses a closed id before spawning (S3a), and re-dispatches under the same worker name, so hc's `name_taken` refuses a second live terminal.
- **Rollback:** `driver.py` plus tests; revert the pull request and restart any drive
  that picked it up.

## Seams

- `Driver.start` reconciliation, `dispatch` and the loop's finish condition for S2–S5, through `RunFixture`/`GateFixture` and `tests/fake_runtime.py`. The fake's `status` must learn `unknown` with a `not_found` `native.error` (`tests/test_run.py`).
- `decide` and `Driver.step_decided` for S6 and S7 (`tests/test_decider.py` `DeciderFixture`, `tests/test_wake.py` S3 style).
- `Driver.idle_ticks` and `idle_signature` for S8 and S8a (`tests/test_pr.py` S6/S8 idle tests; `wait` steps carry `runtime_state`).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T2 | Reconciliation: an orphaned attempt is recognised only on a definite runtime answer, re-dispatched as a new attempt (or moved to `reported` when its report landed), a closed id is never spawned, and the run cannot finish with an unexplained open unit | S2, S2a, S2b, S2c, S3, S3a | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_run.py, packages/agent-orchestrator/tests/fake_runtime.py, packages/agent-orchestrator/tests/fixtures.py, packages/agent-orchestrator/tests/test_agents.py, packages/agent-orchestrator/tests/test_decider.py | regular | complex | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/85 (b5ca091) |
| T3 | The plan cell stops being a lock, and a dispatch that cannot happen raises a wake and can be retried | S4, S5 | T2 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_run.py, packages/agent-orchestrator/tests/test_wake.py, packages/agent-orchestrator/tests/test_reopen.py | regular | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/91 (3cf9fed) |
| T4 | An `answer` keeps the attempt, and `decide` refuses an answer that names another attempt id | S6, S7 | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_decider.py, packages/agent-orchestrator/tests/test_wake.py | regular | complex | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/84 (8e6dc64) |
| T5 | The idle tick leaves a worker the runtime reports `working` alone, up to `--working-minutes` | S8, S8a | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_pr.py, packages/agent-orchestrator/tests/fake_runtime.py | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/83 (00775c4) · done by hand: the merge is final and green, and the ledger refused COMPLETE only because the merged-early path completes a unit with a report that carries no checks (D10) |
