# Compatibility baseline — the pre-workspace tree (2026-11-07)

Plan: `docs/plans/2026-11-07-uv-workspace-python-standards.plan.md` (T1, scenarios S2, S3, S7, S8).

This is the inventory the compatibility contract tests assert on. It describes what the repository
exposes **today**, before the runtime and the orchestrator move into a uv workspace. Nothing here is
a new behaviour: it is the frozen shape a later packaging move must preserve, and the thing a
rollback would fall back to.

The machine-readable form is `docs/compat/compatibility-baseline.json`; the contract tests read it,
so a drift in the tree fails the suite before a reviewer notices it in a diff.

## Entrypoints (S2)

| Command | Shim | Runs | Used by |
|---|---|---|---|
| `agent-runtime` | `bin/agent-runtime` | `server/agent_runtime.py` | Claude, Codex and Pi sessions; `agent-runtime call` |
| `agent-runtime-mcp` | `bin/agent-runtime-mcp` | `server/mcp_server.py` | the MCP server over stdio (Claude, Codex) |
| `agent-runtime-hook` | `bin/agent-runtime-hook` | `hooks/pre_tool_use.py`, `hooks/stop_guard.py`, `hooks/lead_context.py` | the PreToolUse / Stop / SessionStart hooks |
| `agent-runtime-profile` | `bin/agent-runtime-profile` | `server/profiles.py` | the worker-side profile launcher |

Every shim is a `/bin/sh` script that resolves itself through symlinks and execs a Python entrypoint
in the repository, so the repository can be cloned anywhere and still serve the commands.

`install.sh` symlinks exactly these four shims into `BIN_DIR` (default `~/.local/bin`), prints the
Claude marketplace commands and the Codex `config.toml` wiring (`[mcp_servers.agent-runtime]` and the
`[[hooks.PreToolUse]]` blocks) and refuses to overwrite a non-symlink of the same name.

The installer is part of the contract, not an implementation detail: `tests/test_compatibility_contract_integration.py`
runs it as a process and pins the shims it links, the environment variable that chooses the target
directory and the refusal it prints when a name of the same kind is not a symlink. The recorded
values live under `installer` in `compatibility-baseline.json`.

The CLI keeps four subcommands: `resolve`, `status`, `list` and `call`. `call` is the scripted
bridge to the MCP tools and lists them in its usage error.

## Import paths and package dependencies (S2, S8)

* The runtime is standard-library only at deployment: `pyproject.toml` declares `dependencies = []`
  and no module under `server/` imports a third-party package.
* The runtime modules are imported as top-level siblings (`import dispatch_store`, `import hc_tools`,
  `import usage`, …) from `server/`; `hooks/` adds `hook_lib` and `lead_context`.
* Only `server/dispatch_store.py` and `server/hc_tools.py` import `sqlite3`. That is the store
  layer: nothing else may touch the database directly (S3).
* The agent skills (the `orchestrate` skill and its companions) do not live in this repository: they
  ship from `agent-skills/skills/`, which the uv-workspace plan wires in T4. This baseline therefore
  records the runtime they call — the `agent-runtime` shim and the `spawn`, `resume`, `collect` and
  `stop` tools — and not the skill files themselves; a rollback of the workspace leaves the skills
  pointing at the same public surface.

## The public surface orchestration may use (S3)

The MCP server publishes ten tools: `spawn`, `message`, `resume`, `wait`, `status`, `list`,
`collect`, `stop`, `report`, `task`. The orchestration verbs map onto them:

| Verb | Tool |
|---|---|
| dispatch | `spawn` |
| resume | `resume` |
| collect | `collect` |
| cancel | `stop` |
| message / wait / status / list / report / task | the tool of the same name |

Orchestration reaches the runtime through these tools and through `agent-runtime call`; it must not
import `server.*` modules and must not read the SQLite tables.

## Durable store (S8)

The store is `~/.local/state/agent-runtime/dispatch.sqlite3`, `PRAGMA user_version = 2`, with the
tables `teams`, `workers`, `assignments`, `messages`, `receipts`, `callers`, `dispatch_turns`,
`last_wait`, `stall_progress`, `resume_marks` and `tasks`. Opening an older store is additive: the missing tables
and indexes are created, the rows are kept and the version is bumped. **No state migration ever
runs**, so rolling the packaging change back needs no data work.

## PR #39 and PR #45 (S7)

