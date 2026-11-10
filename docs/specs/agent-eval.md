# Agent eval cases

Paths: packages/agent-eval/, packages/agent-eval/tests/, pyproject.toml, uv.lock, docs/specs/agent-eval.md

A coding-agent run (a root session and every session it delegated to, on Claude, Codex or Pi) is frozen into a local, immutable
case, so it can be judged after the harness deletes its transcripts. Claude deletes a session's files
after 30 days. `agent-eval capture` copies them, with the code change, into `<home>/cases/<slug>/`, where
`<home>` is `$AGENT_EVALS_HOME` (default `~/.local/state/agent-evals`). Cases hold verbatim transcripts and
code, so they stay on this machine: outside every git working tree, directories 0700, files 0600, never
overwritten. Git decides what a working tree is (`git rev-parse`), so a stray `.git` entry that git does
not accept does not block the default home.

`agent-eval` is its own workspace member, `packages/agent-eval/`, standard library only. It only reads
the files of the orchestrator, the runtime and the harnesses; it imports none of them and opens none of
their stores.

## Claude chain

`$CLAUDE_CONFIG_DIR/jobs/*/state.json` links sessions. A job with `resumeSessionId` different from
`sessionId` resumed that session (role `continuation`); a job with `forkParentSessionId` forked from it
(role `fork`, and the fork link wins when a job names the same session in both). A job whose `sessionId`
equals its `resumeSessionId` resumed itself and links nothing, so it never hides a later job that resumed
the same session. Capture walks the links in both directions through every job: naming a fork finds its
parent, and the parent's other continuations join too. `--exclude` leaves out what the run did not
include.

Each session's `subagents/agent-*.jsonl` sits beside its transcript under `projects/<dir>/<session>/`,
with the `agent-*.meta.json` of the same name, and its `tool-results/` files (outputs the transcript
offloads) sit in the same directory. Capture reads regular files only: a symlink in those directories is
not followed.

The root session is the oldest session of the first `--session` chain (job `createdAt`, then id), and it
names the default case even when excluded. Sessions of a later `--session` that no link joins to the chain
have role `session`. A session the job state names whose file is already deleted is listed under `missing`;
one the user named that has no file is an error.

## Orchestrate runs

`--orchestrate-run <slug>` names a run of the orchestrator: its state directory `<state>/<slug>/`, where
`<state>` is `--orchestrate-state-dir` (the orchestrator's `--state-dir`, default
`~/.local/state/orchestrate`). The directory is read as its own record. `events.jsonl` gives the run
window, from its earliest to its latest `at`, and each `<task>/task.md` names the checkout its worker was
given on a `Checkout: <path> on branch <branch>` line: those paths are the run's ticket worktrees.

The whole directory joins the case, every regular file gzipped under `orchestrate/` (links are not
followed), with the same sha256 and size as a transcript. Then every session of Claude, Codex or Pi whose
cwd is one of the ticket worktrees (compared as real paths) and whose first record is inside the window,
ends included, joins as role `worker`, with its subagents and tool results when it is a Claude session.
Its `assignment_id` is the 32 hex digits after `assignment_id` in the first prompt, null without one: the
first prompt is the first user message with text, and for Codex the first one that is not the
`# AGENTS.md instructions` or `<...>` context Codex injects. A session `--session` already named is not
captured twice, and `--exclude` leaves a worker out. Without `--session` the case is named after the run.
A session id found under two Claude project directories, or under two harnesses, is refused.

Codex keeps `sessions/**/rollout-*.jsonl` under `$CODEX_HOME` (default `~/.codex`). A session may span
several rollout files; the `id` of each file's first record, a `session_meta`, groups them, whatever the
file name or the day folder says. The earliest file is the session's transcript and starts it; the rest
are in its `files` as kind `rollout`.

Pi keeps `sessions/<folder>/*.jsonl` under `$PI_CODING_AGENT_DIR` (default `~/.pi/agent`). The first
record of each file is the session header; its `id` is the session's id and its `cwd` the session's cwd.
The folder name, which starts with `--` and encodes the cwd lossily, is never read back, and a file whose
first line is not a header is skipped.

## Case layout

`manifest.json` (`schema: case/1`), `transcripts/<id>.jsonl.gz`,
`transcripts/<parent>/<agent-id>.jsonl.gz` and `.meta.json.gz` for a subagent,
`transcripts/<id>/tool-results/<path>.gz`, `transcripts/<id>/rollouts/<file>.gz` for a Codex session's later
rollouts, `orchestrate/<path>.gz` for a run directory, and `changes.patch` when a repository was named.

