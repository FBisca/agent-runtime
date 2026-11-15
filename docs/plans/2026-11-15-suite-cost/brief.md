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

## After the merge: the gate met the runners

CI on the merge commit was red on five of seven jobs, and every reading was the machine or a test
paying for something it is not measuring:

- **The margin.** ubuntu 3.11 ran the whole job 1.7x slower than its 3.12 sibling and put
  `tests/test_dispatch.py` at 27.5 s against a 27.0 s budget: a 2% share deviation cost the build.
  R223 now also needs half again the budget, so the 25 s floor and the margin decide together
  (SC-D4).
- **The named lookup.** `HTTPServer.server_bind` reverse-resolves the address it bound, so each of
  the screening transport's servers waited on the macOS runner's resolver: 111 s for a module that
  costs 6 s here, and 67 s more with a five-second stub in place. `packages/agent-review/tests/local_http.py`
  binds without the lookup and polls its shutdown a hundredth of a second, not half a second
  (6.6 s -> 0.9 s here).
- **A node start per scenario.** `tests/test_workbench_mod.py` ran `node --test` 34 times to read
  104 tests: one run and a name match per scenario instead (28.3 s -> 0.7 s on the macOS job's
  reading).
- **A suite run measured twice.** `acceptance/suite/test_fast_suite.py` ran the fast suite twice for
  the same `--durations=0` output, and collected it four times: one warm-up, one measured run shared
  by both rows, and one guarded run (12.1 s -> 8.5 s here).
- **The three seconds on a runner.** macOS 3.11 measured the fast suite's call time at 3.22 s and one
  unit test at 0.62 s: R222 gains the same half-again margin as R223 (SC-D4).

SC-D4: a shared runner is not the gate. R222 and R223 keep their idle-machine numbers as the thing to
fix and fail the build only at half again, so a slow runner reports instead of refusing.
