# Harness adapters
Paths: packages/agent-runtime/src/agent_runtime/agent_runtime.py, packages/agent-runtime/src/agent_runtime/host_policy.py, packages/agent-runtime/src/agent_runtime/hc_tools.py, hooks/, tests/test_pi_lead.py

A harness is the coding agent that owns an MCP session: Claude Code, Codex or Pi. The runtime contract
(one set of delegation verbs, the dispatch store, the worker lifecycle) lives in
[agent-runtime.md](agent-runtime.md); this spec covers what each harness contributes to it - how the
harness is identified, what binds a session to a team, and what the runtime lets that harness mutate.

Claude Code and Codex are gated by their own PreToolUse hooks, which the runtime treats as defence in
depth and never as the authority. Pi ships no such gate by default, so for a Pi session the server is
the authority: `packages/agent-runtime/src/agent_runtime/host_policy.py` decides what the session may mutate, from a local policy file
the operator writes.

The MCP server and the dispatch store are host-agnostic. A harness is identified by the process that
started the server (its direct parent) and, for the session's generation, by the nearest harness
ancestor in the process tree. Pi is a lead **and** a worker: a lead delegates to Claude, Codex or Pi
workers, and a Pi worker runs as the `pi` family, reports through Pi's MCP registration and never
starts or ends a worker of its own. Pi has no token accounting.

## Trust boundary

