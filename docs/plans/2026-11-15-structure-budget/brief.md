# Brief: a structure budget for the whole tree

Slug: `structure-budget`. Contract: `plan.yaml`. Lane: Full, regular·complex across four packages,
twelve tickets, and a `state` risk on the driver split.

## Why

`packages/agent-orchestrator/src/agent_orchestrator/driver.py` is 9,143 lines: a 6,426-line `Driver`
class of 255 methods, 185 top-level names and a 407-line `main()`. It is a third of all production
code and more than half of its own package. For a coding agent this costs more than tokens: the
repository's own "fix the class, not the instance" rule stops being enforceable when the other
instances sit six thousand lines away in a file nobody reads whole, so a worker sees one `require(...)`
and misses its four siblings.

Nothing gates this. The tree's only structural gates are a ruff diagnostic count (36 of a 45 budget),
a format count (0), a `ty` count (285 of 285) and the per-module suite-cost baseline (R223). No living
spec mentions a line count, a method count or a complexity limit.

Measured over the tracked tree, source and tests together: 11 modules over 1,000 lines (`driver.py`
9,143, `test_hooks.py` 1,953, `plan.py` 1,867, `test_event_wait.py` 1,775, `dispatch_store.py` 1,635,
`hc_tools.py` 1,577), 13 classes over 400 lines, 13 functions over 100 lines, 45 functions over 20
branch points, 23 with eight or more parameters. Test code is 53,550 lines against source's 27,435.

## What changes

- **`scripts/structure_budget.py`**: absent → measures five metrics over every Python file under
  `--root` and compares them with `acceptance/structure/structure_budget.json`, printing one offender
  line per growth and exiting 0, 1 or 2 (T1).
- **`.github/workflows/ci.yml`**: no structure gate → a step after the suite, beside the ruff, format,
  ty and suite-budget steps, failing the build on growth (T1).
- **`pyproject.toml`**: `scripts/structure_budget.py` routed to the installer suite by the `scripts/*`
  wildcard → routed to its own test module (T1).
- **`agent_orchestrator/timeutil.py`**: four copies of `now()` in `driver`, `events`, `runner` and
  `workbench`, two of them returning different types → one definition, `now()` and `iso()`, with every
  call site keeping the type it had (T2).
- **`agent_orchestrator/driver.py`**: a 9,143-line flat module → `agent_orchestrator/driver/`, with
  every published name still resolving (T3).
- **`driver/records.py`**: the pure record helpers out of `__init__.py` (T4).
- **`driver/runtime.py`, `driver/report.py`**: the runtime, `gh` and process helpers and the reporting
  tables out of `__init__.py` (T5).
- **`driver/book.py`**: the ledger spine becomes a `Book` collaborator; the durable record has one
  owner (T6).
- **`driver/gates.py`**: `advance` and the seventeen gate steps become a `Gates` collaborator (T7).
- **`driver/spawn.py`**: starting, spawning, resuming, handing over and swapping become `Spawning`
  (T8).
- **`driver/pull.py`**: the pull request lane — polling, fixing, reviewing, CI, merging, verifying —
  becomes `Pull` (T9).
- **`driver/archive.py`**: archiving, parking, reopening and the crash report become `Archive` (T10).
- **`driver/cli.py`** and `tests/test_cli_surface.py`: one 407-line `main` → one builder per command
  group, with a test pinning the twenty-five command names and holding the console script, `-m` and
  `agent_orchestrator.main` to the same `run_cli` (T11).
- **`driver/__init__.py`**: the package still holding the engine → a re-export facade, with the driver
  package paid on module lines and class methods (T12).

## Decisions

- **SB-D1** One script, `scripts/structure_budget.py`, measures five metrics — module lines, class
  public methods, function lines, function branch points, parameters — against one committed
  baseline, as a ratchet and not a hard ceiling. `ruff`'s `select` stays untouched. Why not ruff: it
  has no module-length rule at all and `too-many-public-methods` (the one that would catch `Driver`'s
  255) is preview-only, so a script is required for two of the five metrics and a second engine would
  mean two baselines and two contract tests (R197/R223, `scripts/suite_budget.py`, `suite-cost`'s
  SC-D3, `ruff rule PLR0904`: "This rule is in preview and is not stable").
  **Corrected 2026-11-15**: the reason above is too strong. It shows a script is required for two
  metrics; it does not show the other three must also be the script's. Measured on 0.17.4, `PLR0912`
  (branches, 12), `PLR0913` (arguments, 7) and `PLR0904` (public methods, 25) match three of the five
  exactly, and 122 of the tree's 189 violations are theirs. Ruff measures the tree in 0.077s against
  the script's 1.81s, and its JSON feeds the same git-baseline comparison, so one baseline and one
  ratchet survive the split. T16 does that; S20 holds it to the same offender lines. Two metrics stay
  in the script because no rule exists: there is no module-length rule, and `PLR0915` counts
  statements (the tree shows 15 of those), not lines.
