# Brief: plan check maps evidence selectors to the tests they select

Slug: `plan-check-evidence-selectors`. Contract: `plan.yaml`. Lane: **Small** (one lint rule, one requirement).

## Why
Nothing in `plan check` mapped a scenario's evidence `check` to the tests it selects. Two real planning runs shipped
the gap:
- a plan whose nine checks all named tests the tickets had yet to write passed `plan check` with a vacuous
  `baseline: fail`;
- a plan shipped `-k "S1 or control"` over a file that also held `test_S10_...`, so S1's evidence ran S10's test:
  pytest's `-k` matches by substring.

## What changes
- **T1** (S1, landed with the plan): `selector_problems` joins `lint_problems`, so `plan check`, `plan approve` and `plan amend` run it
  and the driver's load does not (R116: an approved plan is never re-linted). For a folder plan's `check` that runs
  pytest with `-k` over its `acceptance` files, it refuses when the selector
  (a) names no scenario id, or not the scenario's own;
  (b) selects no test in those files other than a `test_control_*` one;
  (c) selects a test whose name carries a scenario id the selector did not name.
  Each refusal names the scenario and the anchored selector `-k "test_<id>_ or test_control_"`.

## Decisions
- The rule sits in `lint_problems`, not `folder_problems`: `semantic_problems` (and so `folder_problems`) runs in
  the driver's load too, which would re-lint approved plans.
- The acceptance files are read with `ast`: no import, no execution, no pytest run. A test is a `test_*` function,
  or a `test_*` method of a class named `Test*` or of any class with a base (a `unittest.TestCase` subclass is
  collected whatever its name, and the repository's acceptance classes are named `...Tests`).
- `-k` is modelled as `or`-joined terms, each a case-insensitive substring of the test's function, class or module
  name. An expression using `and`, `not` or parentheses is not linted; directory names, markers and parametrize ids
  are not modelled.
- A test name carries a scenario id when one of its `_`-separated words is `S<n>` (upper-case S, optional letter).
- Not linted: a legacy plan (oracle files, no `acceptance/`), a check with no `-k`, a check that runs no pytest,
  evidence with no `.py` acceptance file.

## Out of scope
Linting the check's other file arguments, running pytest's collection, and re-linting approved plans.

## Hand-off
1. `uv run --frozen --all-packages agent-orchestrator plan check docs/plans/2026-11-15-plan-check-evidence-selectors/plan.yaml`.
2. Every plan folder on main still prints `{"valid": true}`.
