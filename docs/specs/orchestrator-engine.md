# Orchestrator engine

Paths: packages/agent-orchestrator/, packages/agent-orchestrator/tests/, pyproject.toml, .github/workflows/ci.yml, docs/specs/orchestrator-engine.md

The engine is the orchestrator's driver: it reads a plan, dispatches tickets, gates their reports,
and writes the plan's Status cells back. This repository owns the engine and its suite; the copy that
used to live in the `agent-skills` repository is the one that goes away.

The behaviours the engine pins are the source plans' scenarios, and each ported test keeps the id of
the scenario it came from. What this file states is what this repository guarantees about them: the
suite runs here, against the member, and the member answers the way the engine always answered.

## Requirements

- **R1** WHEN the engine's own test suite runs, THEN it exercises the member's driver, and the repository holds exactly one copy of the engine. (from orchestrator-engine-suite/S1)
- **R2** WHEN the member's driver is exercised by the ported suite, THEN the suite's behaviour is the member's behaviour: a test that would pass only against the old copy fails here. (from orchestrator-engine-suite/S2)
- **R3** WHEN the repository's CI runs, THEN it resolves the workspace, runs the whole suite — the member's own suite included — and fails the build when the tracked lint or type diagnostics grow. (from orchestrator-engine-suite/S3)
- **R4** WHEN a route names a class, a backend or an effort the driver cannot run, THEN the refusal names the value it named and the way out, and no worker is spawned. (from orchestrator-engine-suite/S4, orchestrator-routing-decoupling/S3)
- **R5** WHEN the runtime refuses a call, THEN the driver sees a `ValueError` and the run reports the refusal instead of crashing. (from orchestrator-engine-suite/S5)
- **R6** WHEN `launch` starts a run, THEN the pane runs the member's CLI. (from orchestrator-engine-suite/S6)
- **R7** WHEN a run is terminated for exceeding its budget, THEN the harness waits, within a bounded window, for the tree it killed to stop running before it decides the tree survived — a process the system has not reaped yet has exited — and a tree that really outlived the run still fails the check. (from orchestrator-engine-suite/S7)
- **R8** WHEN a route names the backend `pi` or the effort `xhigh` or `max`, THEN the worker's spawn carries that backend and that effort, and a route that names no effort still leaves the size/difficulty matrix in charge. (from orchestrator-routing-effort-role/S1, S4)
- **R9** WHEN a step route names a step — `implementer`, `reviewer` or `gaps` — THEN that step's spawn carries the step's backend, model and effort; `--brain-cli` still names one backend for every gate step, and a step route overrides it. a route config or `--step` override naming `archive` is refused before any spawn, naming the fix: the archive has no agent, remove the `archive` route. (from orchestrator-routing-effort-role/S8, S9, deterministic-archive/S7)
- **R10** WHEN a step route names an unknown step, backend, model or effort, THEN the run fails before any worker is spawned, naming the value. (from orchestrator-routing-effort-role/S10)
- **R11** WHEN no step route names a gate, THEN the gate keeps the effort it has always had: the reviewer `high`, the gaps reviewer `medium`; the archive has no agent and no effort. (from orchestrator-routing-effort-role/S3)
- **R12** WHEN a worker writes the report the driver's `CONTRACT` describes, `"context"` included, THEN `Report` accepts it: every top-level key the contract's example names is a `Report` field. (from orchestrator-lead-alerts/S1, S2)
- **R13** WHEN the driver runs a git call that walks or writes the whole tree (`worktree add`, `status`), THEN that call's timeout is sized for a large repository, not `run()`'s 15-second default. (from orchestrator-lead-alerts/S3)
- **R14** WHEN `launch` runs inside an hc terminal, THEN the run is given that terminal, and each new wake is sent to it once with `hc send` (never `--force` or `interrupt`) — the wakes the Lead acts on are `stranded` (a task the loop cannot settle; the run exits non-zero), `not-dispatched` (a dispatch that failed, with the way out), `person` (a person-owned ticket became ready, once per ticket, with what it needs) and the idle request, which `wait --working-minutes N` delays for an agent reported `working`; an orphaned task is logged as an `orphaned` event naming the task and the status answer; a send that fails is logged as `notify-failed` and the run goes on; a run launched outside a terminal sends nothing. (from orchestrator-lead-alerts/S4–S7)
- **R15** WHEN a unit is at `awaiting-merge`, THEN the idle ticks skip it: no idle event, no `idle` request. The merge is a person's step, and the `pr-ready` wake already asked for it. (from orchestrator-lead-alerts/S8)
- **R16** WHEN a unit's brief is written, THEN it names the `task_id` its report must carry, so a worker that reads the brief writes the id the driver checks. (from orchestrator-report-task-id/S1)
- **R17** WHEN a `run` starts on a plan another live driver is running in the same repository, whatever `--state-dir` either names, THEN it is refused before any worker is spawned, naming the holder's pid and start time and the way out (`await` the plan, or stop that driver); a run of another plan in the same repository is not held up, and a run that ends — or crashes — leaves no lock behind. (from orchestrator-run-lock/S1, S2)
- **R18** WHEN a worker's report reaches the driver before the file it names is usable — absent, half written, or
  breaking its contract — THEN the driver waits a bounded grace for the file and reads it; a payload it still cannot
  read is the sending agent's news before it is the Lead's: that agent is told what the driver needs, twice at most,
  with the gate kept open, and only then is the Lead asked, naming the warnings. An agent that is gone is not
  warned. (from orchestrator-payload-race/S1, S2, S3)
