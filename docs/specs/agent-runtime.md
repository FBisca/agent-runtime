# Agent runtime
Paths: .claude-plugin/, packages/agent-runtime/src/agent_runtime/, packages/agent-runtime/tests/, server/, hooks/, bin/, install.sh

The agent runtime gives skills one set of delegation verbs (spawn, message, resume, report, wait, task, collect, stop, status, list) that per-runtime adapters (hc, herdmates, native) implement. `agent-runtime resolve` picks the runtime deterministically. The plugin serves the verbs as MCP tools in Claude and Codex; a SubagentStart hook tells native subagents which reads they may call, and a PreToolUse guard restricts subagents and blocks raw hc commands in Bash. How Claude Code, Codex and Pi are identified and what each may mutate is specified in [harness-adapters.md](harness-adapters.md).

Under structured dispatch (`AGENT_RUNTIME_DISPATCH=1`, set in both Claude and Codex configs), reports, peer messages and tasks live in a private versioned SQLite store, not in hc text. `report` publishes idempotent, server-attributed events, and `wait` returns reports and assignment states together as tool results, with receipts acknowledging delivery. `status` and `collect` are a diagnostic fallback that never substitutes for an accepted report: diagnostics authorize against the dispatch store first and fall back to hc-only state with `delivery_error`, and authorization failures return no hc data.

## Requirements

### Requirement: Runtime resolution
`agent-runtime resolve` SHALL pick the runtime and role deterministically from the environment: `HERDR_PANE_ID` beats `HC_TERMINAL_ID`, which beats native.

#### Scenario: Herdr pane
- WHEN `HERDR_PANE_ID` is set
- THEN `--cli claude` prints `runtime=herdmates` and `--cli codex` prints `runtime=native`, and the pane wins when `HC_TERMINAL_ID` is also set

#### Scenario: hc terminal
- WHEN only `HC_TERMINAL_ID` is set
- THEN it prints `runtime=hc role=lead` when `hc get` reports `parent: null`, `runtime=hc role=worker parent=tX` when it reports `parent: tX`, and `runtime=hc role=unknown` with exit 0 when `hc get` fails or exceeds 2 s

#### Scenario: neither variable and no `--cli`
- WHEN neither variable is set
- THEN it prints `runtime=native role=lead`, and inside a Codex session without `--cli` it resolves as `cli=codex`

### Requirement: Status and list views
`agent-runtime status` and `list` SHALL report one JSON record per agent with a contract state and a non-empty `next` step.

#### Scenario: hc status mapping
- WHEN `status <name>` runs under hc
- THEN it prints `name, runtime, role, parent, state, turn, native, next`, `state` is working, idle, done, blocked, error, exited or unknown, and a blocked worker's `next` asks the user rather than offering raw `hc keys`

#### Scenario: herdmates and native
- WHEN `status` runs under herdmates it maps `herdr pane get` output; under native `state` is unknown and `next` points to the return value or SendMessage

#### Scenario: list
- WHEN `list` runs under hc
- THEN it returns one status per child of the caller

### Requirement: Subagent runtime limits
The PreToolUse hook SHALL deny a native Claude or Codex subagent every runtime tool that mutates or addresses an assignment, before the server runs, and SHALL let a lead through.

#### Scenario: a subagent calls a mutating or assignment-bound tool
- WHEN a native subagent calls `wait`, a mutating tool, a task mutation, a receipt acknowledgment or any runtime tool carrying `assignment_id`, or passes a string `tool_input` that is not a JSON object
- THEN the hook denies it; `task list` and receipt-free status, list and collect without `assignment_id` stay allowed, and a lead passes the hook

#### Scenario: the start notice
- WHEN a Claude subagent or in-process teammate starts
- THEN its context says it is a subagent, names the reads it may call and that it must not run runtime commands

### Requirement: Hook input errors allow
A hook SHALL exit 0 and allow when it cannot decide, never blocking on its own failure.

#### Scenario: unreadable input or state
- WHEN a hook gets invalid JSON on stdin, the store is missing or write-locked, python3 is missing, or no identity resolves
- THEN it exits 0 and allows (Claude: no output; Codex: empty or `{}`), and emits no decision for invalid JSON

### Requirement: Raw hc guard
The PreToolUse guard SHALL refuse the raw hc commands that mutate another agent, with a reason, and allow the ones a lead or a worker legitimately runs.

#### Scenario: mutating hc commands
- WHEN a Bash call runs `hc run`, `hc send` or `hc kill` as a subagent, or `hc keys`, `hc interrupt`, `hc kill` or `hc send --force` aimed at the worker's parent (Claude or Codex), or a native teammate carrying `agent_id` runs `hc send`
- THEN the hook denies it with a reason; wrapped, nested and command-substituted forms are inspected

#### Scenario: allowed commands
- WHEN a lead runs `hc send`, or a worker sends its result to its parent or runs hc aimed at its own child or a sibling, or reads hc state
- THEN it is allowed

### Requirement: Structured report delivery
`report` SHALL publish an idempotent, server-attributed event that the lead receives through a tool result and that survives a restart.

#### Scenario: a worker reports
- WHEN a Claude or Codex worker reports through MCP
- THEN its lead receives a structured report with server-derived sender, team, assignment and message ids, with no hc report text or injected user message; committed reports and pending acknowledgments remain recoverable after the MCP processes restart

