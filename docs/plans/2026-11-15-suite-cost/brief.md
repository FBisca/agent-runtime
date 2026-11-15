# Brief: measure and gate the suite's cost

Slug: `suite-cost`. Contract: `plan.yaml`. Archive: **promote** into
`docs/specs/python-workspace-contracts.md`.

## Why

The whole suite is 2,300 integration tests and minutes of wall time; a ticket that adds a slow or
duplicated test raises it silently. The test-cost plan cut it once and archived R208–R221, but
nothing measures a *new* test, so the suite regrew. Two tests alone paid 60 s for a cleanup-order
bug and 62 s for a real 60 s deadline. The worker never read the `tdd` skill (its packet named only
"red-first"), and the reviewer and the judge had no timings to judge.

## What changes

- **T1** (S1): the child is killed and reaped in one cleanup (LIFO made `wait` run first), and the
  ledger deadline is injectable for the test that asserts it.
- **T2** (S2): the packet names the `tdd` skill and inlines its admission and cost rules for a
  backend with no skill loader; a YAML ticket reports `test_cost`, and verify refuses a changed
  covered test without its module and node seconds.
- **T3** (S3): the reviewer's numbered pass measures the diff's tests and blocks a material slowdown.
- **T4** (S4, S5): the fast suite is checked for three seconds and half a second per unit test, and
  CI compares its junit with `acceptance/suite/suite_budget.json`.

## Decisions

- **SC-D1** `test_cost` is a report field, not a new contract version: an old contract-3 report
  still reads, and verify enforces the field only when the diff changes a covered test.
- **SC-D2** The deadline seam is an environment variable
  (`AGENT_ORCHESTRATOR_LEDGER_DEADLINE`), because the test drives the driver as a subprocess; an
  unreadable or non-positive value keeps 60 s.
- **SC-D3** The per-module budget is a committed junit-derived baseline with a 1.5 tolerance and a
  2 s floor; a new module under the floor is free, and a real regression must be re-measured with
  `--write` on purpose.
