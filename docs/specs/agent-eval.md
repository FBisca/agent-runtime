# Agent eval cases

Paths: packages/agent-eval/, packages/agent-eval/tests/, pyproject.toml, uv.lock, docs/specs/agent-eval.md

A coding-agent run (a root session and every session it delegated to, on Claude, Codex or Pi) is frozen into a local, immutable
case, so it can be judged after the harness deletes its transcripts. Claude deletes a session's files
after 30 days. `agent-eval capture` copies them, with the code change, into `<home>/cases/<slug>/`, where
`<home>` is `$AGENT_EVALS_HOME` (default `~/.local/state/agent-evals`). Cases hold verbatim transcripts and
code, so they stay on this machine: outside every git working tree, directories 0700, files 0600, never
overwritten. Git decides what a working tree is (`git rev-parse`), so a stray `.git` entry that git does
not accept does not block the default home.

`agent-eval` is its own workspace member, `packages/agent-eval/`. Its one dependency is Pydantic, which
validates the labels; capture needs nothing beyond the standard library. It only reads
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

## Naming a session

`--session <ref>` is looked up in all three harnesses: the Claude transcripts under
`$CLAUDE_CONFIG_DIR/projects`, the Codex sessions under `$CODEX_HOME/sessions` and the Pi sessions under
`$PI_CODING_AGENT_DIR/sessions`. A ref is a full id or a unique prefix, and a full id wins over longer ids
it prefixes. A Codex or Pi session has no job state to link it: it joins alone, with its transcript and, for
Codex, its later rollout files, and with no subagent entry and no aux file of its own. The first `--session`'s
session is the `root` and names the default case, whatever the other sessions' age (for a Claude session, the
root is still the oldest session of its chain); a later `--session`'s session that no link joins to it has role
`session`. A named session that is also a worker of the run joins once, with the role the naming gave it.
`--exclude` leaves a named session out and lists it under `excluded`.

A ref that matches no session, several sessions, or one session found under two harnesses exits 2 and writes
nothing: an unknown ref names the directory it looked in for each harness, an ambiguous one every candidate
with its harness, and a session under two harnesses the two places it was found.

## Case layout

`manifest.json` (`schema: case/1`), `transcripts/<id>.jsonl.gz`,
`transcripts/<parent>/<agent-id>.jsonl.gz` and `.meta.json.gz` for a subagent,
`transcripts/<id>/tool-results/<path>.gz`, `transcripts/<id>/rollouts/<file>.gz` for a Codex session's later
rollouts, `orchestrate/<path>.gz` for a run directory, `changes.patch` when a repository was named, and
`labels/<kind>.json` for a judge's label (`agent-eval add-label`, written once and never overwritten).

Manifest `sessions` carry `id`, `harness`, `role` (`root`, `continuation`, `fork`, `session`,
`subagent`, `worker`), `parent`, `file`, `sha256`, `bytes`, `source`, `modified`, `cwd` and `assignment_id`
(the cwd of the session's own first record that carries one, and the id its own first prompt names; each null when
the session does not say, whichever way it joined the case), and `files`:
the session's other files, each `kind` (`meta`, `tool-result` or `rollout`), `file`, `sha256`, `bytes` and
`source`. The digest and
the size are of the original bytes, not of the gzip. The manifest also carries `case`, `captured`,
`excluded`, `missing`, `git` (`{repo, base, head}` as resolved shas, or null) and `orchestrate` (null, or
`{slug, source, window: {start, end}, worktrees, files}` with each file's `file`, `sha256`, `bytes` and
`source`).

A label is `label/1` (a verdict and four scores, `source: transcribed`) or `label/2`, the judge's whole output:
the verdict, the gates, each dimension's anchor label, rationale and findings, the top issue, the smallest
improvement, the probable cause and the judge's provenance. Every finding carries evidence refs into the
case, each a `file` the manifest lists (or `changes.patch`), a verbatim `quote` of at most 300 characters and
optionally the `line` it starts on. A complete `label/2`, which `agent-eval add-label --help` also prints:

