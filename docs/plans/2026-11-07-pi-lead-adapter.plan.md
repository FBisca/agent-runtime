# Plan: Pi Lead Support in Agent Runtime

Slug: `pi-lead-adapter`. Living spec: `docs/specs/harness-adapters.md`. Status: **IMPLEMENTED — T1/T2 done, T3 verified by the user; awaiting merge of #39/#45 and the spec archive**.

## Why now

Pi must be able to delegate to existing Claude/Codex workers through the durable dispatch store, without waiting for Pi-as-worker support. This follows usable standalone Pi and shared skills. Active worker-session log binding and resume-attention PRs must be reconciled before modifying their overlapping files.

## Sources

| Source | What it settles |
|---|---|
| https://github.com/FBisca/agent-runtime/blob/main/server/agent_runtime.py | CLI detection and generation only know Claude/Codex; `resolve` has default Claude behavior. |
| https://github.com/FBisca/agent-runtime/blob/main/server/mcp_server.py | Durable MCP delegation tool surface. |
| https://github.com/FBisca/agent-runtime/pull/45 | Open worker-session binding implementation touches spawn and usage code; preserve its eventual data model. |
| https://github.com/FBisca/agent-runtime/pull/39 | Open resume-attention implementation; avoid duplicate attention handling. |
| https://raw.githubusercontent.com/agentdevhub/Pi/main/packages/coding-agent/docs/mcp.md | Built-in MCP configuration, including direct versus codemode exposure. |
| https://pi.dev/ | No default permission popups; hook semantics differ. |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Add Pi as lead first; keep existing Claude/Codex as worker backends. | Add Pi worker spawning and usage accounting in one PR. | Lower-risk vertical slice to unlock work. | User request; runtime architecture |
| D2 | Use Pi's built-in MCP registration and exercise the real store/transport in tests. | Write a new orchestration server. | Reuse durable dispatch. | Pi MCP docs; agent-runtime README |
| D3 | Introduce a Pi-specific security/approval adapter for mutation tools, or keep them blocked until equivalently protected. | Assume Claude hooks protect Pi. | Pi's extension API and trust model differ. | Pi docs; agent-runtime hooks |
| D4 | Rebase against merged/resolved #39 and #45 before editing conflicting runtime paths. | Cherry-pick competing copies of `hc_tools.py`. | Prevent duplicate session and resume semantics. | PR #39; PR #45 |

## Binding P2 contract — Q5, Q11–Q14, Q38–Q43

**Threat model:** a local stdio MCP connection and process ancestry do not authenticate an arbitrary request. The runtime must bind a lead to an unforgeable/validated local session ownership handle and durable generation, with scoped access checked on *every* sensitive operation. Environment variables alone cannot grant a role. Document trust boundaries, malicious project prompts, compromised child processes, symlink/path attacks, reconnect and restart.

**Mutations:** allow read-only discovery only where scope is validated. Mutating MCP operations are default-deny until the server enforces authorisation, explicit backend selection, allowed path/operation scope and idempotency; Pi extension prompts are defence-in-depth only. Do not silently choose `claude` when `pi` is unknown; enforce account/policy permission for personal use of company-paid workers.

**PR reconciliation:** PR #45 (5 commits; latest commit family includes `cc4b394`, `9f6fb26`, `425a1b4`, `ac69687`, `a12443b`) adds worker-session-log binding; PR #39 (`5cdb8df`, `57bb974`) adds undelivered resume attention. Both publicly open on 2026-11-07. Before editing overlapping files, explicitly decide merge/rebase sequence against *actual then-current* main and re-run the affected tests. Do not treat this as prerequisite to P0/P1.

**Lifecycle:** at-least-once worker delivery, stable task and attempt IDs, deduplicated reports, cancellation reconciliation and no duplicate replacement while the original may still run. Test wait/collect on the real store with fake transport, plus #31 (partial wait) and #34 (stopped worker count) regressions without expanding scope into an arbitrary rewrite.

## Scenarios