Manifest `sessions` carry `id`, `harness`, `role` (`root`, `continuation`, `fork`, `session`,
`subagent`, `worker`), `parent`, `file`, `sha256`, `bytes`, `source`, `modified`, `cwd` and `assignment_id`
(null unless the session joined as a worker, and then only when its first prompt carries one), and `files`:
the session's other files, each `kind` (`meta`, `tool-result` or `rollout`), `file`, `sha256`, `bytes` and
`source`. The digest and
the size are of the original bytes, not of the gzip. The manifest also carries `case`, `captured`,
`excluded`, `missing`, `git` (`{repo, base, head}` as resolved shas, or null) and `orchestrate` (null, or
`{slug, source, window: {start, end}, worktrees, files}` with each file's `file`, `sha256`, `bytes` and
`source`).

## Requirements

- **R207** WHEN `agent-eval capture --session <claude-id>` runs on a session that belongs to a Claude background job, including a job whose state resumes itself (`sessionId == resumeSessionId`) and a job forked from another (`forkParentSessionId`), THEN the case holds every session reachable through every job's resume and fork links, each session's `subagents/agent-*.jsonl` with its `.meta.json`, and each session's `tool-results/` files; the manifest lists each session with harness `claude`, role and parent. (from agent-eval-p0/S1)
- **R208** WHEN `--session` is repeated, THEN every named session and its subagents join the case, so a chain the job state cannot resolve can still be captured. (from agent-eval-p0/S2)
- **R209** WHEN a transcript or session file is captured, THEN it is stored gzipped and the manifest records the sha256 and byte size of the original file; decompressing reproduces those bytes. (from agent-eval-p0/S3)
- **R210** WHEN `--case <slug>` is given, or omitted, or names a case that exists, THEN the case lives at `<home>/cases/<slug>/`; without it the slug is the root (oldest) session id of the first `--session` chain, even when that session is excluded; an existing case makes capture exit non-zero naming it, and nothing is written. (from agent-eval-p0/S4)
- **R211** WHEN `--exclude <id>` names a session of the chain, THEN it is not copied and the manifest lists it under `excluded`. (from agent-eval-p0/S5)
- **R212** WHEN a captured session's file was last modified more than 25 × 86400 seconds ago, THEN capture prints a warning naming the session and the days left before the 30-day deletion, and still captures it; a file 25.5 days old warns, one 24.9 days old does not. (from agent-eval-p0/S6)
- **R213** WHEN `--repo <path> --base <rev> --head <rev>` are given, or omitted, THEN `changes.patch` holds `git diff --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ base..head`, independent of the user's diff config, and the manifest records the resolved shas; without them the manifest records null and no patch is written. (from agent-eval-p0/S7)
- **R214** WHEN the same session id is found under two Claude project directories, THEN capture exits non-zero naming both paths, and nothing is written. (from agent-eval-p0/S7a)
- **R215** WHEN capture writes, THEN every directory it creates, `<home>` and its parents included, is 0700 and every file 0600, and nothing is written inside a git working tree or outside `<home>` (env `AGENT_EVALS_HOME`, default `~/.local/state/agent-evals`). (from agent-eval-p0/S11)
- **R216** WHEN `--orchestrate-run <slug>` is given, THEN the run dir is copied into the case and every Claude, Codex or Pi session whose cwd is one of the run's ticket worktrees and which started inside the run window joins the case as a worker, with its `assignment_id` when the first prompt carries one. (from agent-eval-p0/S8)
- **R217** WHEN a Codex session spans several rollout files, THEN all files sharing its `session_meta` id are captured under that one session. (from agent-eval-p0/S9)
- **R218** WHEN a Pi session is captured, THEN its id is the first record's `id` and its cwd the first record's `cwd`, including directories whose names start with `--`. (from agent-eval-p0/S10)
- **R219** WHEN `agent-eval add-label <case> --kind agent-judge <file>` reads a JSON label, THEN an invalid one exits 2 naming each failing field, a valid `label/1` one is written to `labels/agent-judge.json`, and an existing label is refused (exit 1, nothing written). (from agent-eval-p0/S12)

## Coverage

