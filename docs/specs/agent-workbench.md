# Agent workbench

Paths: packages/agent-orchestrator/src/agent_orchestrator/workbench.py, packages/agent-orchestrator/tests/test_workbench.py, packages/agent-orchestrator/tests/test_discovery.py, herdr/agent-workbench/, tests/test_workbench_mod.py, docs/specs/agent-workbench.md

The workbench is a person's view of a plan from one Herdr pane: the plan's ticket graph, its attempts, its
evidence and the live workers. The view reads the orchestrator's own plan loader and ledger through read-only
JSON commands, writes nothing and guesses no join. This file states what the read commands guarantee; the
Herdr mod that draws them is specified beside them as it lands.

## Requirements

- **R1** WHEN `agent-orchestrator plans list --json` discovers a YAML plan whose file is `<date>-<name>.plan.yaml`, THEN its `slug` is the plan's `slug:` field (the ledger key), not the date-prefixed file name; a Markdown plan keeps its file-derived slug. (from agent-workbench/S1)
- **R2** WHEN `agent-orchestrator workbench plans [path] [--repo R] --json` runs in a repository holding YAML and Markdown plans, THEN stdout is one JSON object with `schema_version` and one entry per plan: slug, format, path, state (from the ledger, else APPROVED or DRAFT from the approval block) and ticket counts by state; a Markdown plan carries `legacy: true` and no graph. A plan that does not load is an entry with an `error` and null state and counts, and the other plans are still listed. (from agent-workbench/S2)
- **R3** WHEN the workbench commands run outside a repository, in one with no plan directory, or in one with several, THEN each case is a distinct JSON error kind (`not_a_repo`, `no_plans`, `ambiguous` naming every directory) with the way out (an explicit path or `--repo`), exit status 2, never a crash and never an empty list standing in for an error. (from agent-workbench/S3)
- **R4** WHEN `agent-orchestrator workbench plan <plan> --json` runs on an approved YAML plan, THEN every ticket carries exactly its `blocked_by` edges and a topological layer (0 for no blockers, else one more than its deepest blocker), tickets are ordered by layer then plan order, and two runs over the same inputs print byte-identical stdout. (from agent-workbench/S4)
- **R5** WHEN the plan file has a cycle, a duplicate ticket id, a reference to a missing ticket, or malformed YAML, THEN the command exits non-zero with the loader's error naming the YAML path and the offending ids (`error.kind` is `invalid_plan`, `error.path` the file), prints no partial view model, and writes nothing. (from agent-workbench/S5)
- **R6** WHEN the authored plan file or an oracle changed after approval, THEN the view model shows the approved snapshot's tickets with `revision.drift: true` and both digests (`approved_digest`, `current_digest`), instead of failing or presenting the edited file as approved; the ledger is read under the approved snapshot's slug and ticket ids, so a plan's state and ticket states survive an edit of its slug or its tickets, in `workbench plan` and in the `workbench plans` listing. (from agent-workbench/S6)
- **R7** WHEN any workbench command runs, THEN it writes nothing under the repository or the state directory (no ledger import, no lock, no file), its output holds no report or message bodies and no environment values, and an unmeasured count, token or duration is null, never 0. (from agent-workbench/S11)

### The Herdr mod

`herdr/agent-workbench/` draws the read commands. Its logic is in two pure modules the repository suite runs under
`node --test`: `model.js` (what an answer, or the lack of one, reads as) and `data.js` (the fetch).