| PR | Commit | Semantics | Regression coverage |
|---|---|---|---|
| #39 | `90fee0253e1088146ce1e14ac672553bdadc1971` (`fix(resume): surface undelivered worker tasks (#39)`) | resume/undelivered attention: a swallowed resume is surfaced, not resent | `tests/test_event_wait.py` (`test_S12_swallowed_resume_returns_not_started_without_resending`, `test_S13_mcp_pending_wait_wakes_for_resume_report`) |
| #45 | `5a383dd85dbb877e64e4d4a2485a5f5220aad66b` (`feat(usage): bind workers to CLI session logs (#45)`) | session usage attribution: a worker binds to the CLI session log it runs in | `tests/test_usage.py` (`test_S5_claude_binds_transcript_and_uses_current_session_directly`, `test_S5a_identity_fallback_reads_claude_code_session_id`) |

Both commits are the live heads of their pull requests and are ancestors of this branch, and the
contract test asserts it, so a rebase that drops them fails loudly. The contract also asserts that
each pinned commit carries the semantics the plan names for that PR (the subject of #39 mentions
the resume work, the subject of #45 the usage work), so pinning the wrong commit fails too.

The contract does **not** re-test their semantics: `tests/test_stop_failures.py`,
`tests/test_event_wait.py`, `tests/test_dispatch.py`, `tests/test_usage.py` and
`tests/test_live_acceptance.py` already own them, and the contract only checks that those tests
are still there. `pr_regression_coverage` in the baseline records, per PR, the tests that own the
live semantics, so deleting one of them breaks the contract instead of quietly shrinking the
suite.

## The contract tests

| Test | Scenario | File |
|---|---|---|
| `test_S2_entrypoint_shims_are_present_and_executable` | S2 | `tests/test_compatibility_contract.py` |
| `test_S2_entrypoint_shims_resolve_to_their_python_entrypoints` | S2 | `tests/test_compatibility_contract.py` |
| `test_S2_recorded_entrypoints_match_the_bin_directory` | S2 | `tests/test_compatibility_contract.py` |
| `test_S2_the_installer_links_every_entrypoint_and_wires_the_hooks` | S2 | `tests/test_compatibility_contract.py` |
| `test_S2_the_cli_keeps_its_subcommands` | S2 | `tests/test_compatibility_contract.py` |
| `test_S2_the_hook_shim_still_fails_open` | S2 | `tests/test_compatibility_contract_integration.py` |
| `test_S2_the_installer_links_every_shim` | S2 | `tests/test_compatibility_contract_integration.py` |
| `test_S2_the_installer_takes_the_documented_bin_dir` | S2 | `tests/test_compatibility_contract_integration.py` |
| `test_S2_the_installer_refuses_to_overwrite_a_non_symlink` | S2 | `tests/test_compatibility_contract_integration.py` |
| `test_S2_the_cli_bridge_reaches_the_public_tools` | S2 | `tests/test_compatibility_contract_integration.py` |
| `test_S3_the_public_tool_surface_covers_the_orchestration_verbs` | S3 | `tests/test_compatibility_contract.py` |
| `test_S3_direct_sqlite_access_stays_inside_the_runtime_store_layer` | S3 | `tests/test_compatibility_contract.py` |
| `test_S7_the_pr_commits_are_pinned_in_the_baseline` | S7 | `tests/test_compatibility_contract.py` |
| `test_S7_the_baseline_pins_the_semantics_the_plan_describes_for_each_pr` | S7 | `tests/test_compatibility_contract.py` |
| `test_S7_each_pr_pins_the_regression_tests_that_own_its_semantics` | S7 | `tests/test_compatibility_contract.py` |
| `test_S7_resume_and_usage_semantics_keep_their_own_regression_tests` | S7 | `tests/test_compatibility_contract.py` |
| `test_S7_this_contract_does_not_duplicate_the_existing_test_names` | S7 | `tests/test_compatibility_contract.py` |
| `test_S7_the_pinned_pr_commits_are_ancestors_of_this_branch` | S7 | `tests/test_compatibility_contract_integration.py` |
| `test_S7_the_pinned_commits_carry_the_semantics_the_baseline_records` | S7 | `tests/test_compatibility_contract_integration.py` |
| `test_S8_the_store_keeps_its_schema_version_and_tables` | S8 | `tests/test_compatibility_contract_integration.py` |
| `test_S8_a_legacy_store_opens_without_a_state_migration` | S8 | `tests/test_compatibility_contract_integration.py` |

## Red contracts for the tickets that build the workspace

Two contracts describe behaviour that does not exist yet and are marked `xfail(strict=True)`: they
stay red on purpose and go green — and then fail the build until the marker is removed — when the
workspace lands.

* `test_S2_the_workspace_packages_exist_beside_the_legacy_shims` (T2/T3).
* `test_S3_the_orchestrator_talks_to_a_public_runtime_client` (T4).
