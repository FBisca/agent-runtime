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
- **R9** WHEN a step route names a step — `implementer`, `reviewer`, `gaps` or `archive` — THEN that step's spawn carries the step's backend, model and effort; `--brain-cli` still names one backend for every gate step, and a step route overrides it. (from orchestrator-routing-effort-role/S8, S9)
- **R10** WHEN a step route names an unknown step, backend, model or effort, THEN the run fails before any worker is spawned, naming the value. (from orchestrator-routing-effort-role/S10)
- **R11** WHEN no step route names a gate, THEN the gate keeps the effort it has always had: the reviewer `high`, the gaps reviewer `medium`, the archive worker the matrix's `low`. (from orchestrator-routing-effort-role/S3)
- **R12** WHEN a worker writes the report the driver's `CONTRACT` describes, `"context"` included, THEN `Report` accepts it: every top-level key the contract's example names is a `Report` field. (from orchestrator-lead-alerts/S1, S2)
- **R13** WHEN the driver runs a git call that walks or writes the whole tree (`worktree add`, `status`), THEN that call's timeout is sized for a large repository, not `run()`'s 15-second default. (from orchestrator-lead-alerts/S3)
- **R14** WHEN `launch` runs inside an hc terminal, THEN the run is given that terminal, and each new wake is sent to it once with `hc send` (never `--force` or `interrupt`); a send that fails is logged as `notify-failed` and the run goes on; a run launched outside a terminal sends nothing. (from orchestrator-lead-alerts/S4–S7)
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
- **R22** WHEN an agent's turn ends while the runtime still lists its assignment as working (`runtime_state` idle or
  done), THEN it is a live agent, not an exit: a reviewer advances only on its own `complete`, even with a valid
  review file on disk; a worker stays pending, except that a valid report for its task already on disk advances it as
  before; an answerer stays working. None is resumed, and none replaces its assignment. Only the timed idle watchdog
  (baseline, then two unchanged ticks) asks the Lead: one `idle` request and one decision wake, naming the agent, the
  file it owes, how long it has been quiet and `decide <plan> <unit> retry | answer | skip`. A failed, exited or
  gone agent, a restart orphan, `unreported`/`error`, and a `blocked` or `decision` report are still handled at once.
  A `complete` that announces a file the driver cannot read keeps the R18 grace and capped recovery, and each
  recovery message names the file to write or repair and says to report complete under the current assignment. The
  reviewer brief says the reviewer may wait on background jobs, that ending a turn does not finish the review, and
  that it writes the review after those jobs finish and then reports complete with the brief's assignment id. A
  reviewer `complete` after its unit's request is open leaves the request open. (from orchestrator-silent-idle/S1–S14)

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
| S1–S14 | R22 | `packages/agent-orchestrator/tests/test_gates.py`: `SilentReviewerTests` (S1–S4, S9–S13); `packages/agent-orchestrator/tests/test_report_timing.py`: `test_S5_*`, `test_S6_a_report_on_disk_when_the_turn_ends_advances_the_worker_as_today`; `packages/agent-orchestrator/tests/test_decider.py`: `test_S5_a_worker_whose_turn_ended_idle_or_done_is_one_idle_request_from_the_watchdog`, `test_S11_blocked_and_decision_reports_are_handled_at_once`; `packages/agent-orchestrator/tests/test_envelope.py`: `test_S7_*`, `test_S8_*`; `packages/agent-orchestrator/tests/test_pr.py`: `test_S14_a_unit_waiting_for_its_merge_is_never_idle` |
| S1 | R19 | `packages/agent-orchestrator/tests/test_routes_driver.py`: `test_S1_a_run_with_no_route_config_keeps_the_legacy_model_decisions`, `test_S1_a_legacy_run_says_the_legacy_config_is_deprecated`; `packages/agent-orchestrator/tests/test_routes.py`: `test_S1_the_legacy_table_answers_the_same_models_as_before` |
| S2 | R20 | `packages/agent-orchestrator/tests/test_route_selection_run.py`: `test_S2_the_configured_backend_model_and_effort_are_chosen`, `test_S2_the_selected_route_is_logged_in_the_route_ledger`, `test_S2_the_selected_route_is_logged_in_the_event_log`, `test_S2_the_repository_policy_config_answers_when_no_file_is_named`, `test_S2_the_user_default_config_answers_when_no_other_config_is_named`, `test_S2_the_named_config_wins_over_the_repository_policy`, `test_S2_an_explicit_override_wins_over_the_config` |
| S3 | R4 | the route refusal tests, listed under R4 below |
| S4 | R21 | `packages/agent-orchestrator/tests/test_route_selection_run.py`: `test_S4_the_gate_review_and_pr_state_machine_stays_stable_under_a_new_provider_route`, `test_S4_the_routed_run_really_dispatched_on_the_new_provider` |

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

