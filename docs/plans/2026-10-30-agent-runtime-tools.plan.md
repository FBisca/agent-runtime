# Plan: agent runtime as tools

Slug: `agent-runtime-tools`. Living spec: `docs/specs/agent-runtime.md`. Status: ARCHIVED 2026-10-30. The dispatch and hardening plans superseded its open tickets T7 and T8.

## Why now

Agents read the `agent-runtime` skill and contract text before every delegation, which costs tokens and leaves each step to the model's judgment. In a live Codex run the agent read the contract, then still chose its own commands. Felippe wants the contract fully deterministic and the skill gone: the verbs become tools that run the right runtime commands themselves. The same live runs found that an interrupted worker looks `idle` (Claude) or `working` (Codex) to hc, so a lead cannot tell finished from interrupted; that fix lands inside the new `status` tool.

## Sources

| Source | What it settles |
|---|---|
| `docs/specs/agent-runtime.md` (R1–R33) | Current resolver, contract verbs, hooks, status shape, guard rules this plan keeps or replaces |
| Session 2026-10-30, grilling Q1–Q8 (interruption) | D7–D14 below |
| Live experiment 2026-10-30 (exp-claude, exp-codex) | Claude marker `⎿  Interrupted · What should Claude do instead?` with hc `idle`; Codex marker `■ Conversation interrupted - use /feedback if something went wrong` with hc still `working`; `hc send "continue…"` resumes both; markers persist in history |
| Live Codex run 2026-10-30 (rollout …01a18c93…) | Codex without `--no-daemon` runs tools in the shared daemon without `HC_TERMINAL_ID`, so it resolved `runtime=native` |
| code.claude.com/docs/en/mcp | Plugins bundle MCP servers (`.mcp.json`, `${CLAUDE_PLUGIN_ROOT}`); tool search defers tool definitions; plugin tools are `mcp__plugin_<plugin>_<server>__<tool>` |
| learn.chatgpt.com/docs/extend/mcp.md (Codex) | `[mcp_servers.<name>]` with `command`, `args`, `env`, `env_vars` (allow and forward), `default_tools_approval_mode`, per-tool `approval_mode`; no deferred loading documented |
| codex.danielvaughan.com plugin posts | Codex plugins exist (`.codex-plugin/plugin.json`) but hook auto-merge is unreliable |
| `ps` of Codex processes | Codex's MCP servers run under the app-server daemon unless `--no-daemon` |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | A stdio MCP server `agent-runtime` (Python stdlib, JSON-RPC) exposes tools `spawn, message, resume, wait, status, list, collect, stop, report`; each resolves the runtime and runs the command itself | Keep the skill plus contract text | Deterministic execution; no contract text in context | Felippe 2026-10-30 |
| D2 | Remove the `agent-runtime` skill (SKILL.md, references/contract.md, adapters text) and the SessionStart pointer; tool descriptions are the whole contract (≤400 words total) | Keep a slim skill | "get rid of skills"; Claude tool search defers definitions; Codex pays the descriptions each session, so keep them short | Felippe 2026-10-30 |
| D3 | Gap rule moves into tool errors: an unsupported request returns `gap: add <verb/option> → <runtime command>; ask the user` | Gap rule text in context | Shown only when it applies | Session |
| D4 | Claude side ships as a plugin in dotfiles (`agents/plugins/agent-runtime/`: `.claude-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json`, `server/`) through a local marketplace in dotfiles; the hooks leave `settings.json` | `claude mcp add --scope user` | One install bundles server and hooks | Felippe 2026-10-30 |
| D5 | Codex: `[mcp_servers.agent-runtime]` in tracked `config.toml` with `env_vars` forwarding `HC_TERMINAL_ID, HC_SCOPE, HC_TERMINAL_SOCKET, HERDR_PANE_ID, HERDR_ENV, HERDR_SOCKET_PATH, HERDR_SESSION`; spawn/message/resume/stop/report `approval_mode = "approve"`; hooks stay inline | Codex plugin | Codex hook auto-merge unreliable; config is already tracked | Session, Codex docs |
| D6 | `codex()` zsh function adds `--no-daemon` when `HC_TERMINAL_ID` is set | Rely on users passing the flag | Without it Codex's tools and MCP server run in the daemon without hc identity | Live Codex run |
| D7 | New state `interrupted`; `next[0]` = `resume` with "continue the task where you stopped" | Keep `idle` with an extra next row | A lead reading `state` must see it | Grilling Q1 |
| D8 | `status` reads the screen when hc says `idle`/`done` (any CLI) or `working` (Codex); a failed read keeps the plain state | Read only on idle/done | Codex stays `working` after Esc while a background command runs | Grilling Q2, live experiment |
| D9 | Only the block after the last non-empty prompt line (`❯ …`/`› …`) counts | Whole screen | Markers persist after later turns | Grilling Q3 |
| D10 | Markers live in a per-CLI data table with one test fixture per marker from today's screens | Hard-coded | Wording changes between CLI versions | Grilling Q4 |
| D11 | Resume once without asking; with ≥2 markers in the worker's history, `next[0]` asks the user (gap row); a lead that interrupted the worker itself does not auto-resume | Always ask; unlimited | Stateless repeat count read from the screen | Grilling Q5 |
| D12 | `resume` and `message` never pass `--force` | Allow force | It would type into a worker's permission dialog | Grilling Q6 |
| D13 | `native.foreground.argv`: arguments with whitespace or longer than 40 characters become `<task>` | Keep full argv | Token waste on every status | Grilling Q7 |
| D14 | The 600-word contract cap becomes a 400-word cap on all tool descriptions together | Keep the old cap | The contract text no longer exists | Grilling Q8 superseded by D2 |
| D15 | PreToolUse(Bash) denies raw `hc run, send, keys, kill, interrupt, rm` for every agent ("use the agent-runtime tools"); reads stay allowed. PreToolUse on the server's mutating tools denies subagents (`agent_id`) | Keep shell parsing per role | Tools become the only route; `agent_id` on tool calls is reliable | Session |
| D16 | `spawn(kind=lookup)` returns an error pointing to the CLI's own subagent tool | Server launches subagents | Subagents are native to each CLI | D15 of prior plan |
| D17 | `~/bin/agent-runtime` CLI stays as a thin debug wrapper over the same library | Delete it | Humans and tests need a shell entry | Session |
| D18 | Herdmates support stays minimal: worker-kind tools under herdmates/native return a gap error naming the native tool | Full herdmates adapter | Agent teams are off (prior D19) | Session |
| D20 | `spawn` starts Claude workers with `--permission-mode auto` | Inherit the agent file's `default` mode | Workers otherwise stop on permission dialogs no tool can answer | T2–T5 review |
| D21 | Leads' raw `hc keys` and `hc interrupt` get PreToolUse `ask` (the user approves each); subagents stay denied | Deny for everyone; a keys tool | A lead must be able to clear a worker's trust dialog, with a person in the loop | T2–T5 review |
| D22 | Workers report with the `report` tool; the spawn report line says so and implementer definitions list the tool | `hc send <caller>` in the line | Raw `hc send` is denied for every agent (D15) | T2–T5 review |
| D19 | One branch `feat/agent-runtime-tools`, one PR, each ticket a gated commit | PR per ticket | Prior D14 | Session |

