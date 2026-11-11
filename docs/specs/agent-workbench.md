# Agent workbench

Paths: packages/agent-orchestrator/src/agent_orchestrator/workbench.py, packages/agent-orchestrator/tests/test_workbench.py, packages/agent-orchestrator/tests/test_discovery.py, docs/specs/agent-workbench.md

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
- **R6** WHEN the authored plan file or an oracle changed after approval, THEN the view model shows the approved snapshot's tickets with `revision.drift: true` and both digests (`approved_digest`, `current_digest`), instead of failing or presenting the edited file as approved. (from agent-workbench/S6)
- **R7** WHEN any workbench command runs, THEN it writes nothing under the repository or the state directory (no ledger import, no lock, no file), its output holds no report or message bodies and no environment values, and an unmeasured count, token or duration is null, never 0. (from agent-workbench/S11)

## Coverage

| Scenario | Requirement | Test |
|---|---|---|
| S1 | R1 | `packages/agent-orchestrator/tests/test_discovery.py`: `test_S1_a_yaml_plan_is_listed_under_its_slug_field_not_its_dated_file_name`, `test_S1_a_yaml_plan_that_does_not_parse_keeps_its_file_derived_slug` |
| S2 | R2 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S2_one_json_object_lists_every_plan_with_its_state_and_ticket_counts`, `test_S2_an_approved_plan_without_a_ledger_is_approved`, `test_S2_a_path_names_the_plan_directory_without_a_repo_flag` |
| S3 | R3 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S3_outside_a_repository_is_not_a_repo_with_the_way_out`, `test_S3_a_repository_without_plans_is_no_plans_never_an_empty_list`, `test_S3_several_plan_directories_are_ambiguous_and_all_named`, `test_S3_a_plan_that_does_not_exist_is_a_named_error` |
| S4 | R4 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S4_every_ticket_has_its_blocked_by_edges_and_a_layer_ordered_by_layer_then_plan`, `test_S4_a_ticket_carries_its_state_and_reason_from_the_ledger` |
| S5 | R5 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S5_a_cycle_is_the_loaders_error_naming_the_tickets_and_no_view`, `test_S5_a_duplicate_ticket_id_is_named`, `test_S5_a_reference_to_a_missing_ticket_is_named`, `test_S5_malformed_yaml_names_the_file` |
| S6 | R6 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S6_an_edited_plan_shows_the_approved_snapshot_with_drift_and_both_digests`, `test_S6_a_changed_oracle_is_drift_too`, `test_S6_a_draft_plan_has_no_approval_to_drift_from` |
| S11 | R7 | `packages/agent-orchestrator/tests/test_workbench.py`: `test_S11_no_workbench_command_writes_under_the_repository_or_the_state_directory`, `test_S11_output_holds_no_report_or_message_bodies_and_no_environment_values`, `test_S11_a_plan_that_does_not_load_is_an_error_entry_with_null_counts_not_zero` |
