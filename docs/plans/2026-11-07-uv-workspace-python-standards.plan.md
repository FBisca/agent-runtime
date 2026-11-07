# Plan: Consolidate Runtime and Orchestration Into a Typed Python uv Workspace

Slug: `uv-workspace-python-standards`. Living spec: `docs/specs/python-workspace-contracts.md`. Status: **APPROVED — READY FOR EXECUTION**.

## Why now

`agent-runtime` currently consists of stdlib-only Python scripts, a Herdr-backed MCP/CLI service and a durable SQLite dispatch store. `orchestrate.py` lives inside `agent-skills` although it operates as a separate execution engine. The coordination, recovery and CLI contracts cross repositories, making synchronized refactors needlessly expensive. Consolidate them **after** Pi bootstrap, Pi skill parity, Pi lead integration and the repair of the broken orchestration vertical slice. This is packaging/architecture work, not a greenfield rewrite.

## Sources

| Source | What it settles |
|---|---|
| https://github.com/FBisca/agent-runtime | Existing stdlib-only runtime, tests, CLI shims and Herdr/SQLite responsibilities. |
| https://github.com/FBisca/agent-skills | Current workflow and orchestrator ownership. |
| https://github.com/FBisca/agent-runtime/pull/39 | Resume/undelivered attention changes that must be reconciled before moving lifecycle code. |
| https://github.com/FBisca/agent-runtime/pull/45 | Session usage attribution changes that must be reconciled before moving files. |
| https://docs.astral.sh/uv/concepts/projects/workspaces/ | Root workspace, members, one lockfile and `uv run --package`. |
| Decisions Q24–Q43 and proposed Q44–Q46 (7 Nov 2026) | Config/data formats, CLI isolation, state recovery, acceptance gates, monorepo and Python standards. Q44–Q46 remain subject to grilling. |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Two repositories overall: `agent-skills` for methodology; `agent-runtime` as a uv workspace with `agent-runtime` and `agent-orchestrator` packages. | New third repository or one monolithic package. | Keep one atomic lifecycle integration while preserving domain boundaries. | Architecture review |
| D2 | Python 3.11+ with shared `uv.lock`, `pyproject.toml` per member and root tools config. | Rewrite in Rust or combine runtime and orchestrator source packages. | Faster incremental adoption and consistent dev tooling. | uv docs; existing Python matrix |
| D3 | Runtime remains stdlib-only at deployment; apply explicit typing and stdlib models there. Orchestrator uses Pydantic v2 for validated external domain contracts and PyYAML for human policy. | Introduce Pydantic into runtime implicitly; YAML for execution state. | Preserve low-dependency runtime and typed orchestrator contracts. | Runtime README; Q24/Q32/Q34 |
| D4 | A versioned JSON CLI/API boundary with capability negotiation separates orchestration from runtime. No imports from private runtime/store internals and no direct SQLite access. | Rely on proximity inside the workspace to share private tables/modules. | Logical independence matters more than Git layout. | Q36/Q41 |
| D5 | Preserve existing `agent-runtime*` command entrypoints, hooks, installer output and `orchestrate.py` invocation via compatibility shim until contract tests pass. | One-shot path breaking move. | Avoid breaking existing Claude/Codex/Pi entrypoints and in-progress plans. | Q36/Q37/Q43 |
| D6 | Extract incrementally after a repaired end-to-end workflow has verified scenarios; benchmark efficiency afterwards. | Benchmark broken legacy path before repair. | Q30 correction. | User decision |

## Binding workspace contract — Q33–Q34, Q36–Q46, Q47

- **Repository decision:** use one Git repo `agent-runtime` with independent installable `agent-runtime` and `agent-orchestrator` Python distribution packages in a single `uv` workspace. Keep `agent-skills` and private `dotfiles` separate. No standalone third execution repo.
- **Runtime stays standard-library-only in production.** Pydantic v2 and YAML parser belong only to `agent-orchestrator`; define public JSON/CLI/Protocol boundaries and test that an isolated runtime wheel imports without their dependencies.
- **Keep entrypoints:** all documented `agent-runtime*` shims, `.mcp.json`, hooks and orchestration skill invocations retain behaviour; add shims/tests **before** moving files. No direct orchestrator reads of runtime private SQLite tables.
- **Toolchain:** `uv sync --locked`, `ruff check`, `ruff format --check`, `ty check` and existing tests, plus build/wheel/install smoke tests, Linux/macOS Python 3.11–3.13 coverage. New code must not worsen legacy ty diagnostics; root-level excludes must not hide either member.
- **Atomicity and safety:** snapshot compatibility of persisted worker state, active run state and CLI outputs. Never migrate database state automatically. Make each packaging move reversible and independently testable.
- **PR collision:** do not move `server/hc_tools.py`, `tests/test_usage.py` or session/resume internals until #39/#45 have been reviewed on their latest heads and reconciled with main; inventory and commit-pin before action.

