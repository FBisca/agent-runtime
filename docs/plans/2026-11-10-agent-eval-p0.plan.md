# Plan: agent-eval P0 — store agent runs as scorable cases

Slug: `agent-eval-p0`. Living spec: `docs/specs/agent-eval.md`. Status: ARCHIVED 2026-11-10.
Contract twin: `2026-11-10-agent-eval-p0.plan.yaml` (same rows; the driver runs the YAML).

## Why now

Agent performance is starting to be scored (`score-agent`, first verdict FAIL 0.64 on 2026-11-09), but nothing is stored:
the score exists only in a chat, and Claude deletes transcripts after 30 days, so that run's evidence disappears around
2026-12-09. P0 of the evaluation pipeline: freeze a run and its delegated sessions into a local case, and store the
judge's score next to it. Labelling, judge isolation and golden sets come after this.

## Sources

| Source | What it settles |
|---|---|
| Request + ChatGPT proposal (2026-11-10) | case = task not session; native JSONL is primary evidence; P0 capture → P1 labels/judges → P2 golden |
| `agent-skills/skills/score-agent/` (+ rubrics) | judge = 4 dims, 1.0/0.8/0.4/0.2/N/A, weights .35/.25/.25/.15; prints text only, stores nothing |
| `svc-platform/.claude/skills/agentic-coaching/SKILL.md` | transcripts can hold investor data → kept outside every repo |
| `vault/02-areas/agent-fleet/decisions/Decision - work data boundary.md` | no code, bodies or client data on a personal remote |
| `agent-fleet/docs/contract.md`, `hooks/session_log.py` | `logs/sessions.jsonl` points at every Claude session |
| `packages/agent-runtime/src/agent_runtime/usage.py`, `docs/plans/2026-11-06-worker-token-metrics.plan.md` | `assignment_id=` marker in worker prompts; binder covers claude/codex only; a Codex session spans several rollout files (D21) |
| `~/.local/state/agent-runtime/worktrees/*.json` | worker→session link designed but empty (0/151) |
| `~/.local/state/orchestrate/<slug>/` | run dir: events, attempts, metrics, tN/report+review; no session ids |
| `~/.claude/jobs/<id>/state.json`, `~/.claude/projects/<slug>/<session>/{subagents,tool-results}/` | job resume and fork links; subagent transcripts + meta; offloaded tool outputs |
| `~/.pi/agent/sessions/--<cwd>--/<ts>_<uuid>.jsonl` | Pi id = first record `id`; no parent id |
| `pyproject.toml` (uv workspace), `packages/agent-orchestrator/` | member layout, Pydantic strict contracts, pytest with strict markers |
| agent-fleet#31 (closed) + its review round 1 | reference code; findings B1, C1–C3, N1–N2 become S1, S4, S6, S7, S7a, S11 |
| Grill rounds 1–2 + 2026-11-10 move to agent-runtime | every row marked "grill" below |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | All eval data local in `~/.local/state/agent-evals/` (0700), no git, no remote | private `agent-evals` repo with transcripts/patches; fleet brain | transcripts hold investor data; code can't reach a personal remote; brain is a disposable index | grill R1 Q1 |
| D2 | Keep 30-day retention; capture works for all three harnesses | raise `cleanupPeriodDays`; add Codex/Pi prune | 30 days is enough to capture in; pruning Codex is irreversible | grill R1 Q2, R2 Q1 |
| D3 | Capture warns when a session file is >25 days old (seconds, not floored days) | silent | the 30-day deletion is the only deadline | grill R2 Q1; #31 review C2 |
| D4 | Capture is manual in P0; the agent-runtime setting `manual \| on_finish` comes with the plan that builds `on_finish` | setting now with one value; auto on every session | a one-value setting is dead config | grill R1 Q3, R2 Q2 |
| D5 | Case = root session + its children (Claude resume/fork chain and subagents, orchestrate workers on Codex/Pi/Claude) | single sessions only | delegated work must be judged with its lead | grill R1 Q4 |
| D6 | Case id = `--case <slug>`, default the root session id; native ids live in the manifest | `hc label eval_task=` | restarts span sessions; labels unused and die on `hc rm` | grill R1 Q5, R2 Q5 |
| D7 | Code is a new workspace member `packages/agent-eval` in agent-runtime; it only reads orchestrator, runtime and harness files; nothing in the orchestration path calls it | agent-fleet (built as #31, closed); inside agent-orchestrator | sits next to the future capture setting, the `finish` trigger and `usage.py` without putting eval logic in the driver | user 2026-11-10 |
| D8 | Case contents: gzipped files + sha256 of original bytes, `manifest.json` (`case/1`), `changes.patch` (local only, diff config pinned), orchestrate run dir copy; no `verification.json` | extracted verification file | test evidence is in the transcripts | grill R2 Q4; #31 review N2 |
| D9 | Claude children via job resume and fork links + `subagents/`; Codex via `session_meta` id across rollout files; Pi via first record `id`; orchestrate workers via cwd = ticket worktree within the run window | wait for the `usage.py` binder fix | capture must not wait on the broken binder | grill R2 Q8; #31 review B1, Q1 |
| D10 | Labels are a strict Pydantic `label/1` model; `agent-eval add-label` validates and writes `labels/agent-judge.json` | free text; hand-written JSON Schema | repo pattern (`agent_orchestrator.contracts`); scores comparable across runs | grill R1 Q7 |
| D11 | First case `org-chart-custodian-leaves`: `22e11767` (+ subagent) and `3eec2edb`; `d7fe1995` excluded; yesterday's score transcribed with `source: transcribed` | `3eec2edb` only | 22e11767 holds the original turns and the gatherer | grill R2 Q3, Q6 |
| D12 | P0 only; steering judge, labeller TUI, golden/holdout, brain indexing deferred | ChatGPT P0–P2 at once | evidence completeness first | grill R1 Q8 |
| D13 | No Linear; plan in agent-runtime `docs/plans/` as `.md` and `.plan.yaml` | Linear; one format | personal tooling | grill R1 Q9, R2 Q7 |
| D14 | A case is immutable: an existing case id or label is refused | overwrite / merge | evidence must not drift after judging | assumption (stated) |
| D15 | Forked sessions join the chain | stop at fork boundary | a fork's history is part of the run that produced it | assumption (stated; #31 review Q1) |

## Scenarios

- **S1** WHEN `agent-eval capture --session <claude-id>` runs on a session of a Claude background job, including a self-resuming job and a forked one, THEN the case holds every session reachable through every resume and fork link, each session's `subagents/agent-*.jsonl` with `.meta.json`, and its `tool-results/`; the manifest lists each with harness `claude`, role and parent.
- **S2** WHEN `--session` is repeated, THEN every named session and its subagents join the case.
- **S3** WHEN a file is captured, THEN it is stored gzipped and the manifest records the sha256 and byte size of the original; decompressing reproduces those bytes.
- **S4** WHEN `--case <slug>` is given, omitted, or names an existing case, THEN the case lives at `<home>/cases/<slug>/`; without it the slug is the root session id of the first chain, even when excluded; an existing case exits non-zero naming it, nothing written.
- **S5** WHEN `--exclude <id>` names a session of the chain, THEN it is not copied and the manifest lists it under `excluded`.
- **S6** WHEN a session file was last modified more than 25 × 86400 s ago, THEN capture warns with the session and the days left before deletion, and still captures; 25.5 days warns, 24.9 does not.
- **S7** WHEN `--repo --base --head` are given or omitted, THEN `changes.patch` is a diff pinned against the user's diff config and the manifest records the shas; without them it records null and writes no patch.
- **S7a** WHEN one session id exists under two Claude project dirs, THEN capture exits non-zero naming both paths, nothing written.
- **S8** WHEN `--orchestrate-run <slug>` is given, THEN the run dir is copied and every Claude, Codex or Pi session whose cwd is a ticket worktree of the run and that started inside the run window joins as a worker, with its `assignment_id` when present.
- **S9** WHEN a Codex session spans several rollout files, THEN all files sharing its `session_meta` id are captured under that session.
- **S10** WHEN a Pi session is captured, THEN its id is the first record's `id` and its cwd the first record's `cwd`, including `--`-prefixed dirs.
- **S11** WHEN capture writes, THEN every directory it creates (`<home>` and parents included) is 0700 and every file 0600, and nothing is written inside a git working tree or outside `<home>`.
- **S12** WHEN `agent-eval add-label <case> --kind agent-judge <file>` reads a label, THEN an invalid one exits 2 naming each failing field, a valid `label/1` is written to `labels/agent-judge.json`, and an existing label is refused.
- **S13** WHEN the first case is captured, THEN `cases/org-chart-custodian-leaves/` holds `22e11767` with its subagent and `3eec2edb`, lists `d7fe1995` as excluded, and `labels/agent-judge.json` holds the 2026-11-09 score (FAIL, 0.64; 0.8/0.8/0.4/0.4) with `source: transcribed`.

## Out of scope

- agent-skills: `score-agent` emits a `label/1` JSON block and calls `agent-eval add-label` when a case exists (after T3).
- agent-runtime runtime: `usage.py` binds Pi sessions; the worker→session link stops being empty (0/151).
- The capture setting (`manual | on_finish`, own file beside `routes.json`, trigger = orchestrator `finish` event).
- Steering judge, labeller TUI, judge isolation runner, golden/holdout sets, brain indexing, redaction, any remote.
- Codex/Pi retention changes.

## Risks

- **Security:** cases hold verbatim transcripts and code. Covered: local only, 0700/0600, never inside a git tree, no remote (S11).
- **Performance:** gzip copies of a few sessions per manual case (3eec2edb is 4.6 MB raw). n/a beyond that.
- **Migration:** new store; `manifest.json` carries `schema: case/1`, labels `schema: label/1`.
- **Rollback:** new package, nothing else calls it; removing the member or deleting `<home>/cases/<slug>` undoes everything.

## Seams

- `agent-eval` CLI driven in-process and as a subprocess from `packages/agent-eval/tests/` (pytest, `unit` marker), every source root overridable (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_SESSION_DIR`, `--orchestrate-state`, `AGENT_EVALS_HOME`) and pointed at fixtures.
- Root `pyproject.toml`: workspace `members` and pytest `testpaths` gain `packages/agent-eval`.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | `packages/agent-eval` + `capture` for Claude runs (chains, subagents, tool-results, gzip/sha256, manifest, exclude, age warning, pinned patch, duplicate refusal, permissions), spec | S1–S7, S7a, S11 | none | packages/agent-eval/, pyproject.toml, uv.lock, docs/specs/agent-eval.md | regular | complex | agent | COMPLETE 2026-11-10 |
| T2 | Orchestrate runs: run dir copy, workers on Claude, Codex (multi-rollout) and Pi | S8–S10 | T1 | packages/agent-eval/, docs/specs/agent-eval.md | regular | complex | agent | COMPLETE 2026-11-10 |
| T3 | `label/1` model and `agent-eval add-label` | S12 | T1 | packages/agent-eval/, docs/specs/agent-eval.md | small | simple | agent | COMPLETE 2026-11-10 |
| T4 | Capture the first case and transcribe the 2026-11-09 score; check against S13 | S13 | T1, T3 | docs/specs/agent-eval.md | | | Felippe | TO-DO |
