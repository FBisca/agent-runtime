# Python workspace contracts

Paths: pyproject.toml, uv.lock, packages/agent-runtime/pyproject.toml, packages/agent-orchestrator/pyproject.toml, .github/workflows/ci.yml, tests/test_workspace_isolation.py, tests/test_orchestrator_contract.py, tests/test_rollback_contract.py

The runtime and the orchestrator are two installable members of one uv workspace. The runtime stays
standard-library only in production; the orchestrator owns the typed, Pydantic-backed contracts and the
YAML route policy. The runtime's behaviour itself lives in [agent-runtime.md](agent-runtime.md), and the
harnesses in [harness-adapters.md](harness-adapters.md); this spec covers the packaging that carries them.

The runtime's own modules carry its contracts and stay inside its member, on the standard library alone:
`hc_tools.py` is the spawn and delegation surface, `pi_mcp.py` owns the registration a Pi worker needs to
reach the parent. A module added for one backend's wiring never pulls a dependency into the runtime
wheel.

The workspace is a packaging change, not a rewrite: the entrypoints, the CLI subcommands, the MCP tool
surface, the durable store format and the permissions of every harness are unchanged. The requirements
below are what the packaging must keep.

The compatibility baseline that pins the pre-workspace inventory is
`docs/compat/compatibility-baseline.json`; the contract tests assert on it, so a drift in the tree fails
the suite before a reviewer notices it in a diff.

## Requirements

- **R192** WHEN a developer runs `uv sync --locked`, THEN both workspace members resolve from the committed lockfile and install reproducibly under the declared Python range. (from uv-workspace-python-standards/S1)
- **R193** WHEN `uv lock --check` runs, THEN the lockfile is current and names both members, so a stale resolution fails loudly instead of silently drifting. (from uv-workspace-python-standards/S1)
- **R194** WHEN the runtime wheel is installed on its own, THEN its CLI and its MCP server work without the orchestrator's Pydantic or PyYAML, and neither dependency leaks into that environment. (from uv-workspace-python-standards/S5)
- **R195** WHEN each member's manifest is read, THEN it declares only its own dependencies: the runtime declares none and the orchestrator declares Pydantic and PyYAML. (from uv-workspace-python-standards/S5)
- **R196** WHEN CI runs on Linux or macOS across the supported Python versions, THEN the workspace resolves, the whole suite runs, `ruff check .`, `ruff format --check .` and `ty check` run, both member wheels build, and the runtime wheel installs in isolation. (from uv-workspace-python-standards/S6)
- **R197** WHEN the legacy tree's lint or type diagnostics are counted, THEN a count above the tracked budget fails the build, so the debt may shrink but never grow unnoticed. (from uv-workspace-python-standards/S6)
- **R198** WHEN the new modules are type-checked, THEN `ty` reports no diagnostic in either member, and the legacy diagnostics stay within their own budget. (from uv-workspace-python-standards/S6)
- **R199** WHEN a Claude, Codex or Pi session runs `agent-runtime`, `agent-runtime-mcp`, `agent-runtime-hook` or a known orchestration invocation, THEN the documented behaviour and the permissions of that harness are unchanged by the move. (from uv-workspace-python-standards/S2)
- **R200** WHEN the orchestrator dispatches, resumes, collects or cancels a worker, THEN it reaches the runtime only through the public CLI or API, and no member source imports a runtime module or opens the store's tables. (from uv-workspace-python-standards/S3)
- **R201** WHEN a task packet, a worker report or a route policy carries an unknown or invalid field, THEN the typed boundary refuses it with a deterministic error naming the field, and the same input always produces the same message. (from uv-workspace-python-standards/S4)
- **R202** WHEN the orchestrator's route policy is read, THEN it is YAML, an unknown model class is an error rather than a silent fall back to another backend, and the machine-facing messages stay JSON. (from uv-workspace-python-standards/S4)
- **R203** WHEN the workspace change is rolled back, THEN the legacy CLI shims and the legacy MCP server still answer, and the durable store opens in both the old and the new tree with the same schema version, the same tables and its rows intact: no state migration ever runs. (from uv-workspace-python-standards/S8)
- **R204** WHEN the orchestrator member runs, THEN it keeps no state of its own: it writes nothing into the runtime's store and its wheel needs neither the runtime member nor the orchestrator's dependencies. (from uv-workspace-python-standards/S8)
- **R205** WHEN the PRs that carry the session, resume and usage semantics merge or change, THEN those semantics stay pinned to their commits and keep their own regression tests, and the workspace contract does not duplicate them. (from uv-workspace-python-standards/S7)
- **R206** WHEN the orchestrator member's own suite runs, THEN the workspace's pytest run collects it, the member's tests and sources stay type-clean by contract, and the suite's own harness measures the behaviour it stands for and not the machine's speed: a terminated run's tree counts as gone when nothing in it is still running. (from orchestrator-engine-suite/S3)
- **R207** WHEN the orchestrator member's tests cite the orchestrator-engine requirement each scenario stands for, including the drive-restart recovery scenarios, THEN each test's docstring first line carries that `orchestrator-engine/R<m>` id, and the member's tests and sources stay type-clean under `ty`. (from drive-restart-recovery/S3)

