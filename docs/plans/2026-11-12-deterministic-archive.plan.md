# Plan: deterministic archive

Slug: `deterministic-archive`. Contract: `2026-11-12-deterministic-archive.plan.yaml`.

## Why now

The archive is the last step of every run, and it holds up the run's end. Over 18 past runs the haiku archive worker
took a median of 20 minutes and 51 turns (up to 100–320 minutes). Five of 17 runs needed a fix round. About 9 of 17
woke the Lead: a converge spec check, the ruff budget, a wrong `task_id`, a missing spec file, or going idle on a
long suite run. About 8 PRs went into keeping its brief right (#119, #124, #137, #143, #144, #146, #167, #180).
Almost nothing in the job needs judgment. The driver already places the oracles and re-runs the outcome evidence,
and the rest is copying bytes and writing text from YAML and JSON.

The gaps reviewer is cheap (about 1 minute, sonnet) and has found real defects. Two follow-up plans came from it
(`drive-restart-recovery-gaps`, `yaml-eec-gaps`). But it reviews `origin/main` before the archive lands, so about
7 of 18 reports list "oracle not promoted, spec not archived, Coverage row missing" as gaps.

## Sources

| Source | What it settles |
|---|---|
| The request and the evaluation in this session (events.jsonl and transcripts of 18 runs) | The archive goes deterministic; gaps starts after the archive merges; keep it simple |
| `docs/specs/plan-contract.md` R9, R17, R18, R23, R25, R28, R29, R70 | When the archive starts, what it copies and places, the requirement line format, held scenarios, YAML only, ARCHIVED on merge |
| `docs/specs/orchestrator-engine.md` R9, R11, R49 | The `archive` and `gaps` step routes; the archive PR conflict round |
| `driver.py` `start_archive`, `dispatch_archive`, `place_oracles`, `yaml_copies`, `held_requirements`, `archive_evidence`, `mark_merged`, `start_gaps` | What the driver already does, and what the worker does |
| `verify.py` `mark_archive` | ARCHIVED needs the PASS archive receipt the driver already writes |
| `docs/plans/2026-11-10-yaml-eec.plan.md` D7, D29 | Coverage records node ids; ticket reports' `covers` maps scenarios to node ids |
| Receipts under `~/.local/state/orchestrate` (187 counted) | `junit` holds counts only, never node ids; the XML is deleted after each run |
| Ticket `report.json` files | `covers` holds every non-human scenario of the 4 most recent plans except one |
| `docs/specs/plan-contract.md` Coverage, `agent-runtime.md` `## Coverage: runtime audit fixes` | The newest Coverage shape: `Requirement \| Scenario \| Test nodes`, `::` ids joined by `<br>` |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Coverage nodes come from the union of the tickets' `covers` for the scenario, then its placed oracle paths; a `human` scenario reads `human: <text>` | Node ids from receipts' junit; reading test names out of the oracle with `ast` | junit holds counts only; `covers` is JSON the gate already checks (R9); a path needs no parsing | Grill Q1, Q1″; receipts count |
| D2 | One section appended at the end of the spec: `## Requirements: <slug>`, then `## Coverage: <slug>` in the `Requirement \| Scenario \| Test nodes` shape | Inserting into existing `## Requirements` and Coverage tables | Appending never parses the Markdown's structure | Grill Q1′, Q2 |
| D3 | The next number is the highest `R<n>` in that spec file plus one; scenarios the spec holds keep their number through the existing `held_requirements` | Numbering across the repo; dropping the held check | One small regex; the held check is already written and tested (R70) | Grill Q3, Q3′ |
| D4 | A missing spec file is created holding the section alone | Waking the Lead | It happened once in 18 runs; creating it keeps the run moving | Grill Q4, Q1′ |
| D5 | The driver commits on `feat/<slug>-archive`, pushes, runs `gh pr create`, and writes the archive unit's `report.json` with the printed PR url; the ledger task stays, so the merge, mark-merged and ARCHIVED steps run unchanged | Replacing the ledger task with new state | Reuses every gate after the report | Grill Q5; `mark_merged`, `step_merge` |
| D6 | No local gate run: the PR's CI, the outcome re-run and the approved-bytes `cmp` on the archive head are the gates | Running the repo gate locally | CI already runs it on the PR | Grill Q6 |
| D7 | A missing node, an outcome failure on the archive head or a PR conflict asks the Lead, with the menu `retry` and `skip`; `retry` rebuilds the branch from the current base | A fix round to a worker; an automatic rebuild-and-push with no Lead | There is no worker; all three are rare | Grill Q7 |
| D8 | Gaps is spawned when the archive unit merges, and the run does not wait for it | Gaps beside the archive; the run waiting for gaps | Removes the "archive not landed" noise; gaps stays advisory | Grill Q8 |
| D9 | The `archive` step route is removed: a config or `--route` naming it is refused with the fix; `gaps` keeps its route | Ignoring the route quietly | A refusal names the reason and the way out (AGENTS.md) | Grill Q9 |
| D10 | t1's worker runs no tests locally; the PR's CI runs the suite, and red-first is not observed for t1 | The worker running `uv run pytest -n auto` locally | User's call mid-run | User, 2026-11-12 |
| D10 | The archive commit and PR carry no AI trailer | — | The person's commits are theirs alone | User instructions |

## Out of scope

- Markdown plans. They no longer run (plan-contract R28).
- Rewriting older specs' Coverage tables into the new shape.
- Changing what the gaps reviewer looks for, or its model.
- The orchestrate skill's wording in the agent-skills repo (SKILL.md l.50 "written once at archive", l.98). It is
  updated by hand once this lands.

## Risks

- **Security:** n/a. The driver runs `git` and `gh` with fixed argv, no shell, on paths it already owns. It writes
  no secret.
- **Performance:** covered. The archive goes from a median of 20 minutes to seconds plus CI. Gaps no longer runs
  beside the archive.
- **Migration:** covered. A run already in flight whose archive worker was spawned keeps its ledger task. Nothing is
  rewritten in older specs. A route config naming `archive` is refused with the fix (D9).
- **Rollback:** covered. Reverting T1 and T2 brings the archive worker back. Specs written by the deterministic
  archive are plain Markdown that the old worker also accepts.

## Seams

- `RunFixture.run_driver()` (`tests/fixtures.py`) runs the `run` CLI in process against `fake_runtime.py`,
  `fake_gh.py` and a real git origin. The outcome oracle drives it through `ArchiveFixture` (`tests/test_archive.py`).
- `fake_gh.py` gains `pr create`, which prints `https://github.com/o/r/pull/9` (T1).
- The existing archive tests (`test_archive.py`, `test_archive_yaml.py`, `test_acceptance.py`, `test_events.py`,
  `test_pi_backend.py`, `test_step_routes.py`) script an archive worker report and a gaps report. T1 drops the
  first and T2 the second.
