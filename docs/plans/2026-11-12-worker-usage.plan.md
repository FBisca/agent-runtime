# Plan: worker usage reaches the orchestrator's metrics

Slug: `worker-usage`. Contract: `2026-11-12-worker-usage.plan.yaml`.

## Why now

Every ticket in every run's `metrics.json` reports `usage: "unknown"` (37 of 37 tickets over 8 runs), every
`attempts.jsonl` record has null tokens and null `tool_calls`/`model_calls` (96 of 96), and no run's `events.jsonl`
holds a single `usage` event (0 of 9,487 rows). The orchestrator is ready to record usage: `driver.record_usage`
keeps `assignments[].usage` from each `wait`, `events.rollup` sums it per ticket and `evidence.sum_tokens` labels it.
The runtime never sends any. `usage.resolve_session_log` binds an assignment to its CLI session log and has no caller.
No code parses a token out of a log, and `wait` attaches nothing.

The predecessor plan (`2026-11-06-worker-token-metrics`) landed the Codex worker profile (T1) and the binding (T2).
Its parsers, surfaces, ledger and baseline (T3 to T6) never landed, and Pi was never in scope. This plan replaces its
T3 and T4 with a numeric contract the orchestrator already reads, covers all three harnesses, and drops T5 and T6.

## Sources