#### Scenario: a retried publication
- WHEN a worker publishes again with the same message id
- THEN one event exists; the same id with different content returns a conflict

### Requirement: Receipt acknowledgment
A caller SHALL acknowledge delivered messages with a receipt, which SHALL be replay-safe and need no separate call.

#### Scenario: replay
- WHEN an unacknowledged batch is read again after cancellation or a lost response
- THEN its messages keep their ids, repeating a valid receipt is harmless, and acknowledged messages are omitted from later reads

#### Scenario: piggyback
- WHEN a caller attaches a valid receipt to its next runtime call
- THEN it is acknowledged; an invalid or foreign receipt fails explicitly and a failed transaction does not lose the batch

### Requirement: Bounded results
A result SHALL stay within the configured byte budget, deferring what does not fit without loss.

#### Scenario: over the limit
- WHEN a result, counting JSON framing and the complete envelope, exceeds the batch or payload limit
- THEN it returns bounded data with `more` and a continuation receipt, deferred messages stay retrievable, and acknowledgments cover only delivered messages

### Requirement: Wait all
`wait(target=all)` SHALL stay pending until every snapshotted assignment completes, then return all reports in one result.

#### Scenario: partial progress
- WHEN progress or one worker's complete report arrives while another selected assignment runs
- THEN the same call remains pending and later returns the accumulated reports together

#### Scenario: every assignment complete
- WHEN every snapshotted assignment reports complete
- THEN it returns `completed`, all reports and an empty unfinished list, without status or collect calls

### Requirement: Wait any and named
`wait(target=any)` SHALL return the first eligible event and any queued eligible batch, and a named wait SHALL filter to the selected worker.

#### Scenario: an eligible event
- WHEN `wait(any)` has an eligible event
- THEN it returns it with any already queued eligible batch, and a named wait returns only the selected worker's or assignment's events

### Requirement: Wait deadline and cancellation
A wait SHALL end at its deadline, on cancellation or when nothing is selected without losing messages or stopping workers.

#### Scenario: deadline, cancellation, empty selection
- WHEN a wait reaches its deadline, is cancelled, or has no selected assignments
- THEN it returns a timeout or empty result where a response is possible, or cancels cleanly; messages remain replayable and workers continue

### Requirement: Wait recovers missed events
A waiting recipient SHALL receive a committed event without model polling, even when notification was lost.

#### Scenario: a lost notification
- WHEN publication races listener registration or the publisher dies after commit before the IPC notification
- THEN the waiting recipient receives the committed event within the finite recovery bound

### Requirement: Responsive MCP calls
A waiting MCP call SHALL leave ping, publication and cancellation responsive and the protocol stream clean.

#### Scenario: a long wait
- WHEN one MCP call waits
- THEN ping, publication and cancellation stay responsive, stdout carries only valid protocol JSON and the call produces one final result

### Requirement: Wait attention and failure
`wait` SHALL return explicit attention or failure, with every unfinished assignment, instead of a timeout whenever a selected worker needs the lead.

#### Scenario: a worker needs the lead
- WHEN a selected assignment blocks, requests a decision, is interrupted, reports hc status `error`, ends its turn without an accepted complete, blocked or decision report, or its worker exits
- THEN the wait returns attention or failure naming all unfinished assignments; hc idle or done alone never proves success, and queued progress does not bypass a bounded hc observation of an exited or interrupted worker

#### Scenario: an observed failure
- WHEN `wait` observes a selected worker exited, interrupted or replaced before completion
- THEN the result carries one persisted failure message, and once its receipt is acknowledged later `wait(all)` snapshots exclude the assignment

### Requirement: Unreported workers
`wait` and `status` SHALL flag a worker that never reported a status after a grace period, without answering for it.

#### Scenario: past the grace period
- WHEN a live worker has never reported a status (`reported:false`, `status_ms:null`, `turn:0`) and was created more than 30 s ago
- THEN `wait` returns attention with `runtime_state: unreported` and a next step naming collect, asking the user, user-approved hc keys or stop, and `status(name)` shows the same state and rows; it never answers the dialog or claims a confirmed trust block

#### Scenario: not unreported
- WHEN the worker is younger than 30 s, or has reported a status once and later shows null, or its terminal exited, was interrupted or replaced
- THEN it is not flagged; a younger worker counts as working and a lost terminal returns its one failed message

#### Scenario: probing cost
- WHEN `wait` polls a team whose members all have a non-null hc status
- THEN it runs no `hc get`; only live members whose `hc ls` status is null are read with `hc get`

### Requirement: Stalled workers
`wait` SHALL report a working assignment that makes no progress once per episode, never stopping or completing it.

#### Scenario: a stall
- WHEN a working assignment has had no `publish` progress and no hc turn change for 15 minutes
- THEN `wait` returns `attention: stalled` with a next step, once per episode, marked in the transaction that records the receipt and counted in the batch attention; progress then renews it for a later idle period

#### Scenario: clocks and precedence
- WHEN an assignment is first observed, it starts its idle clock at that observation; when it has exited or failed, `failed` wins over `stalled`
- THEN the worker is never stopped and never marked complete

