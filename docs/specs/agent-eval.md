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

## Requirements

### Requirement: Claude chain capture
`agent-eval capture --session <claude-id>` SHALL capture every Claude session reachable from the named one through the resume and fork links of every background job, each with its subagents and tool results.

#### Scenario: a chain of resumed and forked jobs
- WHEN the session belongs to a Claude background job, including a job whose state resumes itself (`sessionId == resumeSessionId`) and a job forked from another (`forkParentSessionId`; the fork link wins when a job names the same session in both)
- THEN the case holds every session reachable through every job's links in both directions (naming a fork finds its parent and the parent's other continuations), each session's `subagents/agent-*.jsonl` with its `.meta.json`, and its `tool-results/` files; a self-resuming job links nothing and hides no later job; the manifest lists each session with harness `claude`, role and parent, and a session the job state names whose file is deleted is listed under `missing`

#### Scenario: repeated --session flags
- WHEN `--session` is repeated, or a named session is also in the chain
- THEN every named session and its subagents join the case, each once, so a chain the job state cannot resolve can still be captured

#### Scenario: a symlink is not followed
- WHEN a session directory holds a symlink
- THEN capture reads regular files only and does not follow it

### Requirement: Duplicate session refusal
`capture` SHALL refuse a session id found under two Claude project directories and write nothing.

#### Scenario: one id under two project directories
- WHEN the same session id is found under two Claude project directories, directly or through the chain
- THEN capture exits non-zero naming both paths, and nothing is written

### Requirement: Verbatim gzipped capture
`capture` SHALL store each captured file gzipped and record the sha256 and byte size of the original.

#### Scenario: the original bytes are recoverable
- WHEN a transcript or session file is captured
- THEN it is stored gzipped, the manifest records the sha256 and size of the original bytes, and decompressing reproduces those bytes

### Requirement: Case naming
`capture` SHALL write the case to `<home>/cases/<slug>/`, where the slug is `--case` or the root session id, and SHALL refuse an existing case.

#### Scenario: the default slug
- WHEN `--case` is omitted
- THEN the slug is the id of the root (oldest) session of the first `--session` chain, even when that session is excluded

#### Scenario: an existing case or a slug leaving home
- WHEN the case directory already exists, or the slug would leave `<home>/cases`
- THEN capture exits non-zero naming it, and nothing is written

### Requirement: Session exclusion
`capture` SHALL leave out a session named by `--exclude` and list it under `excluded`.

#### Scenario: an excluded session
- WHEN `--exclude <id>` (a full id or unique prefix) names a session of the chain
- THEN it is not copied and the manifest lists it under `excluded`; an id outside the chain fails and writes nothing

### Requirement: Transcript deletion warning
`capture` SHALL warn when a captured session's file is old enough that Claude is about to delete it, and still capture it.

#### Scenario: a session nearing the 30-day deletion
- WHEN a captured session's file was last modified more than 25 x 86400 seconds ago
- THEN capture prints a warning naming only that session and the days left before the deletion (0 past it) and captures it; a file 25.5 days old warns, one 24.9 days old does not

### Requirement: Change patch capture
`capture` SHALL freeze the code change as `changes.patch` when a repository is named, independent of the user's diff configuration.

#### Scenario: a repository, base and head
- WHEN `--repo <path> --base <rev> --head <rev>` are given
- THEN `changes.patch` holds `git diff --no-ext-diff --no-textconv --src-prefix=a/ --dst-prefix=b/ base..head` whatever the user's diff config, and the manifest records the resolved shas

#### Scenario: no repository, or an unusable one
- WHEN the three flags are omitted
- THEN the manifest records `git: null` and no patch is written; a partial set of flags or an unresolvable revision fails and writes nothing

### Requirement: Private case storage
`capture` SHALL keep cases on this machine, outside every git working tree, directories 0700 and files 0600, never overwritten.

#### Scenario: permissions and location
- WHEN capture writes
- THEN every directory it creates, `<home>` and its parents included, is 0700 and every file 0600, `<home>` is `$AGENT_EVALS_HOME` (default `~/.local/state/agent-evals`), and a failed write leaves nothing behind

#### Scenario: a home inside a working tree
- WHEN `<home>` is inside a git working tree or a `.git` directory
- THEN capture refuses and writes nothing; a stray `.git` entry that git does not accept does not make a working tree

### Requirement: Orchestrate run capture
`capture --orchestrate-run <slug>` SHALL copy the run's directory into the case and add every session of its ticket worktrees as a worker.