- **R12** WHEN the agent-workbench mod is shown for a space whose folder is a repository with plans, THEN it lists the plans (YAML with state and progress, Markdown marked legacy); selecting a YAML plan shows its tickets as a layered list, in the layer and plan order the command printed, with blocked-by chips that carry each blocker's state, and one inspector for the selected ticket: delivers, blockers, state, verified, attempts and gates. A value the answer does not carry (verified, attempts, gates) is shown as unknown or as not reported, never filled in. (from agent-workbench/S12)
- **R13** WHEN the workbench command fails, times out, prints no JSON, returns a scoped plan error, or finds no plans, THEN the mod shows that state scoped to the plan or project with the way out (and for no plans, `/start-planning`), other plans stay usable, and no placeholder data is drawn. A plan that does not load is a row with its error and no progress; a failed plan fetch is that plan's error and leaves the list and the other plans as they were. (from agent-workbench/S13)
- **R14** WHEN the mod is narrower than its layout threshold (720 logical pixels; before the first measurement it is full width), THEN it shows a compact single-column ticket list, one line per ticket, and the inspector as a drill-in with a way back; at full width it shows layers and the inspector side by side. (from agent-workbench/S14)
- **R15** WHEN the selected plan's ledger or run directory changes (the watcher marks the view dirty and a 2-second flush turns any number of changes into one fetch), or the 30-second fallback timer fires, THEN the mod refreshes with at most one fetch in flight, drops any answer older than the latest request (a different space or plan was asked for meanwhile), runs the workbench command with a fixed argv (no shell, the plan path one argument) in the space folder, and writes nothing: the only commands it runs are `workbench plans` and `workbench plan`. (from agent-workbench/S15)
- **R16** WHEN the repository suite runs the mod's pure modules, THEN `node --test` runs them from a pytest integration test, each scenario's test requiring that its S-ID's node tests ran and none failed, and a missing `node` fails that test naming node instead of skipping. (from agent-workbench/S18)

## Coverage

| Scenario | Requirement | Test |
|---|---|---|
| S1 | R1 | `packages/agent-orchestrator/tests/test_discovery.py`: `test_S1_a_yaml_plan_is_listed_under_its_slug_field_not_its_dated_file_name`, `test_S1_a_yaml_plan_that_does_not_parse_keeps_its_file_derived_slug` |
| S2 | R2 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S2_one_json_object_lists_every_plan_with_its_state_and_ticket_counts`, `test_S2_an_approved_plan_without_a_ledger_is_approved`, `test_S2_a_path_names_the_plan_directory_without_a_repo_flag` |
| S3 | R3 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S3_outside_a_repository_is_not_a_repo_with_the_way_out`, `test_S3_a_repository_without_plans_is_no_plans_never_an_empty_list`, `test_S3_several_plan_directories_are_ambiguous_and_all_named`, `test_S3_a_plan_that_does_not_exist_is_a_named_error` |
| S4 | R4 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S4_every_ticket_has_its_blocked_by_edges_and_a_layer_ordered_by_layer_then_plan`, `test_S4_a_ticket_carries_its_state_and_reason_from_the_ledger` |
| S5 | R5 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S5_a_cycle_is_the_loaders_error_naming_the_tickets_and_no_view`, `test_S5_a_duplicate_ticket_id_is_named`, `test_S5_a_reference_to_a_missing_ticket_is_named`, `test_S5_malformed_yaml_names_the_file` |
| S6 | R6 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S6_an_edited_plan_shows_the_approved_snapshot_with_drift_and_both_digests`, `test_S6_a_changed_oracle_is_drift_too`, `test_S6_a_slug_edited_after_approval_keeps_the_ledger_state_of_the_approved_plan`, `test_S6_a_ticket_removed_after_approval_keeps_its_ledger_state`, `test_S6_a_draft_plan_has_no_approval_to_drift_from` |
| S11 | R7 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S11_no_workbench_command_writes_under_the_repository_or_the_state_directory`, `test_S11_output_holds_no_report_or_message_bodies_and_no_environment_values`, `test_S11_a_plan_that_does_not_load_is_an_error_entry_with_null_counts_not_zero` |
| S12 | R12 | `tests/test_workbench_mod.py`: `test_S12_the_mod_lists_plans_and_shows_layered_tickets_with_one_inspector` (the `S12` tests of `herdr/agent-workbench/test/model.test.js`) |
| S13 | R13 | `tests/test_workbench_mod.py`: `test_S13_a_failure_is_shown_in_its_scope_with_the_way_out_and_no_placeholder_data` (the `S13` tests of `model.test.js` and `data.test.js`) |
| S14 | R14 | `tests/test_workbench_mod.py`: `test_S14_a_narrow_mod_shows_one_column_and_a_drill_in_inspector` (the `S14` tests of `model.test.js`) |
| S15 | R15 | `tests/test_workbench_mod.py`: `test_S15_a_refresh_runs_one_fixed_argv_fetch_in_flight_and_drops_stale_answers` (the `S15` tests of `data.test.js`), `test_S15_the_mod_runs_no_shell_and_writes_nothing` |
| S18 | R16 | `tests/test_workbench_mod.py`: `test_S18_node_test_runs_the_mods_pure_modules_from_pytest`, `test_S18_a_missing_node_fails_naming_node_instead_of_skipping` |