A local stdio MCP connection and process ancestry do not authenticate an arbitrary request. The runtime
therefore binds a lead to a scope (the Herdr socket), a terminal (the lead's own `hc` terminal) and a
generation (the harness process's `pid:lstart`), and checks that binding on every sensitive operation.
The store's own rules then reject a stale generation, a reused address and a foreign receipt.

An environment variable is a hint, never an identity: `PI_CODING_AGENT` says a Pi session started this
process, but only the process ancestry, the durable store and the operator's policy grant a role. A
compromised child process, a malicious project prompt, a symlinked checkout, a reconnect and a restart
are all in scope for the requirements below; a Pi-side permission extension is defence in depth and
never the authority.

## Requirements

- **R165** WHEN a session runs under Pi, THEN `agent-runtime resolve` reports `cli=pi` and the runtime never substitutes Claude for it. (from pi-lead-adapter/S1)
- **R166** WHEN Pi is the harness, THEN the lead's generation is the Pi process's `<pid>:<lstart>`, so an MCP server restart keeps the same team and a Pi restart starts a new one. (from pi-lead-adapter/S1)
- **R167** WHEN a Pi session has no visible Pi process, THEN the runtime refuses it with an explicit error instead of accepting a session id from the environment. (from pi-lead-adapter/S4)
- **R168** WHEN the environment claims more than one harness and no harness process is visible, THEN the runtime refuses the session as ambiguous. (from pi-lead-adapter/S4)
- **R169** WHEN a Claude Code or Codex session is resolved, THEN its identity is unchanged: the nearest ancestor's `pid:lstart`, and the session id fallback only when no harness ancestor is visible. (from pi-lead-adapter/S5)
- **R170** WHEN the runtime decides which harness owns a session, THEN it uses the process that started the server (its direct parent), and a session that claims Pi without a Pi parent is treated as Pi so a failed identification is never downgraded to another harness. (from pi-lead-adapter/S4)
- **R171** WHEN a Pi lead calls a mutating tool and no policy authorises mutations, THEN the call is denied and no worker is started. (from pi-lead-adapter/S4)
- **R172** WHEN a Pi lead spawns without naming a worker backend, THEN the call is denied: the runtime never picks a provider for it. (from pi-lead-adapter/S4)
- **R173** WHEN a Pi lead names a worker backend that the policy does not authorise, THEN the spawn is denied and no worker is started. (from pi-lead-adapter/S4)
- **R174** WHEN a Pi lead spawns a worker outside the authorised directories, THEN the spawn is denied and no worker is started. (from pi-lead-adapter/S4)
- **R175** WHEN a Pi lead calls `status`, `collect`, `message`, `resume` or `stop` for a worker outside its own team, THEN the call is denied. (from pi-lead-adapter/S3)
- **R176** WHEN a Pi session runs without `AGENT_RUNTIME_DISPATCH=1`, THEN every call is denied, because without the durable store there is no identity, no attribution and no scope. (from pi-lead-adapter/S2)
- **R177** WHEN the operator's policy file is malformed, THEN the Pi host is refused rather than trusted. (from pi-lead-adapter/S4)
- **R178** WHEN a Pi session resolves as a worker, THEN it is accepted inside its own team with the narrow worker tool set (R188), instead of being refused outright. (from pi-lead-adapter/S1, amended by pi-worker/T3)
- **R179** WHEN a Pi lead spawns a Claude or Codex worker, THEN the worker runs through the existing dispatch store, bound to the Pi lead's team and assignment id. (from pi-lead-adapter/S2)
- **R180** WHEN that worker reports, THEN `wait(target=all)` returns exactly one result whose recipient is the Pi lead's terminal. (from pi-lead-adapter/S2)
- **R181** WHEN the Pi lead's MCP connection drops and reconnects, THEN it sees its own team and the undelivered report, and the task is not dispatched twice. (from pi-lead-adapter/S3)
- **R182** WHEN a worker replays a report with the same `message_id`, THEN the report is delivered once. (from pi-lead-adapter/S2)
- **R183** WHEN a duplicate spawn names a live worker, THEN the spawn is refused and no second worker starts. (from pi-lead-adapter/S2)
- **R184** WHEN a Pi lead's wait is cancelled, THEN the worker keeps running and is not duplicated. (from pi-lead-adapter/S2)
- **R185** WHEN one worker reports while another still works, THEN `wait(target=all)` returns that report and names the unfinished assignment. (from pi-lead-adapter/S2, issue #31)
- **R186** WHEN the Claude Code and Codex integrations run, THEN their session identity, usage binding and resume behaviour are unchanged. (from pi-lead-adapter/S5)
- **R187** WHEN the operator writes the host policy, THEN it can grant mutations, worker backends and directories and nothing else: it never grants a harness a role. (from pi-lead-adapter/S4)
- **R188** WHEN a Pi worker calls `report`, `message`, `wait`, `status`, `collect`, `list` or a read-only `task list` inside its own team, THEN the runtime accepts the call instead of refusing the session. (from pi-worker/S3, pi-worker/S5)
- **R189** WHEN a Pi worker calls `spawn`, `stop` or `resume`, THEN the runtime refuses the call with a reason that names the host policy: a Pi worker starts no worker and ends no worker. (from pi-worker/S4)
- **R190** WHEN a spawn names the `pi` family, THEN the runtime launches interactive `pi --approve --model <model> --thinking <effort>` without `--print`, with the task last, and the task carries the role's content and the report protocol; an effort Pi does not know is refused before anything starts. (from pi-worker/S1, pi-worker/S6; orchestrator-routing-effort-role/S5, S12)
- **R191** WHEN a spawn names the `pi` family, THEN the worker keeps `MINIMAX_API_KEY` and the Pi provider variables, and a claude or codex spawn carries neither; the authorised-directory guarantee is unchanged for a Pi spawn. (from pi-worker/S2, pi-worker/S7, pi-worker/S8)

## Operator policy

`$XDG_CONFIG_HOME/agent-runtime/hosts.json` (default `~/.config/agent-runtime/hosts.json`,
override `AGENT_RUNTIME_HOSTS`):

```json
{
  "hosts": {
    "pi": {
      "mutations": true,
      "backends": ["claude-minimax", "codex", "claude"],
      "paths": ["/Users/you/developer"]
    }
  }
}
```

`mutations` allows the mutating tools (`spawn`, `message`, `resume`, `stop`, `report`, and `task`
except its read-only listing). `backends` names the worker backends the lead may start (built-ins or
configured profiles); an empty list starts no worker, so no company-funded model is used until the
operator says so. `paths` limits where a worker may run; without it only the lead's own working
directory is allowed. The file is read on every mutating call, so a change needs no restart.

## Coverage

| Scenario | Requirement | Test |
|---|---|---|
| S1 | R165, R166, R178 | `tests/test_pi_lead.py`: `test_pi_argv_is_a_harness_and_other_programs_are_not`, `test_pi_marker_never_resolves_to_claude`, `test_pi_generation_is_the_pi_process_pid_and_start_time`, `test_S1_pi_lead_is_identified_as_pi_with_a_stable_generation` |
| S2 | R176, R179, R180, R182, R183, R184, R185 | `tests/test_pi_lead.py`: `test_S2_pi_lead_delegates_to_a_claude_worker_and_collects_one_result`, `test_S2_pi_lead_delegates_to_a_codex_worker`, `test_S2_a_repeated_report_is_delivered_once`, `test_S2_a_duplicate_spawn_does_not_start_a_second_worker`, `test_S2_a_report_wakes_the_wait_while_another_worker_still_works`, `test_a_cancelled_wait_leaves_the_worker_running`, `test_S4_a_pi_lead_needs_the_durable_store` |
| S3 | R175, R181 | `tests/test_pi_lead.py`: `test_S3_a_disconnected_pi_lead_reconnects_to_its_own_team`, `test_S3_a_pi_lead_cannot_reach_another_leads_worker`, `test_S1_a_new_pi_process_is_a_new_generation_and_the_old_one_goes_stale` |
| S4 | R167, R168, R170, R171, R172, R173, R174, R177, R187 | `tests/test_pi_lead.py`: `PiPolicyTests` (the directory and the omitted backend of R172 and R174 are proved there, in process), `test_S4_a_pi_lead_may_not_mutate_without_a_policy`, `test_S4_a_pi_lead_may_not_choose_a_backend_the_operator_did_not_authorize`, `test_a_pi_session_without_a_pi_process_is_refused`, `test_an_ambiguous_pi_session_is_refused` |
| S5 | R169, R186 | `tests/test_pi_lead.py`: `test_claude_and_codex_keep_their_own_identity`, `test_claude_and_codex_keep_their_session_id_fallback`, `test_claude_and_codex_hosts_keep_their_unrestricted_behaviour`, plus the whole existing suite |
| pi-worker/S1 | R190 | `tests/test_pi_worker.py`: `test_S1_a_pi_spawn_builds_the_pi_argv_with_the_task_last`, `test_S1_a_pi_spawn_without_a_model_is_interactive`, `test_S1_the_pi_task_carries_the_role_and_the_report_protocol`, `test_S1_a_pi_spawn_with_no_role_states_the_protocol_without_a_role_line`, `test_S1_the_pi_family_is_offered_in_the_spawn_schema` |
| pi-worker/S2 | R191 | `tests/test_pi_worker.py`: `test_S2_a_pi_spawn_keeps_the_minimax_and_pi_provider_variables`, `test_S2_a_claude_or_codex_spawn_loses_them`, `test_S2_the_scrub_list_covers_the_pi_provider_variables` |
| pi-worker/S3 | R188 | `tests/test_pi_worker.py`: `test_S3_a_pi_worker_may_report`, `test_S3_a_pi_worker_may_read_its_own_team` |
| pi-worker/S4 | R189 | `tests/test_pi_worker.py`: `test_S4_a_pi_worker_may_not_spawn_stop_or_resume`, `test_S4_a_pi_worker_may_not_create_a_team_task`, `test_S4_a_pi_worker_without_a_policy_may_not_report`, `test_S4_a_pi_worker_may_not_spawn_another_worker` |
| pi-worker/S5 | R188 | `tests/test_pi_worker.py`: `test_S5_a_pi_worker_may_not_reach_a_worker_outside_its_team` |
| pi-worker/S6 | R190 | `tests/test_pi_worker.py`: `test_S6_the_effort_maps_onto_pi_thinking`, `test_S6_an_effort_pi_does_not_know_is_refused_before_anything_starts` |
| pi-worker/S7 | R191 | `tests/test_pi_lead.py`: `PiPolicyTests::test_policy_limits_the_directory_a_worker_may_run_in`, `PiPolicyTests::test_policy_allows_the_named_backend_and_nothing_else` (the end-to-end copies in `tests/test_pi_worker.py` are deleted; these keep the guarantee in process) |
| pi-worker/S8 | R191 | `tests/test_pi_worker.py`: `test_S8_a_pi_worker_reports_once_and_the_lead_team_receives_it` |
| pi-worker/S9 | R191 | `docs/pi.md` (the local configuration) and `~/.pi/agent/mcp.json` (the registration) |

## Requirements: pi-lead-rhythm

- **R192** WHEN a lead has undelivered messages or unseen working assignments, THEN the Claude and Codex Stop hooks and the Pi stop dialect refuse the same state with the same reason, and the Pi answer is the neutral `{"block", "reason"}` rather than the Claude shape (from pi-lead-rhythm/S1)
- **R193** WHEN `agent-runtime-hook stop --cli pi --dialect json` runs, THEN it prints `{"block": <bool>, "reason": <text>}` on stdout, and without the dialect the Claude/Codex `{"decision": "block"}` is unchanged (from pi-lead-rhythm/S2)
- **R194** WHEN a Pi lead's turn ends with open work and its stop-guard budget is unspent, THEN the extension turns the block into one more provider request carrying the reason, and an allowed stop adds nothing (from pi-lead-rhythm/S3)
- **R195** WHEN a Pi worker's turn ends with its assignment unreported, THEN the extension requests one more provider request naming both ways out, `report complete` and `report blocked` (from pi-lead-rhythm/S4)
- **R196** WHEN a Claude or Codex worker's turn ends with its assignment unreported, THEN its Stop hook blocks with the same reason shape the lead's guard uses, and the worker can still leave by reporting blocked (from pi-lead-rhythm/S5)
- **R197** WHEN a lead with undelivered reports calls `spawn`, `message`, `resume` or `stop`, THEN the runtime refuses, naming the count and `wait(all)` as the way out, and no worker starts (from pi-lead-rhythm/S6)
- **R198** WHEN the driver appends a wake the lead has not seen, THEN the Pi extension pushes it with its text, and a wake already seen or an empty line pushes nothing (from pi-lead-rhythm/S7)
- **R199** WHEN a Pi session runs a shell command that sleeps 60 seconds or longer, THEN the call is refused with a reason naming `wait(all)`, and a sleep shorter than 60 seconds is untouched (from pi-lead-rhythm/S8)
- **R200** WHEN the runtime resolves a harness, THEN it reports that harness's rhythm capabilities — can refuse a stop, can park a wait, can push a wake, request cap — from one table, and every harness it can resolve has a row (from pi-lead-rhythm/S9)
- **R201** WHEN a lead's stop-guard budget is spent under one host, THEN another host sees the same spent budget, so switching host grants no fresh blocks (from pi-lead-rhythm/S10)

## Coverage: pi-lead-rhythm

| Requirement | Scenario | Test nodes |
|---|---|---|
| R192 | S1 | `tests/test_hooks.py::StopGuardTests::test_S1_the_claude_codex_and_pi_stops_refuse_one_state_with_one_reason`<br>`acceptance/rhythm/test_lead_rhythm.py` |
| R193 | S2 | `tests/test_hooks.py::StopGuardTests::test_S2_the_pi_dialect_prints_a_neutral_answer_and_the_claude_shape_is_unchanged` |
| R194 | S3 | `tests/test_pi_extension.py::StopDecisionTests::test_S3_a_block_becomes_one_more_provider_request_with_the_hook_reason`<br>`tests/test_pi_extension.py::StopDecisionTests::test_S3_the_decision_is_asked_of_the_documented_hook_entry`<br>`tests/test_pi_extension.py::StopDecisionTests::test_S3_an_allowed_stop_adds_nothing`<br>`tests/test_pi_extension.py::StopDecisionTests::test_S3_a_hook_that_cannot_answer_allows_the_stop`<br>`tests/test_pi_extension.py::StopDecisionTests::test_S3_without_an_override_the_entry_is_this_checkouts_own`<br>`tests/test_pi_extension.py::StopDecisionTests::test_S3_a_draft_another_turn_end_handler_proposed_survives_the_block`<br>`acceptance/rhythm/test_pi_extension_guard.py` |
| R195 | S4 | `tests/test_pi_extension.py::WorkerStopTests::test_S4_an_unreported_assignment_asks_for_one_more_request_naming_both_ways_out` |
| R196 | S5 | `tests/test_hooks.py::StopGuardTests::test_S5_a_worker_with_its_own_unreported_assignment_is_blocked_until_it_reports`<br>`tests/test_hooks.py::StopGuardTests::test_S5_a_workers_parked_wait_does_not_exempt_its_own_assignment` |
| R197 | S6 | `tests/test_rhythm_authority.py::RhythmAuthorityTests::test_S6_lead_with_undelivered_reports_is_refused_every_mutating_verb`<br>`tests/test_rhythm_authority.py::RhythmAuthorityTests::test_S6_wait_all_delivers_and_the_verbs_are_allowed_again`<br>`packages/agent-orchestrator/tests/test_intents.py::UnreadReport::test_S6_a_report_landing_before_the_next_spawn_is_read_and_the_unit_dispatches`<br>`tests/test_rhythm_authority.py::ForcedLeadTests::test_S6_the_dispatch_loops_forced_lead_call_is_not_refused_and_the_report_is_kept` |
| R198 | S7 | `tests/test_pi_extension.py::WakePushTests::test_S7_an_unseen_wake_is_pushed_with_its_text`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_wake_already_seen_is_pushed_once_even_after_a_restart`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_an_empty_line_pushes_nothing`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_new_wake_after_a_seen_one_is_pushed`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_line_a_crash_cut_short_pushes_nothing`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_wakes_appended_before_one_poll_are_pushed_in_the_order_the_driver_booked_them`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_worker_session_pushes_nothing_and_does_not_spend_the_leads_wake`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_session_does_not_push_another_runs_wake`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_an_explicitly_named_wake_file_still_belongs_to_a_lead`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_session_start_pushes_what_the_run_already_wrote`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_sibling_run_whose_pane_shares_a_prefix_stays_with_its_own_lead`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_pane_whose_address_is_this_terminal_of_its_own_is_this_runs`<br>`tests/test_pi_extension.py::WakePushTests::test_S7_a_run_whose_pane_name_the_driver_truncated_is_still_its_own`<br>`acceptance/rhythm/test_pi_extension_guard.py` |
| R199 | S8 | `tests/test_pi_extension.py::LongSleepTests::test_S8_a_sleep_of_a_minute_or_more_is_refused_naming_wait_all`<br>`tests/test_pi_extension.py::LongSleepTests::test_S8_a_sleep_shorter_than_a_minute_is_untouched`<br>`tests/test_pi_extension.py::LongSleepTests::test_S8_the_shapes_of_a_long_sleep_are_all_refused`<br>`tests/test_pi_extension.py::LongSleepTests::test_S8_the_shapes_of_a_short_or_absent_sleep_are_all_untouched`<br>`tests/test_pi_extension.py::LongSleepTests::test_S8_another_tool_is_never_refused` |
| R200 | S9 | `tests/test_rhythm.py::CapabilityTests::test_S9_every_harness_the_runtime_resolves_has_a_rhythm_row`<br>`tests/test_rhythm.py::CapabilityTests::test_S9_the_runtime_reports_the_row_of_the_harness_it_resolves` |
| R201 | S10 | `tests/test_hooks.py::StopGuardTests::test_S10_a_spent_budget_is_spent_for_every_host_of_one_terminal` |