## Scenarios

- **S1** WHEN a developer runs `uv sync --locked`, THEN both workspace members and tests resolve reproducibly under the declared Python compatibility range.
- **S2** WHEN an existing Claude or Codex session runs `agent-runtime`, `agent-runtime-mcp`, `agent-runtime-hook`, or a known orchestration invocation, THEN its documented behaviour and permissions remain unchanged during migration.
- **S3** WHEN orchestration dispatches, resumes, collects or cancels a worker, THEN it communicates through the public runtime CLI/API without directly reading internal SQLite tables or importing private runtime modules.
- **S4** WHEN a task packet, worker report, or route policy contains unknown or invalid fields, THEN typed boundary validation rejects it with a deterministic, non-secret error; JSON is used for machine messages and YAML only for authored policy.
- **S5** WHEN runtime is installed as a standalone package, THEN its core CLI/MCP works without installing orchestrator-only Pydantic/PyYAML dependencies.
- **S6** WHEN CI runs Linux/macOS on supported Python versions, THEN unit/integration tests, existing shellcheck checks, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run ty check` run; new modules are checked by ty and legacy diagnostics have a tracked, non-growing adoption budget.
- **S7** WHEN PR #39 and #45 merge or change, THEN their session, resume and usage semantics are preserved with regression tests, not duplicated.
- **S8** WHEN the monorepo change is rolled back, THEN legacy CLI shims, durable store format and existing agent skills continue to function without a state migration.

## Out of scope

- Implementing Pi bootstrap itself; replacing Herdr; rewriting the graph engine; changing existing runtime state schema; switching execution records to YAML; adopting Rust; automatic provider fallback; moving `agent-skills` into the workspace.

## Risks

- **Dependency bleed:** uv has one lockfile/environment; enforce per-package dependency declarations through isolated wheel/install smoke tests, not only `uv run` tests.
- **Python compatibility:** preserve current Python 3.11–3.13 and Linux/macOS CI coverage unless a separate decision changes it.
- **Security:** retain server-validated identity and permissions, guarded mutations, secret scrubbing and idempotency; the monorepo is not an excuse to bypass the runtime API.
- **Path migration:** installer, hooks, profiles, symlinks and existing `server.*` imports may rely on the current tree. Identify each consumer before moving it.
- **In-progress changes:** before relocating runtime files, reconcile PR #39/#45 and rerun their regression scenarios.
- **Rollback:** make packaging changes independently reversible; keep existing executable names and state formats stable.

## Target shape (illustrative; not yet implemented)

```text
agent-runtime/
  pyproject.toml                 # virtual root + tool config + workspace members
  uv.lock
  packages/
    agent-runtime/
      pyproject.toml             # stdlib-only production package
      src/agent_runtime/
    agent-orchestrator/
      pyproject.toml             # Pydantic v2 + YAML policy, typed engine
      src/agent_orchestrator/
  tests/
    integration/                # public API, process lifecycle and CLI contract
  docs/specs/
  docs/plans/
  bin/                           # old commands remain as thin compatibility shims
  hooks/                         # preserve current supported hook entrypoints
  install.sh