- **R19** WHEN a run finds no route config, THEN the 'Model classes' table answers with the model classes it always answered, and the run says that table is deprecated. (from orchestrator-routing-decoupling/S1)
- **R20** WHEN a route names a backend, model and effort the driver can run, THEN dispatch chooses that combination — a command-line override first, then the named file, the repository's `.orchestrate/routes.*`, the user's `~/.config/orchestrate/routes.*` — and logs the chosen route in the route ledger and the event log. (from orchestrator-routing-decoupling/S2)
- **R21** WHEN the provider or step routing changes, THEN the plan schema and the gate, review and pull-request state machine stay as they were. (from orchestrator-routing-decoupling/S4)
- **R22** WHEN the packet requires `scripts/checks.sh` and the report's passed check is `bash scripts/checks.sh`, `./scripts/checks.sh` or `sh ./scripts/checks.sh`, THEN `checkpoint()` accepts COMPLETE (from merged-unit-completes/S1)
- **R23** WHEN the packet requires `pytest` and the report's passed check is `uv run python -m pytest`, THEN `checkpoint()` accepts COMPLETE (from merged-unit-completes/S1a)
- **R24** WHEN a required check appears only behind a prefix not on the strip list (e.g. `echo scripts/checks.sh`) or is absent, THEN `checkpoint()` refuses COMPLETE and names the missing check (from merged-unit-completes/S2)
- **R25** WHEN a COMPLETE report carries a non-required check with `not_run` and a reason, THEN `checkpoint()` accepts it (from merged-unit-completes/S3)
- **R26** WHEN a COMPLETE report carries a `not_run` check with no reason, THEN `checkpoint()` refuses it (from merged-unit-completes/S3a)
- **R27** WHEN a required check is reported `not_run`, THEN `checkpoint()` refuses COMPLETE (from merged-unit-completes/S3b)
- **R28** WHEN a COMPLETE report carries any `failed` check, THEN `checkpoint()` refuses COMPLETE (from merged-unit-completes/S4)
- **R29** WHEN a worker reports its unit ready and the report fails the COMPLETE rules, THEN the driver messages that worker with the refusal reason, the ticket keeps its gate, one fix round is counted, and no Lead request is written (from merged-unit-completes/S5)
- **R30** WHEN report-time refusals reach the fix-round cap, THEN the Lead is asked, as for any capped unit (from merged-unit-completes/S5a)
- **R31** WHEN a PR is merged and its stored report fails the COMPLETE rules, THEN the ledger task is COMPLETE with its original checks, the plan cell is COMPLETE, deferred.md has a `[merged-unverified] <task>: <reason>` line, no Lead request is written, and the dependents become dispatchable (from merged-unit-completes/S6)
- **R32** WHEN a PR merges early (`merged_early`) on a drive with a named `--gate`, THEN the unit completes as in S6 (from merged-unit-completes/S7)
- **R33** WHEN a PR merges early, THEN the ledger task keeps the checks of the worker's stored report, not an empty list (from merged-unit-completes/S7a)
- **R34** WHEN a PR is merged and `packet.checkout` no longer exists, THEN the task is COMPLETE with `checkout_at_checkpoint: null`, a log line names the missing path, and `(task, "awaiting-merge")` is not in the stuck set (from merged-unit-completes/S8)
- **R35** WHEN a PARTIAL checkpoint runs and `packet.checkout` does not exist, THEN `checkpoint()` raises (from merged-unit-completes/S8a)
- **R36** WHEN cleanup runs for a COMPLETE task whose checkout is also used by a non-closed task under a different worker name, THEN the worktree is kept (from merged-unit-completes/S9)
- **R37** WHEN several CI checks fail with long logs, THEN each CI finding carries only its own job's capped excerpt, the full log is at `log_path`, and the fix-round findings file stays under 64 KiB (from merged-unit-completes/S10)
- **R38** WHEN a unit is at `awaiting-merge` and its pull request turns `DIRTY` or `CONFLICTING`, THEN the PR goes back to draft and a conflict fix round gives a fresh worker a rebase brief quoting the merge state; the rebase goes through a delta re-review and CI, and `gh pr ready` runs again before the unit waits for its merge again. (from orchestrator-pr-merge-state/S1)
- **R39** WHEN a unit at `ready-for-ci` has a `DIRTY` or `CONFLICTING` pull request, THEN neither `gh pr ready` nor `gh pr update-branch` runs: the PR stays a draft and the conflict fix round is raised directly. (from orchestrator-pr-merge-state/S2)
- **R40** WHEN a unit's pull request is `BEHIND` its base at `ready-for-ci` or `awaiting-merge`, THEN `gh pr update-branch` runs, the unit goes back to `ready-for-ci` with its checks cleared and its PR still ready (no draft, no fix round), and only checks read on the moved head count as green; the new `pr-ready` wake is keyed on the PR's live `headRefOid`. (from orchestrator-pr-merge-state/S3)
- **R41** WHEN the merge state is `UNKNOWN`, or `gh pr view` fails, at `ready-for-ci` or `awaiting-merge`, THEN no `gh pr ready`, no wake and no fix round: the unit is polled again. (from orchestrator-pr-merge-state/S4)
- **R42** WHEN a unit is at `awaiting-merge` and its checks turn red, THEN the PR goes back to draft and a CI fix round is raised with the failed check's log. (from orchestrator-pr-merge-state/S5)
- **R43** WHEN a unit at `awaiting-merge` has merge state `CLEAN`, `BLOCKED`, `UNSTABLE` or `HAS_HOOKS` and checks green or pending, THEN it keeps waiting with no mutating `gh` call, and R15 still holds. (from orchestrator-pr-merge-state/S6)
- **R44** WHEN a pull request is `MERGED` or `CLOSED` while it also conflicts, is behind or has red checks, THEN the merge or close handling wins and no fix round is raised. (from orchestrator-pr-merge-state/S7)
- **R45** WHEN a disputed answer with `changes_code` sends a unit back from `ready-for-ci` or `awaiting-merge`, THEN the unit's readiness is forgotten across its attempts and its checks cleared: the PR is a draft through the delta re-review and `gh pr ready` runs again on its return. (from orchestrator-pr-merge-state/S8)
- **R46** WHEN `gh pr ready --undo` fails as a unit leaves for a fix round, THEN no fix worker is dispatched; the undo is logged and tried again on a later round. (from orchestrator-pr-merge-state/S9)
- **R47** WHEN `gh pr update-branch` fails and a fresh `gh pr view` shows no conflict, THEN no conflict round is raised: the Lead is asked with the `gh` error and the way out. (from orchestrator-pr-merge-state/S10)
- **R48** WHEN a unit's pull request conflicts after its fix rounds hit the cap, THEN it escalates to the Lead as other capped rounds do, and the PR is left a draft. (from orchestrator-pr-merge-state/S11)
- **R49** WHEN the archive unit's pull request conflicts, THEN its conflict round goes fix, `converge --final`, `ready-for-ci` with no review, and the PR is a draft while it is rebased. (from orchestrator-pr-merge-state/S12)
- **R50** WHEN a drive starts and an open, unparked ledger task's assignment is not in `wait`'s `unfinished`, and `status` for its agent answers `exited`, or `unknown` with hc's `not_found`, THEN the task is orphaned and an `orphaned` event names the task and the status answer. (from drive-restart-recovery/S2)
- **R51** WHEN the loop would finish while an open, unparked ledger task's assignment is not in `wait`'s `unfinished`, THEN it reconciles that task first; a task it cannot settle raises a `stranded` wake, and the run exits non-zero instead of logging `finish`. A task held by a live terminal this drive did not bind is stranded the same way: the `stranded` wake names the task and the runtime's answer. A task whose agent has ended its turn (`idle` or `done`) and whose report has landed for that attempt goes to the gates (`reported`): nothing is spawned and the run does not finish over it. (from drive-restart-recovery/S2a; the two clauses from drive-restart-recovery-gaps/S10 and S11)
- **R52** WHEN `status` for an open task's agent fails (the call raises, or answers `unknown` with any error other than `not_found`), THEN nothing is released or spawned, and a `stranded` wake names the unit and the error. (from drive-restart-recovery/S2b)
- **R53** WHEN the orphaned agent is a reviewer or an answerer, THEN it takes the `exited` path it takes today when the runtime reports the exit. (from drive-restart-recovery/S2c)
- **R54** WHEN an orphaned implementer attempt's report has not landed, THEN the attempt is RELEASED with reason `orphaned: <answer>`, the unit is dispatched again as `t<N>o<k>` on the same worktree and branch under the same worker name, and the plan cell reads `IN PROGRESS`; WHEN its report has landed for that attempt, THEN the gate moves to `reported` and nothing is spawned. (from drive-restart-recovery/S3)
- **R55** WHEN `dispatch` is given a task id the ledger holds as COMPLETE or RELEASED, THEN it refuses before any `spawn` or `resume` call. (from drive-restart-recovery/S3a)
- **R56** WHEN a ticket's cell reads `IN PROGRESS`, the ledger holds at least one attempt of its unit, every attempt is RELEASED, and `status` for its worker answers definitively not live, THEN the ticket is dispatchable again; WHEN the ledger holds no attempt of the unit, THEN it is not dispatched. (from drive-restart-recovery/S4)
- **R57** WHEN a dispatch or re-dispatch fails for any reason, THEN a `not-dispatched` wake names the ticket, the reason and the way out, once per ticket and reason; WHEN the Lead then sets that ticket's cell back to exactly `TO-DO`, THEN the next round tries it again. The way out in the wake says exactly that: "set the ticket's Status cell back to exactly TO-DO". Any other value (for example `TO-DO · retry`) does not retry within the run; the ticket stays out until the run ends, and a new drive reads a cell that starts with `TO-DO` as TO-DO. The Lead hears it on the same wake channel as R14. (from drive-restart-recovery/S5; S12, S12a)
- **R58** WHEN the Lead answers a unit's request with `answer`, THEN the worker is resumed on the same task id with the answer, no new attempt is created, and a report written after the answer under that id passes the gate. (from drive-restart-recovery/S6)
- **R59** WHEN `decide … answer` is given text naming an attempt id of the unit other than the request's `task` (the unit followed by a round letter — fix `r`, retry `d`, orphan `o`, reopen `p` — and a number, in any case, so `T1R1` names `t1r1`), THEN it exits 2, writes no decision, and names the id it expects. (from drive-restart-recovery/S7)
- **R60** WHEN `wait` reports a unit's agent with `runtime_state: working`, THEN the idle tick does not count it and no request is raised, until it has been `working` for `--working-minutes`, when the request says how long. (from drive-restart-recovery/S8)
  The working window is in memory and starts empty at each drive start, so a restart resets it. An idle tick that skips a unit (its gate is `requested`, `awaiting-merge` or parked) clears that unit's window, so a unit that returns to `working` starts a fresh one and is not asked again until another `--working-minutes` have passed. (amended by drive-restart-recovery-gaps/S13, S13a)