| Source | What it settles |
|---|---|
| The request (this session) | Fix usage for all three harnesses: Claude, Codex, Pi |
| `docs/plans/2026-11-06-worker-token-metrics.plan.md` | The marker binding (D5, D6), multi-rollout Codex sessions (D21, refined here by D7: the extra rollouts are guardian child threads), fail open (D16), pure parsers on trimmed fixtures (D15), never write under `~/.codex` or `~/.claude` (D19), the dispatch store schema untouched (D20) |
| `packages/agent-runtime/src/agent_runtime/usage.py` | Worker metadata at spawn (all three CLIs), `resolve_session_log` (Claude and Codex only, uncalled) |
| `packages/agent-runtime/src/agent_runtime/hc_tools.py` (`spawn`, `resume`, `wait`) | `resume` mints a new `assignment_id` and appends its marker to the prompt sent into the live session; `wait` returns the store batch unchanged |
| `packages/agent-runtime/src/agent_runtime/dispatch_store.py:1075` | The wait's byte budget reserves worst-case assignment rows before choosing messages (`MAX_BASE_BYTES`, `MAX_RESPONSE_BYTES`, `MAX_BATCH_BYTES`) |
| `tests/test_mcp_server.py:197-213` | The 400-word tool-description budget (399 used) |
| `packages/agent-orchestrator/src/agent_orchestrator/{driver,events,evidence}.py` | The orchestrator keeps the latest usage per assignment, sums numeric dicts per ticket, and reads `input_tokens`, `output_tokens`, `cached_input_tokens` |
| Gather over `~/.codex/sessions` (132 rollouts) | `token_count.info.total_token_usage` is cumulative within one rollout file, restarts in each resumed file (43 of 86 sessions span several files), repeats an identical total 13 times in 1,334 events; `input_tokens` includes `cached_input_tokens` |
| Gather over `~/.claude/projects` (80 recent transcripts) | 4,241 of 9,230 assistant usage records repeat an earlier `message.id` with identical usage; `input_tokens` excludes cache read and write; Task subagents write `<session>/subagents/*.jsonl` |
| Gather over `~/.pi/agent/sessions` (136 sessions) | Per-message `usage` (`input`, `output`, `cacheRead`, `cacheWrite`, `reasoning`, `cost.total`), no duplicates in 9,015; the marker is in the worker's first prompt for 23 of 23 Pi workers |
| Cost probe (this session) | Codex: no dollar figure for this account (`account/usage/read` returns `null`, `credits.balance` is `"0"` in 2,852 records). Claude: `cost-state.totalCostUSD` is cumulative per session, written at exit, `/clear`, resume and compaction; 442 of 475 worker transcripts carry one; 292 of 475 hold more than one assignment. Pi: `cost.total` per message |
| Grill (this session) | Q1 to Q7 below |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `wait` puts one flat numeric dict `usage` on each assignment row whose worker has a bound log | Old D8's preformatted line; a nested dict | The orchestrator sums numeric dicts and reads these names; a line leaves every ticket `unknown` | grill Q1; `events.py:70` |
| D2 | The keys are `input_tokens` (fresh input, cache excluded), `cached_input_tokens` (cache read), `cache_write_tokens`, `output_tokens` (reasoning included), `reasoning_tokens`, `model_calls`, `tool_calls`, `cost_usd`. A figure the CLI does not report is left out, never `0` | Codex's gross input; Claude's names | One convention for three CLIs; Codex's input is converted by subtracting its cached input; `cache_write_tokens` is already the orchestrator's name in `metrics()` | grill Q2 |
| D3 | Usage is per assignment: the slice of the worker's session from the prompt whose last `assignment_id=<id>` is that assignment up to the next such prompt. A prompt is a Claude `user` record (not `isMeta`, compact summary or tool result), a Codex root-thread `user` message or a Pi `user` message; the runtime's own marker is always last, so a task that quotes another id still starts the right slice. Markers anywhere else start no slice | A worker's running total on every assignment; the first marker in a prompt | The orchestrator sums assignments per ticket; a running total would count a twice-resumed worker three times. `resume` is `hc send` into the live session, so the next assignment continues the same log | grill Q2 (round 1); `hc_tools.py` resume; oracle review |
| D4 | The runtime computes usage when `wait` returns a batch, once per returned batch, through `usage.assignment_usage(env, worker) -> {assignment_id: usage}` | Parsing in the orchestrator; parsing inside the store's read loop | The binding already lives in the runtime; the store loop re-reads many times per wait | grill Q3 |
| D5 | Parsing is incremental: a sidecar `<worker>.usage.json` (0600) beside the worker's metadata keeps each log's consumed byte offset and the running per-assignment counters; a log that shrank or changed identity is re-read from the start | Re-parsing on every wait; caching in the dispatch store | Codex rollouts reach several MB and `wait` is called repeatedly; D20 keeps the store schema untouched | old plan Performance risk; grill Q3 |
| D6 | The store's budget reserves a worst-case `usage` dict in every assignment row it budgets, so the dict attached after the read never pushes the response over its cap | Attaching unbudgeted; dropping usage when the batch is full | A dict of eight numbers is about 250 bytes; reserving it keeps the receipt honest | `dispatch_store.py:1075`; old S14 |
| D7 | A worker's usage includes the threads it caused in their own files: Claude Task subagents (`<session>/subagents/*.jsonl`) and Codex child threads (`thread_source: guardian_review`, same `session_id`, 46 of 132 local rollouts). Each of their records is credited to the assignment whose marked prompt is the latest at or before the record's timestamp; they never start a slice, and the binding always picks the root thread (`id == session_id`) | Leaving them out; binding whichever file matches first | They are cost the worker caused; a guardian thread's prompt quotes the parent's markers, so it would otherwise bind or slice wrongly | grill Q4; oracle review |
| D19 | The Claude project directory is the cwd with every non-alphanumeric character a dash; the binding's `/`-and-`.`-only rule is fixed at its class | Leaving the existing rule | A cwd with `_`, a space or other punctuation never binds today | oracle review; AGENTS.md "fix the class" |
| D20 | Within a slice, a figure is summed over the records that carry it and reported when at least one does (Pi omits `reasoning` on a message with none); absent when none does | Absent when any record lacks it | A per-message optional field is the CLI's convention for zero; across assignments, D14's null rule applies instead because an assignment's absence means it was not measured | oracle review |
| D8 | `model_calls` counts model responses after deduplication (Codex: distinct cumulative totals; Claude: distinct `message.id`, `<synthetic>` excluded; Pi: assistant messages). `tool_calls` counts invocations (Codex `function_call` and `custom_tool_call` items; Claude `tool_use` blocks; Pi `toolCall` blocks) | Old S8's poll classification | Counting is enough to fill `attempts.jsonl`; classification is a later question | grill Q4 (round 2), Q5 (round 1) |
| D9 | `cost_usd` only when the CLI wrote the figure itself. No price table and no rate is ever hard-coded | A dated price table in the repo | The user's rule: no hard-coded values; if it is not dynamic, do not provide it | grill Q5 (round 2) |
| D10 | Pi's `cost_usd` is the sum of its messages' `cost.total` in the slice | — | Pi writes the figure per message | cost probe |
| D11 | Claude's `cost_usd` comes from `cost-state.totalCostUSD`: each checkpoint's increase over the previous one is credited to the assignment active when it was written. An assignment that saw no checkpoint and is followed by one carries `0`; the last assignment before any checkpoint carries none | A statusline injected at spawn; no Claude cost | Exact per ticket once the worker checkpoints; no change to how the worker runs | grill Q6 |
| D12 | Codex carries no `cost_usd` | Polling `account/usage/read` through a Codex app-server; OTel | Both return `null` on this account; revisit if billing becomes usage-based | grill Q7; cost probe |
| D13 | A Pi worker binds like the others: its sessions are `<PI_CODING_AGENT_DIR or ~/.pi/agent>/sessions/--<cwd without its leading / and with / replaced by ->--/*.jsonl`, matched by the marker in the first user prompt after the spawn time | A Pi-specific id passed at spawn | The marker is already in 23 of 23 Pi workers' first prompts | gather |
| D14 | In the orchestrator's rollup and in `attempts.jsonl`, a key that any of the ticket's or attempt's assignments lacks totals to `null`, never a partial sum | Summing whatever is present | A Pi implementer plus a Codex reviewer would otherwise show a cost that covers half the work | grill Q3 (round 2) |
| D15 | `attempts.jsonl` moves to schema 3: `tokens` gains `cache_write` and `reasoning`, the record gains `cost_usd`, and `counters.model_calls` and `counters.tool_calls` are summed from usage under D14 | Keeping schema 2 and leaving the counters null | The counters exist and are always null today; a new field needs a new version | grill Q5 (round 1); `evidence.py:136` |
| D16 | Everything fails open: a missing, unreadable, truncated or foreign log gives no `usage` key, never an error, on `wait` and in `assignment_usage` | Failing the wait | The predecessor's D16 still holds | old plan D16 |
| D17 | Out of scope: the `usage.jsonl` ledger and diff (old T5), the baseline script (old T6), the `status` surface (old S10), the context-window feed for swaps. The predecessor's T3 to T6 are marked superseded | Keeping them | The user cut them; swaps already fail open | grill Q6 (round 1) |
| D18 | The worker report's own `usage` string stays as it is | Removing it | It is the worker's word and is never read for metrics; changing the report contract is a separate change | gather |

