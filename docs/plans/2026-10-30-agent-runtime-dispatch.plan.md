# Plan: durable runtime dispatch through tool results

Slug: `agent-runtime-dispatch`. Living spec: `docs/specs/agent-runtime.md`. Status: APPROVED 2026-10-30.

## Why now

The mixed Codex/Claude smoke test worked, but worker reports arrived as `<hc from="…">` user-message text. `wait(all)` returned on a report while another worker remained working. Leads then needed separate screen collection to verify results. Replace report injection with durable structured delivery and let one pending wait cover an uninterrupted period of concurrent work. Keep status and screen collection as recovery tools.

The user explicitly waived the PRD prerequisite on 2026-10-30. This conversation supplies the feature requirements. This is a plan only; implementation starts after approval.

## Sources

| Source | What it settles |
|---|---|
| User, runtime smoke test and discussion, 2026-10-30 | Mixed workers already launch; reports appear as injected hc text; minimal lead calls/turns are the goal |
| User, grilling Q2–Q4, 2026-10-30: “rest is yes” | Separate messages from completion; durable IDs/ack/replay; same-team peer messaging and minimal shared tasks; exclude cross-team and native Claude Teams integration |
| User, Q1 follow-up and fallback discussion, 2026-10-30 | Dispatcher listens inside one call; avoid per-message lead turns; status/collect may remain fallbacks |
| User, 2026-10-30: “skip PRD use the information you have” | Planning proceeds from session requirements |
| [Current living spec](../specs/agent-runtime.md) | Resolver and hc identity, worker tree, CLI-specific launches, existing guards; native Claude Teams previously disabled |
| [Approved tools plan](2026-10-30-agent-runtime-tools.plan.md) | Nine current tools, stdlib MCP server, report→hc send, wait→hc wait, bounded screen collection, 400-word tool-description budget |
| [Tool implementations](../../agents/plugins/agent-runtime/server/hc_tools.py) | Background spawn already exists; message/resume restricted to children; resume currently waits; report uses hc send |
| [MCP server](../../agents/plugins/agent-runtime/server/mcp_server.py) | Each call runs on a thread; cancellation kills its subprocess; stdio protocol currently returns one result |
| [Current tests](../../agents/plugins/agent-runtime/tests/) | Subprocess JSON-RPC and fake-hc seams, screen fixtures, hooks/config wiring checks |
| [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) and [progress](https://modelcontextprotocol.io/specification/2025-06-18/basic/utilities/progress) | Structured tool results; progress notifications are separate from the final request result; no portable repeated model turns from one result |
| [Claude output limits](https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings), installed 2.1.314; local Codex models cache and official per-tool config | Default host truncation is below legal batch sizes; Claude supports per-tool anthropic/maxResultSizeChars, Codex per-tool output_token_limit |
| [Claude MCP time limits](https://code.claude.com/docs/en/mcp#time-limits-on-mcp-tool-calls), installed Claude 2.1.314 schema | Per-server `timeout` is host-side milliseconds; server `env` cannot configure the host deadline |
| [Codex hooks](https://learn.chatgpt.com/docs/hooks), installed 0.166.0/release hook schema | Native ThreadSpawn PreToolUse has optional agent_id/agent_type; current MCP interception supports deterministic child guards |
| [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli) and tracked `agents/.codex/config.toml` | Configurable call deadline; this checkout already sets runtime `tool_timeout_sec = 600` |

Gather found no covering PRD or associated Linear feature/project. Relevant existing records: nine tools; two live smoke-test workers (`t27`, `t28`); zero durable report/task records in the current implementation. No mailbox backfill exists. Terminal names and IDs can be reused, so migration cannot reconstruct assignment identity from names. Existing local edits to Claude settings and Codex config must be preserved.

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Store worker reports and expose them in the lead's `wait` tool result; stop sending report text through hc | hc text injection, separate mandatory receive call | Structured provenance and fewer turns | User Q1; fallback discussion |
| D2 | One bounded `wait(all)` listens until all selected assignments complete, a blocker/decision/failure needs attention, cancellation, or deadline; buffer routine progress | Return on every report; promise an infinite stream of model-visible results | Minimal lead turns within current MCP hosts | User minimal-turn requirement; MCP sources |
| D3 | Keep `wait(any)` for the next queued event and named waits for a selected worker's assignment; every response carries messages and assignment states together | Status-only wakeups followed by collect | One call supplies the actionable evidence | Q2 and current smoke test |
| D4 | `status` and `collect` remain diagnostic fallbacks; screen collection does not establish successful task completion | Delete recovery tools; make screen scraping mandatory | User's explicit fallback preference | User latest instruction |
| D5 | Persist messages, assignments and tasks in one user-private local SQLite store using Python stdlib; use local IPC notifications to wake cross-process waiters | In-memory server mailboxes; new external service dependency | Each worker has its own MCP process; restart durability needs shared storage | Q3; current server architecture; implementation choice |
| D6 | Scope a team to runtime scope plus lead session generation; identify workers and assignments with stable IDs, with names only for display | Names/hc addresses as durable identity | Name/address reuse and resumed turns must not consume old reports | Q3; code inspection |
| D7 | At-least-once delivery with stable message IDs, per-recipient delivery receipts and idempotent acknowledgments piggybacked on the next runtime call | Extra ack calls; delete messages on first read; exactly-once claim | Recover lost results without adding normal-path calls | Q3; minimal-turn requirement |
| D8 | Report kinds are progress, complete, blocked, and decision; transport/worker failure is separate from success; a matching complete report completes its assignment | Treat hc idle/done, a progress message, or any report as completion | Terminal state and assignment state are different | Q2; observed early wait return |
| D9 | Spawn/resume dispatch work and return promptly; dispatcher continues listening independently of lead reasoning; a resumed assignment gets a new identity | Blocking inside each resume; spawn a new worker for follow-up | Start multiple workers before one shared wait | User mixed background teammates requirement; current resume implementation |
| D10 | Same-team peer messages are queued with server-derived sender identity and delivered through recipients' tool results; active waits wake, idle recipients read on their next call or explicit resume | Peer hc text injection; silently resuming idle workers; cross-team delivery | Preserve tool provenance and bounded lifecycle | Q4; D1; implementation choice |
| D11 | A minimal `task` tool supports create/list/claim/update; dependencies gate atomic claims; complete/blocked reports also update the linked task | Full scheduler; separate mandatory task-complete call | Shared coordination without extra completion calls | Q4; minimal-turn requirement |
| D12 | Native Claude Teams stays disabled; hc remains responsible for terminal creation/input/process lifecycle; all new dispatch state lives in this plugin | Reimplement native Claude Teams; change hc upstream or Herdmates | Fits the existing mixed-CLI adapter and repository boundary | Existing plans/spec; Q4 |
| D13 | Tool results provide `structuredContent` plus serialized JSON text for compatibility; bounded batches expose a continuation receipt; no claim of host-level streaming | Progress notifications as message delivery; unbounded report dumps | Portable current clients and explicit truncation behavior | MCP sources; implementation choice |
| D14 | Runtime tools may accept an optional delivery receipt; mutation tools process its acknowledgment and their requested action atomically where both use the store | Acknowledgment before durable action; one new tool per operation | Replay safety without normal-path round trips | D7; implementation choice |

| D15 | Keep new dispatch opt-in via `AGENT_RUNTIME_DISPATCH=1` until T6 activates the completed path in CLI wiring; default retains legacy transport/waits during intermediate merges | Install incomplete behavior after each ticket merge | T1 alone cannot preserve old blocking waits/resume; migration must remain usable | T1 integration review, 2026-10-30 |

| D16 | Surface an observed selected-worker failure before returning a queued nonterminal event; preserve queued messages in that result | Queued progress indefinitely bypassing runtime reconciliation | Advisory T2 review found repeated unacknowledged progress can hide an exit/interruption | T2 advisory review, 2026-10-30 |

| D17 | Configure a 600-second host ceiling for both CLIs: Claude server-level `timeout: 600000`, Codex `tool_timeout_sec = 600`; internal wait stays at most 590 seconds | Put a host timeout only in the Python server environment; rely on progress to extend a hard deadline | Verified host settings and units, with deadline headroom | Official Claude timeout docs and installed 2.1.314 schema; Codex config |

| D18 | Bound complete tool envelopes, including inbox/base payload, states and receipt; paginate without acknowledging undelivered messages; configure supported per-tool host limits above the proven envelope bound | A messages-only cap; moving receipt fields before data; relying on default host truncation/file fallback | Codex default 10k-token truncation and Claude default 50k-character file fallback can break legal batches; existing legal reports must remain retrievable | Read-only host-output investigation, official docs and installed clients, 2026-10-30 |

| D19 | Before pinning a worker session, prove its dispatched assignment identity or a verified launch-time binding; an address-only first call cannot adopt current membership | Pin whichever team currently owns a reused address on first contact | Review reproduced stale first-use adoption into replacement teams | T3 correctness and advisory review (same S14 blocker), 2026-10-30 |

| D20 | Worker claims require a current unfinished assignment; worker task mutations match both worker and linked assignment; a matching complete report may finish a blocked task | Worker-ID-only ownership; claims by completed assignments; in_progress-only completion hook | Preserve owning-assignment authority and recovery after an accepted blocker | T4 correctness/advisory review (current S17/S18 corrections), 2026-10-30 |

| D21 | Normalize verified Codex/Claude subagent markers at PreToolUse, wire MCP matchers and deny identifiable native-child coordination mutations, including receipt acknowledgments | Keep obsolete Codex Bash-only early return; infer child role from session IDs | Installed Codex 0.166.0 serializes agent_id/agent_type for ThreadSpawn children; the host can deny before shared MCP reaches the server | Official hooks docs and exact installed-release schema investigation, 2026-10-30 |

API direction: retain existing tools and add only `task`. `spawn`/`resume` return `team_id`, `worker_id`, `assignment_id` and optional linked `task_id`. `report(text, kind, assignment_id, message_id?)` derives sender/recipient server-side. `message(to, text, message_id?)` accepts authorized teammates as well as children. `wait(target, timeout, receipt?)` returns `{reason, messages, assignments, unfinished, receipt, more}`. `task(action, ...)` handles shared task operations. Other tools can carry optional `receipt` and return pending inbound messages with their ordinary result. Exact field validation belongs to each ticket's schema and tests, preserving existing positional meanings.

The store owns delivery and assignment truth. IPC is a wakeup hint: register a listener, recheck durable state, then block; after notifications or bounded internal reconciliation, read durable state again. This closes registration races and recovers a publisher that commits then crashes before notifying. Reconciliation is internal and never creates a model call. The implementation must set and test its finite recovery bound. No always-on launchd service is required.

Wait membership is snapshotted at invocation; later spawns do not extend it. An empty target set returns `empty`. An assignment is successfully complete only after its accepted complete report. A blocked/decision report, interrupted worker, or worker exit without completion yields an attention/failure result with unfinished assignments, rather than pretending all completed. Reports may complete an assignment before the CLI ends its turn. A long-lived idle peer receives messages when it waits or next calls a runtime tool; this plan does not promise unsolicited model wakeups.

## Scenarios

- **S1** WHEN a Claude or Codex worker reports through MCP, THEN its lead receives a structured report with server-derived sender, team, assignment and message IDs through a tool result, with no hc report send or injected user message.
- **S2** WHEN publisher/recipient MCP processes restart, THEN committed reports and pending acknowledgments remain recoverable from the same private store.
- **S3** WHEN an unacknowledged batch is read again after cancellation or a lost response, THEN its messages retain their IDs; repeating a valid receipt acknowledgment is harmless and acknowledged messages are omitted from later normal reads.
- **S4** WHEN a worker retries publication using the same message ID, THEN only one event exists; reusing that ID with different content returns a conflict.
- **S5** WHEN `wait(all)` receives progress or one worker's complete report while another selected assignment runs, THEN the same call remains pending and later returns their accumulated reports together.
- **S6** WHEN every snapshotted assignment reports complete, THEN `wait(all)` returns `completed`, all reports and an empty unfinished list in one tool result, without status/collect calls.
- **S7** WHEN a selected assignment blocks, requests a decision, is interrupted, or its worker exits before completion, THEN the wait returns explicit attention/failure with all unfinished assignments; hc idle/done alone never proves success.
- **S8** WHEN `wait(any)` has an eligible event, THEN it returns that event and any already queued eligible batch; a named wait filters to the selected worker/assignment.
- **S9** WHEN a wait reaches its deadline, is cancelled, or has no selected assignments, THEN it returns a timeout/empty result where a response is possible, or cancels cleanly; messages remain replayable and workers continue.
- **S10** WHEN publication races listener registration or the publisher dies after commit before IPC notification, THEN a waiting recipient receives the committed event within the tested finite recovery bound without model polling.
- **S11** WHEN one MCP call waits, THEN ping, publication and cancellation remain responsive; stdout contains only valid protocol JSON and the call produces one final result.
- **S12** WHEN spawn/resume is invoked for multiple mixed-CLI workers, THEN dispatch returns before their assignments finish and all can run concurrently; each resume receives a fresh assignment ID.
- **S13** WHEN a late report references an old assignment or an hc address/name has been reused, THEN it cannot complete the current assignment or leak into another team generation.
- **S14** WHEN two teammates exchange messages while their recipients wait, THEN messages arrive through the recipients' tool results with true sender identity; idle recipients retain messages for their next call/resume without forced input.
- **S15** WHEN a caller targets an unrelated team, forges sender/parent/assignment identity, or a prohibited native subagent calls a mutating tool, THEN the operation fails before publishing or changing task state; existing permission dialogs are never bypassed.
- **S16** WHEN a lead creates and lists tasks with dependencies, THEN every teammate sees the same durable task IDs, dependency state, owner and status after restart.
- **S17** WHEN two teammates concurrently claim a ready task, THEN exactly one wins; unsatisfied dependencies, missing dependencies and dependency cycles are rejected explicitly.
- **S18** WHEN the owning assignment reports complete/blocked, THEN its linked task changes in the same store transaction; dependent tasks become claimable only after successful prerequisites, and duplicate reports do not duplicate transitions.
- **S19** WHEN a caller piggybacks a valid receipt on its next runtime call, THEN acknowledgment requires no separate call; invalid or foreign receipts fail explicitly and a failed transaction does not lose the batch.
- **S20** WHEN a result exceeds the documented batch/payload limit, THEN it returns bounded data with `more` and a continuation receipt; pending messages remain retrievable without silent loss or implicit acknowledgment.
- **S21** WHEN a worker is stopped or removed, THEN its committed reports and task history remain readable; an active incomplete assignment is surfaced as unfinished/failed and cannot strand waiters indefinitely.
- **S22** WHEN structured delivery fails or a human investigates a worker, THEN status/collect still return diagnostic state/screen output, without silently substituting that output for an accepted complete report.
- **S23** WHEN installed Claude and Codex load the tools, THEN schema, permissions, timeout configuration and instructions support the same dispatch flow; total tool descriptions remain within 400 words and normal instructions use wait results with status/collect only as fallback.
- **S24** WHEN a live lead starts both CLIs, exchanges peer messages, observes completion/blocker, resumes one assignment and recovers after an MCP restart, THEN reports remain attributed, replay works, no hc report text appears, and one wait covers each uninterrupted work period within the configured deadline.

- **S25** WHEN dispatch activation is absent during intermediate merges, THEN existing spawn/report/wait/resume behavior stays usable; when explicitly enabled, THEN the new structured path applies.
- **S26** WHEN serialized message arrays meet the configured byte boundary, THEN JSON framing bytes count toward the limit and pagination remains lossless.

- **S27** WHEN `wait(any)` has queued progress and a bounded hc observation reports its selected worker exited or interrupted, THEN the result surfaces failure and unfinished assignments while preserving those messages; replay cannot bypass that observation indefinitely.

- **S28** WHEN a legal response approaches the complete-envelope budget, THEN states, reports, receipt and continuation fit configured Codex/Claude output limits; deferred messages remain retrievable and acknowledgments cover only delivered messages, including inbox piggyback results.

## Out of scope

- Native Claude Teams adoption, enabling its experimental flag, Herdmates integration, hc upstream changes.
- Cross-team messages, remote/multi-machine transport, automatic worker spawning or task scheduling.
- An infinite model-visible tool-result stream, unsolicited wakeups of idle models, optional host-specific channels/tasks integrations.
- A new mandatory receive/ack/status/collect call in the normal flow, task-board UI, report extraction from old terminal history.
- Exactly-once model execution, arbitrary pre-existing worker adoption, retroactive backfill of prior reports.

## Risks

- **Security:** derive routing from caller/runtime registration; enforce same-team permissions and assignment ownership on every store operation; owner-only state directory, DB and IPC endpoints. Treat peer text as attributed data, never user authorization. This is local same-user coordination, not an isolation boundary against malicious processes running as that OS user. Preserve existing subagent and permission-dialog guards.
- **Performance:** bounded report/payload batches and indexed team/recipient/sequence queries; concurrent SQLite transactions use a finite busy timeout. IPC wakes pending waits; bounded internal reconciliation covers lost hints. Call deadlines must fit both CLI hosts; external timeout/cancellation preserves replay. No per-message lead turns or blocking resume.
- **Migration:** initialize a versioned store lazily for newly registered teams; old live workers keep their old instructions and require an explicit fresh assignment before structured completion is expected. Do not infer old assignment IDs. Preserve current uncommitted config edits. Identity generations isolate address reuse. No Linear migration/backfill required.
- **Rollback:** stop the new team, disable/revert the plugin and instruction/config changes, and retain the store for recovery. Old report injection resumes only with old code and fresh worker instructions; queued structured reports are not automatically translated into hc text. Schema versions reject incompatible writers rather than rewriting data.

## Seams

- Existing subprocess JSON-RPC harness, extended to drive multiple MCP processes against one temporary state directory and fake hc. Attach report/replay/wait/identity/task assertions here; include real cross-process publication, cancellation and process restart, with deterministic synchronization instead of timing guesses.
- Existing hook, CLI wiring and recorded-screen tests for permissions and fallback compatibility; real installed CLI smoke tests for host timeouts, attribution and absence of injected reports. No terminal-state-only acceptance of completed assignments.

## Tickets

Each ticket is one vertical slice, targeted below 800 hand-written lines including tests. Dependencies merge first; each successor branches from current main. All paths below are relative to the dotfiles repository. No production code is changed by this planning task.

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Durable report publication and bounded immediate wait results across MCP processes: versioned private SQLite store, team/worker/assignment registration, structured envelope, idempotent publication and replay receipts; report stops using hc send | S1–S4, S13, S20, S25 | none | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR25 cfecece; 141 tests, converge, review PASS; hosted CI n/a) |
| T2 | One event-driven pending wait with strict all/any/named predicates, deadline/cancellation, cross-process wakeups and lost-notification recovery; messages and states returned together | S5–S11 | T1, T8 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR27 be96bc4; 153 tests, converge, review PASS; hosted CI n/a) |
| T3 | Concurrent dispatch and same-team peer exchange: nonblocking resume, fresh assignment identity, inbound delivery through tool results, piggyback acknowledgment and stop semantics | S12, S14, S19, S21 | T2, T9 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR29 0cff3a4; 165 tests, converge, review PASS; hosted CI n/a) |
| T4 | Shared tasks through one MCP task tool: durable create/list/update/atomic claim, dependency validation and completion integrated with report transaction | S16–S18 | T3 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR30 77b4f3a; 170 tests both Python versions, converge/review PASS; CI n/a) |
| T5 | Guard and fallback compatibility across the complete API: team/assignment authorization, hooks for new mutation actions, status/collect diagnostics, conflict/cancellation/restart coverage | S15, S22 | T4 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/hooks/, agents/plugins/agent-runtime/tests/, agents/.codex/config.toml | agent | IN PROGRESS |
| T6 | Both CLI installations and concise tool instructions use dispatch results: deadline wiring, permission/schema compatibility, description budget, implementer/orchestrator guidance and upgrade notes | S23 | T5, T10 | agents/plugins/agent-runtime/, agents/.codex/config.toml, agents/.claude/settings.json, agents/.claude/agents/implementer.md, agents/.codex/agents/implementer.toml, agents/.agents/skills/orchestrate/, README.md | agent | TO-DO |
| T7 | Live mixed-CLI acceptance through existing agent-runtime tools; record report provenance, peer exchange, wait call counts, blocker/resume and restart/replay evidence; fix only dispatch regressions exposed by this run | S24 | T6 | agents/plugins/agent-runtime/, docs/plans/2026-10-30-agent-runtime-dispatch.plan.md | agent | TO-DO |
| T8 | Advisory gap correction: count JSON framing in exact-byte batch limits and prove boundary pagination with a regression | S26 | T1 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR26 bf5575c; 142 tests, converge, review PASS; hosted CI n/a) |