## Scenarios

Server and tools
- **S1** WHEN the server starts on stdio, THEN `tools/list` returns exactly `spawn, message, resume, wait, status, list, collect, stop, report` and their descriptions total ≤400 words.
- **S2** WHEN `spawn(kind=worker, cli, name, task, cwd)` runs under hc, THEN it runs `hc run --bg --name <name> --cwd <cwd>` with `claude --agent implementer` or `codex --no-daemon` plus the Codex implementer instructions, puts the task and a report line in the first message, and returns the worker's address and parent.
- **S3** WHEN `spawn(kind=lookup)` is called, THEN it returns an error naming the CLI's own subagent tool.
- **S4** WHEN `message` or `resume` runs under hc, THEN it runs `hc send` (`resume` adds `--wait`) and never `--force`.
- **S5** WHEN `wait(target)` runs under hc with `any`, `all` or a name, THEN it runs `hc wait` accordingly and returns normalized status for the workers it names.
- **S6** WHEN `collect(name)` runs, THEN it returns the worker's screen text after its last prompt line, bounded to 200 lines.
- **S7** WHEN `stop(name)` targets a terminal that is not the caller's child, THEN it refuses with a reason.
- **S8** WHEN `report(text)` is called by an hc worker, THEN it runs `hc send <parent>`; called by a lead, THEN it returns an error.
- **S9** WHEN a worker-kind tool runs under herdmates or native, THEN it returns a gap error naming the runtime's own tool.
- **S10** WHEN a request has no supported mapping, THEN the error reads `gap: add <verb/option> → <runtime command>; ask the user`.

