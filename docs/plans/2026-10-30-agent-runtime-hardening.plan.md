# Plan: agent runtime dispatch hardening

Slug: `agent-runtime-hardening`. Living spec: `docs/specs/agent-runtime.md`. Status: ARCHIVED 2026-10-30.

## Why now

The dispatch plan shipped and passed live acceptance, but its final advisory review found gaps that reproduce the live-run failure class: a worker whose terminal vanished outside the runtime cannot be stopped, so every later `wait(all)` returns `failed`; failed spawns can orphan running terminals; diagnostics break exactly when the store or identity fails; native subagents can pin a session and read lead reports. Claude leads outside auto mode are prompted on every runtime call, the optional spawn-time `task_id` promised by the dispatch plan was never built, hot queries scan large message bodies, and docs still list nine tools and version 0.2.0.

The user waived the PRD prerequisite for this follow-up on 2026-10-30; the archived dispatch plan and the living spec define the feature.

## Sources

| Source | What it settles |
|---|---|
| User, 2026-10-30: scope and PRD waiver | All eight review gaps in scope; no PRD |
| User, grilling rounds 1–2, 2026-10-30: "Yes to all" | D1–D13 below |
| [Archived dispatch plan](2026-10-30-agent-runtime-dispatch.plan.md) | D4 diagnostics fallback, D6 names display-only, D8 failure ≠ success, D11/D20 task ownership and dependency gating, D12 hc owns lifecycle, D19 pin only after proven assignment, D21 subagent guard at the hook, D25 failed spawn inactive, D26 wait membership; API direction promised optional linked `task_id`; Risks/Performance asked for indexed queries |
| [Living spec](../specs/agent-runtime.md) | R54 (stop must not strand waiters), R55 (diagnostics when delivery fails), R56 (same flow in both CLIs, 400-word description budget), R48 (subagent mutations fail first), R51 (report updates linked task), R64–R65 (failed spawn, membership) |
| Read-only gather of `agents/plugins/agent-runtime/` at a174381 | `stop` needs `hc get`+`hc rm` before `store.stopped`; observed exits live only in memory; spawn post-run failures skip `discard`; diagnostics catch only sqlite/OS errors after identity/actor; hook allows subagent `wait`/`status` with `assignment_id`; only one secondary index; `acked` stored after large `body`; no task release on stop/fail; resume leaves task on old assignment |
| Tracked `agents/.claude/settings.json`, `agents/.codex/config.toml` | Claude allows only `mcp__notion` and does not enable the plugin; Codex pre-approves the six mutating runtime tools, reads fall to `auto` |
| `README.md:111–114`, `.claude-plugin/plugin.json`, spec line 4 | Nine-tool lists and stale 0.2.0 version |
| Read-only store query, 2026-10-30 | 3 teams, 13 workers, 14 assignments; 2 pre-D23 orphans in unreachable team 1 (one `working`, one NULL address); no live stranded worker; schema `user_version` 2 |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | When hc reports the terminal missing, `stop` records the worker stopped in the caller's team and skips kill/rm; a name now owned by another lead's terminal marks only our record and never touches theirs | Require `hc get`/`hc rm` success first | A vanished worker otherwise strands every later wait (R54/R65) | User Q1 |
| D2 | `wait` persists an observed exit, interrupt or replacement as a failed assignment plus one failure message to the lead; acknowledging it removes the assignment from later snapshots | In-memory failure recomputed each call | Ack can then clear it per D26 without a mandatory stop | User Q2 |
| D3 | status/collect/list return hc state first; store storage errors and missing identity become `delivery_error`; `list` joins the fallback; `role=unknown` degrades to hc-only; authorization failures stay errors | Widen catch to every StoreError; keep store-first | R55 fallback must survive store/identity failure; T5's forged-caller rule stays | User Q3 |
| D4 | Spawn resolves identity and validates inputs before `hc run`; every failure after `hc run` (no address, cancel, bind error, unsent first message) removes the terminal and fails the assignment | Keep unsent terminals for inspection | No orphan terminal runs a task the store cannot accept | User Q4 |
| D5 | The hook allows native subagents only `task list` and receipt-free `status`/`list`/`collect` without `assignment_id`; it denies `wait` and any call carrying `assignment_id` | Deny only mutations and receipts | Prevents session pinning and lead-report reads by subagents (D19/D21) | User Q5 |
| D6 | Tracked Claude settings allow the whole runtime server `mcp__plugin_agent-runtime_agent-runtime` and track `enabledPlugins` for `agent-runtime@dotfiles` plus the `dotfiles` marketplace | Allow reads only; keep enablement local | Parity with Codex approvals (R56); a clean checkout must load the plugin; hooks still guard subagents | User Q6 |
| D7 | Add indexes `assignments(team,current)`, `assignments(worker)`, `workers(address)`, `messages(assignment,acked)`, `tasks(team,status)`, `tasks(assignment)` with `CREATE INDEX IF NOT EXISTS`; no schema version bump | Pruning, deferred read transactions, per-team IPC wakeups | Removes body-overflow scans on every wait without breaking 0.2.x writers | User Q7 |
| D8 | `spawn` accepts optional `task_id` and links it in the registration transaction: task pending, dependencies complete, one winner versus concurrent claims, owner = new worker and assignment; result returns `task_id` | Lead-side `update` link; link after spawn | Delivers the plan's API direction under D11/D20 | User Q8 |
| D9 | Stop and failed spawn return an unfinished linked task to `pending` with no owner in the same transaction | Leave `in_progress` until the lead resets it | No task stays owned by a dead assignment | User Q9 |
| D10 | No migration for pre-D23 orphan records | One-time cleanup | Unreachable team; no snapshot can include them | User Q10 |
| D11 | `resume(task_id)` moves the task to the new assignment when it is in_progress/blocked and owned by the same worker, or pending with dependencies complete, in the same transaction; resume without `task_id` leaves links unchanged | Auto-transfer on every resume | Fresh assignment identity (D9 of dispatch) must not strand the task | User Q11 |
| D12 | A failure persisted by D2 also returns its unfinished linked task to `pending` | Release only on stop/spawn failure | Every failure path releases tasks identically | User Q12 |
| D13 | `stop` stays name-based, scoped to the caller's team in the store | Stop by `worker_id` | D1 already handles reused names; no new field needed | User Q13 |
| D14 | README, `plugin.json` description and the spec intro list all ten tools and no hard-coded stale version; plugin version bumps once at the end so installed caches refresh | Bump per ticket | Docs drift found in review; caches copy the plugin | Gather; dispatch D15 practice |
| D15 | status, list, collect and task list piggyback pending inbox messages only when the call carries a `receipt`; `wait` and mutating tools are unchanged | Deny subagent reads; hook-injected no-inbox flag | Subagents are already denied receipts (D5), so they never see the lead's inbox; leads still receive everything through `wait` and later receipt-bearing calls; same in both CLIs | T4 review finding; user, 2026-10-30 |