### Requirement: Concurrent dispatch
`spawn` and `resume` SHALL return before the assignment finishes so mixed-CLI workers run concurrently.

#### Scenario: several workers
- WHEN `spawn` or `resume` is invoked for several mixed-CLI workers
- THEN dispatch returns before the assignments finish, they run concurrently, and each resume receives a fresh assignment id

### Requirement: Stale reports
A late report SHALL NOT complete a current assignment or reach another team generation.

#### Scenario: an old assignment or a reused address
- WHEN a late report references an old assignment, or an hc address or name has been reused
- THEN it cannot complete the current assignment or leak into another team generation

### Requirement: Peer messages
Teammates SHALL exchange messages through the recipients' tool results with the true sender identity.

#### Scenario: waiting and idle recipients
- WHEN two teammates exchange messages
- THEN messages arrive in a waiting recipient's tool result with true sender identity, and an idle recipient retains them for its next call or resume without forced input

### Requirement: Durable shared tasks
The `task` tool SHALL keep durable tasks with dependencies that every teammate sees identically and claims atomically.

#### Scenario: shared view
- WHEN a lead creates and lists tasks with dependencies
- THEN every teammate sees the same task ids, dependency state, owner and status after a restart

#### Scenario: concurrent claim
- WHEN two teammates claim a ready task at once
- THEN exactly one wins; unsatisfied dependencies, missing dependencies and cycles are rejected explicitly

#### Scenario: report drives the task
- WHEN the owning assignment reports complete or blocked
- THEN its linked task changes in the same transaction, dependents become claimable only after successful prerequisites, and a duplicate report does not duplicate a transition

### Requirement: Task release on worker loss
A task SHALL return to `pending` with no owner when its worker is lost.

#### Scenario: stopped, failed or observed failure
- WHEN a stopped worker, a failed spawn or a persisted observed failure has an unfinished linked task
- THEN the task returns to `pending` with no owner in the same transaction

### Requirement: Spawn with a task
`spawn` SHALL link a ready task to the new worker atomically.

#### Scenario: a ready task
- WHEN a lead spawns a worker with a ready `task_id`
- THEN the task links to the new worker and assignment, the result returns `task_id`, and the worker's complete report finishes the task

#### Scenario: an unusable task
- WHEN the task is missing, foreign, not pending, has unmet dependencies or loses a concurrent claim
- THEN spawn fails before starting a terminal and exactly one claimant owns the task

### Requirement: Resume with a task
`resume` SHALL move a task to the fresh assignment only when asked.

#### Scenario: with and without `task_id`
- WHEN a lead resumes a worker with `task_id` for its in_progress or blocked task, or a ready pending task
- THEN the task moves to the fresh assignment and that assignment's complete report finishes it; resume without `task_id` leaves links unchanged

### Requirement: Stopped workers
`stop` SHALL leave committed history readable and never strand waiters or another lead's terminal.

#### Scenario: history and waiters
- WHEN a worker is stopped or removed
- THEN its committed reports and task history stay readable, an active incomplete assignment is surfaced as unfinished or failed, and later `wait(all)` snapshots exclude stopped or acknowledged assignments (returning `empty` when none remains)

#### Scenario: a missing or reused terminal
- WHEN the worker's hc terminal no longer exists, `stop` succeeds and the next `wait(all)` excludes it; when its name now belongs to another lead's terminal, only the caller's record is marked stopped and the other terminal is neither killed nor removed

### Requirement: Diagnostic fallback
`status`, `collect` and `list` SHALL authorize against the dispatch store first and fall back to hc-only state with `delivery_error`, never replacing an accepted report.

#### Scenario: delivery failure
- WHEN structured delivery fails, the store fails, identity cannot be resolved or hc role lookup times out, or the session does not own the terminal
- THEN they return hc state or the diagnostic view with an explicit `delivery_error` naming the refusal instead of an error, and never stand in for an accepted complete report

#### Scenario: authorization
- WHEN a forged identity or an unproven worker calls them
- THEN the call fails with its authorization error and returns no hc data, inbox or pin

#### Scenario: inbox only with a receipt
- WHEN `status`, `list`, `collect` or `task list` run without a `receipt`
- THEN the result carries no inbox piggyback and acknowledges nothing; with a valid receipt, pending inbox messages are returned

### Requirement: Dispatch activation
The structured path SHALL apply when dispatch is enabled and the legacy path SHALL stay usable when it is absent.

#### Scenario: with and without activation
- WHEN `AGENT_RUNTIME_DISPATCH` is absent
- THEN spawn, report, wait and resume keep their legacy behaviour; when enabled, the structured path applies

### Requirement: Tool schemas and descriptions
The ten runtime tools SHALL publish schemas and instructions that support the dispatch flow within 400 words of descriptions.

#### Scenario: tools/list
- WHEN a client lists tools
- THEN all ten tools including `task` are listed, total descriptions stay within 400 words, `wait` and `resume` say delivery is pull-only so reports landing later need a new `wait(all)`, and `spawn` publishes optional string `model` and `instructions_file`, an `agent` property whose description says the agent needs the `report` tool and that `none` means no role, and `effort` as an enum of `low`, `medium`, `high`, `xhigh`, `max`

