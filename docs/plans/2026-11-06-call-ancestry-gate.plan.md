# Plan: `agent-runtime call` refuses LLM-descended callers

Slug: `call-ancestry-gate`. Living spec: `docs/specs/agent-runtime.md`. Status: APPROVED 2026-11-06.

## Why now

call-cli review 3 found two more ways around the Bash hook: shell expansions (B8) and a full-path `env`/`nohup`/`sudo` prefix (B9). Parsing shell text has no end; call-cli D6 deferred the threat model. In the orchestrate lean-loop design (agent-skills plan `orchestrate-lean-loop`), the only legitimate caller of `call` is the driver script in its own Herdr pane. Agents use the MCP tools. So the CLI can check where it runs from, not what was typed.

## Sources

| Source | What it settles |
|---|---|
| Design session with Felippe, 2026-11-06 (D10) | Structural check in the CLI; the hook stays as early feedback |
| `docs/plans/2026-11-06-call-cli.plan.md` D6; `~/.local/state/orchestrate/call-cli/T1r2/review.md` B8, B9, N11 | The forms that get past the hook |
| `~/.local/state/orchestrate/call-cli/deferred.md` | Open Bash-gate question |
| README `agent-runtime call` paragraph; `server/agent_runtime.py` `cmd_call` | Exit 2 for usage, outside hc, and generation conflict |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `call` walks its process ancestors and refuses (exit 2, nothing dispatched) if any is a `claude` or `codex` executable | Harden the hook parser further; accept B8/B9 as a guardrail | Covers every shell form, because nothing is parsed | Felippe |
| D2 | The walk stops at the hc server process: a pane's processes start below it, so a driver pane passes and a worker's Bash (claude → shell → call) does not | Walk up to pid 1 | Restarting `hc server` from a Claude Bash would make every pane look LLM-descended | Claude |
| D3 | Ancestry that can't be read fails closed (exit 2) | Fail open | It is a permission check | Claude |
| D4 | The PreToolUse hook keeps its current verdicts as early, readable feedback; the CLI check is the boundary | Remove the hook | The hook answers before a command runs and explains why | Felippe |
| D5 | `call --lead <id>` runs as a lead even when its hc terminal has a parent; the team is scoped by the `--lead` id. Only allowed once the ancestry check (D1) has passed | Role from the terminal's parent alone (today); starting the driver pane without a parent | A driver nested under the Lead's terminal is visible in Herdr but currently resolves as a worker (`caller has no matching active assignment`). The parent link is display-only (dispatch D6); D1 keeps workers from claiming the lead role | Felippe, 2026-11-06 (dogfood) |

## Scenarios

- **S1** WHEN `agent-runtime call <tool>` runs and an ancestor below the hc server is a `claude` or `codex` executable, THEN it exits 2 naming that ancestor (pid and name) and dispatches nothing.
- **S2** WHEN the walk reaches the hc server process with no such ancestor, THEN `call` runs as today.
- **S3** WHEN the process table can't be read or the walk ends without reaching the hc server or pid 1, THEN it exits 2 and dispatches nothing.
- **S4** WHEN a Claude Bash runs a B8 or B9 form (`$(printf call)`, `/usr/bin/env agent-runtime call wait`, `/usr/bin/nohup …`), THEN the CLI refuses it as in S1, whatever the hook decided.
- **S5** WHEN `call --lead L1 spawn …` runs in an hc terminal that has a parent and passes the ancestry check, THEN it resolves as lead L1: the spawn succeeds and the new worker belongs to L1's team, not to the parent terminal's team.
- **S6** WHEN `call` runs without `--lead` in an hc terminal that has a parent, THEN the role is resolved from the terminal as today (worker).

## Out of scope

- Other ways to reach the dispatch store from inside an LLM process (`python3 -c 'import agent_runtime'`, N11): a library import can't be gated by the CLI.
- Changing the hook's parser.

## Risks

- **Security:** narrows who can dispatch; S3 fails closed. Same-user processes can still reach the store through a library import (out of scope).
- **Performance:** one process-table read per `call`, a few ms.
- **Migration:** a person who ran `agent-runtime call` from inside a Claude session (e.g. `! agent-runtime call …`) is now refused; run it from a plain terminal or an hc pane. Documented in the README.
- **Rollback:** revert the PR; the hook still gates as before.

## Seams

- `cmd_call` with an injected process table (pid → ppid, executable), in `tests/test_agent_runtime.py`.
- One real check on this machine: the driver pane passes, a Claude Bash is refused (T2).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | Ancestry walk in `call` stopping at the hc server, fail-closed, README line | S1–S4 | none | server/agent_runtime.py, tests/test_agent_runtime.py, README.md | small | complex | agent | SKIPPED · Lead is merging the branch with the T3 work; the driver-owned attempt is superseded by a Lead-run branch |
| T2 | Verify on this machine: the hc server process is found and is not under a claude ancestor after a clean-env restart; a driver pane nested under the Lead passes and dispatches; a Claude Bash is refused | none | T1, T3 | | | | Felippe | TO-DO |
| T3 | `call --lead` forces the lead role in a parented terminal, after the ancestry check | S5, S6 | T1 | server/agent_runtime.py, server/dispatch_store.py, tests/test_agent_runtime.py, README.md | small | complex | agent | TO-DO |