## Out of scope

- The predecessor's ledger, diff, baseline and `status` metric line (D17).
- Dollar figures for Codex (D12) and any price table (D9).
- Classifying tool calls (polls vs reads).
- Feeding `context_tokens` and `effective_window` to the swap check.
- Any change to the MCP tool descriptions or parameters: the result shape changes, the advertised schema does not.

## Risks

- **Security:** the parsers read session logs, which hold prompts, file content and command output. Only counts,
  model call counts and dollar figures leave the parser. The sidecar holds offsets and numbers, 0600 in the 0700
  state directory. A test asserts that no prompt or tool text reaches the `usage` dict or the sidecar.
- **Performance:** covered by D5 (incremental offsets) and D4 (once per returned batch). A fresh parse is bounded by
  the log's size; a later `wait` reads only appended bytes.
- **Migration:** `attempts.jsonl` schema 2 to 3 (D15). Old records keep their version; readers key on
  `schema_version`. The manifest moves with it. No store migration (D20 of the predecessor).
- **Rollback:** remove the call in `wait` and the rows return to today's shape; delete the sidecars and nothing
  remains. The orchestrator's null rule and schema 3 are independent of the runtime change.

## Seams

- **`usage.assignment_usage(env, worker)`**: the runtime's measurement, driven from outside with real files under a
  temporary `HOME`, `CODEX_HOME` and `PI_CODING_AGENT_DIR`, and metadata written with `usage.write_worker_metadata`.
  The runtime outcome oracle (S12) attaches here.
- **`wait`** in `hc_tools.py`: `ToolCase` and the fake `hc` in `tests/test_tools.py`; `tests/test_budgets.py` for the cap.
- **The orchestrator run**: `GateFixture` in `packages/agent-orchestrator/tests/test_gates.py` scripts wait steps whose
  `assignments[0].usage` the driver records; `metrics.json` and `attempts.jsonl` are read back. The orchestrator
  outcome oracle (S13) attaches here.
- **Living spec:** `docs/specs/agent-runtime.md`. The orchestrator's scenarios (S10, S11) amend
  `docs/specs/orchestrator-engine.md` R2 at archive; this contract names one spec.