## Scenarios

- **S1** WHEN a lead stops a worker whose hc terminal no longer exists, THEN `stop` succeeds, the worker is recorded stopped, and the next `wait(all)` excludes it instead of returning `failed`.
- **S2** WHEN a lead stops a worker whose name now belongs to another lead's terminal, THEN only the caller's record is marked stopped and the other terminal is neither killed nor removed.
- **S3** WHEN `wait` observes a selected worker exited, interrupted or replaced before completion, THEN that result reports the failure with one persisted failure message, and after its receipt is acknowledged later `wait(all)` snapshots exclude the assignment.
- **S4** WHEN a stopped worker, a failed spawn or a persisted observed failure has an unfinished linked task, THEN the task returns to `pending` with no owner in the same transaction.
- **S5** WHEN status, collect or list run while the store fails, identity cannot be resolved, or hc role lookup times out, THEN they return hc state with an explicit `delivery_error` instead of an error.
- **S6** WHEN a forged identity or an unproven worker calls status, collect or list, THEN the call still fails with its authorization error and returns no hc data, inbox or pin.
- **S7** WHEN spawn fails after `hc run` (address unreadable, cancellation, bind error or unsent first message), THEN the terminal is removed, the assignment is failed, and the worker is inactive.
- **S8** WHEN spawn cannot resolve identity or validate its inputs, THEN no terminal is started.
- **S9** WHEN a native Claude or Codex subagent calls `wait` or any runtime tool with `assignment_id`, THEN the hook denies it before the server runs; `task list` and receipt-free status/list/collect without `assignment_id` remain allowed.
- **S10** WHEN Claude loads the tracked settings from a clean checkout, THEN the agent-runtime plugin and marketplace are enabled and all ten runtime tools are allowed without prompts, while subagent denials still apply.
- **S11** WHEN the store initializes, THEN the D7 indexes exist, `user_version` is unchanged, and old-shape 0.2.x writes still succeed.
- **S12** WHEN `wait` snapshots and reads membership, THEN the query plans use the indexes and do not scan message bodies.
- **S13** WHEN a lead spawns a worker with a ready `task_id`, THEN the task links to the new worker and assignment atomically, the result returns `task_id`, and the worker's complete report finishes the task.
- **S14** WHEN spawn names a task that is missing, foreign, not pending, has unmet dependencies, or loses a concurrent claim, THEN spawn fails before starting a terminal and exactly one claimant owns the task.
- **S15** WHEN a lead resumes a worker with `task_id` for its in_progress/blocked task, or a ready pending task, THEN the task moves to the fresh assignment and that assignment's complete report finishes it; resume without `task_id` leaves links unchanged.
- **S16** WHEN users read README, `plugin.json` or the spec intro, THEN all ten tools including `task` are listed, no stale version is stated, and tool descriptions stay within 400 words.
- **S17** WHEN the hardened plugin is installed and a lead kills a worker's terminal outside the runtime, THEN `stop` succeeds and the next `wait(all)` is `empty`, and the live mixed-CLI acceptance still passes.
- **S18** WHEN status, list, collect or task list run without a `receipt`, THEN the result carries no inbox piggyback and acknowledges nothing; WHEN the same call carries a valid receipt, THEN pending inbox messages are returned as before.