```json
{
  "schema": "label/2",
  "kind": "agent-judge",
  "subject": {
    "session": "8b1d6f52-0c3e-4a79-9d1f-5e2a7c4b9013"
  },
  "judge": {
    "skill": "score-agent",
    "rubric_sha256": "9f2c4d7a1b3e5f60718293a4b5c6d7e8f9a0b1c2d3e4f5061728394a5b6c7d8e",
    "model": "claude-opus-4-7"
  },
  "judged_at": "2026-11-10",
  "source": "judged",
  "verdict": "FAIL",
  "score": 0.69,
  "gates": [],
  "dimensions": {
    "quality": {
      "label": "problematic",
      "score": 0.4,
      "rationale": "The worker reported the fix done while its patch still returns None.",
      "findings": [
        {
          "kind": "proven",
          "claim": "The worker reported done on a patch that returns None.",
          "deduction": true,
          "evidence": [
            {
              "file": "transcripts/8b1d6f52-0c3e-4a79-9d1f-5e2a7c4b9013.jsonl.gz",
              "line": 212,
              "quote": "All done, the fix is in place."
            },
            {
              "file": "changes.patch",
              "line": 14,
              "quote": "+    return None"
            }
          ]
        }
      ]
    },
    "tests": {
      "label": "mostly_purposeful",
      "score": 0.8,
      "rationale": "The suite ran and passed, but no test covers the idle path.",
      "findings": [
        {
          "kind": "risk",
          "claim": "Only the happy path is tested.",
          "deduction": true,
          "evidence": [
            {
              "file": "transcripts/8b1d6f52-0c3e-4a79-9d1f-5e2a7c4b9013.jsonl.gz",
              "line": 140,
              "quote": "3 passed in 0.12s"
            }
          ]
        }
      ]
    },
    "efficiency": {
      "label": "highly_efficient",
      "score": 1.0,
      "rationale": "It read the task, changed one file and stopped.",
      "findings": [
        {
          "kind": "strength",
          "claim": "It started from the task file.",
          "deduction": false,
          "evidence": [
            {
              "file": "transcripts/8b1d6f52-0c3e-4a79-9d1f-5e2a7c4b9013.jsonl.gz",
              "line": 31,
              "quote": "Read task.md and follow it."
            }
          ]
        }
      ]
    },
    "communication": {
      "label": "insufficient_evidence",
      "score": null,
      "rationale": "The case holds no message the worker sent to its parent.",
      "findings": []
    }
  },
  "top_issue": "Reported done on a change that does nothing.",
  "smallest_improvement": "Run the idle path before reporting done.",
  "probable_cause": {
    "cause": "instructions",
    "confidence": "medium"
  }
}
```

`add-label` refuses a `label/2` whose evidence is not in the case, whose dimensions break the rubric's
rules, whose score is not the weighted mean of its dimensions (quality .35, tests .25, efficiency .25,
communication .15, renormalised over the scored dimensions, rounded half up to 2 decimals) or whose verdict does
not follow from its gates and scores.

## Listing cases

`agent-eval list` reads every directory under `<home>/cases/` that holds a real `manifest.json` and prints
one line per case: the slug, the capture date, the session count, the label's verdict, its score and the
judging model, each `-` when the case has no label yet. Newest first, by the manifest's `captured` and
then by slug. `--json` prints the same rows as one array, with `null` for an unlabelled case's verdict,
score and judge (the whole field set: `case`, `captured`, `sessions`, `verdict`, `score`, `judge`).