- **SB-D2** The gate covers every Python file, source and test, with no exemption list and no
  `exclude` key: the walk skips `.git`, `.venv`, `node_modules` and `__pycache__` and nothing else.
  Why not source only: agent reading cost is mostly tests — twice the source, and the thing a worker
  reads to learn a contract. Why not `git ls-files`: the evidence drives the script over a temporary
  tree that is not a repository, so a tracked-file walk would be untestable from outside (and 142 of
  the tracked files are not Python anyway, and the shell side already has shellcheck).
- **SB-D3** The requirement lands in `docs/specs/python-workspace-contracts.md` under a new
  `## Requirements: structure-budget`, promoted with `domain: structure` at package `.` (a new root
  `acceptance/structure/`). Why not `orchestrator-engine.md`: it owns one package, and this rule binds
  all four plus the root tests. Why promote: a budget whose ceiling is meant to shrink needs its
  requirement to outlive the plan.
- **SB-D4** The plan lands the gate **and** the reductions it then measures: the driver split plus the
  `now()` dedupe. Why not the gate alone: it would leave the trigger file untouched and read as the
  plan not having happened. Why not every offender: `plan.py`, `dispatch_store.py`, `hc_tools.py` and
  the six >1,000-line test modules are mechanical line-moving that the baseline makes visible and
  non-growing on the day the gate lands, so they are follow-on plans.
- **SB-D5** `driver` becomes a package, `agent_orchestrator/driver/`, rather than the flat `driver.py`
  plus sibling modules. Why not flat siblings: `driver.__file__.parent` is asserted against the
  member's source directory in `test_engine_home.py` and used as `PACKAGE` in `test_routes_docs.py`,
  and both mean "the member's source directory" — after the split that is honestly
  `agent_orchestrator.__file__`; the sibling shape would buy a two-line test fix at the cost of seven
  package-top modules and a facade that must keep late-importing to dodge the cycle `plan.py` and
  `workbench.py` already work around.
- **SB-D6** The ceilings are 1,000 module lines, 25 class public methods, 50 function lines, 12
  function branch points and 7 parameters, with **zero tolerance** on a recorded row. What I rejected:
  the pylint/ruff default of 5 parameters (the tree has 23 functions at eight or more and would
  grandfather all of them, making every new signature a fight), and any tolerance factor (an
  unambiguous "grew past its recorded number" needs no fudge, and a legitimate growth is what
  `--write` is for).
- **SB-D7** `now()` moves to a new `timeutil.py` as `now() -> datetime` and `iso() -> str`; the
  `run_git()` duplication across `agent_eval` and `agent_review` stays as it is. Why: no workspace
  member imports another, so deduping `run_git` means creating a fifth member with its own manifest,
  lockfile entry, `impacted_map` row and compatibility baseline — a packaging decision spent on 12
  lines, and one that belongs in its own plan. `now()` is the copy that has actually drifted.
- **SB-D8** The offender line is the contract: `{path}: {metric} {value} > recorded {row} (ceiling
  {n})`, `{path}: {metric} {value} > ceiling {n}`, or `{path}: {metric} paid (recorded {n})`, with
  exit 0 clean, 1 for an offender and 2 for a refusal. `--metric` is repeatable (a last-wins parser
  would check one metric while the caller believes it asked for two) and a `--paid` path that
  measures no file is a refusal, not a pass (otherwise a misspelt path reports a paid debt).
- **SB-D9** `--write` re-measures every file that already has a row, adds a row for any file over a
  ceiling, drops a row whose file is gone, and records `measured_at`. A row may deliberately sit
  under its ceiling: that is how an area declares a tighter limit than the tree-wide ceiling, and it
  is what makes `--paid` mean "no debt here" rather than "not too much debt".
- **SB-D10** The durable-record claim is its own scenario, S13, verified by `test_ledger.py` with a
  negative control that reorders the marker keys the ledger writes; the surface claim, S10, is
  verified by the whole orchestrator suite and is satisfied only by untagged tickets. Why: a negative
  that breaks the ledger cannot prove a claim about the CLI's published names, and a plan that edits
  away the very assertion guarding its own claim does not get to call that assertion evidence.

## Out of scope

`plan.py` (1,867 lines), `dispatch_store.py` (1,635), `hc_tools.py` (1,577) and the six >1,000-line
test modules are recorded by the baseline and split by follow-on plans; `run_git()` stays duplicated;
`ruff`'s complexity rules stay off; the JavaScript under `herdr/` and `install.sh` keep the gates they
have (none, and shellcheck).

## Risks

- **state** (T6, T7, T10, T12): the durable record survives the split — S13 runs `test_ledger.py` and
  its negative control proves the check is load-bearing; S11 proves the structure.
- **state, second order** (all eleven driver tickets): the split is one serial chain (expand, then one
  batch per collaborator, then contract), so each ticket branches from the previous one's merge and no
  two ever touch `driver/` at once; the whole orchestrator suite is S10's evidence for the untagged
  batches and runs in CI for every ticket.