- **R61** WHEN a unit's signature is unchanged for two ticks and its agent is not reported `working` (CI waiting, or no runtime observation), THEN the Lead is asked, as today. (from drive-restart-recovery/S8a)

- **R62** WHEN an agent's turn ends while the runtime still lists its assignment as working (`runtime_state` idle or
  done), THEN it is a live agent, not an exit: a reviewer advances only on its own `complete`, even with a valid
  review file on disk; a worker stays pending, except that a valid report for its task already on disk advances it as
  before; an answerer stays working. None is resumed, and none replaces its assignment. Only the timed idle watchdog
  (baseline, then two unchanged ticks) asks the Lead: one `idle` request and one decision wake, naming the agent, the
  file it owes, how long it has been quiet and `decide <plan> <unit> retry | answer | skip`. A failed, exited or
  gone agent, a restart orphan, `unreported`/`error`, and a `blocked` or `decision` report are still handled at once.
  The Lead's decision on such a unit (other than skip or mark blocked) fails an answers record still `working`, since the hand-over replaces that assignment. A `complete` that announces a file the driver cannot read keeps the R18 grace and capped recovery, and each
  recovery message names the file to write or repair and says to report complete under the current assignment. The
  reviewer brief says the reviewer may wait on background jobs, that ending a turn does not finish the review, and
  that it writes the review after those jobs finish and then reports complete with the brief's assignment id. A
  reviewer `complete` after its unit's request is open leaves the request open. (from orchestrator-silent-idle/S1–S14)