## The ported suite

The suite arrived with the engine. Each file keeps the scenario ids it was written against, so a
failure names the behaviour it broke and not a line number in a file that moved.

| File | Requirement | Scenarios it answers |
|---|---|---|
| `test_agents.py` | R2, R5 | `orchestrate-lean-loop/S33` — a spawn that is refused or re-attached |
| `test_archive.py` | R2 | `code-driven-orchestrator/S15`, `code-driven-orchestrator/S16` — the archive worker, the gaps reviewer, the metrics |
| `test_baselines.py` | R2 | `orchestration-baselines/S0`, `orchestration-baselines/S3` — the repaired vertical slice, the bounded benchmark |
| `test_compat.py` | R2 | `orchestrate-lean-loop/S34` — a run directory written by the previous version |
| `test_contracts.py` | R2 | `code-driven-orchestrator/S6a`, `code-driven-orchestrator/S11`, `orchestrate-lean-loop/S1`, `orchestrate-lean-loop/S12`–`S17`, `orchestrate-lean-loop/S26` — the report, review and findings contracts |
| `test_decider.py` | R2 | `code-driven-orchestrator/S11`–`S13` — the decider and the pause it causes |
| `test_envelope.py` | R2 | `orchestrate-lean-loop/S10`, `orchestrate-lean-loop/S12`–`S17`, `orchestrate-lean-loop/S26` — the review envelope |
| `test_events.py` | R2 | `orchestrate-lean-loop/S27` — the event log |
| `test_evidence.py` | R2 | `orchestration-baselines/S2` — the per-attempt evidence |
| `test_fake_gh.py` | R2 | the fake `gh` — a draft PR that never goes ready |
| `test_gates.py` | R2 | `code-driven-orchestrator/S5`–`S7` — the report, the review, the fix round |
| `test_launch.py` | R2, R6 | `code-driven-orchestrator/S17`, `orchestrate-lean-loop/S49`, `orchestrate-lean-loop/S50` — the pane the run starts in |
| `test_merge.py` | R2 | `code-driven-orchestrator/S6`, `code-driven-orchestrator/S6a`, `code-driven-orchestrator/S8`–`S10` — the merge and the cleanup |
| `test_orchestrate.py` | R2 | the ledger: `claim`, `bind`, `checkpoint`, `release`, `show`, `context`, `metrics` |
| `test_payload.py` | R18 | `orchestrator-payload-race/S1` — the bounded wait for a report's payload |
| `test_pi_backend.py` | R2 | `pi-worker-driver/S1`–`S6` — the Pi worker backend |
| `test_pr.py` | R2 | `orchestrate-lean-loop/S6`, `orchestrate-lean-loop/S20`–`S24` — the pull request and its checks |
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
| `test_wake.py` | R2 | `orchestrate-lean-loop/S1`–`S5`, `orchestrate-lean-loop/S7`, `orchestrate-lean-loop/S31` — the requests, the wakes and the decisions |