Only a case's own record is read, its `manifest.json` and `labels/<kind>.json`, never a transcript or an
orchestrate file, so a listing does not cost what the run behind it weighed. A directory without a
manifest, or with one that does not parse, is not a case: it is skipped, and one unreadable label makes
that case show as unlabelled rather than failing the listing. Nothing is written, and an empty or missing
`<home>` prints nothing (`[]` with `--json`) and exits 0.

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
- **R219** WHEN `agent-eval add-label <case> --kind agent-judge <file>` reads a JSON label, THEN an invalid one exits 2 naming each failing field, a valid `label/1` one (`source: transcribed`) is written to `labels/agent-judge.json`, and an existing label is refused (exit 1, nothing written). (from agent-eval-p0/S12)
- **R220** WHEN `agent-eval add-label <case> --kind agent-judge <file>` reads a `label/2` JSON, THEN a valid one is written to `labels/agent-judge.json` as given; an invalid one exits 2 naming each failing field by its path (`dimensions.quality.findings[0].evidence[1].quote`), and nothing is written. A valid label has `subject.session`, `judge` (`skill`, 64-hex `rubric_sha256`, `model`), `judged_at` (a real date), `source: judged`, `verdict` PASS, FAIL, BLOCKED or INCOMPLETE, `score` (0..1 or null), `gates`, the four dimensions (each `label`, `score` 1.0, 0.8, 0.4, 0.2 or null, `rationale`, `findings`), `top_issue`, `smallest_improvement` and `probable_cause` (`cause` model, harness, repo, instructions, mixed or unknown; `confidence` low, medium or high), with `schema: label/2` and `kind`; any other field is refused. A finding has `kind`, `claim`, `deduction` and `evidence`. (from agent-eval-label-evidence/S1)
- **R221** WHEN a `label/2` evidence ref (`file`, optional `line`, required `quote` of at most 300 characters) is checked, THEN it is accepted only when `file` is a file of the case listed in its manifest (a transcript, an orchestrate file) or `changes.patch`, and `quote` occurs verbatim in that file's decompressed text, on line `line` when given (the quote starts on it); a ref to an absolute path, a path leaving the case, an unlisted file, a line past the end or a quote that is not there exits 2 naming the ref. (from agent-eval-label-evidence/S2)
- **R222** WHEN a dimension of a `label/2` is checked, THEN a scored dimension has at least one finding with evidence; a dimension scored below 1.0 has at least one finding with `deduction: true`; a finding's `kind` is proven, risk or strength; a finding with `deduction: true` has evidence; a null score has label `insufficient_evidence`, a rationale and no deduction, and a scored dimension never has that label; anything else exits 2 naming the dimension. (from agent-eval-label-evidence/S3)
- **R223** WHEN the score and verdict of a `label/2` are checked, THEN `score` equals the weighted mean (quality .35, tests .25, efficiency .25, communication .15) renormalised over the scored dimensions, rounded half up to 2 decimals on the exact decimal mean, and is null only when none is scored; `verdict` is BLOCKED exactly when `gates` is non-empty (each gate with `gate`, `rationale` and evidence), else INCOMPLETE when quality or tests is null, else PASS when every scored dimension and the score are at least 0.8, else FAIL; a mismatch exits 2 naming the expected value. (from agent-eval-label-evidence/S4)
- **R224** WHEN a label names its subject or source, THEN a `label/2` whose `subject.session` is not a session of the case manifest exits 2 naming it; a `label/1` with `source: judged` exits 2 saying a judged label is `label/2`; a `label/1` with `source: transcribed` is still accepted as before. (from agent-eval-label-evidence/S5)
- **R225** WHEN `agent-eval add-label --help` is read, or the spec's case layout, THEN both show `labels/<kind>.json` and a complete `label/2` example, and the spec names Pydantic as the package's one dependency. (from agent-eval-label-evidence/S6)
- **R226** WHEN `score-agent` is given a case directory, THEN it judges from the case's own files, writes a `label/2` JSON whose every evidence ref points into the case with a verbatim quote, and prints its score block rendered from that label. (from agent-eval-label-evidence/S6a)
- **R227** WHEN the case `orchestrator-silent-idle` is judged again, THEN `labels/agent-judge.json` holds a `label/2` from score-agent with `source: judged`, accepted by `add-label`, whose every deduction's quote can be found in the case. (from agent-eval-label-evidence/S7)
- **R228** WHEN `agent-eval capture --session <ref>` names a Codex or Pi session, THEN the case holds its transcript (with its later rollout files, for Codex) and the manifest lists it with its harness, the cwd of its first record, its role and parent and no subagent entry or aux file of its own; the first `--session`'s session is the `root` and names the default case whatever the other sessions' age, a later one has role `session`, a named session that is also a worker of the run joins once with the role the naming gave it, and `--exclude` leaves it out and lists it; a ref that matches no session, several sessions, or one session under two harnesses exits 2 and writes nothing, naming the directory looked in for each harness, every candidate with its harness, or the two places it was found. (from agent-eval-named-capture/S1, S2, S3)
- **R229** WHEN a session is captured, whichever road it entered the case by (named, in a chain, as a subagent or as a worker of a run), THEN the manifest records its `cwd` and its `assignment_id` when its own first prompt carries one, and null when it does not; the same session captured by name and as a worker records the same two fields. (from agent-eval-named-capture/S4)
- **R230** WHEN `agent-eval capture --session <id>` names a Codex or Pi session, THEN the case holds that session's transcript (with its later rollout files, for Codex), and the manifest lists it with its harness (`codex` or `pi`), the cwd of its first record, its role and parent, and no subagent entry and no aux file of its own. (from agent-eval-named-capture/S1)
- **R231** WHEN the `--session` ref matches no session, several sessions in one harness, or the same session under two harnesses, THEN capture exits 2 and writes nothing: an unknown ref names the directory it looked in for each harness, an ambiguous one names every candidate with its harness, and a session found under two harnesses names the two places it was found. (from agent-eval-named-capture/S2)
- **R232** WHEN `--session` names a Codex or Pi session, alone or beside another `--session`, with or without a run, THEN the first `--session`'s session is the `root` and names the default case, whatever the other sessions' age, a later one has role `session`, a named session that is also a worker of the run joins once, keeping the role the naming gave it, and `--exclude` leaves a named session out and lists it. (from agent-eval-named-capture/S3)
- **R233** WHEN the four Pi sessions of the 2026-11-10 batch are captured by name, THEN each case holds the session's whole transcript with its harness `pi`, the cwd of its first record and role `root`. (from agent-eval-named-capture/S5)
- **R234** WHEN `agent-eval list` runs, THEN it prints one line per case under `<home>/cases` with the slug, the capture date, the session count, the verdict, the score and the judging model, newest first by the manifest's `captured` and then by slug; a case with no label prints `-` for the verdict, the score and the judge; a directory under `cases/` that holds no `manifest.json`, or one that does not parse, is not listed; nothing is written. (from agent-eval-list/S1)
- **R235** WHEN `agent-eval list --json` runs, THEN it prints one JSON array with `case`, `captured`, `sessions`, `verdict`, `score` and `judge` per case, `null` verdict, score and judge for an unlabelled case, and it reads no transcript, so a case of any size is listed in the time its own record takes. (from agent-eval-list/S2)
- **R236** WHEN `<home>` or its `cases/` directory does not exist, THEN `agent-eval list` prints nothing (`[]` with `--json`), exits 0 and creates nothing. (from agent-eval-list/S3)
- **R237** WHEN a Codex rollout's first user messages are Codex's own injected context (`# AGENTS.md instructions`, `<environment_context>`, `<user_instructions>`, `<guardian_tool_descriptions>`, `<no retained transcript delta entries>`), THEN they are skipped and the next user message is the first prompt, even one that starts with `<`, so its `assignment_id` is recorded. (from the 2026-11-10 eval batch, case 9008700e)
- **R238** WHEN `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR` or `AGENT_EVALS_HOME` is unset or empty, THEN agent-eval reads `~/.claude`, `~/.codex`, `~/.pi/agent` or `~/.local/state/agent-evals` respectively. (from the 2026-11-10 eval batch, case 9008700e)

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
| agent-eval-label-evidence/S1 | R220 | `packages/agent-eval/tests/test_add_label2.py`: `test_S1_a_valid_label2_is_written_as_given`, `test_S1_an_invalid_label2_names_each_failing_field_by_its_path`, `test_S1_each_shape_fault_is_refused_and_nothing_is_written` |
| agent-eval-label-evidence/S2 | R221 | `test_S2_a_ref_into_a_listed_file_with_a_verbatim_quote_is_accepted`, `test_S2_a_ref_that_does_not_resolve_exits_2_naming_it`, `test_S2_a_gate_ref_is_resolved_like_any_other`, `test_S2_a_listed_file_that_is_gone_from_the_case_is_refused` |
| agent-eval-label-evidence/S3 | R222 | `test_S3_a_dimension_that_breaks_a_rule_exits_2_naming_it`, `test_S3_a_perfect_dimension_needs_evidence_but_no_deduction`, `test_S3_every_dimension_is_named_when_several_break` |
| agent-eval-label-evidence/S4 | R223 | `test_S4_the_recomputed_score_and_verdict_are_accepted`, `test_S4_a_wrong_score_exits_2_naming_the_expected_value`, `test_S4_a_score_where_nothing_is_scored_must_be_null`, `test_S4_a_wrong_verdict_exits_2_naming_the_expected_value`, `test_S4_a_gate_makes_the_verdict_blocked_and_only_a_gate_does`, `test_S4_a_blocked_verdict_without_a_gate_is_refused` |
| agent-eval-label-evidence/S5 | R224 | `test_S5_a_subject_that_is_not_a_session_of_the_manifest_exits_2_naming_it`, `test_S5_a_label2_needs_a_readable_manifest`, `test_S5_a_judged_label1_exits_2_saying_a_judged_label_is_label2`, `test_S5_a_transcribed_label1_is_still_accepted`, `test_S5_a_label2_of_another_kind_is_refused`, `test_S5_an_existing_label_still_stays` |
| agent-eval-label-evidence/S6 | R225 | `test_S6_the_help_shows_where_the_label_goes_and_a_complete_label2`, `test_S6_the_spec_case_layout_and_example_match_the_help`, `test_S6_the_spec_names_pydantic_as_the_one_dependency`, `test_S6_the_example_is_accepted_by_add_label_against_the_case_it_cites` |
| agent-eval-label-evidence/S6a | R226 | human: score-agent's `case-mode.md` (agent-skills#72, merged); a run on a copy of `orchestrator-silent-idle` was accepted by `add-label` on its first try, 2026-11-10 |
| agent-eval-label-evidence/S7 | R227 | human: `orchestrator-silent-idle/labels/agent-judge.json`, `label/2` FAIL 0.70, stored by `add-label` with mode 0600, 2026-11-10 |
| agent-eval-named-capture/S1 | R228 | `packages/agent-eval/tests/test_capture_named_outcome.py`: `test_R228_a_named_pi_session_is_captured_with_its_harness_and_cwd`, `test_R228_a_named_session_brings_no_subagent_and_nothing_else`, `test_R228_a_named_codex_session_is_captured_with_its_later_rollouts`, `test_R228_a_named_session_is_captured_under_a_unique_prefix_of_its_id`, `test_R228_a_named_codex_session_is_captured_under_a_unique_prefix_of_its_id`, `test_R228_the_capture_help_names_every_harness` |
| agent-eval-named-capture/S2 | R228 | `test_R228_an_unknown_ref_is_refused_naming_every_harness_it_looked_in`, `test_R228_an_ambiguous_prefix_inside_one_harness_is_refused_naming_both`, `test_R228_a_prefix_that_matches_two_harnesses_is_refused_naming_both`, `test_R228_the_same_session_under_two_harnesses_is_refused` |
| agent-eval-named-capture/S3 | R228 | `test_R228_the_first_named_session_is_the_root_and_names_the_case`, `test_R228_a_named_session_that_is_also_a_run_worker_joins_once`, `test_R228_a_named_session_can_be_excluded_and_is_listed` |
| agent-eval-named-capture/S4 | R229 | `test_R229_a_named_session_records_its_cwd_and_assignment_id`, `test_R229_a_subagent_records_its_own_cwd_and_assignment_id`, `test_R229_a_session_captured_by_name_and_as_a_worker_records_the_same_fields`, `test_R229_a_chain_member_records_its_own_cwd_and_assignment_id`, `test_R229_a_session_without_a_marker_records_a_null_assignment_id` |
| agent-eval-named-capture/S1 | R230 | `packages/agent-eval/tests/test_capture_named_outcome.py`: `NamedSessionTests::test_R228_a_named_pi_session_is_captured_with_its_harness_and_cwd`, `NamedSessionTests::test_R228_a_named_session_brings_no_subagent_and_nothing_else`, `NamedSessionTests::test_R228_a_named_codex_session_is_captured_with_its_later_rollouts`, `NamedSessionTests::test_R228_a_named_session_is_captured_under_a_unique_prefix_of_its_id`, `NamedSessionTests::test_R228_a_named_codex_session_is_captured_under_a_unique_prefix_of_its_id` |
| agent-eval-named-capture/S2 | R231 | `packages/agent-eval/tests/test_capture_named_outcome.py`: `RefusalTests::test_R228_an_unknown_ref_is_refused_naming_every_harness_it_looked_in`, `RefusalTests::test_R228_an_ambiguous_prefix_inside_one_harness_is_refused_naming_both`, `RefusalTests::test_R228_a_prefix_that_matches_two_harnesses_is_refused_naming_both`, `RefusalTests::test_R228_the_same_session_under_two_harnesses_is_refused` |
| agent-eval-named-capture/S3 | R232 | `packages/agent-eval/tests/test_capture_named_outcome.py`: `RootTests::test_R228_the_first_named_session_is_the_root_and_names_the_case`, `RootTests::test_R228_a_named_session_that_is_also_a_run_worker_joins_once`, `RootTests::test_R228_a_named_session_can_be_excluded_and_is_listed` |
| agent-eval-named-capture/S5 | R233 | human: Felippe's capture of the four Pi sessions of the 2026-11-10 batch by name (T2): each case holds the whole transcript, harness `pi`, the session's cwd and role `root` |
| agent-eval-list/S1 | R234 | `packages/agent-eval/tests/test_list.py`: `test_R234_two_cases_are_listed_newest_first_with_their_verdict_and_score`, `test_R234_an_unlabelled_case_is_listed_with_a_dash_not_hidden`, `test_R234_a_directory_that_is_not_a_case_is_not_listed` |
| agent-eval-list/S2 | R235 | `test_R235_json_prints_one_array_with_nulls_for_an_unlabelled_case`, `test_R235_listing_reads_no_transcript` |
| agent-eval-list/S3 | R236 | `test_R236_an_empty_or_missing_home_prints_nothing_and_creates_nothing` |
| eval batch d1110 | R237 | `packages/agent-eval/tests/test_harness_defaults.py`: `CodexInjectedTests::test_R237_codex_own_messages_are_injected`, `CodexInjectedTests::test_R237_a_user_prompt_that_starts_with_a_bracket_is_not_injected`; `packages/agent-eval/tests/test_capture_named_outcome.py`: `ManifestFieldTests::test_R237_a_codex_prompt_that_starts_with_a_bracket_keeps_its_assignment_id` |
| eval batch d1110 | R238 | `packages/agent-eval/tests/test_harness_defaults.py`: `DefaultHomeTests::test_R238_each_harness_home_defaults_under_the_user_home_when_unset`, `DefaultHomeTests::test_R238_an_empty_override_counts_as_unset` |
