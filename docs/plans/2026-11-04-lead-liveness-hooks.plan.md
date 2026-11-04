# Plan: lead liveness hooks (Stop guard, context reminder, stall attention)

Slug: `lead-liveness-hooks`. Living spec: `docs/specs/agent-runtime.md`. Status: ARCHIVED 2026-11-04.

## Why now

Delivery is pull-only (R34, R57): a lead that ends its turn without re-arming `wait(all)` never hears from its workers, and the PR #10 nudge only helps while the lead is still calling tools. A comparison with kunchenguid/firstmate (Claude + Codex evaluators, 2026-11-04) found three gaps worth closing:
- firstmate's harness refuses to let a lead stop while it still supervises work; ours has nothing.
- A lead that compacts or clears its context loses track of its workers.
- A worker that stays `working` with no progress makes `wait(all)` time out forever.

Grilling on the same day (3 rounds, with an independent Codex review of each round) settled the design below.

## Sources

| Source | What it settles |
|---|---|
| Request 2026-11-04: "6, 1, 4, 5", then "it's 3" | Scope: hook robustness (#6), Stop guard (#1), context reminder (#4), parked prompt (#5, later dropped, D13), stall escalation (#3) |
| firstmate `bin/fm-turnend-guard.sh`, `.claude/settings.json`, `.codex/hooks.json`, `docs/turnend-guard.md` (github.com/kunchenguid/firstmate @54717a2) | Prior art: lead-only Stop block, own block budget of 3, fail open, `{}` on Codex exit 0 |
| firstmate `bin/fm-claude-stop-autoarm.sh`, `bin/fm-codex-stop-park.sh` | Idle-wake push exists, but Codex park is pinned to codex 0.163.0; not adopted (D2) |
| Evaluator reports (Claude, Codex; session scratchpad) | Ranked adoption list; we keep the SQLite reports and the pull transport; skip fleet, AFK, backends, Calm UI |
| Codex design reviews r1, r2 (session scratchpad) | Lost-response limitation; identity mismatch; person-needed attention re-blocking; saturating budget; action-aware context; trust modal |
| Claude Code hooks reference (code.claude.com/docs hooks, env-vars, mcp; Claude Code 2.1.318) | Stop: exit 2 or `decision:block` + reason; `stop_hook_active`; 8-continuation cap that resets on any tool call; `background_tasks` entries `type:"MCP task"` with `server`/`tool`; SessionStart sources include `compact` and `clear`; UserPromptSubmit `additionalContext`; MCP calls background after 120 s |
| Codex source rust-v0.166.0 (`hooks/src/events/stop.rs`, `core/src/session/turn.rs`, `hooks/src/discovery.rs`) | Stop/SessionStart/UserPromptSubmit exist; Stop output `decision`/`reason`, empty or `{}` on exit 0, no `additionalContext` for Stop; `stop_hook_active` per turn, no block cap; untrusted hooks don't run and raise a "Hooks need review" modal; trust keys are positional |
| Live process env check (2026-11-04) | `CLAUDE_SESSION_ID` is never set (Claude sets `CLAUDE_CODE_SESSION_ID`); codex and its MCP server have no `CODEX_THREAD_ID`; server and hook resolve the same `pid:lstart` |
| Local store read-only query (2026-11-04) | 17 of 18 groups of lead-addressed `acked=0` messages were already delivered; teams 1 and 5 still have "working" workers under dead or superseded leads; nested-lead teams have 0 workers; no timestamps anywhere |
| `docs/plans/2026-11-03-lead-delivery-gaps.plan.md` D3, D4, D6, D9, D10, D12, D16, D17 | Push rejected, hook surface deferred (amended here, D2); no pre-trust; fixed constants; worker-as-lead; unfinished count |
| `docs/plans/2026-10-30-agent-runtime-tools.plan.md` D2, D5; followups D6, D12 | Session pointer removed (R16/R17 stale); Codex hooks inline in config.toml; unparseable stdin fails open |
| `docs/specs/agent-runtime.md` R16, R17, R34, R36, R40, R42, R52, R57, R65, R68, R83, R90 | Contracts this plan keeps or replaces |
| `server/hc_tools.py:173-207`, `server/dispatch_store.py:29-45,86-138,401-405,486-565`, `server/dispatch_wait.py:17-80`, `server/dispatch_events.py`, `hooks/pre_tool_use.py:189-204`, `tests/test_hooks.py:197-211` | Current identity, snapshot, receipts, attention, event sockets, hook fail-open, hook wiring tests |
| Grilling 2026-11-04 (Felippe, rounds 1-3, all recommendations accepted) | D1–D17 below |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | "Undelivered" = `acked=0` AND the message id is in no `receipts` row | Plain `acked=0`; ack-on-delivery | A lead's last `wait` never gets the next call that would ack it, so `acked=0` stays forever and would block every lead; ack semantics stay unchanged | Gather store query; grilling r1 Q1 |
| D2 | Amend lead-gaps D3/D6: a bounded Stop block and hook-supplied context are allowed. Report transport stays pull-only; nothing is injected into the user turn | Idle-wake push (asyncRewake, `codex queue`); hc terminal injection | A Stop block is the host refusing to end a turn, not injection; idle-wake is unpinned on our Codex version and needs a contract change | Grilling r1 Q2; review r1 |
| D3 | Accept the lost-final-response limitation: a receipt is written before the reply is sent, so a lost reply reads as delivered and the guard allows | Ack or host evidence before counting as delivered | The error is on the allow side; the R36 replay already covers recovery | Review r1; grilling r2 Q1 |
| D4 | Generation = nearest Claude/Codex ancestor `pid:lstart` only, from one shared function used by server and hooks; drop the `CODEX_THREAD_ID`/`CLAUDE_SESSION_ID` suffix; keep the no-ancestor fallback unchanged | Keep the suffix; read `CLAUDE_CODE_SESSION_ID`; a per-thread team | The suffix never applies live, and a hook under a nested Codex shell would mismatch it; `/clear` and `/new` share the process on purpose. No migration, since no live team has a suffix | Fact check; review r2; grilling r2 Q2, r3 Q4 |
| D5 | Non-goal: separating Codex threads that share one MCP server | Per-call thread routing | The MCP server's env is fixed at startup and requests carry no trusted thread identity | Review r2 |
| D6 | Hooks look only at the team whose `(scope, lead, generation)` equals the hook's own and is the latest for (scope, lead) | Latest team per (scope, lead) regardless of generation | Dead-lead teams (1, 5) would block forever; the next process must not pick up the previous one's team | Gather data; grilling r1 Q4 |
| D7 | Resume continuity (a new process via `--resume` or a Codex restart) is out of scope; the runtime's own `resume` tool, which keeps the process, is unaffected | Identity rework so a resumed session rejoins its old team | Large: touches `authorize`/R65; noted as a known gap | Grilling r1 Q5; review r1 |
| D8 | The guard blocks only when there are undelivered messages, or working assignments the lead hasn't seen (D9); blocked, decision and failed assignments never block | Block on `unfinished_count` | When a worker needs a person, ending the turn to ask the user is correct | Grilling r1 Q3 |
| D9 | Per-team "last delivered wait" row `(reason, fingerprint)`, written in the final-read transaction alongside the receipt. Fingerprint = current assignment states + turns + max seq of messages addressed to the lead. Only `wait(all)` writes the row (narrower targets would read as "seen everything"), and the row also stores the batch's attention count for D12. The lead has seen everything when `reason ≠ timeout` and the fingerprint is unchanged | Per-assignment revisioned attention episodes (review r2) | Far smaller; hc-only attention never reaches the store anyway. Accepted cost: a blocked→working→blocked flip the store never records goes unseen | Grilling r2 Q4, r3 Q2; T2 review 2026-11-04 (target=all only, lead-addressed seq, attention count) |
| D10 | Claude background exemption: a running `background_tasks` entry with `type:"MCP task"`, server agent-runtime and tool `wait` allows the stop; other background work doesn't | A server-written fenced coverage record (review r2) | When the backgrounded wait returns, the lead gets a new turn and the next Stop checks again, so any gap closes after one wait. The risk that a backgrounded call is cut off is documented | Fact check; grilling r2 Q3, r3 Q1 |
| D11 | Block budget: 3 blocks per user work period, in a counter file keyed by state root, scope, lead and generation, under `$XDG_STATE_HOME/agent-runtime/stop-guard/` (0600) with an `flock` serializing increments and resets. It resets only on UserPromptSubmit or a check that finds nothing to block on; running out of budget, the D10 exemption and errors don't reset it. When the budget is spent the guard allows, with one stderr note | Reset on every allow; a side table in the store; trusting the host cap | Resetting on every allow loops block-block-block/allow; Claude's 8-cap resets on tool calls; Codex has no cap; hooks never write the store | Grilling r1 Q6, r2 Q5, r3 Q5; review r2 |
| D12 | Context hook (Claude): SessionStart with `source` ∈ {compact, clear}, plus UserPromptSubmit. One action-aware line: "call wait(all)" when there is unseen work; "N workers need you — check status" when the last wait delivered attention and nothing changed since; nothing otherwise. Replaces stale R16/R17 and the no-SessionStart assertion at `test_hooks.py:211` | An unconditional "call wait(all)"; a startup session pointer | A fixed "call wait" line repeats useless advice when workers need a person; `clear` added because D4 keeps `/clear` on the same team | Grilling r2 Q7, r3 Q6; review r2 |
| D13 | Drop the parked-launch-prompt signal (#5) until someone reproduces it | A composer-scraping marker | The task goes in as CLI argv (`hc_tools.py:265-278`); `first_message` applies only to hc templates; the 30 s `unreported` attention covers startup; D12 of lead-gaps rejects dialog-specific markers | Fact check; review r1; grilling r2 Q6 |
| D14 | Stall: a side table per assignment with `last_progress_ms`, `last_turn` and `stall_reported_for`. Progress = a `publish` report or a change in the hc turn. A working assignment idle ≥ 15 min (fixed constant) makes `wait` return attention `stalled` once per episode (`stall_reported_for ≠ last_progress_ms`), written in the final-read transaction. The clock starts at first observation, so legacy rows are never stalled at once; exited and failed beat stalled; never kill, never infer success | A column on `assignments` (positional INSERT); a configurable threshold; repeating on every wait | Additive schema as D7 of hardening; dedupe like `fail_observed` | Grilling r1 Q8, r3 Q3; review r1, r2 |
| D15 | Shared hook helpers (#6): open the store with a `mode=ro` URI connection and a 200 ms busy timeout; honour `XDG_STATE_HOME`; don't depend on `AGENT_RUNTIME_DISPATCH` in the hook env; allow silently on bad stdin, missing store, locked store or no identity; the shim exits 0 when python3 is missing; write correct output per event | Reuse `Store()` (its constructor and `transaction()` write); a doc-only audit | Existing hooks already fail open; new hooks must too, and must never write the store | Gather; grilling r1 Q7, r3 Q7 |
| D16 | Codex parity is gated: Claude ships first. The Codex ticket starts with a probe of the installed codex (Stop/SessionStart/UserPromptSubmit payload, exit-0 output, block, `stop_hook_active` per turn, budget reset on UserPromptSubmit) and must show that hc-spawned Codex workers don't stall on "Hooks need review". `install.sh` prints Codex Stop/context hooks only after the probe passes | Ship both CLIs together | Codex trust keys are positional and untrusted hooks raise a modal that would block workers | Fact check; grilling r2 Q8, r3 Q8 |
| D17 | The guard covers any caller that leads a team with needs, including a worker leading its own children (lead-gaps D16); callers that lead no team, or only teams with no needs, are always allowed | Top-level leads only | A worker-lead that stops without waiting loses its children's reports the same way | Lead-gaps D16; review r1 |
| D18 | A working assignment whose stall was already reported and that has made no progress since (`stall_reported_for = last_progress_ms`) counts as seen: it never blocks Stop and never triggers the "call wait(all)" line | Keep counting it as unseen working, bounded only by the 3-block budget | After a stall report, the next `wait(all)` times out (once per episode), so `last_wait.reason` returns to timeout and the guard would nag on every prompt for a worker the lead was already told about; like D8, a worker that needs a person must not block | Gaps review T3+T4; user 2026-11-04 |

## Scenarios

**Identity (D4)**
- **S1** WHEN the MCP server and a hook run under the same Claude or Codex process, even with `CODEX_THREAD_ID` or `CLAUDE_CODE_SESSION_ID` set, THEN both compute generation `<pid>:<lstart>` of the nearest CLI ancestor, with no suffix.
- **S2** WHEN a lead runs `/clear` (Claude) or `/new` (Codex) in the same process, THEN its next runtime call stays on the same team and sees the same assignments.

**Last delivered wait (D9)**
- **S3** WHEN a lead's `wait` returns a final batch, THEN the team's last-wait row holds that batch's reason and the current fingerprint, committed in the same transaction as the receipt.
- **S4** WHEN after that a message arrives, an assignment changes state or a turn is recorded, THEN the fingerprint computed from the store differs from the stored one.

**Stall (D14)**
- **S5** WHEN a working assignment has had no `publish` progress and no hc turn change for ≥ 15 min, THEN `wait` returns attention with reason `stalled` for it, once.
- **S6** WHEN that assignment then makes progress and later goes idle ≥ 15 min again, THEN `wait` returns a new `stalled` attention.
- **S7** WHEN an assignment is first observed (new or existing before the upgrade), THEN its idle clock starts at that observation; WHEN it has exited or failed, THEN `failed` wins over `stalled`; the worker is never stopped and never marked complete.

**Hook helpers (D15)**
- **S8** WHEN a new hook gets invalid JSON on stdin, the store is missing or write-locked, python3 is missing, or no identity resolves, THEN it exits 0 and allows (Claude: no output; Codex: empty or `{}`).
- **S9** WHEN a hook reads the store, THEN it never creates or changes files under the store directory, and honours `XDG_STATE_HOME` whether or not `AGENT_RUNTIME_DISPATCH` is set in its env.

**Context reminder (D12)**
- **S10** WHEN Claude SessionStart fires with `source` compact or clear, or UserPromptSubmit fires, for a lead with undelivered messages or unseen working assignments, THEN `additionalContext` is `agent-runtime: N unfinished, M undelivered — call wait(all)`.
- **S11** WHEN the last delivered wait reported attention and the fingerprint is unchanged, THEN `additionalContext` is `agent-runtime: N workers need you — check status`.
- **S12** WHEN the caller leads no team with needs, its team's generation differs from the hook's, or SessionStart `source` is startup, resume or fork, THEN the hook prints nothing.

**Stop guard (D8, D10, D11, D17)**
- **S13** WHEN a Claude lead's turn ends with undelivered messages or unseen working assignments, THEN the Stop hook blocks with reason `agent-runtime: N unfinished, M undelivered — call wait(all) before ending the turn`.
- **S14** WHEN only blocked, decision or failed assignments remain, or the last wait's reason ≠ timeout and the fingerprint is unchanged, THEN Stop allows.
- **S15** WHEN `background_tasks` holds a running `MCP task` with server agent-runtime and tool `wait`, THEN Stop allows without spending budget.
- **S16** WHEN the guard has blocked 3 times since its last reset, THEN the next Stop allows with one stderr note, and later Stops keep allowing until UserPromptSubmit fires or a check finds nothing to block on.
- **S17** WHEN a Stop allows because the budget is spent, because of the S15 exemption, or because of an error, THEN the budget is not reset; WHEN UserPromptSubmit fires, THEN it is reset to 3.
- **S18** WHEN a worker that leads its own children ends its turn with unseen work in that team, THEN its Stop blocks as in S13; WHEN a caller leads no team, or only teams of dead or superseded generations, THEN Stop allows.
- **S18a** WHEN the only working assignments left have a reported, unchanged stall (`stall_reported_for = last_progress_ms`), THEN Stop allows and the context hook prints no "call wait(all)" line; WHEN such a worker makes progress, THEN it counts as unseen again.

**Codex (D16)**
- **S19** WHEN a Codex lead's turn ends with unseen work, THEN its Stop hook blocks with `decision:"block"` and the S13 reason, and on allow exits 0 with empty or `{}` output; the budget behaves as S16/S17, reset by Codex UserPromptSubmit.
- **S20** WHEN Codex SessionStart (compact or clear) or UserPromptSubmit fires for a lead with needs, THEN its context carries the S10/S11 line.
- **S21** WHEN hc spawns a Codex worker after the new Codex hooks are installed, THEN the worker starts its task without stopping at a "Hooks need review" prompt.

## Out of scope

- Idle-wake push (Claude `asyncRewake`, `codex queue`) and any hc terminal injection (D2).
- The parked-launch-prompt signal (D13).
- Resume continuity across a new process (D7); cleanup of orphaned workers under dead leads (D6).
- Separating Codex threads that share one MCP server (D5).
- firstmate's Calm UI mod, AFK daemon, secondmate fleet, extra backends and harnesses.
- A configurable stall threshold (D14).

## Risks

- **Security:** hooks only read the store (`mode=ro`) and write only their own 0600 budget file. Context and reasons are fixed lines with counts; no worker text reaches the lead through a hook. Trusting the new Codex hooks is a person's step (T7).
- **Performance:** each hook is one indexed read-only query with a 200 ms busy timeout under a 5 s hook timeout; UserPromptSubmit runs on every prompt but exits early for non-leads. Stall bookkeeping adds one side-table upsert per observed assignment per wait poll.
- **Migration:** additive side tables only (hardening D7; `user_version` unchanged). Older code ignores them. The suffix never applied live, so D4 splits no team. R16/R17 are retired in the spec.
- **Rollback:** delete the Stop/SessionStart/UserPromptSubmit entries from `hooks/hooks.json` and `~/.codex/config.toml`; the side tables stay unused. The guard can't trap a lead: 3-block budget, Claude's 8-cap, fail-open on every error. Known limits: a lost final reply (D3) and a cut-off backgrounded wait (D10) both resolve to allow.

## Seams

- `tests/test_hooks.py` `run()` subprocess helper: runs a hook against a temp `XDG_STATE_HOME` seeded through `Store(env)`, as `test_peer_dispatch.py`/`test_diagnostics.py` do (S8–S18).
- `tests/test_event_wait.py` `AsyncRPC` with fake hc `ls`/`get` replies and an injectable clock (S3–S7).
- `tests/test_tools.py` / `tests/test_mcp_server.py` identity tests, with fixtures shaped like real CLI ancestors (S1, S2).
- An opt-in live check in `tests/test_live_acceptance.py` for the Claude `background_tasks` shape (S15) and the Codex probe (S19–S21).

## Tickets

Each ticket adds its scenarios to the living spec under pre-assigned R-numbers: T1 R107–R108, T2 R109–R110, T3 R111–R113, T4 R114–R118 (also retires R16/R17), T5 R119–R124 plus R128 (S18a, added with D18), T6 R125–R127.

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Server and hooks share one suffix-free generation, so `/clear` and `/new` stay on the same team | S1, S2 | none | server/hc_tools.py, server/agent_runtime.py, tests/test_tools.py, tests/test_mcp_server.py, tests/test_agent_runtime.py, tests/test_dispatch.py, tests/test_peer_dispatch.py, tests/test_diagnostics.py, tests/test_event_wait.py, README.md, docs/specs/agent-runtime.md | agent | COMPLETE (PR #12 squash b174e56; converge PASS; review no blocking, 2 fix rounds (locale-stable lstart, test harness); CI green; unittest 329 OK) |
| T2 | `wait` records a per-team last-delivered row (reason, fingerprint) in its final-read transaction | S3, S4 | none | server/dispatch_store.py, server/dispatch_wait.py, server/hc_tools.py, tests/test_event_wait.py, tests/test_dispatch.py, tests/test_indexes.py, docs/specs/agent-runtime.md | agent | COMPLETE (PR #11 squash b92d8fa; converge PASS; review: 1 blocking fixed (wait(all)-only row), 2 rounds; CI green; unittest 324 OK) |
| T3 | A working assignment idle ≥ 15 min returns one `stalled` attention per episode | S5–S7 | T2 | server/dispatch_store.py, server/dispatch_wait.py, server/hc_tools.py, tests/test_event_wait.py, tests/test_dispatch.py, docs/specs/agent-runtime.md | agent | COMPLETE (PR #15 squash 6129cb4; Codex worker lost resume (issue #13), redone by Claude worker; converge PASS; review no blocking, 1 fix round (needs-person states keep their next, budget over longest next, no write lock on unchanged polls); CI green; unittest 353 OK) |
| T4 | Shared fail-open read-only hook helpers, plus the Claude SessionStart(compact/clear)/UserPromptSubmit reminder | S8–S12 | T1, T2 | hooks/, bin/agent-runtime-hook, tests/test_hooks.py, README.md, docs/specs/agent-runtime.md | agent | COMPLETE (PR #14 squash 1c3db4e; converge PASS; review no blocking, 1 fix round (imports inside fail-open, python3 guard in hooks.json, server identity, one read snapshot, SQL receipt match, R115 reworded to dispatch.sqlite3); CI green; unittest 339 OK) |
| T5 | Claude Stop guard with the saturating 3-block budget and background-wait exemption, plus an opt-in live check of the `background_tasks` shape | S13–S18, S18a | T4 | hooks/, tests/test_hooks.py, tests/test_live_acceptance.py, README.md, docs/specs/agent-runtime.md | agent | COMPLETE (PR #18 squash d79873f; converge PASS; review 3 blocking fixed (plugin-scoped server name `plugin:agent-runtime:agent-runtime` matched, real-form S15 test, empty live check removed: `claude -p` never backgrounds MCP calls, so background_tasks shape is from the hooks reference only); CI green except macOS 3.12 runner never started, merged on owner instruction; unittest 365 OK) |
| T6 | Probe the installed codex hook contract, then add the Codex Stop/context adapter and `install.sh` wiring; stop and report if the probe fails | S19–S21 | T5 | hooks/, bin/agent-runtime-hook, install.sh, tests/test_hooks.py, tests/test_live_acceptance.py, README.md, docs/verification/codex-hooks.md, docs/specs/agent-runtime.md | agent | COMPLETE (PR #25 squash 786c788; probe passed on codex-cli 0.166 (docs/verification/codex-hooks.md); first worker lost to lead restart, finished by a fresh worker from its WIP; converge PASS; review: spec conflict with #24 fixed by merge, then shim fails open on missing script, stricter S21/install tests, copy-only-3-blocks + stale trust warning; CI not started within 5 min, merged under session rule; unittest 389 OK) |
| T7 | Trust the new Codex hooks via `/hooks` in `~/.codex/config.toml` and confirm one hc-spawned Codex worker starts its task | S21 (sign-off) | T6 | ~/.codex/config.toml | Felippe | COMPLETE (owner added the hooks via dotfiles PR FBisca/dotfiles#4, trusted them via /hooks 2026-11-04; S21 confirmed: hc-spawned Codex worker s21-codex-probe started and reported with no "Hooks need review" prompt) |