Status and interruption
- **S11** WHEN hc reports `idle` or `done` (any CLI) or `working` (Codex) and the block after the last prompt line holds that CLI's interrupt marker, THEN `status` returns `state: interrupted` with `next[0]` = `resume` "continue the task where you stopped".
- **S12** WHEN the worker's history holds two or more interrupt markers since its task began, THEN `next[0]` is an ask-the-user gap row instead of `resume`.
- **S13** WHEN a marker appears only before the last prompt line, THEN `status` does not report `interrupted`.
- **S14** WHEN the screen read fails, THEN `status` keeps hc's plain state.
- **S15** WHEN `status` or `list` returns `native`, THEN `foreground.argv` arguments with whitespace or over 40 characters read `<task>`.

Hooks and wiring
- **S16** WHEN a subagent (payload with `agent_id`) calls `spawn`, `message`, `resume`, `stop` or `report`, THEN PreToolUse denies it.
- **S17** WHEN any agent's Bash runs `hc run`, `send`, `keys`, `kill`, `interrupt` or `rm`, THEN PreToolUse denies it with "use the agent-runtime tools"; `hc get`, `ls`, `read`, `wait` stay allowed.
- **S18** WHEN Claude Code loads the dotfiles plugin, THEN its manifest registers the MCP server and the hooks (wiring test of `plugin.json`, `.mcp.json`, `hooks/hooks.json`), and `settings.json` no longer carries the agent-runtime hooks.
- **S19** WHEN Codex reads `config.toml`, THEN `[mcp_servers.agent-runtime]` exists with the D5 `env_vars` and approval modes, and the Bash hook is wired.
- **S20** WHEN `codex` runs in a shell with `HC_TERMINAL_ID` set, THEN it starts with `--no-daemon`; without it, unchanged.
- **S21** WHEN `orchestrate` SKILL.md and both implementer files are searched, THEN they point to the `agent-runtime` tools and contain no `~/bin/agent-runtime contract` or raw `hc run/send` instruction; the `agent-runtime` skill directory is gone.

Live
- **S22** WHEN the server runs in a Claude session in Herdr.app, THEN `status` of the caller resolves `runtime=hc role=lead`.
- **S23** WHEN the server runs in a Codex `--no-daemon` session in Herdr.app, THEN it resolves the same way.
- **S24** WHEN a lead spawns a Claude and a Codex worker through the tools, interrupts one with Esc, and calls `status`, THEN it sees `interrupted`, resumes it with `resume`, and both reports arrive.

## Out of scope

- The biweekly tools audit (still deferred).
- Codex plugin packaging.
- A full Herdmates adapter.
- hc templates.

## Risks

