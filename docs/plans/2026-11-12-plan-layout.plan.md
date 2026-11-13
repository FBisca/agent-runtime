# Plan: plan layout

Slug: `plan-layout`. Contract: `2026-11-12-plan-layout.plan.yaml`.

## Why now

A plan is three siblings today (`X.plan.yaml`, `X.plan.md`, `X.oracle/`). Its tests carry a fake `.py.oracle`
suffix, and they are promoted into `tests/` beside unit tests, with nothing to group them. Plans always live in the
target repo and always archive through a PR that writes a spec, so a repo the user does not want to touch
(svc-platform) cannot host a plan without being changed. The first external plan (mfo-view-quality) already runs
from the state dir by hand. With the cleanup (#184) done, now is the moment to set the layout before more plans pile
up in the old one.

## Sources

| Source | What it settles |
|---|---|
| The request and the grill of 2026-11-12 | Every decision below marked "user" |
| `docs/specs/plan-contract.md` | R5 forbids discovery from *creating* a dir or config, not reading one; R6 already runs a plan outside the repo with `--repo`; R25/R29 archive promotion; R2 digest |
| `plan.py:253-256, 520-548, 810-905` | Oracle dir derivation; digest covers canonical plan bytes and oracle file names, not paths; discovery via `git ls-files` |
| `verify.py:74-92`, `driver.py:4646-4667` | Two more string derivations of the sibling layout (fix the class) |
| `runner.py:286-288` | Placement copies snapshot bytes into the scratch tree, from any source dir |
| `routes.py:36-39, 268-280` | Config precedent: `<repo>/.orchestrate/`, YAML |
| svc-platform (top level) | pnpm/turbo TypeScript monorepo, vitest; `/.orchestrate/` already excluded through `.git/info/exclude` |
| Root `pyproject.toml`, `ci.yml:31` | Explicit `testpaths`; CI runs `uv run pytest -n 4`; one `fixtures.py` in the tree |
| Run state | Live drivers on agent-workbench and orchestrator-audit-fixes; mfo-view-quality mid-approve from the state dir |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | "oracle" becomes "acceptance" | Keep "oracle"; "evidence" | Plain word for what the tests are; "evidence" is what they produce | User |
| D2 | Config at `<repo>/.orchestrate/config.yaml`, nested nodes `plans.dir`, `acceptance.dir`, `archive.mode`; repo-only, paths relative/`~`/absolute, unknown keys refused; missing file = docs/plans, acceptance, promote | Flat keys; a user-level fallback | Same place and format as routes.yaml; svc-platform already excludes `/.orchestrate/` | User, grill Q1, Q14 |
| D3 | One folder per plan: `<plans.dir>/<date>-<slug>/` with plan.yaml, plan.md, acceptance/, negatives/ | Siblings `X.plan.*` + `X.oracle/` | One thing to review, move and delete | User, grill Q11 |
| D4 | Acceptance tests live in the plan folder until archive; promote moves them to `<package>/<acceptance.dir>/<domain>/`, which CI collects | Commit them red to the package dir; a deselect marker | Main only ever holds green tests and CI keeps every delivered one alive | User, grill Q2 |
| D5 | Archive always runs; its mode decides the acceptance: `promote` (move tests, write spec, delete the plan folder, one PR) or `discard` (delete the plan folder and its tests, write nothing; `spec` optional; delete-only PR when the folder is tracked) | A `keep` mode; leaving the plan folder after promote | The spec and the promoted tests carry the plan; discard keeps svc-platform untouched | User, grill Q3, Q7, Q8 |
| D6 | The driver, not the archive worker, moves the acceptance files and deletes the plan folder before the worker starts | Ask the worker to do it | Deterministic, and today the driver already places promoted oracles (R25) | Engine precedent R25 |
| D7 | `domain:` is an explicit top-level slug, required for promote; start-planning proposes it, reusing an existing domain folder when one fits | Derive from the spec stem; the plan slug | Spec stems are coarse and echo package names (`agent-runtime/agent-runtime`); slugs are transient | User, grill Q18 follow-ups |
| D8 | Keys: `acceptance: [files]`, `package: <dir \| .>`, `negative:` relative to the folder, top-level `archive:` override; `place` and `promote` go | Keep `place` | The destination follows from package, config and domain | User, grill Q10, Q12 |
| D9 | Root pytest gets `pythonpath` for each package's tests/ and `testpaths` for each acceptance dir; `plan check` refuses an acceptance basename that collides with a test module in its package | A conftest per acceptance dir | Only one `fixtures.py` exists; pytest's prepend import mode needs unique basenames | User, grill Q9; gather |
| D10 | `plans list` reads the configured dir from the filesystem; without a config it lists as today; nothing is created | `git ls-files` only | Ignored and external plans must be visible; R5 still holds | User, grill Q4 |
| D11 | The engine reads both layouts; new plans use folders; the legacy reader is removed by a later plan once agent-workbench, orchestrator-audit-fixes, label-evidence and mfo-view-quality have archived | Re-approve and migrate now | Key and suffix renames change digests; two drivers are live | User, grill Q5, Q15 |
| D12 | Tests already promoted into tests/ stay there | Move them to acceptance/ | Churn with no behaviour change | User, grill Q16 |
| D13 | agent-runtime commits its own config with all three nodes | Rely on defaults | Documents the layout for the next agent | User, grill Q17 |
| D14 | `format_oracle.py` becomes `format_acceptance.py`, run only when the repo configures ruff | Format every language | The engine is language-agnostic; svc-platform is TypeScript | User, grill Q6 |
| D15 | The agent-skills changes are a person ticket (T6) for the Lead, after the engine lands | An agent ticket | An agent ticket cannot target another repository | User, grill Q13 |
| D16 | This plan itself uses the legacy layout | Hand-write a folder plan | The engine reads folders only once T1–T4 land | Engine today |
| D17 | T6 leaves no "oracle" anywhere in the start-planning and orchestrate skills (text, scripts, tests, file names): a test file a plan brings is "acceptance", what a scenario runs is "evidence" | Rename only the format script and the skeleton | One vocabulary; the engine already reads `oracle` as legacy | User, 2026-11-13 |

## Out of scope

- Removing the legacy reader (D11): a later plan.
- Moving already-promoted tests (D12).
- CI changes beyond `testpaths`/`pythonpath`.
- D34's mid-run upgrade gaps (from yaml-eec): their own plan.
- Formatting non-Python acceptance tests.

## Risks

- **Security:** the integrity rule (S7) must cover the plan folder and every acceptance destination, or a worker
  could edit the test that grades it. Three code paths derive the layout today (`plan.oracle_dir`, `verify.py:86`,
  `driver.beside_plan`); T3 routes them through one resolver. A discard deletes files only inside the configured
  plans dir and the plan's own folder.
- **Performance:** n/a. Discovery globs one directory level.
- **Migration:** legacy plans keep their keys, files and digests (S12). Live drivers read their approved snapshots
  and are not touched; mfo-view-quality stays legacy until it archives. Legacy reader removal is a later plan (D11).
- **Rollback:** each ticket is one squash-merged PR. Before T4, folder plans can be checked and run but archive as
  legacy; reverting T4 restores today's archive. Folder plans approved in between need re-approval after a revert of
  T2.

## Seams

- Config and discovery: `plans list --json --repo` through the CLI (`tests/test_discovery.py`).
- plan/1 rules, digest and schema: `load_script().run_cli(["plan", ...])` in process (`tests/test_plan.py`).
- Placement: `runner.run_evidence` (`tests/test_runner.py`); integrity: `tests/test_verify.py`; drift:
  `tests/test_workbench.py`.
- Archive: the real driver over the fake runtime and gh (`GateFixture`/`YamlPlanFixture`, `tests/test_yaml_eec.py`).
- Collection: `uv run pytest` itself, at the repo root.

The outcome oracle `test_plan_layout.py` drives S2, S10 and S11 through `YamlPlanFixture` and the CLI, with legacy
controls that pass before and after. S12 reuses the existing yaml-eec and plan suites.