- **R63** WHEN `decide … answer` is given text naming an attempt id of the unit other than the request's `task` — including a reopen attempt `t<N>p<n>`, and any uppercase form such as `T1` or `T1R1` — THEN it exits 2, writes no decision, and names the id it expects. An uppercase form of the expected id is accepted. (from drive-restart-recovery-gaps/S9)
- **R64** WHEN the loop would finish while an open, unparked task is held by a live terminal this drive did not bind, THEN a `stranded` wake names the task and the runtime's answer, and the run exits non-zero instead of logging `finish`. (from drive-restart-recovery-gaps/S10)
- **R65** WHEN the loop would finish while an open task's agent has ended its turn (`idle` or `done`) and its report has landed for that attempt, THEN the gate moves to `reported`, nothing is spawned, and the run does not finish over it. (from drive-restart-recovery-gaps/S11)
- **R66** WHEN a ticket is in the not-dispatched state and the Lead edits its cell to anything other than exactly `TO-DO` (for example `TO-DO · retry`), THEN the same run does not dispatch it again. (from drive-restart-recovery-gaps/S12)
- **R67** WHEN a dispatch fails, THEN the `not-dispatched` wake names the ticket, the reason, and the way out "set the ticket's Status cell back to exactly `TO-DO`". (from drive-restart-recovery-gaps/S12a)
- **R68** WHEN a unit the idle tick skipped (its gate was `requested`, `awaiting-merge` or parked) returns to `working`, THEN its working window starts again at that tick, and the idle tick does not count it until another `--working-minutes` have passed. (from drive-restart-recovery-gaps/S13)
- **R69** WHEN a drive starts, THEN the working window starts empty: `--working-minutes` is measured from the first tick that sees the agent `working`, and a restart resets it. (from drive-restart-recovery-gaps/S13a)
- **R70** WHEN the archive brief is written for a YAML plan, THEN every scenario the plan's spec already carries in a requirement's provenance is kept as it is and the brief appends none of them again: a requirement that names several scenarios at once (`(from <slug>/S1, S2, S3)`) holds every one of them, not only the first. (from yaml-eec/S29)
- **R71** WHEN the driver books a change while a Lead command holds the ledger lock, THEN it waits with jittered backoff (0.2 s growing to 5 s) under one monotonic deadline of 60 s that counts the lock waits and the sleeps alike, then books the change; a busy ledger is not an error of the step that met it, and the drive does not exit. (from orchestrator-audit-fixes/S1)
- **R72** WHEN the ledger stays busy past that deadline, or any other exception escapes the driver during startup, the loop or the finalization, THEN the driver logs `crashed <reason>` (`crashed ledger busy >60s` for the ledger), writes a `crash` wake without taking the ledger lock, and exits non-zero; a `run` refused because another driver holds the run lock logs no crash. (from orchestrator-audit-fixes/S2, S3)
- **R73** WHEN the driver and the evidence runner both write the ledger, THEN they take the same lock through one shared helper (`ledger.locked`) and the same deadline rule. (from orchestrator-audit-fixes/S6)
- **R74** WHEN the orchestrator creates a ledger, or makes its next locked write to a ledger that lacks the marker, THEN the ledger carries `"kind": "agent-orchestrator/ledger"` beside its existing `version: 1`, on disk before any file a worker reads (the contract, a brief) exists; a ledger of another kind is refused. (from orchestrator-audit-fixes/S7)
- **R75** WHEN a Lead read command (`show`, the read half of `accept` and `evidence`) runs during a drive, THEN it reads an unlocked snapshot of the ledger; any write it then makes takes the lock and revalidates against the current ledger, refusing with a reason (`the ledger changed since it was read`) when the keys it decided on moved, and writes nothing. (from orchestrator-audit-fixes/S5)
- **R76** WHEN `await` runs while no driver holds the run lock and the run has not finished, THEN it returns `crashed -> <log line>` when the run's driver logged a crash, and `driver gone without a crash log` when a driver started for the run (a `start` line with a pid) and died without one; a wake the Lead has not seen is printed first; a run whose driver has not started yet, or whose driver holds the run lock, is still awaited. (from orchestrator-audit-fixes/S4)
- **R77** WHEN `plan check` or `plan approve` reads an evidence `env` naming HOME, PATH or any XDG_* variable, THEN it refuses with the reason and the way out (drop the name; the runner supplies a scratch value). (from orchestrator-audit-fixes/S17)
- **R78** WHEN the evidence runner builds a check environment, THEN the scratch HOME and XDG_STATE_HOME are applied after the declared variables, so no declaration can override them. (from orchestrator-audit-fixes/S18)
- **R79** WHEN an oracle target resolves outside the scratch tree, or a component of its path is a symlink (e.g. one added by the patch under test), THEN the receipt is an evidence error naming the path, nothing is written outside the scratch tree, and the driver does not crash. (from orchestrator-audit-fixes/S19)

