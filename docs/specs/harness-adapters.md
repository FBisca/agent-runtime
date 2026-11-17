# Harness adapters

A harness is the coding agent that owns an MCP session: Claude Code, Codex or Pi. The runtime contract (one set of delegation verbs, the dispatch store, the worker lifecycle) lives in [agent-runtime.md](agent-runtime.md); this capability covers how each harness is identified, what binds a session to a team, what the runtime lets that harness mutate, and the stop and wake rhythm each harness keeps.

The MCP server and the dispatch store are host-agnostic. A harness is identified by the process that started the server (its direct parent) and, for the session's generation, by the nearest harness ancestor in the process tree. A local stdio connection and process ancestry do not authenticate an arbitrary request, so a lead is bound to a scope (the Herdr socket), a terminal and a generation (the harness process's `pid:lstart`), and that binding is checked on every sensitive operation. An environment variable is a hint, never an identity. Claude Code and Codex are gated by their own PreToolUse hooks, which are defence in depth; Pi ships no such gate, so for a Pi session the server decides what the session may mutate, from the operator's policy file `$XDG_CONFIG_HOME/agent-runtime/hosts.json` (default `~/.config/agent-runtime/hosts.json`, override `AGENT_RUNTIME_HOSTS`), read on every mutating call:

```json
{"hosts": {"pi": {"mutations": true, "backends": ["claude-minimax", "codex"], "paths": ["/Users/you/developer"]}}}
```

## Requirements

### Requirement: Pi session identity
The runtime SHALL identify a Pi session as `cli=pi` from the process that started the server and SHALL give it the Pi process's `<pid>:<lstart>` as its generation, never substituting another harness.

#### Scenario: a Pi lead resolves as Pi
- WHEN a session runs under Pi, or claims Pi without a Pi parent
- THEN `agent-runtime resolve` reports `cli=pi`, so a failed identification is never downgraded to another harness

#### Scenario: the generation follows the Pi process
- WHEN the MCP server restarts under the same Pi process, or Pi restarts
- THEN the first keeps the same team and the second starts a new one, the old generation going stale

### Requirement: Unidentifiable harness refused
The runtime SHALL refuse a Pi session with no visible Pi process, and a session the environment claims for more than one harness, with an explicit error.

#### Scenario: no Pi process is visible
- WHEN a Pi session has no visible Pi process
- THEN it is refused instead of accepting a session id from the environment

#### Scenario: the claim is ambiguous
- WHEN the environment claims more than one harness and no harness process is visible
- THEN the session is refused as ambiguous

### Requirement: Claude and Codex identity unchanged
The runtime SHALL resolve a Claude Code or Codex session exactly as before the Pi harness existed.

#### Scenario: identity and fallback
- WHEN a Claude Code or Codex session is resolved
- THEN its identity is the nearest ancestor's `pid:lstart`, the session id is the fallback only when no harness ancestor is visible, and the host keeps its unrestricted behaviour

### Requirement: Pi lead needs the dispatch store
The runtime SHALL deny every call from a Pi session that runs without `AGENT_RUNTIME_DISPATCH=1`.

#### Scenario: no durable store
- WHEN a Pi session runs without `AGENT_RUNTIME_DISPATCH=1`
- THEN every call is denied, because without the durable store there is no identity, no attribution and no scope

### Requirement: Pi host policy
The runtime SHALL let the operator's host policy grant a Pi lead mutations, worker backends and directories and nothing else, and SHALL deny what the policy does not grant.

#### Scenario: no policy, no mutation
- WHEN a Pi lead calls a mutating tool and no policy authorises mutations, or the policy file is malformed
- THEN the call is denied (a malformed policy refuses the Pi host rather than trusting it) and no worker is started

#### Scenario: a backend the policy does not authorise
- WHEN a Pi lead spawns without naming a worker backend, or names one the policy does not authorise
- THEN the spawn is denied, no worker is started, and the runtime never picks a provider for it

