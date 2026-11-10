# Python workspace contracts

Paths: pyproject.toml, uv.lock, packages/agent-runtime/, packages/agent-orchestrator/, packages/agent-orchestrator/tests/, .github/workflows/ci.yml, tests/test_workspace_isolation.py, tests/test_orchestrator_contract.py, tests/test_rollback_contract.py

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
| S6 | R196, R197, R198 | `tests/test_workspace_isolation.py`: `test_S6_workflow_runs_the_toolchain_on_the_matrix` |
| S7 | R205 | `tests/test_compatibility_contract.py`: `test_S7_the_pr_commits_are_pinned_in_the_baseline`, `test_S7_resume_and_usage_semantics_keep_their_own_regression_tests`, `test_S7_the_baseline_pins_the_semantics_the_plan_describes_for_each_pr`, `test_S7_each_pr_pins_the_regression_tests_that_own_its_semantics`, `test_S7_this_contract_does_not_duplicate_the_existing_test_names`; `tests/test_compatibility_contract_integration.py`: `test_S7_the_pinned_pr_commits_are_ancestors_of_this_branch`, `test_S7_the_pinned_commits_carry_the_semantics_the_baseline_records` |
| S8 | R203, R204 | `tests/test_rollback_contract.py`: `test_S8_the_legacy_tree_still_answers_through_its_shims`, `test_S8_a_store_written_by_the_new_tree_opens_in_the_legacy_tree_without_a_migration`, `test_S8_a_store_written_by_the_legacy_tree_opens_in_the_new_tree_without_a_migration`; `tests/test_orchestrator_contract.py`: `test_S8_the_member_writes_no_state_into_the_runtime_store`, `test_S8_the_runtime_wheel_needs_neither_member_nor_its_dependencies` |