## Out of scope

- Pruning acknowledged messages, receipts or callers; deferred read transactions; per-team IPC wakeup scoping.
- Migrating pre-D23 orphan records.
- Stop by `worker_id`; lead-side task assignment through `update`.
- Archiving the separate `agent-runtime-tools` plan.
- Native Claude Teams, hc upstream changes, cross-team messaging.

## Risks

- **Security:** D5 narrows subagent access; D6 allow rules do not bypass hook denials or worker permission dialogs; D3 keeps authorization failures as errors so diagnostics never leak another team's state; D1 never acts on another lead's terminal.
- **Performance:** D7 indexes remove per-wait scans of large message bodies; D2 adds at most one write per observed failure; no new polling.
- **Migration:** indexes are additive with `IF NOT EXISTS`; no schema version change, so running 0.2.x servers keep working; D10 leaves orphans; D6 moves enablement into tracked settings, so the user's local settings diff must be three-way merged at install.
- **Rollback:** revert the PRs and reinstall the previous plugin version; indexes and persisted failure messages are harmless to older code; restore local settings from the pre-integration backup.

## Seams

- Existing subprocess JSON-RPC harness with fake hc and temporary SQLite stores: S1–S8, S11–S15.
- Hook unit tests for both CLI payload shapes: S9.
- Settings/wiring tests over tracked `agents/.claude/settings.json`: S10.
- Description-budget and docs assertions in the activation tests: S16.
- Opt-in live acceptance test (`AGENT_RUNTIME_LIVE=1`) after install: S17.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Stop and observed failures never strand waits: stop of a missing or reassigned terminal, persisted observed failures with one failure message, and task release on stop/failure | S1–S4 | none | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR39 c946059; converge/review PASS; CI n/a) |
| T2 | Spawn never orphans terminals: identity and input validation before `hc run`, terminal removal on every post-run failure, task release on failed spawn | S7, S8 | T1 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR40 cee2193; converge/review PASS; CI n/a) |
| T3 | Diagnostics survive store and identity failures: hc-first status/collect/list with `delivery_error`, authorization still enforced | S5, S6 | T2 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR41 7c33549; converge/review PASS; CI n/a) |
| T4 | Subagent guard and Claude permissions: hook denies subagent `wait` and `assignment_id` calls in both CLIs; tracked Claude settings enable and allow the runtime | S9, S10 | none | agents/plugins/agent-runtime/hooks/, agents/plugins/agent-runtime/tests/, agents/.claude/settings.json | agent | COMPLETE (PR37 770a88f; converge/review PASS; CI n/a) |
| T5 | Indexed dispatch queries with an unchanged schema version and query-plan assertions | S11, S12 | none | agents/plugins/agent-runtime/server/dispatch_store.py, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR38 a2bf629; converge/review PASS; CI n/a) |
| T6 | Task links on spawn and resume: optional `task_id`, atomic linking with dependency gating, transfer on resume, returned `task_id` | S13–S15 | T2, T5 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR42 55333e1; converge/review PASS; CI n/a) |
| T7 | Docs and version: ten-tool lists in README, `plugin.json` and spec intro, description budget kept, plugin version bump | S16 | T3, T4, T6, T9 | README.md, agents/plugins/agent-runtime/, docs/specs/agent-runtime.md, agents/.claude/agents/implementer.md, agents/.codex/agents/implementer.toml, agents/.claude-plugin/marketplace.json | agent | COMPLETE (PR44 b9ba449; 277 tests, converge/review PASS; CI n/a) |
| T9 | Receipt-gated inbox piggyback on the read tools subagents may call, so native subagents never read a lead's inbox | S18 | T3 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR43 049c8ab; converge/review PASS; CI n/a) |
| T8 | Install the hardened plugin (main fast-forward with local settings three-way merge, plugin update, session restarts), then the agent runs the live acceptance including an external terminal kill | S17 | T7 | agents/plugins/agent-runtime/tests/ | Felippe | COMPLETE (user-approved install 2026-10-30: main rebased, local config kept, plugin 0.2.3; live S17 external kill and S24 mixed-CLI passed) |