- **S1** WHEN a locally authorised, session-bound Pi lead connects to agent-runtime via MCP, THEN runtime identifies it as Pi rather than silently substituting Claude and assigns a stable lead generation.
- **S2** WHEN the Pi lead delegates a task to an existing Claude or Codex worker, THEN spawn, message, wait and collect use the existing durable store and produce one correctly attributed result.
- **S3** WHEN the Pi lead reconnects/restarts, THEN it sees only its authorized scope and does not inherit an unrelated lead's assignments.
- **S4** WHEN a Pi session requests unsafe worker mutations, THEN equivalent policy enforcement denies or explicitly requires approval; Claude/Codex hooks remain unchanged.
- **S5** WHEN existing Claude/Codex integration tests run, THEN they pass with no change in session identity, usage binding or resume behavior.

## Out of scope

- Pi workers, Pi session-log token accounting, general routing policy changes, automatic adaptive graph scheduling.

## Risks

- **Security:** MCP tool calls may execute mutations without Claude hooks; test deny/approval paths before enabling full delegation.
- **Performance:** connection initialization and ancestry scanning must remain bounded; no expensive per-tool session discovery.
- **Migration:** overlap with PRs #39 and #45; update from main after they are reconciled, not from stale branches.
- **Rollback:** turn off Pi MCP server registration; established Claude/Codex records remain valid.

## Seams

- Existing real MCP/IPC tests with fake terminal transport.
- `detect_cli`/`cli_generation` unit seam; authorization/guard seam for tool mutations.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | Add Pi lead identification/generation with isolated-session unit tests; preserve existing Claude/Codex behaviour, but reject ambiguous identity instead of defaulting to Claude. | S1, S3, S5 | none | `server/agent_runtime.py`, `tests/` | regular | complex | agent | COMPLETE 2026-11-07 · `feat/pi-lead-support`; R165-R170 in `docs/specs/harness-adapters.md` |
| T2 | Add a bounded Pi MCP lead delegation path to existing worker backends, with real store/IPC tests and an explicit mutation guard. | S2, S4, S5 | T1 | `server/mcp_server.py`, `server/hc_tools.py`, `hooks/`, `tests/` | regular | complex | agent | COMPLETE 2026-11-07 · `feat/pi-lead-support`; the guard lives in `server/host_policy.py`, the Claude/Codex hooks are untouched; R171-R187 |
| T3 | Register guarded agent-runtime MCP in Pi configuration, verify Pi lead spawn/wait/collect in an isolated worktree, approve or roll back. | S1, S2, S3, S4 | T2 | `README.md` |  |  | user | COMPLETE 2026-11-07 · registered in `~/.pi/agent/mcp.json`, policy in `~/.config/agent-runtime/hosts.json`, live spawn/message/wait/collect verified on MiniMax |

**Dependency:** Pi bootstrap and skills portability smoke tests. Start this plan after PR #39/#45 have been merged or consciously reconciled and rebase `main` before touching their files.


## P2 failure-path acceptance additions

- WHEN a forged/stale Pi session or mismatched generation invokes `spawn`, `stop` or cross-lead `collect`, THEN server denies the action irrespective of client-side approvals.
- WHEN a valid Pi lead omits a worker backend or requests a forbidden company-funded route, THEN it receives an explicit error and no spawn occurs.
- WHEN the MCP connection drops after a worker has been spawned, THEN restart reconciliation cannot duplicate the task, leak another lead's reports or erase partial artifacts.
- WHEN PR #39/#45 cannot be cleanly reconciled, THEN P2 is **blocked with a documented rebase/merge choice** while P0/P1 remain executable.

**Status: IMPLEMENTED.** T1 and T2 landed on `feat/pi-lead-support`: Pi is a harness of its own (`cli_of_args`, `PI_CODING_AGENT`), its lead generation is the Pi process `pid:lstart`, a Pi session without a visible Pi process or with two claimed hosts is refused, and the server authorises Pi mutations through `server/host_policy.py` (`~/.config/agent-runtime/hosts.json`: mutations, authorized backends, path scope). Claude Code and Codex are unchanged. The requirements are R165-R187 in [`docs/specs/harness-adapters.md`](../specs/harness-adapters.md).

**Outstanding:** PR #39 merges cleanly; PR #45 conflicts in one hunk of `dispatch_identity` (keep the Pi guard, take `CLAUDE_CODE_SESSION_ID`); the reconciled tree passes 549 tests. The branch still needs a rebase onto main once #39/#45 land.