```

The workspace root may use `tool.uv.package = false`; members are each separately installable. Actual paths are conditional on preserving existing imports and hook/installer behaviour.

## Python standards (binding implementation acceptance rules)

- Python `>=3.11` unless a later compatibility review changes it. Pin a supported version in `.python-version` for local development while continuing CI's supported-version matrix.
- `uv` for dependency resolution and locking; one root `uv.lock`; package-specific dependency manifests and `uv run --package ...` for developer commands.
- `ruff check` and `ruff format --check` in CI; `ruff` owns imports/formatting/linting. Avoid a second competing formatter.
- Static type checking uses **Astral ty only**, pinned in the uv lockfile; no mypy/Pyright gate. Enforce strict new/changed-module typing and a reviewed shrinking legacy-error budget; do not mass-disable diagnostics.
- Full typing on public functions, return values and side-effecting interfaces; `Protocol` for runtime client, `StrEnum` for states, `TypedDict`/dataclasses for runtime stdlib-only DTOs.
- Pydantic v2 `BaseModel` with `extra='forbid'`, explicit schema versions, constrained fields and validation at external JSON/YAML boundaries in orchestrator; avoid Pydantic for transient internal objects or runtime core unless the no-deps decision changes.
- Prefer deterministic `unittest` or `pytest` as appropriate; retain the existing `unittest` suite, create contract/acceptance tests before moving files, use fakes for `hc` and external CLIs, and keep opt-in live tests.
- No unbounded subprocess loops, bare `except`, shell injection through string interpolation, unserialised secrets, or silent provider fallback. Preserve `subprocess` argv-list execution and explicit deadlines/cancellation.
- Atomic durable writes and idempotent transitions stay governed by runtime contracts; log structured, redacted JSON events with correlation IDs, unknown token counts as unknown.
- Add CI checks for wheel builds and isolated installation of each member, because uv shared env alone cannot enforce runtime dependency isolation.

## Seams

- Existing unit tests over fake Herdr and stdio MCP, `agent-runtime` command shims, `install.sh`, Codex/Claude hooks, `orchestrate.py` CLI and `metrics.json`, acceptance tests for restart and deduplication.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | Baseline current entrypoints/import paths, package dependencies and live PR #39/#45 overlap; add red compatibility contract tests. | S2, S3, S7, S8 | Pi P0–P2; repaired workflow | `tests/`, `bin/`, `install.sh`, `hooks/`, `docs/` | regular | complex | agent | TO-DO |
| T2 | Introduce virtual-root uv workspace and package manifests without moving runtime internals; add reproducible lock, Ruff/ty CI and isolated install checks. | S1, S5, S6 | T1 | `pyproject.toml`, `uv.lock`, `packages/*/pyproject.toml`, `.github/workflows/` | regular | complex | agent | TO-DO |
| T3 | Migrate runtime behind preserved command shims with typed stdlib boundaries and keep PR #39/#45 behaviour. | S2, S5, S7, S8 | T2 | `server/`, `packages/agent-runtime/`, `bin/`, `hooks/`, `tests/` | regular | complex | agent | TO-DO |
| T4 | Move orchestrator into its own member, preserving the CLI contract; introduce `RuntimeClient` Protocol, Pydantic JSON domain contracts, typed YAML routing and no SQLite imports. | S3, S4, S8 | T3 | `packages/agent-orchestrator/`, `tests/`, `agent-skills/skills/orchestrate/` (separate PR) | large | complex | agent | TO-DO |
| T5 | Run full CI + smoke sessions on Pi/Claude/Codex; verify upgrade/rollback and mark migration ready for explicit approval. | S1–S8 | T4 | `tests/`, `docs/`, `README.md` |  |  | user | TO-DO |

**Dependency:** Pi bootstrap → skill compatibility → Pi lead runtime integration (subject to PR reconciliation) → broken-workflow repair / verification → typed route seam → uv packaging / orchestrator extraction → comparative evaluation → optional adaptive graph. The three old repos remain two: `agent-skills` plus `agent-runtime` (and `dotfiles` remains configuration). Owner approval has been granted. Implement only the scheduled phase after its technical preflight and dependency checks.

## Explicit amendment: use ty, not mypy/Pyright (Q47 accepted)

- `uv` owns the environment and locked development dependencies. `ruff` owns formatting/lint, **`ty` is the required static type checker**. No alternative type-checker CI path.
- Baseline checks: `uv sync --locked`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`. These are target commands, not evidence they pass today.
- Root `pyproject.toml` centralizes ty configuration where possible; ensure both workspace packages are actually discovered/checked. Pin compatible tool versions and exercise 3.11–3.13 in CI.
- Add `ty` CI contract tests for `agent_runtime` and `agent_orchestrator`, with valid imports and Pydantic v2 models; use ty's documented Pydantic support. Type-check public Protocols and error paths, not just happy paths.
- Incremental adoption: new and changed code must introduce no new diagnostics; keep existing diagnostics in a reviewed, shrinking baseline with explicit issue owners. Avoid broad `Any` or `ignore all` settings. Because ty's diagnostic baseline format is preview, evaluate version pinning and a stable suppression approach before relying on it as a long-term file format.
- The selective public installer is a separate **P4c** plan, sequenced after workspace packaging. Avoid making P0/P1 wait for it.

**Review disposition:** Owner approved implementation on 2026-11-07 without the previously requested independent specialist sign-off. This waiver does not waive tests, security controls or release gates.


## Packaging and compatibility gate

- `uv` workspace `uv.lock` is a *development/release resolution unit*, not a licence to import orchestrator dependencies from the runtime. Test `pip install --no-deps` or equivalent isolated wheel path for each package plus declared-dependency installation.
- No Rust rewrite. Preserve Python 3.11–3.13 compatibility of runtime even if local `.python-version` chooses a specific development interpreter.
- The new installer P4c must not be rolled out before published package artifacts and legacy command compatibility are verified.
- Cross-repository `agent-skills` shim changes must have their own reviewed PR and test evidence, not be silently patched as part of workspace packaging.

**Status: APPROVED — READY FOR EXECUTION. No code or repository moves have been made yet.**
