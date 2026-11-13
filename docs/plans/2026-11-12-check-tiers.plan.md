# Plan: check tiers

Slug: `check-tiers`. Contract: `2026-11-12-check-tiers.plan.yaml`.

## Why now

Every ticket worker runs the repository's whole suite before it reports: with no `--gate`, the brief says "the
repository's full CI gate (see its AGENTS.md / CLAUDE.md)" (`driver.py` `task_text`). agent-runtime's whole suite
takes about 12.5 minutes, and parallel worktrees run it at once on one machine. In the 2026-11-10 eval batch that
contention produced the only FAIL (case f6e1fd01): a backgrounded gate run, then two duplicate foreground copies that
died on a broken pipe and `exit=124`, about 20 minutes lost, while two other worktrees ran their own whole suites.
The fix that removes the load is tiers, declared per repo and generic across languages: a worker passes the cheap
tiers, and the whole suite runs once per plan.

## Sources

| Source | What it settles |
|---|---|
| The request (this session) | Generic for every repo, embedded in agent-orchestrator; no heavy-job admission in agent-runtime; pilots agent-runtime, agent-skills, rastro-ai |
| Eval case `d1110-…f6e1fd01`, `labels/agent-judge.json` and transcript lines 457-582 | The cost: repeated whole-suite runs, contention with other worktrees, duplicates after the backgrounding notice |
| `packages/agent-orchestrator/src/agent_orchestrator/driver.py` `task_text`, packet `checks`, `require_completable`, `verify_integration` | Where the brief, the required checks, the COMPLETE rule and the integration run live today |
| `docs/plans/2026-11-12-plan-layout.plan.yaml` (approved) T1, S1 | The repo config loader (`config.py`, strict keys, a missing file means defaults) that this plan extends |
| `.orchestrate/config.yaml` and `config.py` on main (#188) | The loader exists and refuses an unknown key (`checks: unknown key`), so the `checks` node is new |
| `agent-fleet/hooks/heavy-jobs/` | The existing Claude-only, svc-platform-only queue; left as it is |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Tiers in the orchestrator, declared in `.orchestrate/config.yaml` under `checks` | A machine-wide heavy-job queue in agent-runtime (`agent-runtime job`) | The tiers remove the load; a queue only orders it. Kept out of this plan | grill Q1, Q4 |
| D2 | One `affected` command template per repo, placeholders `{base}` and `{paths}` | A separate `select` command; a declarative path map; `{files}` | Language-agnostic: the repo's own tool selects (pytest-impacted, `vitest related`, a crate script). `{files}` is unknown when the worker is briefed; `{paths}` (the ticket's owned paths) is a superset of the change, so selection never misses | grill Q2, Q5, Q11 |
| D3 | `affected` may be reported `not_run` with a reason; `fast` may not | Falling back to `full` when nothing is selected | A fallback brings back the load the plan removes | grill Q6 |
| D4 | `--gate` overrides the tiers; a repo without `checks` behaves as today, and `plan check` warns | Making tiers mandatory | Opt-in per repo, no break for repos that have not declared tiers | grill Q7 |
| D5 | `full` runs once at verify on the pinned default-branch commit, on by default | CI only by default | One honest local gate per plan, not per ticket | grill Q3b |
| D6 | `--skip-full` per run and `checks.full.verify: false` per repo move it to CI | `--skip-verify` | Skipping all of verify would also skip the outcome evidence | grill Q3a |
| D7 | Verify records a `full` event (`state` PASS / FAIL / skipped, `sha`, `reason`) in `events.jsonl`; a FAIL rejects the plan through the existing `plan-rejected` wake | A receipt shaped like a scenario's | The whole suite is not a scenario; the event and the wake are the observable seams | this plan |
| D8 | Pilots: agent-runtime (agent ticket), agent-skills and rastro-ai (person tickets in their repos); rastro-ai's `affected` is a repo script over `cargo metadata` | A `{crates}` placeholder; svc-platform now | Crate knowledge stays in the repo that owns it; svc-platform later | grill Q8, Q9, Q10 |
| D10 | The config is read from `<repo>/.orchestrate/config.yaml` in the repo root's working tree when the run starts; `{base}` is the `--base` ref as given (default `origin/main`), never a resolved SHA; `full.run` runs through bash like an evidence check | Reading the config from the pinned commit; rendering `{base}` as a SHA | Matches plan-layout S1's loader; a ref keeps the rendered command stable across fix rounds | oracle review |
| D11 | The brief keeps its "Before you report COMPLETE, run:" line listing the required checks only, and adds one line naming `checks.full` as the check the worker does not run | Free wording | Gives the oracle a line to assert on, so a brief that still tells the worker to run the whole suite fails | oracle review |
| D12 | S8 is proven by a new test, `packages/agent-orchestrator/tests/test_check_tiers_repo.py`, that loads agent-runtime's `.orchestrate/config.yaml` and asserts its tiers; the file joins its ticket's Paths (re-approved mid-run) | Proving S8 by report checks only; making S8 `human` evidence | The covers gate needs a test node the ticket adds or changes for each scenario it satisfies, and AGENTS asks one test per scenario. The first answer (report checks only) could not pass covers | run decision, 2026-11-13 |
| D13 | The S6 oracle reads `events.jsonl` from `run_dir` itself (re-approved mid-run, same base) | Calling the fixture's reader | RunFixture's `events` became `run_events` mid-plan; the fixture name exists at only one of base and main, so the oracle depends on neither | run decision, 2026-11-13 |
| D9 | Case 6358e831 (a subagent redoing an open PR's work) and the "N of M runs" report field stay out | | A command boundary cannot catch the first; the second had one finding | grill Q2 (plan B), plan A Q2 |

## Out of scope

- Heavy-job admission, queueing or duplicate refusal in agent-runtime (`agent-runtime job`).
- svc-platform's config (a later change in that repo), and the agent-fleet heavy-job guard.
- Choosing tests by coverage maps (`pytest-testmon`); a repo may put any tool behind `affected`.
- rtk's fabricated `Pytest: No tests collected` on piped output (upstream).

## Risks

- **Security:** the config's commands run in the repo's own checkout, as the plan's evidence checks already do; `{paths}` is shell-quoted. Covered by S2.
- **Performance:** the point of the plan; verify adds one whole-suite run per plan, which `--skip-full` removes. Covered by S6, S7.
- **Migration:** opt-in; a repo without `checks` keeps today's behaviour and gets a warning (S4). plan-layout T1's loader has landed (#188).
- **Rollback:** delete the `checks` node, or pass `--gate`; nothing is stored outside the run's events.

## Seams

- `RunFixture.run_driver()` and the ledger packet / `t1/task.md` for the brief (S2, S3).
- `AcceptanceFixture` (`test_acceptance.py`): `write_plan`, `merged_on_main`, scripted worker and reviewer, `history()`, `wakes()`, `events("full")` for verify (S6, S7).
- `require_completable` through the report gate (`test_contracts.py`) for S5.
- The config loader's own tests (`test_config.py`, from plan-layout T1) for S1; `plan check` through `run_cli` for S4.