## Coverage

| Scenario | Requirement | Test |
|---|---|---|
| S1 | R1 | `packages/agent-orchestrator/tests/test_engine_home.py`: `test_S1_the_suite_drives_the_member_driver`, `test_S1_the_repository_holds_one_copy_of_the_engine` |
| S2 | R2 | `packages/agent-orchestrator/tests/`: the ported suite, file by file, below |
| S3 | R3 | `packages/agent-orchestrator/tests/test_engine_home.py`: `test_S3_ci_runs_the_member_suite_and_the_tracked_budgets` |
| S4 | R4 | the route refusal tests, listed under R4 below |
| S5 | R5 | `packages/agent-orchestrator/tests/test_agents.py`: `test_S33_a_refused_spawn_leaves_no_open_intent`; `packages/agent-orchestrator/tests/test_run.py`: `test_a_refused_spawn_keeps_the_reservation_and_exits_1` |
| S6 | R6 | `packages/agent-orchestrator/tests/test_launch.py`: `test_S17_starts_run_in_a_named_pane` |
| S7 | R7 | `packages/agent-orchestrator/tests/test_baselines.py`: `test_S3_a_run_that_stalls_is_terminated_and_recorded_as_a_failure` |
| S1 | R15 | `packages/agent-orchestrator/tests/test_run.py`: `test_S1_the_unit_brief_names_the_task_id_the_report_must_carry` |
| S1, S2 | R17 | `packages/agent-orchestrator/tests/test_run_lock.py`: `test_S1_a_second_driver_of_the_same_plan_is_refused`, `test_S1_the_lock_holds_whatever_the_state_dir`, `test_S2_another_plan_in_the_same_repository_runs`, `test_S2_a_finished_run_releases_the_lock_and_names_itself` |
| S1, S2, S3 | R18 | `packages/agent-orchestrator/tests/test_payload.py`: `test_S1_a_file_that_lands_inside_the_grace_is_read`, `test_S1_a_file_that_never_lands_ends_the_wait_at_the_deadline`; `packages/agent-orchestrator/tests/test_gates.py`: `test_S2_a_payload_that_never_lands_warns_the_sender_before_the_lead`, `test_S3_a_sender_that_cannot_be_warned_goes_straight_to_the_lead` |
| S1–S14 | R62 | `packages/agent-orchestrator/tests/test_gates.py`: `SilentReviewerTests` (S1–S4, S9–S13); `packages/agent-orchestrator/tests/test_report_timing.py`: `test_S5_*`, `test_S6_a_report_on_disk_when_the_turn_ends_advances_the_worker_as_today`; `packages/agent-orchestrator/tests/test_decider.py`: `test_S5_a_worker_whose_turn_ended_idle_or_done_is_one_idle_request_from_the_watchdog`, `test_S11_blocked_and_decision_reports_are_handled_at_once`; `packages/agent-orchestrator/tests/test_envelope.py`: `test_S7_*`, `test_S8_*` (including the Lead's `answer` after an idle answerer); `packages/agent-orchestrator/tests/test_pr.py`: `test_S14_a_unit_waiting_for_its_merge_is_never_idle` |
| S1 | R19 | `packages/agent-orchestrator/tests/test_routes_driver.py`: `test_S1_a_run_with_no_route_config_keeps_the_legacy_model_decisions`, `test_S1_a_legacy_run_says_the_legacy_config_is_deprecated`; `packages/agent-orchestrator/tests/test_routes.py`: `test_S1_the_legacy_table_answers_the_same_models_as_before` |
| S2 | R20 | `packages/agent-orchestrator/tests/test_route_selection_run.py`: `test_S2_the_configured_backend_model_and_effort_are_chosen`, `test_S2_the_selected_route_is_logged_in_the_route_ledger`, `test_S2_the_selected_route_is_logged_in_the_event_log`, `test_S2_the_repository_policy_config_answers_when_no_file_is_named`, `test_S2_the_user_default_config_answers_when_no_other_config_is_named`, `test_S2_the_named_config_wins_over_the_repository_policy`, `test_S2_an_explicit_override_wins_over_the_config` |
| S3 | R4 | the route refusal tests, listed under R4 below |
| S4 | R21 | `packages/agent-orchestrator/tests/test_route_selection_run.py`: `test_S4_the_gate_review_and_pr_state_machine_stays_stable_under_a_new_provider_route`, `test_S4_the_routed_run_really_dispatched_on_the_new_provider` |
| S1 | R23 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S1_a_pr_that_conflicts_while_it_waits_for_its_merge_goes_back_to_draft_and_is_rebased` |
| S2 | R24 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S2_a_conflicting_draft_at_ready_for_ci_is_never_readied_before_its_rebase` |
| S3 | R25 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S3_a_pr_behind_while_it_waits_is_updated_stays_ready_and_wakes_again_on_its_new_head` |
| S4 | R26 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S4_an_unknown_merge_state_is_polled_again_with_no_action` |
| S5 | R27 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S5_red_checks_while_it_waits_are_a_ci_fix_round_on_a_draft` |
| S6 | R28 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S6_a_blocked_unstable_or_hooked_pr_keeps_waiting_with_no_gh_change` |
| S7 | R29 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S7_a_merge_wins_over_a_conflict_and_red_checks` |
| S8 | R30 | `packages/agent-orchestrator/tests/test_envelope.py`: `test_S8_a_disputed_change_drafts_the_pr_again_and_readies_it_after_the_re_review` |
| S9 | R31 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S9_a_failed_draft_undo_holds_the_fix_worker_until_it_succeeds` |
| S10 | R32 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S10_an_update_branch_failure_that_is_no_conflict_goes_to_the_lead` |
| S11 | R33 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S11_a_conflict_past_the_cap_goes_to_the_lead_and_leaves_the_pr_a_draft` |
| S12 | R34 | `packages/agent-orchestrator/tests/test_archive.py`: `test_S12_a_conflicting_archive_pr_is_rebased_with_no_review` |
| drive-restart-recovery-gaps/S10 | R51 | `packages/agent-orchestrator/tests/test_run.py`: `RecoveryTests.test_S2a_a_terminal_at_work_that_wait_does_not_list_is_stranded_not_finished` |
| drive-restart-recovery-gaps/S11 | R51 | `packages/agent-orchestrator/tests/test_run.py`: `RecoveryTests.test_S2a_a_finished_turn_whose_report_landed_goes_to_the_gates_not_to_finish` |
| drive-restart-recovery-gaps/S9 | R63 | `packages/agent-orchestrator/tests/test_wake.py`: `test_S7_an_answer_naming_another_attempt_exits_2_writes_nothing_and_names_the_expected_id` |
| drive-restart-recovery-gaps/S10 | R64 | `packages/agent-orchestrator/tests/test_run.py`: `RecoveryTests.test_S2a_a_terminal_at_work_that_wait_does_not_list_is_stranded_not_finished` |
| drive-restart-recovery-gaps/S11 | R65 | `packages/agent-orchestrator/tests/test_run.py`: `RecoveryTests.test_S2a_a_finished_turn_whose_report_landed_goes_to_the_gates_not_to_finish` |
| drive-restart-recovery-gaps/S12 | R66 | `packages/agent-orchestrator/tests/test_run.py`: `test_S12_a_not_dispatched_ticket_whose_cell_is_not_exactly_TO_DO_is_not_tried_again_in_the_run` |
| drive-restart-recovery-gaps/S12a | R67 | `packages/agent-orchestrator/tests/test_run.py`: `test_S12a_the_not_dispatched_wake_names_the_ticket_the_reason_and_the_exact_TO_DO_way_out` |
| drive-restart-recovery-gaps/S13 | R68 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S13_a_unit_the_idle_tick_skipped_while_requested_starts_a_fresh_working_window` |
| drive-restart-recovery-gaps/S13a | R69 | `packages/agent-orchestrator/tests/test_pr.py`: `test_S13a_a_drive_starts_with_an_empty_working_window_and_a_restart_resets_it` |
| yaml-eec/S29 | R70 | `packages/agent-orchestrator/tests/test_archive_yaml.py`: `test_S29_a_requirement_naming_several_scenarios_keeps_all_of_them` |
| orchestrator-audit-fixes/S1 | R71 | `packages/agent-orchestrator/tests/test_ledger.py`: `BoundedLockTests.test_S1_a_held_ledger_is_waited_out_with_jittered_backoff_and_then_booked`, `test_S1_the_backoff_grows_to_5_seconds_and_no_further`, `test_S1_the_driver_waits_for_a_ledger_a_lead_command_holds_and_books_the_change` |
| orchestrator-audit-fixes/S2 | R72 | `packages/agent-orchestrator/tests/test_ledger.py`: `BoundedLockTests.test_S2_*`; `packages/agent-orchestrator/tests/test_await.py`: `CrashGuardTests.test_S2_*` |
| orchestrator-audit-fixes/S3 | R72 | `packages/agent-orchestrator/tests/test_await.py`: `CrashGuardTests.test_S3_*` |
| orchestrator-audit-fixes/S4 | R76 | `packages/agent-orchestrator/tests/test_await.py`: `AwaitTests.test_S4_*` |
| orchestrator-audit-fixes/S5 | R75 | `packages/agent-orchestrator/tests/test_ledger.py`: `SnapshotTests.test_S5_*`, `AcceptsRevalidateTests.test_S5_accept_refuses_when_the_ledger_moved_after_it_was_read` |
| orchestrator-audit-fixes/S6 | R73 | `packages/agent-orchestrator/tests/test_ledger.py`: `BoundedLockTests.test_S6_*` |
| orchestrator-audit-fixes/S7 | R74 | `packages/agent-orchestrator/tests/test_ledger.py`: `KindMarkerTests.test_S7_*`, `RunMarkerTests.test_S7_a_run_creates_its_ledger_with_the_marker_before_any_file_a_worker_reads` |
| orchestrator-audit-fixes/S17 | R77 | `packages/agent-orchestrator/tests/test_plan.py`: `PlanTests.test_S17_*` |
| orchestrator-audit-fixes/S18 | R78 | `packages/agent-orchestrator/tests/test_runner.py`: `Containment.test_S18_*` |
| orchestrator-audit-fixes/S19 | R79 | `packages/agent-orchestrator/tests/test_runner.py`: `Containment.test_S19_*` |

### R4 — the route refusals

- `packages/agent-orchestrator/tests/test_routes.py`: `test_S3_a_route_for_an_unknown_class_is_refused`,
  `test_S3_a_route_with_an_unknown_effort_is_refused`,
  `test_S2_an_unsupported_backend_is_refused_with_an_explanation`
- `packages/agent-orchestrator/tests/test_routes_driver.py`:
  `test_S3_a_route_for_an_unknown_class_fails_the_run_before_any_worker_is_spawned`,
  `test_S2_an_invalid_route_config_fails_the_run_before_any_worker_is_spawned`

### R8, R9, R10, R11 — the routing and the steps

- `packages/agent-orchestrator/tests/test_routes.py`: `test_S1_a_route_may_name_the_pi_backend`,
  `test_S1_the_efforts_above_high_are_valid_efforts`,
  `test_S8_a_step_route_answers_its_own_step`,
  `test_S10_a_step_route_naming_an_unknown_step_is_refused_by_name`,
  `test_S10_a_step_route_naming_an_unknown_backend_is_refused_by_name`,
  `test_S10_a_step_route_naming_no_model_is_refused`
- `packages/agent-orchestrator/tests/test_route_selection.py`: `test_S1_an_override_may_name_the_pi_backend`,
  `test_S9_a_step_override_names_the_backend_model_and_effort`,
  `test_S10_a_step_override_for_an_unknown_step_is_refused`,
  `test_S10_a_step_override_on_an_unknown_backend_is_refused`
- `packages/agent-orchestrator/tests/test_route_selection_run.py`:
  `test_S1_a_route_naming_pi_and_the_top_effort_reaches_the_worker_spawn`,
  `test_S1_an_override_naming_pi_dispatches_a_pi_worker`,
  `test_S4_the_matrix_answers_the_effort_when_no_route_names_one`
- `packages/agent-orchestrator/tests/test_step_routes.py`: `test_S8_each_step_names_its_own_backend_model_and_effort`,
  `test_S8_a_step_route_for_the_implementer_overrides_the_class_route`,
  `test_S9_the_brain_names_one_backend_for_every_gate_step`,
  `test_S9_a_step_route_overrides_the_brain`,
  `test_S10_a_step_route_naming_an_unknown_step_fails_the_run_before_any_worker_is_spawned`,
  `test_S10_a_step_route_naming_an_unknown_backend_fails_the_run_before_any_worker_is_spawned`,
  `test_S10_a_step_override_naming_an_unknown_step_fails_the_run_before_any_worker_is_spawned`,
  `test_S3_a_gate_without_a_step_route_keeps_todays_effort`,
  `test_S3_a_step_route_names_the_gates_effort`

### R12, R13, R14, R15 — the report the contract asks for, the tree-sized calls, the Lead's alert, the merge wait

- `packages/agent-orchestrator/tests/test_contracts.py`: `test_S1_report_accepts_the_context_object_workers_write`,
  `test_S2_every_key_the_contract_example_names_is_a_report_field`
- `packages/agent-orchestrator/tests/test_gates.py`: `test_S3_worktree_add_gets_a_checkout_sized_timeout`
- `packages/agent-orchestrator/tests/test_wake.py`: `test_S4_S5_a_new_wake_is_sent_once_to_the_launching_terminal`,
  `test_S4_a_run_without_a_notify_terminal_sends_nothing`, `test_S6_a_failed_send_is_logged_and_the_run_goes_on`
- `packages/agent-orchestrator/tests/test_launch.py`: `test_S7_the_run_is_told_the_launching_terminal_to_alert`,
  `test_S7_a_run_launched_outside_a_terminal_alerts_no_one`
- `packages/agent-orchestrator/tests/test_pr.py`: `test_S14_a_unit_waiting_for_its_merge_is_never_idle`

### R22–R37 — a merged unit always completes

- `packages/agent-orchestrator/tests/test_checkpoint_rules.py`: `test_S1_…`, `test_S1a_…`, `test_S2_…`, `test_S3_…`, `test_S3a_…`, `test_S3b_…`, `test_S4_…`, `test_S8a_…` — the pure COMPLETE rules (R22–R28, R35)
- `packages/agent-orchestrator/tests/test_gates.py`: `test_S5_a_ready_report_that_fails…`, `test_S5a_report_refusals…`, `test_S5_a_report_the_ledger…` — the report-time refusal (R29, R30)
- `packages/agent-orchestrator/tests/test_merge.py`: `test_S6_…`, `test_S8_…` (two), `test_S9_cleanup_keeps…` — the merge wins, the missing checkout, the busy check (R31, R34, R36)
- `packages/agent-orchestrator/tests/test_pr.py`: `test_S7_…`, `test_S7a_…`, `test_S10_several_failing_checks…` — the early merge and the CI excerpt (R32, R33, R37)

## The ported suite

The suite arrived with the engine. Each file keeps the scenario ids it was written against, so a
failure names the behaviour it broke and not a line number in a file that moved.

| File | Requirement | Scenarios it answers |
|---|---|---|
| `test_agents.py` | R2, R5 | `orchestrate-lean-loop/S33` — a spawn that is refused or re-attached |
| `test_archive.py` | R2, R34 | `code-driven-orchestrator/S15`, `code-driven-orchestrator/S16`, `orchestrator-pr-merge-state/S12` — the driver-built archive, the gaps reviewer, the metrics, the archive PR's conflict asks the Lead |
| `test_baselines.py` | R2 | `orchestration-baselines/S0`, `orchestration-baselines/S3` — the repaired vertical slice, the bounded benchmark |
| `test_compat.py` | R2 | `orchestrate-lean-loop/S34` — a run directory written by the previous version |
| `test_contracts.py` | R2 | `code-driven-orchestrator/S6a`, `code-driven-orchestrator/S11`, `orchestrate-lean-loop/S1`, `orchestrate-lean-loop/S12`–`S17`, `orchestrate-lean-loop/S26` — the report, review and findings contracts |
| `test_decider.py` | R2 | `code-driven-orchestrator/S11`–`S13` — the decider and the pause it causes |
| `test_envelope.py` | R2, R30 | `orchestrate-lean-loop/S10`, `orchestrate-lean-loop/S12`–`S17`, `orchestrate-lean-loop/S26`, `orchestrator-pr-merge-state/S8` — the review envelope |
| `test_events.py` | R2 | `orchestrate-lean-loop/S27` — the event log |
| `test_evidence.py` | R2 | `orchestration-baselines/S2` — the per-attempt evidence |
| `test_fake_gh.py` | R2 | the fake `gh` — a draft PR that never goes ready; scripted draft views, checks while ready and a failing draft undo |
| `test_gates.py` | R2 | `code-driven-orchestrator/S5`–`S7` — the report, the review, the fix round |
| `test_ledger.py` | R71–R75 | `orchestrator-audit-fixes/S1`, `S2`, `S5`–`S7` — the shared ledger lock, its deadline, the snapshot and the kind marker |
| `test_await.py` | R72, R76 | `orchestrator-audit-fixes/S2`–`S4` — the crash guard and what `await` says of a run whose driver died |
| `test_launch.py` | R2, R6 | `code-driven-orchestrator/S17`, `orchestrate-lean-loop/S49`, `orchestrate-lean-loop/S50` — the pane the run starts in |
| `test_merge.py` | R2 | `code-driven-orchestrator/S6`, `code-driven-orchestrator/S6a`, `code-driven-orchestrator/S8`–`S10` — the merge and the cleanup |
| `test_orchestrate.py` | R2 | the ledger: `claim`, `bind`, `checkpoint`, `release`, `show`, `context`, `metrics` |
| `test_payload.py` | R18 | `orchestrator-payload-race/S1` — the bounded wait for a report's payload |
| `test_pi_backend.py` | R2 | `pi-worker-driver/S1`–`S6` — the Pi worker backend |
| `test_pr.py` | R2, R23–R29, R31–R33 | `orchestrate-lean-loop/S6`, `orchestrate-lean-loop/S20`–`S24`, `orchestrator-pr-merge-state/S1`–`S7`, `S9`–`S11` — the pull request, its checks and its merge state |
| `test_reopen.py` | R2 | `code-driven-orchestrator/S4` — a ticket the plan sets back to TO-DO |
| `test_report_timing.py` | R2 | `orchestration-baselines/S0` — the report that lands as the turn ends |
| `test_reuse.py` | R2 | `orchestrate-lean-loop/S8`, `orchestrate-lean-loop/S9`, `orchestrate-lean-loop/S11` — the re-review and the panes it stops |
| `test_rounds.py` | R2 | `orchestrate-lean-loop/S18`, `orchestrate-lean-loop/S32` — the fix-round cap |
| `test_route_selection.py` | R2, R4 | `orchestrator-routing-decoupling/S2`, `orchestrator-routing-decoupling/S3` — the route override |
| `test_route_selection_run.py` | R2, R4 | `orchestrator-routing-decoupling/S2`–`S4` — the route a run resolves |
| `test_routes.py` | R2, R4 | `orchestrator-routing-decoupling/S1`, `orchestrator-routing-decoupling/S3` — the typed route schema |
| `test_routes_driver.py` | R2, R4 | `orchestrator-routing-decoupling/S1`, `orchestrator-routing-decoupling/S3` — the route config a run reads |
| `test_step_routes.py` | R2, R8–R11 | `orchestrator-routing-effort-role/S3`, `S8`–`S10` — the step routes |
| `test_run_lock.py` | R17 | `orchestrator-run-lock/S1`, `S2` — one driver per plan and repository |
| `test_run.py` | R2, R5 | `code-driven-orchestrator/S1`–`S4`, `code-driven-orchestrator/S14` — the dispatch loop and its restart |
| `test_swap.py` | R2 | `orchestrate-lean-loop/S19` — the agent below the context reserve |
| `test_wait_checks.py` | R2 | the CI gate's wait |
| `test_wake.py` | R2, R59 | `orchestrate-lean-loop/S1`–`S5`, `orchestrate-lean-loop/S7`, `orchestrate-lean-loop/S31`, `drive-restart-recovery/S7` — the requests, the wakes and the decisions |
