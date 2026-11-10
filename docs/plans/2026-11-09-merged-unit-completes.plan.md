# Plan: a merged unit always completes

Slug: `merged-unit-completes`. Living spec: `docs/specs/orchestrator-engine.md`. Status: ARCHIVED 2026-11-10.

## Why now

Four drives so far needed the plan cell set by hand after a human merge, because of two defects. First, the ledger refuses COMPLETE for a merged PR, either because of a check-name string mismatch or because an honest `not_run` / `failed` was reported. The refusal turns into a Lead request, which turns into an auto-SKIP that blocks the dependents. Second, the awaiting-merge gate raises FileNotFoundError when the unit's worktree is gone, and the ticket stays stuck for the rest of the run. Both are filed in `2026-11-09-installer-archive-and-ledger-followup.handoff.md` §2–§3.

## Sources

| Source | What it settles |
|---|---|
| `docs/plans/2026-11-09-installer-archive-and-ledger-followup.handoff.md` §2, §3 | The two defects, their symptoms, and the candidate fixes |
| `agent-skills/docs/plans/2026-11-09-test-suite.handoff.md` (~L42–50) | The same defects seen from agent-skills. Offers "refuse at the report, not at the merge" |
| `packages/agent-orchestrator/src/agent_orchestrator/driver.py` @ origin/main | `checkpoint()` COMPLETE rules (exact-string `issubset`, all `passed`); `step_merge` and `merged_early` catch ValueError only; `checkout_snapshot` uses `resolve(strict=True)`; the `step_cleanup` busy check matches on `worker` only; `record_report` always checkpoints PARTIAL; `merged_report` has `checks: []` |
| `docs/specs/orchestrator-engine.md` | The living spec for merge, cleanup and gates (R2, R15) |
| `AGENTS.md` | A behaviour change starts as a scenario and goes red first. Classify a test by its behaviour |
| Grill, this session (2026-11-09) | Q1–Q11 answers, recorded as D1–D11 |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Scope: code fixes §2 and §3 only | Person-owned tickets for §1 (land T5, archive) and §4 (engine tree cleanup) | Those are one-off operational steps with no behaviour to test | Grill Q1 |
| D2 | A merged PR wins: COMPLETE whatever the stored report says | A Lead request followed by auto-SKIP (today) | A merge is final. Its record must not wait on a human answer | Handoff §2; grill Q2 |
| D3 | Also refuse a non-completable report when the worker files it, so the worker fixes it before the merge | Refusing only at the merge | The defect is caught while the worker can still act on it | agent-skills handoff; grill Q2 |
| D4 | A report-time refusal messages the worker with the reason, keeps the gate and counts one fix round. The round cap escalates to the Lead as today | A Lead request on every refusal | The worker owns the report. The cap keeps a loop bounded | Grill Q8 |
| D5 | Merge-wins writes `[merged-unverified] <task>: <reason>` to deferred.md through `defer_merged`, plus a log line. The ledger keeps the original checks unchanged | A finding only, or rewriting the checks to `passed` | The evidence stays honest and the human sees what went unverified | Grill Q9 |
| D6 | Check names are normalised before comparing: strip leading `bash`, `sh`, `uv run`, `python -m`, `python3 -m` (in any combination) and `./`, collapse whitespace, then compare exactly. This applies to both the gate string and the reported command | `endswith` (would let `echo scripts/checks.sh` pass); exact match (today) | Fixes the mismatch class without accepting a command that never ran the gate | Handoff §2; grill Q3, Q10 |
| D7 | `not_run` with a reason is accepted on COMPLETE for a non-required check only. A required check must be `passed`, and `failed` anywhere still refuses. The CONTRACT text given to workers states these rules and the normalisation | Dropping `not_run`; accepting it everywhere | An honest report about an optional check isn't punished, and the gate stays strict | Grill Q4 |
| D8 | On the merge path, a missing `packet.checkout` records `checkout_at_checkpoint: null`, logs the path and completes | Catching OSError only (the ticket still never completes) | Merged = final. The snapshot is evidence, not a precondition | Handoff §3; grill Q5, Q11 |
| D9 | Off the merge path (a PARTIAL checkpoint while the worker is live), a missing checkout still raises | Tolerating it everywhere | A live worker with no tree is a real fault | Grill Q11 |
| D10 | The cleanup busy check also counts any non-closed task with the same `packet.checkout`, whatever its worker name | Worker-only match (today) | Closes the gap where a sibling under another worker name loses its tree (the likely t12/t12d1 cause) | Fact-check of origin/main; grill Q5 |
| D11 | Pure `checkpoint()` rules get a new unmarked unit test file. The merge, report and cleanup flows get integration tests through GateFixture and fake gh | Putting the unit tests in `test_orchestrate.py` / `test_merge.py` (integration-marked) | AGENTS.md: classify by behaviour | Grill Q6 |
| D12 | No hot-patch of the live drives. The fixes land on main and reach a drive when it restarts from main (handoff §4) | Patching the feature-branch engine | Swapping a checkout under a running driver is the §3 trap | Handoff §4; grill Q7 |
| D13 | `merged_report` takes checks from the newest attempt of the unit that holds a report (current attempt first, then its predecessors) | The current attempt only (T2 as merged) | A unit merged during a fix round has no report on its live attempt; the evidence lives on the predecessor | T5 worker block, 2026-11-10 |
| D14 | A merge completes the unit's newest attempt; S6/S7 tests assert on it, since a report-time refusal (T4) releases the first attempt before the merge | Asserting on the first attempt (T2's tests) | T4 and T2 interact: on #88 CI, S6/S7 read RELEASED | T4 CI on PR #88, 2026-11-10 |
| D15 | A CI finding keeps a capped excerpt (the failing job's own tail) and the full log at `log_path` | Pasting `gh run view --log-failed` whole into every failing check's item (today) | Six 270 KB copies broke the 64 KiB handoff limit and stalled T1 and T4 | T1, T4 escalations, 2026-11-10 |

## Scenarios

- **S1** WHEN the packet requires `scripts/checks.sh` and the report's passed check is `bash scripts/checks.sh`, `./scripts/checks.sh` or `sh ./scripts/checks.sh`, THEN `checkpoint()` accepts COMPLETE.
- **S1a** WHEN the packet requires `pytest` and the report's passed check is `uv run python -m pytest`, THEN `checkpoint()` accepts COMPLETE.
- **S2** WHEN a required check appears only behind a prefix not on the strip list (e.g. `echo scripts/checks.sh`) or is absent, THEN `checkpoint()` refuses COMPLETE and names the missing check.
- **S3** WHEN a COMPLETE report carries a non-required check with `not_run` and a reason, THEN `checkpoint()` accepts it.
- **S3a** WHEN a COMPLETE report carries a `not_run` check with no reason, THEN `checkpoint()` refuses it.
- **S3b** WHEN a required check is reported `not_run`, THEN `checkpoint()` refuses COMPLETE.
- **S4** WHEN a COMPLETE report carries any `failed` check, THEN `checkpoint()` refuses COMPLETE.
- **S5** WHEN a worker reports its unit ready and the report fails the COMPLETE rules, THEN the driver messages that worker with the refusal reason, the ticket keeps its gate, one fix round is counted, and no Lead request is written.
- **S5a** WHEN report-time refusals reach the fix-round cap, THEN the Lead is asked, as for any capped unit.
- **S6** WHEN a PR is merged and its stored report fails the COMPLETE rules, THEN the ledger task is COMPLETE with its original checks, the plan cell is COMPLETE, deferred.md has a `[merged-unverified] <task>: <reason>` line, no Lead request is written, and the dependents become dispatchable.
- **S7** WHEN a PR merges early (`merged_early`) on a drive with a named `--gate`, THEN the unit completes as in S6.
- **S7a** WHEN a PR merges early, THEN the ledger task keeps the checks of the worker's stored report, not an empty list.
- **S8** WHEN a PR is merged and `packet.checkout` no longer exists, THEN the task is COMPLETE with `checkout_at_checkpoint: null`, a log line names the missing path, and `(task, "awaiting-merge")` is not in the stuck set.
- **S8a** WHEN a PARTIAL checkpoint runs and `packet.checkout` does not exist, THEN `checkpoint()` raises.
- **S9** WHEN cleanup runs for a COMPLETE task whose checkout is also used by a non-closed task under a different worker name, THEN the worktree is kept.
- **S10** WHEN several CI checks fail with long logs, THEN each CI finding carries only its own job's capped excerpt, the full log is at `log_path`, and the fix-round findings file stays under 64 KiB.

## Out of scope

- Handoff §1: land the installer plan's T5 (`gh pr merge --squash`, without `--delete-branch`) and let the driver archive it into `docs/specs/installer-contract.md`. You do this by hand.
- Handoff §4: one engine tree. After both live drives end: check out main and fast-forward pull, remove `../agent-runtime.worktrees/drive`, delete `local/drive-engine`, then relaunch from the main `.venv` with an explicit `--gate` and a named `--routes-file`. You do this by hand.
- Back-filling the cells already hand-set in past drives.
- Any change to the ledger schema.

## Risks

- **Security:** Merge-wins records COMPLETE for unverified work. This is mitigated because a human already merged it, and `[merged-unverified]` in deferred.md plus the kept checks make it visible. D6's strip list is closed (no `endswith`), so an arbitrary command can't pass for the gate.
- **Performance:** n/a. One more path comparison per cleanup and one string normalisation per check.
- **Migration:** No schema change. `checkout_at_checkpoint` may now be null, so readers of that field must tolerate null (T3 checks the in-repo readers). The live drives keep the stale engine until they restart (D12).
- **Rollback:** Every change is in `driver.py` plus tests. Revert the PR, and restart any drive that picked it up.

## Seams

- `checkpoint(data, task_id, report)`, a pure function over a dict ledger, for S1–S4 and S8a (unit, new file).
- `Driver.step_merge`, `merged_early` and `step_cleanup` through `tests/fixtures.py` GateFixture with `fake_gh` answering MERGED, for S6–S9 (integration, `test_merge.py` / `test_pr.py`).
- The report path (`record_report` → gate) through GateFixture, for S5 and S5a (integration, `test_gates.py`).

## Tickets

Every ticket branches from main after its blockers merge. Line numbers in the handoff are stale, so tickets reference functions by name.

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | `checkpoint()` COMPLETE rules: check-name normalisation (D6), `not_run`+reason for non-required checks (D7), and the CONTRACT text updated to state both | S1, S1a, S2, S3, S3a, S3b, S4 | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_checkpoint_rules.py | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/82 (432be9c) |
| T2 | Merge wins: `step_merge` and `merged_early` complete a merged unit whose report fails the rules, write `[merged-unverified]` to deferred.md, and make no Lead request | S6, S7 | T1 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_merge.py, packages/agent-orchestrator/tests/test_pr.py | regular | complex | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/87 (2bc1534) |
| T3 | Missing checkout on the merge path completes with a null snapshot, while a PARTIAL checkpoint still raises. The cleanup busy check also matches on `packet.checkout`. Covers `settle_merged`, the merge path's home since T2 | S8, S8a, S9 | T2 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_merge.py, packages/agent-orchestrator/tests/test_checkpoint_rules.py | regular | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/89 (0ca1605) |
| T4 | Report-time refusal: a ready report that fails the COMPLETE rules is bounced to its worker with the reason and counts a fix round. The cap escalates to the Lead. S6/S7 tests assert on the unit's newest attempt (D14) | S5, S5a | T1, T5 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_gates.py, packages/agent-orchestrator/tests/test_merge.py, packages/agent-orchestrator/tests/test_pr.py | regular | complex | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/88 (e150fdc) (set by hand: the live driver could not re-read CI past its oversized findings; see #97) |
| T5 | Test gap from T2 review: `merged_early` keeps the worker's original checks in the ledger, asserted so a revert to `checks: []` fails and `merged_report` takes checks from the newest attempt with a report (D13) | S7a | T2 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_pr.py, packages/agent-orchestrator/tests/test_merge.py | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/92 (5ff9080) |
| T6 | CI findings carry a capped per-job excerpt and the full log at `log_path` (D15) | S10 | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_pr.py | small | simple | agent | COMPLETE 2026-11-10 · merged https://github.com/FBisca/agent-runtime/pull/98 (aa217f6) (set by hand: the live driver predates #87 and refused the report) |
