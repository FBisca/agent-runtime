# Harness adapters
Paths: server/agent_runtime.py, server/host_policy.py, server/hc_tools.py, hooks/, tests/test_pi_lead.py

A harness is the coding agent that owns an MCP session: Claude Code, Codex or Pi. The runtime contract
(one set of delegation verbs, the dispatch store, the worker lifecycle) lives in
[agent-runtime.md](agent-runtime.md); this spec covers what each harness contributes to it - how the
harness is identified, what binds a session to a team, and what the runtime lets that harness mutate.

Claude Code and Codex are gated by their own PreToolUse hooks, which the runtime treats as defence in
depth and never as the authority. Pi ships no such gate by default, so for a Pi session the server is
the authority: `server/host_policy.py` decides what the session may mutate, from a local policy file
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
- **R190** WHEN a spawn names the `pi` family, THEN the runtime launches interactive `pi --model <model> --thinking <effort>` without `--print`, with the task last, and the task carries the role line and the report protocol; an effort Pi does not know is refused before anything starts. (from pi-worker/S1, pi-worker/S6)
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
| S4 | R167, R168, R170, R171, R172, R173, R174, R177, R187 | `tests/test_pi_lead.py`: `PiPolicyTests`, `test_S4_a_pi_lead_may_not_mutate_without_a_policy`, `test_S4_a_pi_lead_may_not_choose_a_backend_the_operator_did_not_authorize`, `test_S4_a_pi_lead_may_not_start_a_worker_outside_the_authorised_directory`, `test_S4_a_pi_lead_may_not_omit_the_backend`, `test_a_pi_session_without_a_pi_process_is_refused`, `test_an_ambiguous_pi_session_is_refused` |
| S5 | R169, R186 | `tests/test_pi_lead.py`: `test_claude_and_codex_keep_their_own_identity`, `test_claude_and_codex_keep_their_session_id_fallback`, `test_claude_and_codex_hosts_keep_their_unrestricted_behaviour`, plus the whole existing suite |
| pi-worker/S1 | R190 | `tests/test_pi_worker.py`: `test_S1_a_pi_spawn_builds_the_pi_argv_with_the_task_last`, `test_S1_a_pi_spawn_without_a_model_is_interactive`, `test_S1_the_pi_task_carries_the_role_and_the_report_protocol`, `test_S1_a_pi_spawn_with_no_role_states_the_protocol_without_a_role_line`, `test_S1_the_pi_family_is_offered_in_the_spawn_schema` |
| pi-worker/S2 | R191 | `tests/test_pi_worker.py`: `test_S2_a_pi_spawn_keeps_the_minimax_and_pi_provider_variables`, `test_S2_a_claude_or_codex_spawn_loses_them`, `test_S2_the_scrub_list_covers_the_pi_provider_variables` |
| pi-worker/S3 | R188 | `tests/test_pi_worker.py`: `test_S3_a_pi_worker_may_report`, `test_S3_a_pi_worker_may_read_its_own_team` |
| pi-worker/S4 | R189 | `tests/test_pi_worker.py`: `test_S4_a_pi_worker_may_not_spawn_stop_or_resume`, `test_S4_a_pi_worker_may_not_create_a_team_task`, `test_S4_a_pi_worker_without_a_policy_may_not_report`, `test_S4_a_pi_worker_may_not_spawn_another_worker` |
| pi-worker/S5 | R188 | `tests/test_pi_worker.py`: `test_S5_a_pi_worker_may_not_reach_a_worker_outside_its_team` |
| pi-worker/S6 | R190 | `tests/test_pi_worker.py`: `test_S6_the_effort_maps_onto_pi_thinking`, `test_S6_an_effort_pi_does_not_know_is_refused_before_anything_starts` |
| pi-worker/S7 | R191 | `tests/test_pi_worker.py`: `test_S7_a_pi_spawn_outside_the_authorized_directory_is_refused`, `test_S7_a_pi_spawn_needs_an_authorized_backend` |
| pi-worker/S8 | R191 | `tests/test_pi_worker.py`: `test_S8_a_pi_worker_reports_once_and_the_lead_team_receives_it` |
| pi-worker/S9 | R191 | `README.md` (the local configuration) and `~/.pi/agent/mcp.json` (the registration) |