#### Scenario: a directory the policy does not authorise
- WHEN a Pi lead spawns a worker outside the authorised directories (only the lead's own directory when the policy lists none)
- THEN the spawn is denied and no worker is started

#### Scenario: the policy grants no role
- WHEN the operator writes the policy
- THEN it can grant mutations, backends and directories only, never a role: a Pi session that resolves as a worker is accepted inside its own team with the narrow worker tool set

### Requirement: Pi lead team scope
The runtime SHALL deny a Pi lead any `status`, `collect`, `message`, `resume` or `stop` on a worker outside its own team.

#### Scenario: another lead's worker
- WHEN a Pi lead names a worker of another team
- THEN the call is denied

### Requirement: Pi lead delegation
The runtime SHALL run a worker a Pi lead spawns through the existing dispatch store, bound to the Pi lead's team and assignment id.

#### Scenario: a Claude or Codex worker reports to the Pi lead
- WHEN a Pi lead spawns a Claude or Codex worker and the worker reports
- THEN `wait(target=all)` returns exactly one result, whose recipient is the Pi lead's terminal

### Requirement: Pi lead delivery is exactly once
The runtime SHALL deliver a worker's report and start a worker once, across replays, duplicates, reconnects and cancelled waits.

#### Scenario: a replayed report
- WHEN a worker replays a report with the same `message_id`
- THEN it is delivered once

#### Scenario: a duplicate spawn
- WHEN a spawn names a live worker
- THEN it is refused and no second worker starts

#### Scenario: a reconnect or a cancelled wait
- WHEN the Pi lead's MCP connection drops and reconnects, or its wait is cancelled
- THEN it sees its own team and the undelivered report, the task is not dispatched twice, and the worker keeps running

#### Scenario: one report while another worker still works
- WHEN one worker reports while another still works
- THEN `wait(target=all)` returns that report and names the unfinished assignment

### Requirement: Pi worker tool set
The runtime SHALL accept a Pi worker's `report`, `message`, `wait`, `status`, `collect`, `list` and read-only `task list` inside its own team, and SHALL refuse it `spawn`, `stop` and `resume` and anything outside its team.

#### Scenario: a worker inside its team
- WHEN a Pi worker reports or reads its own team
- THEN the runtime accepts the call instead of refusing the session

#### Scenario: a worker beyond its remit
- WHEN a Pi worker calls `spawn`, `stop` or `resume`, creates a team task, or reaches a worker outside its team
- THEN the runtime refuses with a reason that names the host policy: a Pi worker starts no worker and ends no worker

### Requirement: Pi worker launch
The runtime SHALL launch a `pi` family worker interactively with the task last, the role and report protocol in the task, and the Pi provider variables kept.

#### Scenario: the Pi argv
- WHEN a spawn names the `pi` family
- THEN the runtime launches `pi --approve --model <model> --thinking <effort>` without `--print`, the task carrying the role's content and the report protocol, and refuses an effort Pi does not know before anything starts

#### Scenario: provider variables
- WHEN a spawn names the `pi` family
- THEN the worker keeps `MINIMAX_API_KEY` and the Pi provider variables, a claude or codex spawn carries neither, and the worker reports once to the lead's team

### Requirement: Stop guards agree across harnesses
The Claude and Codex Stop hooks and the Pi stop dialect SHALL refuse the same lead state with the same reason, the Pi answer being the neutral `{"block", "reason"}`.

#### Scenario: one state, one reason
- WHEN a lead has undelivered messages or unseen working assignments
- THEN each harness's stop guard blocks with the same reason

#### Scenario: the Pi dialect
- WHEN `agent-runtime-hook stop --cli pi --dialect json` runs
- THEN it prints `{"block": <bool>, "reason": <text>}`, and without the dialect the Claude and Codex `{"decision": "block"}` is unchanged

### Requirement: Pi lead stop continuation
The Pi extension SHALL turn a stop-guard block into one more provider request carrying the reason, and an allowed stop SHALL add nothing.

#### Scenario: open work with budget left
- WHEN a Pi lead's turn ends with open work and its stop-guard budget is unspent
- THEN the extension requests one more turn with the hook's reason; an allowed stop, or a hook that cannot answer, adds nothing

### Requirement: Pi worker stop continuation
The Pi extension SHALL ask a Pi worker whose turn ends with its assignment unreported for one more request naming both ways out.

#### Scenario: an unreported assignment
- WHEN a Pi worker's turn ends with its assignment unreported
- THEN the extension requests one more turn naming `report complete` and `report blocked`

### Requirement: Worker stop guard
The Stop hook SHALL block a Claude or Codex worker whose assignment is unreported, with the reason shape the lead's guard uses.

#### Scenario: an unreported assignment
- WHEN a Claude or Codex worker's turn ends with its assignment unreported, even while a wait of its own is parked
- THEN its Stop hook blocks, and the worker can still leave by reporting blocked

### Requirement: Undelivered reports block mutation
The runtime SHALL refuse a lead's `spawn`, `message`, `resume` and `stop` while reports for it are undelivered.

#### Scenario: undelivered reports
- WHEN a lead with undelivered reports calls a mutating verb
- THEN the runtime refuses, naming the count and `wait(all)` as the way out, and no worker starts; after `wait(all)` delivers, the verbs are allowed again

### Requirement: Pi wake push
The Pi extension SHALL push each wake the driver appends that the lead has not seen, with its text, once.

#### Scenario: an unseen wake
- WHEN the driver appends a wake the lead has not seen, including one written before the session started
- THEN the extension pushes it with its text, in the order booked; a wake already seen (even after a restart), an empty or crash-cut line, another run's wake, and any wake in a worker session push nothing

### Requirement: Pi long sleep refused
The runtime SHALL refuse a Pi shell command that sleeps 60 seconds or longer, naming `wait(all)`.

#### Scenario: a sleep
- WHEN a Pi session runs a shell command that sleeps 60 seconds or longer, in any shape of the command
- THEN the call is refused with a reason naming `wait(all)`; a shorter sleep, no sleep, or another tool is untouched

### Requirement: Harness rhythm capabilities
The runtime SHALL report a resolved harness's rhythm capabilities from one table in which every harness it can resolve has a row.

#### Scenario: the row of the resolved harness
- WHEN the runtime resolves a harness
- THEN it reports whether that harness can refuse a stop, park a wait, push a wake, and its request cap

### Requirement: Stop budget shared across hosts
A lead's spent stop-guard budget SHALL be spent for every host of one terminal.

#### Scenario: switching host
- WHEN a lead's stop-guard budget is spent under one host
- THEN another host sees the same spent budget, so switching host grants no fresh blocks
