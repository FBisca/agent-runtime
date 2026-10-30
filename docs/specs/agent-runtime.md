# Agent runtime
Paths: agents/.agents/skills/agent-runtime/, agents/plugins/agent-runtime/, bin/bin/agent-runtime

The agent runtime contract gives skills one set of delegation verbs (spawn, message, resume, report, wait, collect, stop, status, list) that per-runtime adapters (hc, herdmates, native) implement. `agent-runtime resolve` picks the runtime deterministically: `HERDR_PANE_ID` (Claude to herdmates, Codex to native) beats `HC_TERMINAL_ID` (hc, role from `hc get`), which beats native.

Startup hooks inject a one-line pointer in Claude and Codex sessions; subagents get a no-runtime-commands notice; a PreToolUse guard restricts subagents and hc workers. The full contract loads via `agent-runtime contract` only when delegating.

## Requirements

- **R1** WHEN `HERDR_PANE_ID` is set and `--cli claude`, THEN `agent-runtime resolve` prints `runtime=herdmates`. (from agent-runtime/S1)
- **R2** WHEN `HERDR_PANE_ID` is set and `--cli codex`, THEN it prints `runtime=native`. (from agent-runtime/S2)
- **R3** WHEN only `HC_TERMINAL_ID` is set and `hc get` reports `parent: null`, THEN it prints `runtime=hc role=lead`. (from agent-runtime/S3)
- **R4** WHEN only `HC_TERMINAL_ID` is set and `hc get` reports `parent: tX`, THEN it prints `runtime=hc role=worker parent=tX`. (from agent-runtime/S4)
- **R5** WHEN neither variable is set, THEN it prints `runtime=native role=lead`. (from agent-runtime/S5)
- **R6** WHEN both variables are set, THEN `HERDR_PANE_ID` wins. (from agent-runtime/S6)
- **R7** WHEN `hc get` fails or exceeds 2 s, THEN it prints `runtime=hc role=unknown` and exits 0. (from agent-runtime/S7)
- **R8** WHEN `agent-runtime contract` runs, THEN it prints the verbs, the gap rule and only the resolved runtime's adapter. (from agent-runtime/S8)
- **R9** WHEN `agent-runtime help` runs, THEN it lists the runtime's capabilities, each marked in-contract yes or no. (from agent-runtime/S9)
- **R10** WHEN a capability is marked not in contract, THEN the printed gap rule says: propose the change in chat in one line, ask before the raw command. (from agent-runtime/S10)
- **R11** WHEN `agent-runtime status <name>` runs under hc, THEN it prints JSON with `name, runtime, role, parent, state, turn, native, next` and `next` is non-empty. (from agent-runtime/S11)
- **R12** WHEN hc reports working, idle, done, blocked, exited/finished or anything else, THEN `state` is working, idle, done, blocked, exited or unknown, and a blocked worker's `next` includes a raw `hc keys` row marked as a gap. (from agent-runtime/S12)
- **R13** WHEN `status` runs under herdmates, THEN it maps `herdr pane get` output (the logic moved from `orchestrate.py herdr`) and the two moved tests pass. (from agent-runtime/S13)
- **R14** WHEN `status` runs under native, THEN `state` is unknown and `next` points to the return value or SendMessage. (from agent-runtime/S14)
- **R15** WHEN `agent-runtime list` runs under hc, THEN it returns one status per child of the caller. (from agent-runtime/S15)
- **R16** WHEN a Claude session starts, THEN its context contains `agent-runtime: runtime=… role=… parent=… · run agent-runtime contract before delegating`. (from agent-runtime/S16)
- **R17** WHEN a Codex session starts after the hook is trusted, THEN its context contains the same line. (from agent-runtime/S17)
- **R18** WHEN a Claude subagent or in-process teammate starts, THEN its context says it is a subagent and must not run runtime commands. (from agent-runtime/S18)
- **R19** WHEN a subagent's Bash call runs `hc run`, `hc send` or `hc kill`, THEN the PreToolUse hook denies it with a reason. (from agent-runtime/S19)
- **R20** WHEN a Bash call inside an agent session runs `herdmates teammux-launch`, THEN it is denied. (from agent-runtime/S20)
- **R21** WHEN a lead (not a subagent) runs `hc send`, THEN it is allowed. (from agent-runtime/S21)
- **R22** WHEN the bodies of orchestrate `SKILL.md`, `implementer.md` and `implementer.toml` are searched, THEN `hc`, `herdmates`, `herdr` appear only in a pointer to `agent-runtime`. (from agent-runtime/S22)
- **R23** WHEN `orchestrate.py inspect` runs, THEN it includes the `agent-runtime resolve` result, the `herdr` subcommand is gone, and the other 11 orchestrate tests pass. (from agent-runtime/S23)
- **R24** WHEN the lead spawns a Claude implementer through the hc adapter, THEN the worker nests under the lead, does the task and reports with `hc send` to the lead. (live-verified 2026-10-30, plan T8) (from agent-runtime/S24)
- **R25** WHEN the lead spawns a Codex worker through the hc adapter, THEN it runs with `--no-daemon`, receives the task and reports to the lead. (live-verified 2026-10-30, plan T8) (from agent-runtime/S25)
- **R26** WHEN S24 and S25 run, THEN the Herdr.app sidebar shows both workers nested under the lead. (live-verified 2026-10-30, plan T8) (from agent-runtime/S26)
- **R27** WHEN an in-process teammate under hc runs `hc send <lead> x`, THEN the PreToolUse guard denies it (its call carries `agent_id`). Satisfied by teams off (CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=0). (live-verified 2026-10-30, plan T8) (from agent-runtime/S27)
- **R28** WHEN an hc worker runs `hc keys`, `hc interrupt`, `hc kill` or `hc send --force` aimed at its parent, THEN the PreToolUse guard denies it with a reason. (from agent-runtime/S28)
- **R29** WHEN an hc worker runs `hc send <parent> "<result>"`, or any hc command aimed at its own child or a sibling, THEN it is allowed. (from agent-runtime/S29)
- **R30** WHEN `agent-runtime contract` runs for any runtime, THEN `spawn` lists kinds `lookup` and `worker`, `lookup` maps to that CLI's subagent tool, and the routing rule is printed. (from agent-runtime/S30)
- **R31** WHEN a Codex hc worker's Bash call runs `hc keys`, `hc interrupt`, `hc kill` or `hc send --force` aimed at its parent, THEN Codex's PreToolUse hook denies it. (from agent-runtime/S31)
- **R32** WHEN `agent-runtime` runs inside a Codex session without `--cli`, THEN it resolves as `cli=codex` (so Codex in the herdr TUI gets `runtime=native`). (from agent-runtime/S32)
- **R33** WHEN `agent-runtime contract` runs for any runtime, THEN it lists `resume(name, task)` with that runtime's resume operation, and `help` marks resume in-contract. (from agent-runtime/S33)
