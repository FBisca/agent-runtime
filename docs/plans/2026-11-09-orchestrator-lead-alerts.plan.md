# Plan: The Driver Tells the Lead When It Needs Them

Slug: `orchestrator-lead-alerts`. Living spec: `docs/specs/orchestrator-engine.md`. Status: **APPROVED**
(owner asked for the three fixes on 2026-11-09, after a live run in `svc-platform` stalled on each).

## Why

A live run (`org-chart-custodian-leaves`) stopped three times for reasons the Lead could not see:

1. `git worktree add` on a large repository outran `run()`'s 15-second default, so the first
   dispatch failed and the run exited.
2. The worker wrote the report the driver's own `CONTRACT` asks for — it ends with
   `"context":{"status":"unknown"}` — and `Report` forbids `context`. A finished ticket became a
   `decision` request.
3. The request's wake went to `wakes.jsonl` only. The `orchestrate` skill tells the Lead to launch
   and end its turn, so nobody ran `await`, and the run idled with nobody told.

## Decisions

| # | Choice | Rejected | Why | Status |
|---|---|---|---|---|
| D1 | `Report` accepts an optional `context` object. | Dropping `context` from `CONTRACT`. | Workers already write it; the ledger checkpoint carries it (`context` command). | APPROVED |
| D2 | A test fills the `CONTRACT` example and validates it with `Report`. | Fixing this one field. | The class is "the driver's instructions name a shape its model refuses". | APPROVED |
| D3 | Checkout-sized git calls (`worktree add`, `status`) take their own timeout. | Raising `run()`'s default. | Short calls should still fail fast. | APPROVED |
| D4 | A new wake is sent to the launching terminal with `hc send` (not `--force`). | `hc interrupt` on the Lead; a desktop notification. | `interrupt` presses the stop key: it ends the Lead's turn instead of telling it anything. `send` is the channel the `<hc>` exit notice already uses. | APPROVED |
| D5 | `launch` passes its `HC_TERMINAL_ID` as `run --notify-terminal`; without one, nothing is sent. | Reading the parent at run time. | The run's own `HC_TERMINAL_ID` is its own pane, not the Lead's. | APPROVED |
| D6 | A failed send is logged (`notify-failed`) and the run goes on. | Failing the run. | The wake is already on disk; the alert is a convenience. | APPROVED |

## Scenarios

- **S1** WHEN a worker's report carries `"context":{"status":"unknown"}`, THEN `validate_report` accepts it.
- **S2** WHEN the `CONTRACT` text's example report is filled in, THEN `Report` accepts it.
- **S3** WHEN the driver adds a worktree, THEN the `git worktree add` call has a timeout of at least 300 seconds.
- **S4** WHEN a run with `--notify-terminal t175` writes a new wake, THEN it runs `hc send t175 <one line naming the run and the wake>` once.
- **S5** WHEN the same wake is written again, THEN no second send happens.
- **S6** WHEN `hc send` fails, THEN the run logs `notify-failed` and finishes as it would have.
- **S7** WHEN `launch` runs under `HC_TERMINAL_ID=t175`, THEN the `run` argv carries `--notify-terminal t175`; WHEN there is no `HC_TERMINAL_ID`, THEN it carries none.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | Report `context`, contract parity test, checkout timeouts, wake alerts | S1–S7 | none | packages/agent-orchestrator/, docs/specs/orchestrator-engine.md | small | simple | agent | COMPLETE |