### Requirement: Spawn failure cleanup
A failed `spawn` SHALL leave no live terminal, an inactive worker and a failed assignment.

#### Scenario: failure after `hc run`
- WHEN spawn fails after `hc run` (address unreadable, timeout, cancellation, bind error or unsent first message)
- THEN the terminal is removed, the assignment is failed, the linked task returns to `pending` with no owner, the lead receives one `failed` message even when the worker already reported blocked or decision, and messages or resumes addressed to the worker fail explicitly

#### Scenario: nothing to start
- WHEN spawn cannot resolve identity or validate its inputs
- THEN no terminal is started

#### Scenario: another worker's terminal
- WHEN cleanup runs while another spawn of the same name has bound its live terminal, or the ownership check hits a store error
- THEN the other terminal is kept and the failed spawn still fails with its original error; when the timeout comes after the worker's complete report landed, the error says the result already landed

#### Scenario: a failed resume
- WHEN a resume fails after the resumed worker's report already landed
- THEN that report stands and the linked task stays with the worker's assignment

### Requirement: Store indexes
The store SHALL index membership and wait queries without changing its schema version.

#### Scenario: initialization and plans
- WHEN the store initializes
- THEN the indexes exist, `user_version` is unchanged and old-shape writes still succeed; `wait` snapshot and membership queries use the indexes and do not scan message bodies

### Requirement: Live scope binding
A worker SHALL bind to its lead across hc scopes with its assignment id.

#### Scenario: different `HC_SCOPE` tokens
- WHEN a lead and its worker run in real hc terminals with different `HC_SCOPE` tokens on one hc server
- THEN the worker's first runtime call with its assignment id binds and its reports reach the lead

### Requirement: Pending work on results
A lead with unfinished assignments SHALL be told to wait on every spawn, resume and message result.

#### Scenario: unfinished work
- WHEN a lead (including a worker leading its own children) with N > 0 unfinished assignments gets a `spawn`, `resume` or `message` result
- THEN it carries `unfinished: N`, counting the assignment that call created, and a `next` telling it to call `wait(target=all)`

#### Scenario: nothing unfinished
- WHEN the caller has no unfinished assignments as lead, or is a worker messaging its lead or a peer
- THEN the `message` result carries no `unfinished` or `next`

### Requirement: Worker launch flags
`spawn` SHALL launch an interactive worker that does not notify the lead through hc.

#### Scenario: argv
- WHEN `spawn(kind=worker)` starts a worker
- THEN the argv carries no one-shot flag (`--print`, `-p` for Claude, `exec` for Codex) so the pane can be resumed, and a claude or codex worker's `hc run` argv carries `--no-notify` before the program

### Requirement: Spawn model
`spawn` SHALL pass a valid `model` to the CLI and refuse an invalid one before launching.

#### Scenario: a model
- WHEN `spawn` runs with `model: "haiku"` for Claude or `"gpt-5.5"` for Codex
- THEN the argv is `claude --permission-mode auto --model haiku <prompt>` or `codex --no-daemon [-p worker] --model gpt-5.5 <prompt>`, prompt last, and `-p <name>` appears only when the worker profile is usable (`worker` by default, `AGENT_RUNTIME_CODEX_PROFILE` overrides); without `model` the argv has no model flag

#### Scenario: an invalid model
- WHEN `model` is blank, not a string, has leading, trailing or inner whitespace or control characters, or starts with `-`
- THEN `spawn` returns an error and no `hc run` happens

### Requirement: Spawn effort
`spawn` SHALL pass a valid `effort` to the CLI and refuse any other.

#### Scenario: effort
- WHEN a claude-family worker has `effort` set the argv carries `--effort <level>` after any `--model`; a codex-family worker carries `-c model_reasoning_effort="<level>"` after `--no-daemon` and any `--model`; without `effort` neither CLI gets an effort argument
- THEN anything other than `low`, `medium`, `high`, `xhigh` or `max` returns an error and nothing is launched

### Requirement: Spawn agent role
`spawn` SHALL load a named `agent` role for its CLI and refuse an unusable one before launching.

#### Scenario: a named agent
- WHEN `agent` is `scene-reviewer` for Claude, or `reviewer` for Codex
- THEN the Claude argv is `claude --agent scene-reviewer --permission-mode auto [--model <m>] <prompt>`, and the Codex prompt starts with `developer_instructions` from `~/.codex/agents/reviewer.toml`, then the task; `plugin:reviewer` is accepted for Claude only