## Coverage

| Scenario | Requirement | Test |
|---|---|---|
| S1 | R192, R193 | `tests/test_workspace_isolation.py`: `test_S1_uv_lock_is_current_for_both_members`, `test_S1_uv_sync_frozen_installs_both_members` |
| S2 | R199 | `tests/test_compatibility_contract.py`: `test_S2_entrypoint_shims_are_present_and_executable`, `test_S2_entrypoint_shims_resolve_to_their_python_entrypoints`, `test_S2_recorded_entrypoints_match_the_bin_directory`, `test_S2_the_installer_links_every_entrypoint_and_wires_the_hooks`, `test_S2_the_cli_keeps_its_subcommands`; `tests/test_compatibility_contract_integration.py`: `test_S2_the_hook_shim_still_fails_open`, `test_S2_the_cli_bridge_reaches_the_public_tools`, `test_S2_the_installer_links_every_shim`, `test_S2_the_installer_takes_the_documented_bin_dir`, `test_S2_the_installer_refuses_to_overwrite_a_non_symlink` |
| S3 | R200 | `tests/test_compatibility_contract.py`: `test_S3_the_public_tool_surface_covers_the_orchestration_verbs`, `test_S3_direct_sqlite_access_stays_inside_the_runtime_store_layer`; `tests/test_orchestrator_contract.py`: `test_S3_no_source_of_the_member_touches_the_runtime_store`, `test_S3_the_client_calls_the_runtime_cli_and_returns_its_answer`, `test_S3_a_failing_runtime_is_reported_not_swallowed` |
| S3 | R206 | `packages/agent-orchestrator/tests/test_engine_home.py`: `test_S3_ci_runs_the_member_suite_and_the_tracked_budgets` |
| S4 | R201, R202 | `tests/test_orchestrator_contract.py`: `test_S4_a_report_without_a_task_id_is_refused`, `test_S4_a_report_with_an_unknown_field_is_refused`, `test_S4_the_error_is_the_same_every_time`, `test_S4_a_route_policy_naming_an_unknown_class_is_refused`, `test_S4_a_yaml_route_policy_loads_and_its_unknown_field_is_refused` |
| S5 | R194, R195 | `tests/test_workspace_isolation.py`: `test_S5_runtime_wheel_works_without_orchestrator_dependencies`, `test_S5_member_manifests_declare_their_own_dependencies` |
| S6 | R196, R197, R198 | `tests/test_workspace_isolation.py`: `test_S6_workflow_runs_the_toolchain_on_the_matrix`, `test_S6_the_format_budget_counts_what_ruff_reports` |
| S7 | R205 | `tests/test_compatibility_contract.py`: `test_S7_the_pr_commits_are_pinned_in_the_baseline`, `test_S7_resume_and_usage_semantics_keep_their_own_regression_tests`, `test_S7_the_baseline_pins_the_semantics_the_plan_describes_for_each_pr`, `test_S7_each_pr_pins_the_regression_tests_that_own_its_semantics`, `test_S7_this_contract_does_not_duplicate_the_existing_test_names`; `tests/test_compatibility_contract_integration.py`: `test_S7_the_pinned_pr_commits_are_ancestors_of_this_branch`, `test_S7_the_pinned_commits_carry_the_semantics_the_baseline_records` |
| S8 | R203, R204 | `tests/test_rollback_contract.py`: `test_S8_the_legacy_tree_still_answers_through_its_shims`, `test_S8_a_store_written_by_the_new_tree_opens_in_the_legacy_tree_without_a_migration`, `test_S8_a_store_written_by_the_legacy_tree_opens_in_the_new_tree_without_a_migration`; `tests/test_orchestrator_contract.py`: `test_S8_the_member_writes_no_state_into_the_runtime_store`, `test_S8_the_runtime_wheel_needs_neither_member_nor_its_dependencies` |

## Requirements: test-cost