#### Scenario: the run directory and its workers
- WHEN `--orchestrate-run <slug>` names a run under `--orchestrate-state-dir` (default `~/.local/state/orchestrate`)
- THEN every regular file of the run directory joins gzipped under `orchestrate/` (links are not followed), and every Claude, Codex or Pi session whose cwd is one of the ticket worktrees (read from each `task.md`'s `Checkout:` line, compared as real paths) and whose first record is inside the run window of `events.jsonl`, ends included, joins as role `worker` with its `assignment_id` when its first prompt names one

#### Scenario: outside the run
- WHEN a session is outside the worktrees or the window, or the run is unknown, has no session, or its slug would leave the state directory
- THEN the session stays out, or capture fails naming the run and writes nothing; a worker already named joins once, `--exclude` leaves one out and lists it, and a Claude worker found under two project directories is refused

### Requirement: Codex rollout grouping
`capture` SHALL group the rollout files of one Codex session under that session.

#### Scenario: a session spanning several rollout files
- WHEN several `sessions/**/rollout-*.jsonl` files under `$CODEX_HOME` share the `id` of their first record (a `session_meta`), whatever the file name or day folder
- THEN the earliest file is the session's transcript and starts it, and the rest are in its `files` as kind `rollout`

### Requirement: Pi session capture
`capture` SHALL take a Pi session's id and cwd from the first record of its file.

#### Scenario: a Pi session file
- WHEN a Pi session under `$PI_CODING_AGENT_DIR/sessions/<folder>/*.jsonl` is captured
- THEN its id is the first record's `id` and its cwd that record's `cwd`, never the folder name (which starts with `--`); a file whose first line is not a session header is skipped

### Requirement: Transcribed label
`agent-eval add-label` SHALL accept a `label/1` that is valid in form, and write it once.

#### Scenario: a label/1
- WHEN `agent-eval add-label <case> --kind agent-judge <file>` reads a `label/1` JSON with `source: transcribed`
- THEN a valid one is written to `labels/agent-judge.json`; an invalid or unreadable one exits 2 naming each failing field; an existing label is refused (exit 1) and left unchanged

### Requirement: Judge label shape
`add-label` SHALL accept a `label/2` only in its closed shape and name each failing field by its path.

#### Scenario: a valid label/2
- WHEN a `label/2` has `subject.session`, `judge` (`skill`, 64-hex `rubric_sha256`, `model`), `judged_at` (a real date), `source: judged`, `verdict` PASS, FAIL, BLOCKED or INCOMPLETE, `score` (0..1 or null), `gates`, the four dimensions (each `label`, `score` 1.0, 0.8, 0.4, 0.2 or null, `rationale`, `findings`), `top_issue`, `smallest_improvement` and `probable_cause` (`cause` model, harness, repo, instructions, mixed or unknown; `confidence` low, medium or high), with `schema: label/2` and `kind`
- THEN it is written to `labels/agent-judge.json` as given

#### Scenario: an invalid label/2
- WHEN a field is missing, malformed or not in the shape (a finding has `kind`, `claim`, `deduction` and `evidence`)
- THEN `add-label` exits 2 naming each failing field by its path (`dimensions.quality.findings[0].evidence[1].quote`), and nothing is written

### Requirement: Label evidence refs
`add-label` SHALL accept an evidence ref only when it points into the case and quotes it verbatim.

#### Scenario: a ref that resolves
- WHEN a ref (`file`, optional `line`, required `quote` of at most 300 characters) names a file of the case listed in its manifest, or `changes.patch`
- THEN it is accepted when `quote` occurs verbatim in the file's decompressed text, on line `line` when given (the quote starts on it); gate refs resolve like any other

#### Scenario: a ref that does not
- WHEN a ref names an absolute path, a path leaving the case, an unlisted or vanished file, a line past the end, or a quote that is not there
- THEN `add-label` exits 2 naming the ref

### Requirement: Label dimension rules
`add-label` SHALL hold each dimension of a `label/2` to the rubric's rules.

#### Scenario: a dimension that breaks a rule
- WHEN a scored dimension has no finding with evidence, a dimension scored below 1.0 has no finding with `deduction: true`, a finding's `kind` is not proven, risk or strength, a deduction has no evidence, a null score lacks label `insufficient_evidence` or a rationale or carries a deduction, or a scored dimension has that label
- THEN `add-label` exits 2 naming every dimension that breaks a rule

### Requirement: Label score and verdict
`add-label` SHALL accept a `label/2` only when its score and verdict follow from its dimensions and gates.

#### Scenario: the recomputed score and verdict
- WHEN `score` is the weighted mean of the scored dimensions (quality .35, tests .25, efficiency .25, communication .15, renormalised over the scored ones, rounded half up to 2 decimals on the exact decimal mean, null only when none is scored) and `verdict` is BLOCKED exactly when `gates` is non-empty (each gate with `gate`, `rationale` and evidence), else INCOMPLETE when quality or tests is null, else PASS when every scored dimension and the score are at least 0.8, else FAIL
- THEN the label is accepted

#### Scenario: a mismatch
- WHEN the score or verdict differs from that recomputation
- THEN `add-label` exits 2 naming the expected value

### Requirement: Label subject and source
`add-label` SHALL check that a label names a session of the case and a source that fits its schema.

#### Scenario: subject and source
- WHEN a `label/2`'s `subject.session` is not a session of the case manifest, or its manifest is unreadable, or its kind is another than the one asked, or a `label/1` has `source: judged`
- THEN `add-label` exits 2 naming it (a judged label is `label/2`); a `label/1` with `source: transcribed` is still accepted, and an existing label still stays

### Requirement: Label documentation
`agent-eval add-label --help` and this spec SHALL show `labels/<kind>.json` and the same complete `label/2` example, and the spec SHALL name Pydantic as the package's one dependency.

#### Scenario: the help and the spec agree
- WHEN `add-label --help` and the Case layout section of this spec are read
- THEN both show `labels/<kind>.json` and the same `label/2` example, and the example is accepted by `add-label` against the case it cites

### Requirement: Named session capture
`capture --session <ref>` SHALL capture a session of any of the three harnesses by id or unique prefix, and refuse a ref that does not name exactly one.

#### Scenario: a Codex or Pi session
- WHEN `--session` names a Codex session under `$CODEX_HOME/sessions` or a Pi session under `$PI_CODING_AGENT_DIR/sessions` (Claude's are under `$CLAUDE_CONFIG_DIR/projects`; a full id wins over longer ids it prefixes)
- THEN it joins alone, with its transcript (and later rollout files for Codex), listed with its harness, the cwd of its first record, role and parent, and with no subagent entry and no aux file of its own

#### Scenario: a ref that names no single session
- WHEN a ref matches no session, several sessions, or one session found under two harnesses
- THEN capture exits 2 and writes nothing: an unknown ref names the directory it looked in for each harness, an ambiguous one every candidate with its harness, and a session under two harnesses the two places it was found

#### Scenario: root, roles and exclusion
- WHEN `--session` is repeated, with or without a run
- THEN the first `--session`'s session is the `root` and names the default case whatever the other sessions' age, a later one that no link joins has role `session`, a named session that is also a worker of the run joins once with the role the naming gave it, and `--exclude` leaves a named session out and lists it under `excluded`

### Requirement: Session cwd and assignment id
`capture` SHALL record each session's `cwd` and `assignment_id` in the manifest whichever way it entered the case.

#### Scenario: the fields of a session
- WHEN a session is captured by name, in a chain, as a subagent or as a worker of a run
- THEN the manifest records the cwd of its own first record that carries one and the 32 hex digits after `assignment_id` in its own first prompt (the first user message with text), each null when the session does not say; the same session captured by name and as a worker records the same two fields

### Requirement: Codex injected context
`capture` SHALL skip the context Codex injects when it looks for a session's first prompt.

#### Scenario: injected messages
- WHEN a Codex rollout's first user messages are Codex's own (`# AGENTS.md instructions`, `<environment_context>`, `<user_instructions>`, `<guardian_tool_descriptions>`, `<no retained transcript delta entries>`)
- THEN they are skipped and the next user message is the first prompt, even one that starts with `<`, so its `assignment_id` is recorded

### Requirement: Harness home defaults
agent-eval SHALL read each harness's own home when its override is unset or empty.

#### Scenario: an unset or empty override
- WHEN `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR` or `AGENT_EVALS_HOME` is unset or empty
- THEN agent-eval reads `~/.claude`, `~/.codex`, `~/.pi/agent` or `~/.local/state/agent-evals` respectively

### Requirement: Case listing
`agent-eval list` SHALL print one line per case under `<home>/cases`, newest first, reading only the case's own record.

#### Scenario: cases and their labels
- WHEN `agent-eval list` runs
- THEN each case prints its slug, capture date, session count, verdict, score and judging model (each `-` when it has no label yet, or when its label is unreadable), newest first by the manifest's `captured` and then by slug; a directory with no `manifest.json`, or one that does not parse, is skipped; only `manifest.json` and `labels/<kind>.json` are read, never a transcript, and nothing is written

### Requirement: Case listing as JSON
`agent-eval list --json` SHALL print the same rows as one array.

#### Scenario: the JSON rows
- WHEN `agent-eval list --json` runs
- THEN it prints one array with `case`, `captured`, `sessions`, `verdict`, `score` and `judge` per case, `null` verdict, score and judge for an unlabelled case, and it reads no transcript

### Requirement: Empty home listing
`agent-eval list` SHALL print nothing and create nothing when there are no cases.

#### Scenario: a missing home
- WHEN `<home>` or its `cases/` directory does not exist
- THEN it prints nothing (`[]` with `--json`), exits 0 and creates nothing