#### Scenario: an unusable agent
- WHEN `agent` is not a string, blank, fails `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, contains `:` for Codex, or the Codex toml is missing or lacks `developer_instructions`
- THEN `spawn` returns an error (naming the path for the toml) and no `hc run` happens

### Requirement: Worker protocol without a role
A worker spawned without a role SHALL be told the report protocol.

#### Scenario: no role
- WHEN `spawn` omits `agent`, or passes `agent: "none"`
- THEN every CLI family uses no role (naming `implementer` selects that role), Claude's argv carries no `--agent`, Codex's prompt starts with the task and reads no toml, and the prompt states that a final message alone never reaches the parent, how to `report` (`report(kind, assignment_id)` under dispatch, `report(text)` otherwise) and the `STATUS: COMPLETE | BLOCKED | PARTIAL` block

### Requirement: Spawn instructions file
`spawn` SHALL start a role-less worker from an instructions file, only where the CLI does not load a role itself.

#### Scenario: a Codex or role-less backend
- WHEN `spawn` runs with `instructions_file` (absolute or `~/` path) on Codex or a backend with no role file
- THEN the prompt starts with the file's content, then the task, and no `~/.codex/agents` toml is read

#### Scenario: refused
- WHEN `instructions_file` is not a string, blank, not an absolute or `~/` path, or names a missing file or directory, or is combined with `agent`, or used with `cli: "claude"`
- THEN `spawn` returns an error naming it and no `hc run` happens

### Requirement: Claude profile settings
A claude-family profile SHALL choose the worker's permission mode and settings file, and an invalid profile SHALL stop the spawn.

#### Scenario: a valid profile
- WHEN a profile sets `permission_mode` (`acceptEdits`, `auto`, `bypassPermissions`, `dontAsk`, `manual`, `plan`) and an existing `settings` file (absolute or `~/`)
- THEN the argv carries `--permission-mode <mode>` in place of `auto` and `--settings <path>` right after it; a profile without them keeps `--permission-mode auto`

#### Scenario: an invalid profile
- WHEN the mode is unknown, `settings` is not an absolute or `~/` string, either is set on a non-claude profile, or the settings file does not exist at spawn time
- THEN the config is invalid (only built-ins are published) or `spawn` errors naming the path, before `hc run`

### Requirement: Pi workers
A `pi` worker SHALL run trust-prompt free with the role and the runtime's MCP server it needs.

#### Scenario: argv and role
- WHEN `spawn` runs a `pi` worker, optionally with `agent: "<name>"`
- THEN the argv is `pi --approve [--model <m>] [--thinking <level>] <prompt>`, and the prompt carries the role's content from `~/.claude/agents/<name>.md` (front matter aside; `AGENT_RUNTIME_ROLE_DIR` moves the directory) ahead of the task while the report protocol still applies; a missing, empty or unreadable role file errors naming it before `hc run`

#### Scenario: MCP registration
- WHEN a `pi` worker is spawned
- THEN Pi's user-level `mcp.json` (`$PI_CODING_AGENT_DIR/mcp.json`, else `~/.pi/agent/mcp.json`) carries the `agent-runtime` server with this runtime's absolute server path, `AGENT_RUNTIME_DISPATCH=1`, `exposure: deferred` and `toolExposure: {report: direct}` (the block the installer prints exposes the same), every other server in the file survives, and an unreadable or unwritable configuration is an error naming it before `hc run`

### Requirement: Call command
`agent-runtime call <tool> --lead L --json '<args>'` SHALL run a dispatch tool from an hc terminal and print its JSON result.

#### Scenario: success and errors
- WHEN it runs in an hc terminal
- THEN it prints the tool's JSON result and exits 0, and a tool error is printed as JSON with exit 1; when the caller is not an hc terminal it exits 2 with the `require_hc` message, and invalid `--json` or an unknown tool exits 2 with usage, starting nothing

#### Scenario: shared lead
- WHEN two separate `call` processes in the same hc terminal pass the same `--lead`
- THEN `wait` in the second sees and receives reports for assignments spawned by the first

### Requirement: Call guard
The PreToolUse hook SHALL deny a subagent `agent-runtime call` and ask before a non-subagent mutating one.

#### Scenario: through Bash
- WHEN a subagent runs `agent-runtime call <tool>` through Bash, by name, path or python
- THEN the hook denies it, reading arguments as the CLI parser does; a non-subagent running a mutating tool is asked

### Requirement: Call generation conflict
`call` SHALL refuse a `--lead` that does not match the live generation of the terminal's latest team.

#### Scenario: a second generation
- WHEN `call` runs with a `--lead` different from the generation of the terminal's latest team and that team has unfinished assignments
- THEN it exits 2 naming the live generation, starts nothing, and the other generation's `wait` still receives its reports

### Requirement: Operation keys
`spawn` and `resume` SHALL accept an `operation_id` that makes a retry return the first assignment instead of repeating it.

#### Scenario: a new key
- WHEN `spawn` or `resume` carries an `operation_id` (1-128 characters of `A-Z a-z 0-9 . _ : -`, dispatch only) this owner has not used
- THEN one row (team, owner, operation_id, fingerprint, assignment id) is written in the transaction that registers the assignment, before anything is sent; the owner is the lead's terminal on its hc server and the fingerprint covers verb, name, CLI family, profile, task text, cwd, model, effort, role, instructions text and task link

#### Scenario: a repeat
- WHEN the same owner repeats the key with the same fingerprint
- THEN the call returns the existing assignment with its state and `replayed: true`, sends and launches nothing and takes no team slot; a failed first call is reported as `failed` with the way out; a different fingerprint is refused naming the conflict

#### Scenario: a replaced hc socket
- WHEN keys are minted and later the socket at the same path and terminal is replaced (owner includes the socket path, terminal id and device, inode and ctime)
- THEN restarts on the same socket keep the keys, prior-instance keys return `unknown`, and keyed spawn and resume refuse them without launching; unavailable socket identity fails closed with retry guidance

### Requirement: Operation lookup
`agent-runtime call operation` SHALL report the owner-scoped state of an `operation_id` read-only.

#### Scenario: state
- WHEN `call operation --json '{"operation_id": ...}'` runs
- THEN it prints absent, pending, bound, working, blocked, decision, complete, failed or unknown with the assignment id, takes no receipt and never acknowledges a report; the operations table is additive, created by the first keyed call, with `user_version` staying 2

#### Scenario: store trouble
- WHEN the real Store cannot be constructed or read (`OSError`, `sqlite3.Error`, `StoreError`)
- THEN it returns `unknown`, a null assignment id and a reason naming the failure and how to restore access

#### Scenario: a CLI verb only
- WHEN it runs under a new `--lead` in a terminal whose previous lead is live
- THEN the lookup is exempt from the live-lead conflict check while every writer keeps it, and either MCP dispatch path receiving `operation` returns -32602 naming the unknown tool before any handler runs

### Requirement: Caller identity
`agent-runtime resolve --json` SHALL report who the caller is from the process ancestry, never from markers alone.

#### Scenario: caller kind
- WHEN `resolve --json` runs
- THEN it carries `caller: {kind: lead|worker|unknown, verified: bool}`; worker environment markers alone never verify, observed worker ancestry wins over missing markers, a finished terminal never verifies anyone, the nearest live ancestor terminal decides, and a caller outside any hc terminal is `unknown` unverified (or its marker's kind, unverified)

### Requirement: Session generation
A session SHALL keep one generation, computed the same way by the server and the hooks.

#### Scenario: one generation
- WHEN the MCP server and a hook run under the same Claude or Codex process, even with `CODEX_THREAD_ID` or `CLAUDE_CODE_SESSION_ID` set
- THEN both compute `<pid>:<lstart>` of the nearest CLI ancestor, and a lead running `/clear` or `/new` in the same process stays on the same team

#### Scenario: a missed ancestry walk
- WHEN a later call's ancestry walk misses its budget
- THEN the session keeps the generation it resolved first, the team does not fork, and the first team's workers do not go stale

### Requirement: Terminal ownership
The newest live-or-occupied team row SHALL own an hc terminal, and a session that does not own it SHALL be refused.

#### Scenario: a second session
- WHEN a second CLI session inherits a live lead's terminal id and calls a runtime verb with a different generation
- THEN the call is refused with an error naming the terminal, the owning generation and the way out (its own hc terminal, or a new session once the owner is gone), no team row is registered for it, and the live lead keeps its team and workers

#### Scenario: a dead and empty row
- WHEN the terminal's newest team row belongs to a gone process and holds no worker, assignment, message, receipt, caller, task, last_wait row or operation
- THEN it is skipped and the live lead's calls and spawns authorize against its own team

#### Scenario: a live or occupied row
- WHEN the newest row's process still runs, or is gone but holds any of those
- THEN that row keeps the terminal and the live lead is fenced with "stale team generation" naming the owning generation and the way out

### Requirement: Hooks read terminal ownership
The lead-liveness hooks SHALL apply the same ownership rule as the server.

#### Scenario: a newer team row
- WHEN the context reminder or Stop guard runs for a lead whose terminal has a newer team row
- THEN a dead and empty row does not hide the lead's own team and a live or occupied one still owns the terminal

### Requirement: Lead wait record
The store SHALL record each lead's last final `wait(all)` and a fingerprint to detect unseen work.

#### Scenario: recording and comparing
- WHEN a lead's `wait(all)` returns a final batch
- THEN the team's last-wait row holds its reason, attention count and current fingerprint, committed with the receipt (narrower and cancelled waits leave it unchanged); a later lead-addressed message, assignment state change or turn changes the computed fingerprint, while outbound and worker peer messages do not

### Requirement: Lead reminder context
The context hook SHALL remind a lead of unseen work in one action-aware line and print nothing otherwise.

#### Scenario: work for the lead
- WHEN Claude SessionStart fires with `source` compact or clear, Codex SessionStart (compact or clear) fires, or UserPromptSubmit fires on either, for a lead with undelivered messages or unseen working assignments
- THEN `additionalContext` is `agent-runtime: N unfinished, M undelivered — call wait(all)`; when the last delivered wait reported attention and the fingerprint is unchanged it is `agent-runtime: N workers need you — check status`

#### Scenario: nothing to say
- WHEN the caller leads no team with needs, its team's generation differs from the hook's, or SessionStart `source` is startup, resume or fork
- THEN the hook prints nothing

### Requirement: Stop guard
The Stop hook SHALL block a lead's turn from ending while it has unseen work.

#### Scenario: unseen work
- WHEN a Claude or Codex lead (or a worker leading its own children) ends its turn with undelivered messages or unseen working assignments
- THEN Stop blocks with `agent-runtime: N unfinished, M undelivered — call wait(all) before ending the turn`

#### Scenario: nothing to wait for
- WHEN only blocked, decision or failed assignments remain, the last wait's reason is not timeout and the fingerprint is unchanged, or the only working assignments left carry a reported unchanged stall
- THEN Stop allows, and a worker's progress makes the stall count as unseen again

#### Scenario: a worker with its own assignment
- WHEN a caller leads no team but holds a current assignment in state pending or working
- THEN Stop blocks with a reason naming `report complete` and `report blocked`; with no such assignment, or only teams of dead or superseded generations, it allows

### Requirement: Stop guard budget
The Stop guard SHALL block at most three times before it allows with a note.

#### Scenario: budget spent
- WHEN the guard has blocked 3 times since its last reset
- THEN the next Stop allows with one note in the dialect's channel (a stderr line for Claude and Codex, the `reason` of `{"block": false, "reason": <text>}` for `--dialect json`) and later Stops allow until UserPromptSubmit fires or a check finds nothing to block on

#### Scenario: resets
- WHEN a Stop allows because the budget is spent, because of the backgrounded-wait exemption or because of an error
- THEN the budget is not reset; UserPromptSubmit resets it to 3

### Requirement: Backgrounded wait exemption
A lead with a running backgrounded `wait` SHALL be allowed to stop without spending budget, and a worker SHALL never be exempt.

#### Scenario: a background MCP wait
- WHEN a lead's `background_tasks` holds a running `MCP task` for server agent-runtime and tool `wait`
- THEN Stop allows without spending budget; a worker is never exempt because a parked wait does not deliver its own assignment, and an unknown task shape only loses the exemption

### Requirement: Hooks never write the store
A hook SHALL only read the dispatch store, wherever it lives.

#### Scenario: reading
- WHEN a hook reads the store
- THEN it never writes `dispatch.sqlite3` and honours `XDG_STATE_HOME` whether or not `AGENT_RUNTIME_DISPATCH` is set in its environment

### Requirement: Probe timeouts
A bounded probe SHALL NOT hide a failed worker or a launcher's diagnostic behind a queued report.

#### Scenario: a slow probe
- WHEN a bounded probe (a wait's status probe, a launcher's startup diagnostic) needs more than 0.2 s
- THEN the runtime still observes it and reports the failed or blocked worker, or the launcher's diagnostic, instead of the queued report or a bare exit

### Requirement: Team limit
A team SHALL hold at most 32 active workers, counting active workers only.

#### Scenario: stopped workers do not count
- WHEN a team has 32 registered workers, 31 stopped, and the lead spawns a new name
- THEN the spawn succeeds

#### Scenario: a full team
- WHEN a team has 32 active workers and the lead spawns a new name or resumes a stopped name
- THEN the call is refused before anything launches with "team limit is 32 active workers: stop an active worker, then retry", the slot is reserved in the transaction that registers the assignment so concurrent calls cannot exceed it, and resuming an already active worker consumes no new slot

### Requirement: Receipts path guard
The PreToolUse hook SHALL refuse a worker or subagent any mutation of an orchestrator ledger, its receipts or lock, and nothing else.

#### Scenario: a guarded path
- WHEN a worker or subagent mutates a `receipts/` subtree, a ledger or a lock under the default root `~/.local/state/orchestrate/`, or under any root a `<repo-key>/<slug>/receipts/` path, the sibling `<slug>.json` or its lock where the ledger carries `"kind": "agent-orchestrator/ledger"`, written relative, absolute, through a symlink alias, `$HOME`, `${HOME}` or `~`
- THEN the hook denies it with the reason and the way out (fix the code and let the driver re-run the evidence); reading it is allowed, and the hook resolves the caller's role before reading a candidate ledger (leads read none; workers read the first 4096 bytes of regular files, never a FIFO or device)

#### Scenario: an unguarded path
- WHEN the path has a `state/receipts` component but no orchestrator ledger beside it (e.g. `src/state/receipts/slice.ts`), or is an unmarked ledger outside the default root
- THEN the hook allows it

### Requirement: Store failure handling
A store failure SHALL roll back what is reversible and say what was and was not done.

#### Scenario: before the external effect
- WHEN `spawn`, `resume`, `message`, `report`, `stop` or `task` raises `sqlite3.Error` or `StoreError` before its external effect
- THEN every reversible pending change is rolled back, a resume's pending assignment is failed and its moved task released back, and the error says what was not done and that the call can be retried

#### Scenario: after an irreversible effect
- WHEN the failure follows a sent message or a killed or removed terminal
- THEN the error names the effect that happened and the store state left to reconcile; a store refusal (an unregistered name, a foreign receipt) is never presented as a retryable store failure, a failing rollback never replaces the original error, and a failure of the piggybacked inbox never turns a committed effect into an error

### Requirement: MCP request ids
The server SHALL answer a `tools/call` with a non-scalar id with an error and keep serving.

#### Scenario: an array, object or boolean id
- WHEN a `tools/call` request carries such an id
- THEN the server answers JSON-RPC error -32600 with `id: null` and serves the next request; valid notifications keep their semantics

### Requirement: Call thread release
The server SHALL NOT retain the threads of completed calls.

#### Scenario: many calls
- WHEN many tool calls have completed in one server process
- THEN the number of retained call threads returns to the number in flight

### Requirement: Call caps
The server SHALL refuse calls beyond fixed caps at once with a retryable reason instead of queuing them.

#### Scenario: over the cap
- WHEN 56 `wait` calls are in flight and another `wait` arrives, or 64 calls of any kind are in flight and another arrives
- THEN the extra call is refused at once with a retryable reason; the 8 slots above 56 stay available to non-wait tools, and `ping` and cancellation are outside the cap

### Requirement: Claude worker usage
A Claude worker's usage SHALL be measured from its transcript, each message once.

#### Scenario: counting
- WHEN a transcript repeats an assistant record under the same `message.id`, carries a `<synthetic>` record, and its Task subagents wrote `<session>/subagents/*.jsonl`
- THEN each `message.id` counts once for tokens and model calls, every `tool_use` block is a tool call, `<synthetic>` counts for nothing, and each subagent record is credited to the assignment whose marked prompt is the latest at or before its timestamp

#### Scenario: cost checkpoints
- WHEN the transcript carries `cost-state` checkpoints with a cumulative `totalCostUSD`
- THEN each increase is credited to the assignment active when it was written; a slice with no checkpoint carries `cost_usd` 0 when a later checkpoint exists and none otherwise

### Requirement: Codex worker usage
A Codex worker's usage SHALL be measured from its root thread's rollout.

#### Scenario: counting
- WHEN the cumulative `total_token_usage` repeats a total or carries `info: null`, and a child thread (`thread_source: guardian_review`, same `session_id`) has its own rollout
- THEN the worker binds to the root rollout, each assignment's figures are the increase of the root's cumulative total across its slice, a child's figures are credited by time and never start a slice, a repeated total counts as no model call, and `function_call` and `custom_tool_call` items are tool calls

#### Scenario: no dollar figure
- WHEN a Codex worker is measured, or any record lacks a figure
- THEN Codex usage never carries `cost_usd`, and a figure is absent, never 0, when no record of the slice carries it; no dollar figure is computed from a rate

### Requirement: Pi worker usage
A Pi worker's usage SHALL be measured from its session file.

#### Scenario: binding and summing
- WHEN a Pi worker is measured
- THEN its session is found under `<PI_CODING_AGENT_DIR or ~/.pi/agent>/sessions/--<cwd slug>--/` by the marker in its first user prompt after the spawn time, per-message `usage` is summed per slice, assistant messages are model calls, `toolCall` blocks tool calls, and `cost_usd` sums the messages' `cost.total`

### Requirement: Assignment usage slices
A resumed worker's session SHALL be divided into one slice per assignment, by marked prompts only.

#### Scenario: marked prompts
- WHEN a session holds several user prompts carrying `assignment_id=<id>` and other records mention an assignment id
- THEN each slice runs from the prompt whose last marker is its id to the next such prompt; a prompt is a Claude `user` record that is not `isMeta`, a compact summary or a tool result, a Codex root-thread `user` message, or a Pi `user` message, and a marker anywhere else starts no slice

#### Scenario: three harnesses
- WHEN a Claude, a Codex and a Pi worker are each spawned on one assignment and resumed on a second
- THEN each assignment gets exactly its own slice's tokens, model calls, tool calls and (Claude, Pi) cost, and a worker with no log gets an empty dict

### Requirement: Usage measurement resilience
Measuring usage SHALL never fail, returning what it could measure.

#### Scenario: damaged input
- WHEN the worker has no metadata or bound log, or the log is missing, unreadable, truncated mid-line or not JSON
- THEN `assignment_usage` returns what it could measure (an empty dict when nothing), never raises, and the complete lines before a truncated last line still count

### Requirement: Incremental usage reads
Measuring usage again SHALL read only what was appended and match a fresh parse.

#### Scenario: a grown, shrunk or replaced log
- WHEN `assignment_usage` is called after the log grew
- THEN it reads only the appended bytes from the offsets kept in `<worker>.usage.json` (0600) and returns what a fresh parse returns; a log that shrank or was replaced is re-read from the start, and the sidecar holds offsets and numbers only, never prompt, tool or file text

### Requirement: Wait usage rows
`wait` SHALL attach each bound worker's usage to its assignment rows without ever failing the wait.

#### Scenario: rows
- WHEN the lead calls `wait` and the batch names assignments of bound workers
- THEN each row carries `usage` for that assignment, a row whose worker has no measurement carries none, the store's byte budget reserves a worst-case usage dict per budgeted row, a failure while measuring never fails the wait, and tool descriptions and parameters are unchanged

### Requirement: Hook-only exceptions
The hook SHALL never be the only thing that refuses something, except where a native subagent cannot be told from its Lead.

#### Scenario: the one exception
- WHEN AGENTS.md and this spec state that the hook is never the only refuser
- THEN they name one exception: a native in-process subagent cannot be told from its Lead outside the host hook, so the hook alone denies such a subagent a runtime tool (the denial in "Subagent runtime limits"), and no other refusal is an exception

### Requirement: Live mixed-CLI acceptance
A live lead SHALL drive both CLIs end to end.

#### Scenario: opt-in live run
- WHEN a live lead (`AGENT_RUNTIME_LIVE=1`) starts both CLIs, exchanges peer messages, observes completion and a blocker, resumes one assignment and recovers after an MCP restart, and a worker's terminal is killed outside the runtime
- THEN reports remain attributed, replay works, no hc report text appears, one wait covers each uninterrupted work period, `stop` succeeds and the next `wait(all)` is `empty`
