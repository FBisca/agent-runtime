# Plan: the driver watches a pull request's merge state

Contract: [`2026-11-10-orchestrator-pr-merge-state.plan.yaml`](2026-11-10-orchestrator-pr-merge-state.plan.yaml)
(scenarios, tickets, evidence; validated by `agent-orchestrator plan check`). This file is the narrative: why,
sources, decisions, risks. It is not covered by the approval digest.

Slug: `orchestrator-pr-merge-state`. Living spec: `docs/specs/orchestrator-engine.md`. Status: AWAITING APPROVAL.

## Why now

Run `yaml-eec`, unit `t4` (2026-11-10): PR #96 passed review and CI, was marked ready and moved to `awaiting-merge`.
Main then moved and the PR turned `DIRTY` / `CONFLICTING`. Nothing reacted: `step_merge` reads only `MERGED` and
`CLOSED`, `poll` skips `awaiting-merge`, and R15 keeps the idle ticks away from it. The conflict fix round that
already exists only fires when `gh pr update-branch` fails, and only after `step_ci` sees `BEHIND`; GitHub reports
`DIRTY`, not `BEHIND`, when a branch conflicts. So the unit sat forever on a merge it could not get.

## Sources

| Source | What it settles |
|---|---|
| `~/.local/state/orchestrate/yaml-eec/t4/report.2.json`, `yaml-eec/events.jsonl` | The incident: t4 reached `awaiting-merge` at 11:03:56, then only idle events; PR #96 `DIRTY`/`CONFLICTING` |
| `driver.py` @ `a4bf51e`: `step_ci`, `update_branch`, `step_merge`, `poll`, `ensure_draft`, `fail_gate`, `step_fix`, `wake`, `idle_ticks`, the disputed-answer exit | Today's PR state machine and its gaps (gatherer report) |
| `docs/specs/orchestrator-engine.md` R14, R15, R21 | Wakes are sent once; awaiting-merge is never idle; routing changes don't touch the PR state machine |
| `docs/plans/2026-11-09-merged-unit-completes.plan.md` D2 | A merge wins over any gate state |
| `docs/plans/2026-11-10-yaml-eec.plan.md` D9 | New plans are YAML |
| `docs/plans/2026-11-10-orchestrator-silent-idle.plan.yaml` | Claims R22 |
| `tests/test_pr.py` (`PrFixture`, `BranchTests`), `tests/fake_gh.py`, `tests/fixtures.py` | The seam; the fake has no `headRefOid` and gates `prs.json` on readiness |
| User, this session | The flows: poll both gates; UNKNOWN poll; BEHIND update; DIRTY conflict round with delta re-review; red at awaiting-merge CI round; draft undo on leaving; human review out of scope |
| Grill, delegated to Codex `gpt-6.1-sol` (2026-11-10) | Q1–Q8 and the gaps, recorded as D1–D15 |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Merge state and checks are read on every round at ready-for-ci and awaiting-merge | Only at ready-for-ci (today) | Main moves after the PR is marked ready | User |
| D2 | Precedence: MERGED/CLOSED, then DIRTY/CONFLICTING, then UNKNOWN or a view error, then BEHIND, then checks | Checks first | A conflict can keep CI from starting; one conflict round covers red checks that come with it | User; Grill gap |
| D3 | UNKNOWN or a failed `gh pr view`: no action, polled again | Treat as clean | GitHub computes mergeability lazily | User |
| D4 | DIRTY/CONFLICTING raises the conflict round directly, with the `pr view` state as evidence; the rebase brief no longer says "what gh pr update-branch said" | Try update-branch first | update-branch can't resolve a conflict | Grill Q4 |
| D5 | BEHIND: update-branch on the same task, back to ready-for-ci, the PR stays ready, no fix round | A fix round; undo draft | Nobody wrote code | Grill Q2 |
| D6 | The pr-ready wake is keyed on the live `headRefOid` from `pr view`, falling back to the report head | Report head only | update-branch moves the head without a new report, so the second wake would be deduped | Grill Q1 |
| D7 | `step_ci` checks merge state before `gh pr ready`, while the PR is still a draft | Ready first (today) | A DIRTY PR would be readied and then wait | Grill Q3 |
| D8 | One helper undoes the draft and clears `pr_ready`/`draft_done` across every attempt of the unit, on every code-changing exit from ready-for-ci or awaiting-merge: conflict, CI red, disputed answer with changes_code | Only the conflict path | Fix the class; `unit_flag` ORs historical flags, so clearing only the current task does nothing | User; Grill Q5 |
| D9 | A failed `gh pr ready --undo` holds the fix worker back; it is retried on a later round | Dispatch anyway | A ready PR mid-fix can be merged half-done | Grill gap |
| D10 | A non-zero update-branch is a conflict round only when a fresh `pr view` shows DIRTY/CONFLICTING; otherwise the Lead is asked with the gh error | Every non-zero exit is a conflict (today) | Access or network failures are not something a rebase can fix | Grill gap |
| D11 | After update-branch, checks are cleared and green is accepted only from polls after the update, on a head that differs from the one before | Reuse stored green | Stale green would wake the user for a head CI never ran on | Grill gap |
| D12 | Repeated conflicts use the existing unit fix-round counter and cap; escalation leaves the PR a draft | A separate conflict cap | One cap per unit | Grill gap |
| D13 | The archive unit keeps its conflict round without a review; paths that read `reviewed_commit` are guarded | Give the archive a reviewer | It has none today | Grill Q6 |
| D14 | One YAML contract plus this narrative; one regular/complex ticket built in a worktree with a PR to main; requirements from R23 | `.plan.md` only; `/orchestrate` | yaml-eec D9; one ask, delivery speed | Grill Q8 |
| D15 | Human review (`CHANGES_REQUESTED`) is out of scope | Include it | Separate plan | User |

## Scenarios

In [`2026-11-10-orchestrator-pr-merge-state.plan.yaml`](2026-11-10-orchestrator-pr-merge-state.plan.yaml), S1–S12.

## Out of scope

- Human review on the PR (`reviewDecision`, review comments).
- The delta re-review range after a rebase: `reviewed_commit` may not be an ancestor of the new head, so the two-dot
  diff can include base changes. `git range-diff` is a follow-up.
- Unsticking yaml-eec t4 (#96 is merged).

## Risks

- **Security:** n/a. No new input reaches a shell; `gh` argv is built from the stored PR URL as today.
- **Performance:** one extra `gh pr view` per waiting unit per round (about 30 s apart). Acceptable for a handful of
  units; `gh` failures are polled again rather than retried in a loop.
- **Migration:** a run on an older driver that sits at awaiting-merge with a stored `pr_ready` is picked up on restart:
  the merge-state step reads the live PR, not the flags.
- **Rollback:** revert the PR; the state book keeps the same gates, so a running plan continues on the old driver.

## Seams

`test_pr.py` through `PrFixture`/`GateFixture`, which runs the whole driver loop against the fake runtime and fake
`gh`. The fake learns to script merge state and `headRefOid` whether or not the PR is a draft (`test_fake_gh.py`
covers it). No new seam.

## Tickets

In the YAML: T1 (regular, complex, risk state and external), S1–S12.