- **R208** WHEN `uv run pytest -n auto` runs the whole suite on an idle machine with Defender's real-time scanner off, THEN it passes in at least 30% less wall time than on main before T0, and in 5 minutes or less; `uv run pytest` (the fast suite) finishes in under 3 seconds (from test-cost/S1)
- **R209** WHEN a living spec in `docs/specs/` cites a test by name, as a test function, a test class or a `path::` node id, THEN that test exists, in the file the citation names when it names one; a cited test file exists; a deleted, merged or renamed test has its spec rows updated in the same change, naming the test that keeps the guarantee (from test-cost/S2)
- **R210** WHEN the tests marked `unit` run, THEN no test starts a process (git included) or opens a network socket (from test-cost/S3)
- **R211** WHEN a test module sits under the suite's `testpaths`, THEN it sets `pytestmark` to `pytest.mark.unit` or `pytest.mark.integration` (from test-cost/S4)
- **R212** WHEN `uv run pytest` is given only options, including an option's value such as `-p no:cacheprovider`, THEN it selects the same tests as `uv run pytest`; naming a path or a node id still runs exactly what was named (from test-cost/S5)
- **R213** WHEN a ticket deletes, merges or moves a test, THEN its PR lists each such test with the test that keeps its guarantee (or the new level it moved to), and the seconds of each module it touched before and after, measured back to back on the same machine (from test-cost/S6)
- **R214** WHEN a ticket replaces a test's real wait, THEN the test waits on an injected clock or a barrier, and asserts a signal the code produced; a real wait remains only where the assertion is about elapsed time (from test-cost/S7)
- **R215** WHEN an orchestrator test class needs a run driven through the plan lifecycle, THEN the plan is approved once per class, not once per test; the `tool_versions` lookup hits its cache across the class because the per-test HOME no longer changes PATH (from test-cost/S8)
- **R216** WHEN an installer test needs the v0.1.0 release, THEN the release is built once per session; a test that only reads it shares it, a test that changes it takes a copy (from test-cost/S9)
- **R217** WHEN a runtime test needs a state it does not exercise (a full team, a long-lived lead, a spent budget), THEN the state is seeded through the store or a fixture, and only the step under test drives the public path (from test-cost/S10)
- **R218** WHEN a runtime test talks to no worker session or no server, THEN none is started for it; the worker sessions and servers start only for the tests that talk to them (from test-cost/S11)
- **R219** WHEN an orchestrator test only reads a delivered or archived plan, THEN it reads a snapshot built once per class; a test that changes the plan takes a copy (from test-cost/S12)
- **R220** WHEN a test proves pure logic (validation, precedence, parsing, classification) through a subprocess or a server, THEN it runs that logic in process; it keeps the `integration` marker while it writes the filesystem, and one test per boundary still shows the refusal or the result crossing it (from test-cost/S13)
- **R221** WHEN a ticket of this plan that owns orchestrator lifecycle fixtures or route tests starts, THEN no other plan's open ticket changes those files: remove-legacy-routing's tickets (#266 and the one after it) have merged (from test-cost/S14)

## Coverage: test-cost

| Requirement | Scenario | Test nodes |
|---|---|---|
| R208 | S1 | human: Felippe times `uv run pytest -n auto` and `uv run pytest` on the same machine, idle, scanner off, once on main before T0 and once after the last ticket merges, and confirms the whole suite is at least 30% faster and at most 5 minutes, the fast suite under 3 seconds, and the drop in collected tests equals the deletions the PRs list, with no test newly skipped or marked `live` |
| R209 | S2 | `acceptance/suite/test_spec_citations.py` |
| R210 | S3 | `acceptance/suite/test_fast_suite.py` |
| R211 | S4 | `acceptance/suite/test_spec_citations.py` |
| R212 | S5 | `acceptance/suite/test_fast_suite.py` |
| R213 | S6 | human: the PR body lists each deleted, merged or moved test with its keeper (or new level), one row per test, and the module seconds before and after; checked before merge, with no test written to stand in for it |
| R214 | S7 | `tests/installer/test_shared_release.py::NoClockTests::test_S7_the_fakes_signal_the_installer_and_return_without_a_sleep`<br>`tests/test_dispatch.py::ClockedWaitTests::test_S7_a_wait_with_nothing_to_report_answers_timeout_when_the_injected_clock_reaches_it`<br>`packages/agent-orchestrator/tests/test_wake.py::AwaitTests::test_S2_await_waits_for_a_wake_that_arrives_later`<br>`packages/agent-orchestrator/tests/test_wake.py::AwaitTests::test_S2_an_unfinished_run_with_nothing_to_print_does_not_say_finished` |
| R215 | S8 | `packages/agent-orchestrator/tests/test_fixtures.py::FixtureCostTests::test_S8_a_class_approves_its_plan_once`<br>`packages/agent-orchestrator/tests/test_fixtures.py::FixtureCostTests::test_S8_path_stays_put_so_tool_versions_hits_its_cache`<br>`packages/agent-orchestrator/tests/test_fixtures.py::FixtureCostTests::test_S8_each_test_of_a_class_starts_from_the_same_fresh_world` |
| R216 | S9 | `tests/installer/test_shared_release.py::SharedReleaseTests::test_S9_the_release_is_built_once_and_a_reader_shares_it`<br>`tests/installer/test_shared_release.py::SharedReleaseTests::test_S9_a_test_that_changes_the_release_takes_a_copy` |
| R217 | S10 | `tests/test_team_limit.py::SeededTeamTests::test_S10_a_seeded_full_team_refuses_the_33rd_active_worker`<br>`acceptance/runtime/test_runtime_audit_outcome.py::TeamLimitOutcome::test_S10_a_full_team_is_seeded_and_only_the_step_under_test_launches`<br>`tests/test_operations.py::OutcomeScenario::test_S10_the_long_lived_lead_is_seeded_and_only_the_step_under_test_launches`<br>`tests/test_dispatch.py::DispatchTests::test_S10_seeded_reports_reach_the_lead_as_a_worker_session_would_publish_them`<br>`tests/test_budgets.py::BudgetTests::test_S10_a_seeded_backlog_is_read_back_as_the_lead_would_have_created_it` |
| R218 | S11 | `tests/test_event_wait.py::EventWaitTests::test_S11_a_test_starts_only_the_sessions_it_talks_to`<br>`tests/test_stop_failures.py::StopFailureTests::test_S11_a_stop_test_that_talks_to_the_lead_alone_starts_no_worker_session`<br>`tests/test_stop_failures.py::StopStoreFailureTests::test_S11_a_stop_driven_in_process_starts_no_server`<br>`tests/test_peer_dispatch.py::PeerDispatchTests::test_S11_a_dispatch_test_that_talks_to_no_worker_starts_none`<br>`tests/test_spawn_cleanup.py::FastReportTests::test_S11_a_spawn_driven_in_process_starts_no_server`<br>`tests/test_spawn_cleanup.py::StoreFailureTests::test_S11_a_store_failure_driven_in_process_starts_no_server` |
| R219 | S12 | `packages/agent-orchestrator/tests/test_acceptance.py::DeliveredSnapshotTests::test_S12_a_delivered_plan_is_driven_once_per_class_and_a_test_that_changes_it_takes_a_copy` |
| R220 | S13 | `packages/agent-eval/tests/test_capture_in_process.py::test_S13_an_unknown_ref_is_refused_in_process_without_spawning_a_cli`<br>`packages/agent-eval/tests/test_capture_in_process.py::test_S13_the_capture_help_is_printed_in_process`<br>`packages/agent-eval/tests/test_capture_in_process.py::test_S13_a_refusal_crosses_the_subprocess_boundary_with_exit_2`<br>`tests/test_tools.py::SpawnTests::test_S13_a_refusal_still_crosses_the_server_process`<br>`tests/test_hooks.py::RawHcTests::test_S17_garbage_input_does_not_deny`<br>`tests/test_agent_runtime.py::CallTests::test_S1_hc_terminal_runs_the_tool_and_prints_its_json`<br>`tests/test_agent_runtime.py::CliTests::test_missing_hc_is_unknown`<br>`tests/test_diagnostics.py::DiagnosticTests::test_hc_role_lookup_failure_degrades_status_collect_and_list`<br>`tests/test_agent_runtime.py::CliTests::test_null_parent_through_symlink_is_lead`<br>`tests/test_pi_lead.py::PiPolicyTests::test_policy_limits_the_directory_a_worker_may_run_in`<br>`packages/agent-orchestrator/tests/test_step_routes.py::StepRouteTests::test_S9_a_step_route_overrides_the_brain`<br>`packages/agent-orchestrator/tests/test_rounds.py::CapPrecedenceTests::test_S32_a_ticket_field_sets_the_cap_of_one_ticket`<br>`packages/agent-orchestrator/tests/test_runner.py::Classification::test_S13_a_skipped_or_xfailed_case_is_UNKNOWN`<br>`packages/agent-orchestrator/tests/test_swap.py::ContextReserveTests::test_S19_the_reserve_is_set_by_a_flag`<br>`packages/agent-orchestrator/tests/test_route_selection_run.py::RouteSettlementTests::test_S2_an_explicit_override_wins_over_the_config`<br>`packages/agent-orchestrator/tests/test_step_routes.py::StepRouteBoundaryTests::test_S3_a_gate_without_a_step_route_spawns_on_todays_effort` |
| R221 | S14 | human: remove-legacy-routing's tickets merged before T1 started (recorded when person ticket T9 was marked COMPLETE) |