- **Security:** mutating tools refuse subagents by `agent_id` (S16); raw `hc` mutations are denied for every agent (S17), so the tools are the only route. Codex tool calls carry no subagent marker and the mutating tools are pre-approved (`approval_mode = "approve"`, inferred from the Codex docs' example), so a Codex subagent could call them; misuse only misroutes reports, since raw `hc keys`/`kill` stay denied.
- **Performance:** one `hc read` per idle/done/Codex-working status and per child in `list`, each with a 2 s timeout; tool descriptions ≤400 words.
- **Migration:** the skill, contract text and SessionStart pointer are removed in the same branch that adds the tools; `~/bin/agent-runtime` stays for debugging; the living spec is rewritten at archive.
- **Rollback:** disable the plugin and the Codex `[mcp_servers.agent-runtime]` entry, revert the branch; the zsh wrapper only adds a flag.

## Seams

- The MCP server driven over stdio JSON-RPC with a fake `hc` on PATH (unittest).
- The status library fed recorded screens (fixtures from the live experiment).
- Hook scripts fed JSON on stdin.
- Wiring tests for the plugin manifest, `.mcp.json`, `hooks.json`, `config.toml` and `aliases.zsh`.
- Live Herdr.app session for S22–S24.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Plugin skeleton and local marketplace; stdio MCP server listing the nine tools; library moved from the skill into the plugin; `~/bin/agent-runtime` points at it | S1, S18 | none | agents/plugins/agent-runtime/, agents/.claude-plugin/, bin/bin/agent-runtime, agents/.agents/skills/agent-runtime/, agents/.agents/skills/orchestrate/scripts/, agents/.agents/skills/orchestrate/tests/, agents/.stow-local-ignore | agent | COMPLETE (7c7960a…0f1b0e8; converge PASS; review blocking fixed) |
| T2 | hc tool implementations: spawn, message, resume, wait, collect, stop, report; herdmates/native gap errors | S2–S10 | T1 | agents/plugins/agent-runtime/ | agent | COMPLETE (converge PASS; review blocking fixed and re-checked) |
| T3 | `status`/`list`: interrupted detection with marker table and fixtures, repeat rule, read-failure fallback, argv trimming | S11–S15 | T1 | agents/plugins/agent-runtime/ | agent | COMPLETE (converge PASS; review blocking fixed and re-checked) |
| T4 | Hooks in the plugin: Bash raw-hc deny for all agents, subagent deny on mutating tools; remove SessionStart pointer and old hooks from settings.json | S16, S17, S18 | T1 | agents/plugins/agent-runtime/, agents/.claude/settings.json | agent | COMPLETE (converge PASS; review blocking fixed and re-checked) |
| T5 | Codex: `[mcp_servers.agent-runtime]` with env_vars and approvals, Bash hook update; `codex()` wrapper in aliases.zsh | S19, S20 | T1 | agents/.codex/config.toml, zsh/.zsh/aliases.zsh, agents/plugins/agent-runtime/ | agent | COMPLETE (converge PASS; review blocking fixed and re-checked) |
| T6 | Consumers: orchestrate and implementer point to the tools; delete the agent-runtime skill and the CLI's contract/help; README | S21 | T2, T3, T4 | agents/.agents/skills/orchestrate/, agents/.agents/skills/agent-runtime/, agents/plugins/agent-runtime/server/agent_runtime.py, agents/plugins/agent-runtime/tests/, agents/.claude/agents/implementer.md, agents/.codex/agents/implementer.toml, README.md | agent | COMPLETE (1160e2a; converge PASS; text and deletions spot-checked by the orchestrator) |
| T7 | Merge the PR, pull the live checkout, install the plugin from the dotfiles marketplace, restart Claude and Codex, approve the Codex hook and MCP server | none | T2–T6 | none | Felippe | SUPERSEDED (dispatch and hardening plans) |
| T8 | Live run: S22–S24 with Claude and Codex workers through the tools; check Codex honours the hook's `ask` | S22, S23, S24 | T7 | agents/plugins/agent-runtime/ | agent | SUPERSEDED (dispatch and hardening plans) |
