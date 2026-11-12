# Plan: Runtime audit fixes

Slug: `runtime-audit-fixes`. Living spec: `docs/specs/agent-runtime.md`. Status: APPROVED 2026-11-11 by the plan's
owner; `plan approve` at base `b5ff683`, digest `d7a69bee…`.

Contract: [`2026-11-11-runtime-audit-fixes.plan.yaml`](2026-11-11-runtime-audit-fixes.plan.yaml) (scenarios, tickets,
evidence). Oracle: [`2026-11-11-runtime-audit-fixes.oracle/`](2026-11-11-runtime-audit-fixes.oracle/). This file is the
narrative and is not covered by the approval digest. Its sibling is
[`orchestrator-audit-fixes`](2026-11-11-orchestrator-audit-fixes.plan.md), which waits on this plan's T5 and T6.

## Why now

The 2026-11-11 static audit of the repository found runtime defects that hit every user, not just the orchestrator:
- **A long-lived lead stops being able to spawn (#34).** The live store holds three teams pinned at 32 workers. Two of
  them have no active workers left, and one of those was renamed by hand to get round the limit.
- **The receipts guard blocks ordinary paths.** It denies `src/state/receipts/...` in any repository, and it blocked
  the audit's own read-only commands twice.
- **`resume` leaves a stranded assignment on a busy store.**
- **A malformed request id kills the MCP server, and call threads are never freed.**

The orchestrator plan also needs two runtime seams that don't exist yet: an idempotent operation key to recover
spawns and resumes after a restart, and a verified caller identity for the `plan approve` gate.

## Sources

| Source | What it settles |
|---|---|
| Static audit, 2026-11-11: FINDINGS.md, ISSUES.md 4, 6, 7, 8 and 10 (session scratchpad) | The defects with file:line, failure scenarios and proposed fixes |
| Independent Codex review of the audit (CODEX_REVIEW.md) | F3 confirmed for the cap only (failed counts are intentional, R54). F2 is medium severity. M3 found the thread leak. F6: Pi already has a server-side policy |
| Fresh-context gather at f4e500b (GATHER.md) | Every cited site still present. Spec id ranges: agent-runtime is at R172, with R173+ reserved by driver-adopts-workers. Live store: 3 teams at 32 workers, 140 workers with active=1 |
| GitHub #34 | The team-limit defect, plus the alternatives it offers (count active, archive on stop) |
| `docs/specs/agent-runtime.md` R54, R56, R58, R74, R89 | Failed assignments stay unfinished. 400-word cap on tool descriptions. Native-subagent rules are hook-enforced |
| `docs/specs/python-workspace-contracts.md` R200, R204 | The orchestrator reaches the runtime only through its public CLI/API |
| AGENTS.md | The hook is never the only refuser. A refusal names the reason and the way out. Fix the class |
| Grill, rounds 1-3, delegated by the owner to the Codex reviewer (`audit-critic`) | The decisions marked "grill" below |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Two plans: runtime fixes here, orchestrator fixes in a sibling plan | One plan spanning both packages | A plan/1 contract names one living spec (plan.py:151) | grill Q11 |
| D2 | The limit counts active workers. A slot is reserved atomically for a new name and for reactivating a stopped name, and an active resume takes none | Count all rows (today); count active only at register | `register` skips existing names and `bind` reactivates them, so counting active rows at register alone lets a team exceed 32 | grill Q7 |
| D3 | No liveness sweep and no store cleanup. The 3 full teams are left as they are | Reconcile dead-but-active rows; migrate the live store | Out of the defect's scope; the fix frees slots as workers are stopped | grill Q7 |
| D4 | The receipts guard protects a `receipts/` subtree, its ledger and its `.lock` under the default root, or beside a ledger marked `"kind": "agent-orchestrator/ledger"`, after canonical resolution | Generic `state/receipts`; a registry of state roots; protect the whole state root | The hook cannot see `--state-dir`. A structural marker covers every root with no shared state, and protecting the whole root would block legitimate outputs | grill Q10, Q17, Q20 |
| D5 | Until the orchestrator writes the marker, an unmarked ledger outside the default root is unprotected, and that gap is documented | Block all `receipts/` everywhere | It closes when orchestrator-audit-fixes T1 lands | grill Q20 |
| D6 | Each handler's failure path is repaired by side-effect phase: roll back reversible pending changes, report irreversible ones, never discard a committed report | Blanket `except sqlite3.Error: rollback` | `stop` may already have killed a terminal, and diagnostic catches are not missing rollbacks | grill Q18 |
| D7 | Invalid ids get -32600 with `id: null`. Finished threads are reaped. Calls are capped at 64 live, 56 of them waits; overflow is refused at once, never queued; ping and cancel are uncapped | No cap; queueing | Immediate rejection plus reserved control capacity bounds threads without deadlocking a lead | grill Q8 |
| D8 | `spawn`/`resume` take an optional `operation_id`. An additive operations table (team, non-null owner key, id, fingerprint, assignment) is written with the assignment before any send. A replay returns the existing assignment; a changed fingerprint is refused | Key by (unit, attempt, operation); bump user_version to 3 | Composite keys collide across plans. Version 3 is rejected by existing servers. A NULL owner defeats SQLite UNIQUE | grill Q15, Q19 |
| D9 | The fingerprint covers every normalized argument that changes behaviour: verb, name, cli, task, cwd, model, effort, profile, instructions, task link | Hash of verb, name, cli and task only | A replay with a changed model or cwd must not be silently treated as the same operation | grill Q19 |
| D10 | Lookup keeps the existing call syntax: `agent-runtime call operation --json '{"operation_id": ...}'`. It is owner-scoped, never acknowledges a report, and `unknown` comes with a reason | A new `--id` flag | `call` accepts a tool name plus a JSON object (agent_runtime.py:772-790) | grill Q19 |
| D11 | `resolve --json` adds `caller {kind, verified}` from the ancestry walk. Env markers are hints, never sufficient | Use `resolve` unchanged; trust env markers | `resolve` has no agent_id and defaults native callers to lead | grill Q9, Q16 |
| D12 | Native in-process subagents stay hook-only, documented narrowly as the single exception to "the hook is never the only refuser" | Claim CLI enforcement for them | A native subagent's Bash runs as a child of its Lead's host process: no CLI-side walk can tell them apart | grill Q16 |
| D13 | New requirement ids start at R240; tests cite `agent-runtime/R<n>` | Continue from R173 | R173+ is reserved by driver-adopts-workers; R240 is above every spec's current maximum | grill Q13 |
| D14 | This plan can start now; driver-adopts-workers rebases on it | Wait for adopts-workers | These are correctness fixes; adopts-workers is approved but not started | grill Q14 |

## Scenarios

The WHEN/THEN text lives in the contract. In summary:
- **Team limit (#34):**
  - **S1:** stopped workers don't count.
  - **S2:** the 33rd active worker is refused atomically, with the way out.
  - **S3:** an active resume takes no slot.
- **Receipts guard:**
  - **S4:** a repository `state/receipts` path is allowed.
  - **S5:** a default-root receipt is denied, and reads are allowed.
  - **S6:** a marked ledger's receipts, ledger and lock are denied under any root, including through aliases.
- **Store failures:**
  - **S7:** `resume` rolls back on `sqlite3.Error`.
  - **S8:** every handler's failure is handled by side-effect phase.
- **MCP server:**
  - **S9:** a non-scalar id gets -32600 and the server keeps serving.
  - **S10:** finished threads are reaped.
  - **S11:** the 64/56 cap.
- **Operation keys:**
  - **S12:** a keyed registration happens before the send.
  - **S13:** a replay returns the first assignment.
  - **S14:** a changed fingerprint is refused.
  - **S15:** `call operation` lookup.
- **Caller identity:** **S16:** a verified caller from the ancestry walk.
- **Outcome:** **S17:** the end-to-end oracle.
- **Documentation:** **S18:** the native-subagent exception is written down.

## Out of scope

- The spec ledger repair, plus a CI trace gate (audit fix 9). It gets its own plan.
- Retiring the legacy dispatch path, R58 (audit D-A). It gets its own plan.
- Pruning the store and indexing receipts (audit F23).
- Cleaning up the live store's 3 full teams.
- The remaining low-severity findings (F24 small items, refusal wording F12 beyond the handlers touched here).
- Closing the stale issues #31 and #22. That is a verification chore.

## Risks

- **Security:**
  - The receipts guard narrows, so a false negative would let a worker forge evidence. S5 and S6 pin the protected set, and the gap in D5 is explicit and temporary.
  - The caller identity (S16) is reported as verified only when the ancestry walk proves it.
- **Performance:**
  - The operations table adds one indexed insert per keyed spawn or resume.
  - The thread reaping and the cap bound memory.
  - No new full-table scans.
- **Migration:**
  - The operations table is additive at user_version 2.
  - An older server ignores it, and old positional writes stay compatible (S15).
  - No data backfill.
- **Rollback:**
  - Each ticket is one revertable PR.
  - Reverting T5 leaves an unused table behind and harmless.
  - Reverting T2 restores today's over-broad guard.

## Seams

- **MCP stdio:** the real server against the fake hc (tests/fixtures), as tests/test_mcp_server.py and test_pi_lead.py already do.
- **The hook as a subprocess:** JSON on stdin (tests/test_hooks.py).
- **The `agent-runtime` CLI:** `resolve --json`, `call operation`.
- **Store fault injection:** a patched connection raising `sqlite3.Error`, as tests/test_spawn_cleanup.py does.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | Active-worker team limit with atomic slot reservation and a refusal that names the way out | S1, S2, S3 | none | dispatch_store.py, hc_tools.py, tests/test_team_limit.py, tests/test_pi_lead.py, spec | small | complex | agent | TO-DO |
| T2 | Receipts guard scoped to the default root and marked ledgers, after canonical resolution | S4, S5, S6 | none | hooks/pre_tool_use.py, hooks/hook_lib.py, tests/test_hooks.py, spec | small | complex | agent | TO-DO |
| T3 | Handler failure paths, audited by side-effect phase: roll back or report | S7, S8 | T1 | hc_tools.py, dispatch_store.py, tests/test_spawn_cleanup.py, tests/test_stop_failures.py, spec | regular | complex | agent | TO-DO |
| T4 | MCP id validation, thread reaping and the 64/56 cap | S9, S10, S11 | none | mcp_server.py, tests/test_mcp_server.py, spec | small | complex | agent | TO-DO |
| T5 | Operation keys: table, `operation_id` on spawn/resume, replay/conflict, `call operation`, plus the outcome oracle | S12–S15, S17 | T1, T2, T3, T4 | dispatch_store.py, hc_tools.py, mcp_server.py, agent_runtime.py, tests/test_operations.py, tests/test_runtime_audit_outcome.py, tests/test_budgets.py, spec | regular | complex | agent | TO-DO |
| T6 | Verified caller identity in `resolve --json`, and the native-subagent exception in AGENTS.md and the spec | S16, S18 | none | agent_runtime.py, tests/test_agent_runtime.py, AGENTS.md, spec | small | complex | agent | TO-DO |