| Scenario | Requirement | Test |
|---|---|---|
| S1 | R207 | `packages/agent-eval/tests/test_capture_claude.py`: `test_R207_resume_fork_subagents_meta_and_tool_results`, `test_R207_a_self_resuming_job_does_not_hide_the_continuation`, `test_R207_naming_the_fork_captures_its_parent_and_siblings`, `test_R207_a_fork_with_its_own_resume_link_is_still_a_fork`, `test_R207_unique_id_prefix_is_enough`, `test_R207_ambiguous_prefix_is_a_usage_error_and_writes_nothing`, `test_R207_unknown_session_fails_and_writes_nothing`, `test_R207_a_session_the_job_names_but_whose_file_is_deleted_is_listed_missing`, `test_R207_a_symlink_in_a_session_directory_is_not_followed` |
| S2 | R208 | `test_R208_repeated_session_joins_every_named_session_and_subagents`, `test_R208_a_session_named_twice_or_also_in_the_chain_is_captured_once` |
| S3 | R209 | `test_R209_stored_gzipped_with_sha256_and_size_of_the_original` |
| S4 | R210 | `test_R210_default_slug_is_the_root_session_id`, `test_R210_default_slug_is_the_root_even_when_the_root_is_excluded`, `test_R210_the_slug_is_the_root_of_the_first_session_chain`, `test_R210_case_flag_names_the_directory`, `test_R210_an_existing_case_is_refused_by_name_and_nothing_is_written`, `test_R210_an_existing_default_case_is_refused`, `test_R210_a_slug_cannot_leave_home`, `test_R207_the_oldest_root_of_a_chain_with_two_roots_is_the_root` |
| S5 | R211 | `test_R211_an_excluded_session_is_not_copied_and_is_listed`, `test_R211_an_id_prefix_names_the_session_and_none_is_listed_by_default`, `test_R211_excluding_a_session_outside_the_chain_fails_and_writes_nothing` |
| S6 | R212 | `test_R212_a_25_5_day_old_session_warns_with_days_left_and_is_still_captured`, `test_R212_a_24_9_day_old_session_does_not_warn`, `test_R212_the_days_left_count_down_with_the_age`, `test_R212_only_the_old_session_is_named`, `test_R212_past_the_deadline_says_zero_days_left_and_still_captures` |
| S7 | R213 | `test_R213_the_patch_holds_the_pinned_diff_and_the_manifest_the_resolved_shas`, `test_R213_the_patch_does_not_depend_on_the_users_diff_config`, `test_R213_without_them_the_manifest_records_null_and_no_patch`, `test_R213_partial_arguments_are_a_usage_error_and_write_nothing`, `test_R213_an_unresolvable_revision_fails_and_writes_nothing` |
| S7a | R214 | `test_R214_one_id_under_two_project_directories_is_refused_naming_both`, `test_R214_a_duplicate_reached_through_the_chain_is_refused_too` |
| S11 | R215 | `test_R215_directories_0700_and_files_0600_home_and_parents_included`, `test_R215_a_home_inside_a_git_working_tree_is_refused_and_nothing_is_written`, `test_R215_a_stray_dot_git_that_git_does_not_accept_is_not_a_working_tree`, `test_R215_a_home_inside_a_dot_git_directory_is_refused`, `test_R215_the_default_home_is_under_the_user_state_dir`, `test_R215_nothing_is_left_behind_when_a_write_fails` |
| S8 | R216 | `packages/agent-eval/tests/test_capture_orchestrate.py`: `test_R216_the_run_dir_is_copied_into_the_case`, `test_R216_claude_codex_and_pi_sessions_of_the_ticket_worktrees_join_as_workers`, `test_R216_a_session_outside_the_worktrees_or_the_run_window_stays_out`, `test_R216_the_window_is_inclusive_at_both_ends`, `test_R216_the_case_slug_defaults_to_the_run_and_session_flags_add_to_it`, `test_R216_a_worker_the_chain_already_holds_is_captured_once`, `test_R216_exclude_leaves_a_worker_out_and_lists_it`, `test_R216_without_an_orchestrate_run_no_worker_joins_and_the_manifest_says_null`, `test_R216_an_unknown_run_fails_naming_it_and_writes_nothing`, `test_R216_a_run_with_no_session_to_capture_fails_and_writes_nothing`, `test_R216_a_run_slug_cannot_leave_the_state_dir`, `test_R216_a_symlink_in_the_run_dir_is_not_followed`, `test_R216_a_claude_worker_found_under_two_project_directories_is_refused` |
| S9 | R217 | `test_R217_every_rollout_file_of_a_session_is_captured_under_it`, `test_R217_rollout_files_with_the_same_id_in_other_directories_still_join`, `test_R217_a_later_rollout_does_not_pull_in_a_session_that_started_outside_the_window` |
| S10 | R218 | `test_R218_id_and_cwd_come_from_the_first_record_in_a_dash_dash_directory`, `test_R218_a_directory_name_that_looks_like_the_worktree_does_not_decide_the_cwd`, `test_R218_a_file_whose_first_record_is_not_a_session_header_is_skipped` |
| S12 | R219 | `test_R219_an_invalid_label_exits_2_naming_each_failing_field`, `test_R219_a_label_valid_only_in_form_exits_2`, `test_R219_an_unreadable_label_file_exits_2`, `test_R219_a_valid_label_is_written_to_labels_agent_judge_json`, `test_R219_an_existing_label_is_refused_and_left_unchanged` |
