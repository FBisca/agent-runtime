# Handoff — after T6 and the archive

**State:** the plan `docs/plans/2026-11-07-uv-workspace-python-standards.plan.md` is **ARCHIVED
2026-11-08**. T1–T6 are complete. The scenarios live on as requirements R192–R205 in
`docs/specs/python-workspace-contracts.md`.

**Pull request:** #64 (`feat/uv-workspace-archive`), two commits, not merged yet.

---

## 1. What landed

| Commit | What it delivers |
|---|---|
| `6c44017` | `tests/test_rollback_contract.py` — the rollback check, with the pre-workspace commit pinned in the compatibility baseline |
| `3690c37` | the living spec `docs/specs/python-workspace-contracts.md`, the contract tests re-tagged `python-workspace-contracts/R<m>`, the two stale spec `Paths:` lines fixed, the plan ARCHIVED, and the `uv sync` command corrected in README and AGENTS.md |

## 2. The evidence

| Check | Result |
|---|---|
| `uv run pytest -n 4` | **587 passed**, 4 skipped, 272 subtests (163 s) |
| `uv run pytest -m integration -n 4` | 492 passed, 4 skipped, 262 subtests |
| ruff / ruff format / ty budgets | 40/45 · 30/31 · 277/285 — all inside budget, `ty check packages/` clean |
| shellcheck | clean on `install.sh bin/*` |
| `converge --final --base 7f52364` | coverage, tagged, scope and spec all PASS |
| CI on the matrix | green on `main` for the merged T1–T5 commits, and green on PR #64 itself (all six matrix entries plus shellcheck) |
| Live smoke on **Pi, Claude and Codex** | each worker spawned through the migrated runtime's MCP surface, reported `progress` then `complete`, and stopped cleanly |
| Rollback check | the pre-workspace tree's shims and MCP server answer; a store written by either tree opens in the other with `user_version=2`, the same 11 tables and its rows intact |

The three smoke sessions ran through `server/mcp_server.py` — the entry point the plugin, the
installer and `~/.pi/agent/mcp.json` all still use — so the entry point, the store, the report
protocol and the worker lifecycle were exercised on each harness.

## 3. Two defects found on the way, both fixed here

1. **The living specs pointed at paths the runtime had left.** `harness-adapters.md` still named
   `server/host_policy.py` and `server/hc_tools.py`, which moved into the member; `agent-runtime.md`
   never learned the member's home. The converge gate called `harness-adapters` stale. Both `Paths:`
   lines now name the member's files.
2. **The setup command was wrong for the workspace.** `uv sync` installs **neither** member — the
   root is a virtual root (`package = false`). README and AGENTS.md told users to run it, so a fresh
   checkout got a test environment with no `agent-runtime` and no `agent-orchestrator` in it. Both now
   say `uv sync --frozen --all-packages`, which installs both. **This one is worth a second pair of
   eyes**: it is a documentation fix that a user would have hit before any test failed.

## 4. What is still open

1. **Merge PR #64.** The CI matrix must be green first; the two commits are already independent.
2. **The `agent-skills` cross-repository pull request (D8).** Still open, still outside this
   repository. Until it lands, the engine exists twice: the skill's copy and the copy in
   `packages/agent-orchestrator/`. The two drift. The skill's own test suite moves with it (D11).
3. **The next plans in the queue.** `docs/plans/2026-11-07-selective-remote-installer.plan.md`
   (P4c) — the selective remote installer — and, in `agent-skills`, the orchestration-baselines and
   orchestrator-routing plans.

## 5. What the next agent should not redo

- The rollback check is **not** a manual procedure any more. `tests/test_rollback_contract.py`
  materialises the tree and drives it; if the pre-workspace commit moves, the baseline pin and the
  test fail together.
- The scenarios are **not** to be re-tagged. The requirement ids are stable now; the plan's `S<n>`
  ids live only in the plan file and in the spec's `(from uv-workspace-python-standards/S<n>)`
  provenance.
- The lint and type budgets are measured debt, not round numbers: ruff 40, ruff format 30, ty 277.
  A change that pushes a count over its budget fails CI on purpose. The format budget sits one file
  below its limit, so a new unformatted file breaks the build — run `uv run ruff format` on what you
  add.

## 6. The commands

```bash
cd ~/developer/agent-runtime                     # the checkout
uv sync --frozen --all-packages                  # both members
uv run pytest -n 4                               # the whole suite
uv run pytest -m integration -n 4                # the integration half
uv run ruff check . && uv run ruff format --check . && uv run ty check
shellcheck install.sh bin/*
python3 ~/.agents/skills/converge/scripts/converge.py \
  docs/plans/2026-11-07-uv-workspace-python-standards.plan.md --final --base 7f52364
```

The branch is `feat/uv-workspace-archive`; `main` is untouched.