| T9 | Advisory gap correction: reconcile selected worker failure before finalizing queued nonterminal any-events, including replay | S27 | T2 | agents/plugins/agent-runtime/server/, agents/plugins/agent-runtime/tests/ | agent | COMPLETE (PR28 626f258; 155 tests, converge, review PASS; hosted CI n/a) |

| T10 | Close host-output gap: complete-envelope pagination/receipt accounting and supported per-tool output limits, preserving legal single-report retrieval; largest-envelope regression and host wiring checks | S28 | T5 | agents/plugins/agent-runtime/, agents/.codex/config.toml | agent | TO-DO |

Host-output investigation identified T10: legal 128KiB message batches exceed default Codex/Claude output policies; activation remains blocked until envelope and host budgets agree. Live T7 must include a legal large-response probe through both installed hosts.

Advisory T2 gap review identified T9 (partial S7: queued progress can bypass runtime observation).

T5 advisory late-read pin finding duplicates its blocking S22 correctness finding and is handled by the current T5 correction.

T4 advisory state/ownership findings duplicate its blocking S17/S18 correctness findings and are handled by the current T4 correction.

T3 advisory first-use identity finding duplicates its blocking S14 correctness finding and is handled by the current T3 correction.

Advisory T1 gap review identified T8 (partial S20: array framing excluded from the declared message-byte budget). T1 correctness review ran on Sonnet 5.5 because this runtime exposed no native Agent/Opus tool; no blockers in its initial diff. D15/S25 record the integration correction before further implementation.

Approval covers these decisions and acceptance scenarios. Implementation handoff: `/orchestrate docs/plans/2026-10-30-agent-runtime-dispatch.plan.md`. Living-spec archival follows the orchestration/converge workflow after all required checks pass.
