# Plan: spawn's role text is optional and comes from where the caller says

Slug: `spawn-role`. Living spec: `docs/specs/agent-runtime.md`. Status: OPEN.

## Why now

`spawn` always forces the implementer role on every worker. `agent` (R137) can pick a *different*
role, but only among the tomls that already live in `~/.codex/agents/`, and a role file is
mandatory: a missing one is an error (R141). A lead whose worker needs no role policy — a
researcher, a summarizer, a worker whose whole policy is in the task — cannot say so, and a
worker whose policy lives in a plain markdown file the user already has cannot be pointed at it.

The role text also arrives in the wrong place for Codex: `codex_instructions()` pastes the toml's
`developer_instructions` in front of the task as prompt text. Claude gets a real subagent
(`--agent`); Codex gets a paraphrase of one. The tomls must stay in `~/.codex/agents/` either way —
Codex reads them itself when it is started outside the runtime, so the fix is additive: let the
runtime skip the role, or take it from a file the caller names.

## Sources

| Source | What it settles |
|---|---|
| `server/hc_tools.py:138` `codex_instructions()` | The Codex preamble is `developer_instructions` from `~/.codex/agents/<agent>.toml`; missing or blank is a `ToolError` before `hc run` |
| `server/hc_tools.py:250` `spawn_agent()` | `agent` defaults to `implementer`; the name is charset-restricted per CLI family |
| `server/hc_tools.py:339,363` | The runtime already injects the report instruction (`call the agent-runtime report tool with your result.`) and the `assignment_id`/`kind` suffix — independent of any role file |
| `dotfiles/agents/.codex/agents/implementer.toml` | The role file carries the Return-block shape, the `report(kind, assignment_id)` detail, the peer protocol and "a final message alone never reaches the parent" — the contract a roleless worker would lose |
| `~/.codex/agents` → `dotfiles/agents/.codex/agents` | The tomls are Codex's own multi-agent roles; Codex reads them when it runs outside the runtime, so the directory stays |
| `docs/specs/agent-runtime.md` R131, R137, R138, R139, R140, R141, R142, R149 | Unset `model`/`agent` keeps today's argv; `agent` validation; the tools/list schema and the 400-word budget |
| `codex --help` (0.166.0) | No `--agent`; the preamble has to be prompt text or config |
| `codex debug prompt-input` (0.166.0) | `-c model_instructions_file=…` **replaces** the base instructions ("You are Codex, an agent based on GPT-6…"), and `-c additional_developer_instructions=…` is parsed but ignored outside managed policy — neither is an additive lever, so the runtime's prepend stays |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `agent: "none"` is reserved: no role file, no preamble, no toml read | An empty role toml; a `no_role` flag | One word in the existing argument; `none` reads as "no role" | User, 2026-11-06 |
| D2 | `agent: "none"` for Claude drops `--agent` (Claude's default agent runs) | `--agent none` (Claude has no such agent and exits) | Claude has no "no subagent" switch | `claude --help` |
| D3 | The runtime injects the worker protocol when no role file supplies one | Leaving a roleless worker without the contract | The report channel is injected but the return shape and the peer protocol are not; a roleless worker that cannot report is worse than no roleless worker | `hc_tools.py:339,363` vs `implementer.toml` |
| D4 | The protocol is injected **only** when there is no role file | Always | Role files stay the single source for their own role; today's prompts stay byte-identical (R131, R138) | R138 |
| D5 | `instructions_file` (codex family only) sets the preamble from any file the caller names | Reading the file's `developer_instructions` from a toml elsewhere; inline `instructions` text | A role the user already has as markdown, without a second copy of it as a toml; inline text would land in argv as a rule, not a path | User, 2026-11-06 |
| D6 | The path must be absolute or `~/`, must exist, must be a regular file, must be non-blank; every failure is an error before `hc run` | Best-effort read; any relative path | Same rule as a profile's `settings` (R144, R146); a silent empty preamble would hide the mistake | R144, R146 |
| D7 | `agent` and `instructions_file` together are an error | `instructions_file` wins | One preamble per worker; the caller picks one source | — |
| D8 | `~/.codex/agents/*.toml` stay exactly as they are | Moving the roles into the runtime | Codex reads them itself when it runs outside the runtime (interactive `codex`, its own multi-agent spawn) | User, 2026-11-06 |
| D9 | `instructions_file` is rejected for the Claude family | `--append-system-prompt-file` | The flag exists in the 2.1.319 binary but is undocumented; Claude already has `--agent <name>` | `claude --help` |
| D10 | `instructions_file` is not a profile key yet | Profile-level `agent`/`instructions_file` | A profile names a CLI family and its environment; the role belongs to the call, not the profile | `profiles.py` |

## Scenarios

- **S1** WHEN `spawn` runs with `cli: "codex"` and `agent: "none"`, THEN the program argv is `codex --no-daemon [-p worker] <prompt>`, the prompt starts with the task, and no `~/.codex/agents/<agent>.toml` is read — a missing toml is not an error.
- **S2** WHEN `spawn` runs with `cli: "claude"` and `agent: "none"`, THEN the program argv is `claude --permission-mode auto <prompt>` — no `--agent` flag.
- **S3** WHEN `spawn` runs with `agent: "none"` (either CLI), THEN the prompt states the worker protocol: report with the agent-runtime report tool (`kind` = progress, complete, blocked or decision; `assignment_id`), a final message alone never reaches the parent, and the result block shape (STATUS, task id, files changed, checks, decisions, remaining work).
- **S4** WHEN `spawn` runs with `cli: "codex"` and `instructions_file: "<absolute or ~/ path>"`, THEN the prompt starts with the file's content, then the task, and no `~/.codex/agents` toml is read.
- **S5** WHEN `instructions_file` is not a string, is blank, is not an absolute or `~/` path, or names a missing file or a directory, THEN `spawn` returns an error naming the path and no `hc run` happens.
- **S6** WHEN `spawn` runs with `instructions_file` and `cli: "claude"`, THEN `spawn` returns an error and no `hc run` happens.
- **S7** WHEN `spawn` runs with both `agent` and `instructions_file`, THEN `spawn` returns an error and no `hc run` happens.
- **S8** WHEN a client lists tools, THEN `spawn`'s schema has an optional string `instructions_file` property, the `agent` description says `none` means no role, and all tool descriptions total ≤400 words.

## Out of scope

- Moving, renaming or editing `~/.codex/agents/*.toml` (D8).
- A role file that is not a Codex toml (the runtime reads `developer_instructions`; `instructions_file` covers plain text).
- `instructions_file` for the Claude family (D9).
- Profile-level role defaults (D10).
- Codex applying a toml `model`/`model_reasoning_effort` (#20).
- `agent`/`instructions_file` on `resume` (resume reuses the live process).

## Risks

- **Security:** `instructions_file` reads a path the lead names and puts its content in the worker's prompt. A lead can already read any file it can read; subagents cannot call `spawn` (the PreToolUse guard denies the mutating tools to subagents), so this adds no capability to them. The path must be absolute or `~/` so it cannot depend on the worker's cwd.
- **Prompt cost:** the protocol adds ~60 words to a roleless worker's first message only.
- **Drift:** the runtime's protocol and `implementer.toml`'s Communication section say the same thing in different words. The runtime's copy is the minimum a roleless worker needs; the toml keeps the fuller contract for Codex's own subagents.
- **Compatibility:** `agent: "none"` is a new reserved value; a `none.toml` in `~/.codex/agents/` would no longer be reachable. Unset `agent` keeps today's argv (R138).
- **Rollback:** revert the PR; a caller passing `agent: "none"` would fall back to looking for `none.toml` and fail loudly (R141) rather than silently.

## Seams

- `tests/test_tools.py` `SpawnTests`: the real MCP server over stdio with a fake `hc` logging argv and a fake `HOME` holding `.codex/agents/*.toml` (S1, S2, S3, S4, S8).
- `tests/test_spawn_cleanup.py`: invalid input → no `run`, no assignment (S5, S6, S7).
- `tests/test_mcp_server.py`: the tools/list schema and the 400-word budget (S8).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | `agent: "none"`, the runtime worker protocol, `instructions_file`, schema and docs | S1–S8 | none | server/hc_tools.py, server/mcp_server.py, tests/test_tools.py, tests/test_spawn_cleanup.py, tests/test_mcp_server.py, docs/specs/agent-runtime.md, README.md | agent | CODE DONE: red→green, `python3 -B -m unittest discover -s tests` 463 OK (4 skipped); not committed, not pushed |
