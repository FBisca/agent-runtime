# Plan: spawn chooses the worker's model and agent

Slug: `spawn-model`. Living spec: `docs/specs/agent-runtime.md`. Status: ARCHIVED 2026-11-04.

## Why now

`spawn` cannot pick a model ([#17](https://github.com/FBisca/agent-runtime/issues/17)) or an agent ([#19](https://github.com/FBisca/agent-runtime/issues/19)). A lead that wants a cheap worker (Haiku for a mechanical task or a roleplay) or a non-coding persona falls back to the CLI's native subagent tool and loses the runtime's message/wait flow.

## Sources

| Source | What it settles |
|---|---|
| [Issue #17](https://github.com/FBisca/agent-runtime/issues/17) | Optional `model` on `spawn`, passed as `--model <model>` to `claude` and `codex`; unset keeps today's behaviour; `agent` arg mentioned as related |
| [Issue #19](https://github.com/FBisca/agent-runtime/issues/19) | Optional `agent`: Claude `--agent <agent>`; Codex reads `<agent>.toml` instructions, name must be path-safe; personas without `report` |
| `docs/specs/agent-runtime.md` R25, R56/R99, R72/R73, R96 | Codex keeps `--no-daemon`; tool descriptions ≤400 words; invalid input starts no terminal; `--no-notify` precedes the program |
| `docs/plans/2026-10-30-agent-runtime.plan.md` D11, D18 | Claude runs `--agent implementer`; Codex gets implementer.toml instructions in the first message; resume is `hc send` into the live terminal, never a new program |
| `docs/plans/2026-10-30-agent-runtime-tools.plan.md` D20, D13 | Claude keeps `--permission-mode auto`; status/list show trimmed argv |
| `docs/plans/2026-10-30-agent-runtime-hardening.plan.md` D4, D8 | Validate before `hc run`; optional `task_id` is the pattern for an optional spawn string |
| `server/hc_tools.py:247-256` | Program argv; the prompt must stay the last element (assignment suffix is appended to `program[-1]`) |
| `server/hc_tools.py:134-148` `codex_instructions()` | Missing/unreadable toml already raises `ToolError` before `hc run` |
| Live check, 2026-11-04 | `claude -p --agent implementer --model haiku` ran on `claude-haiku-5-5`: CLI `--model` overrides the agent file's `model: sonnet` |
| Live check, 2026-11-04 | `claude --agent no-such-agent` exits at once: "not found. Available agents: claude, Explore, general-purpose, implementer, Plan, scene-reviewer, statusline-setup" (mixed case names) |
| `codex --help` (0.166.0) | `-m, --model <MODEL>` exists on the interactive top-level form spawn uses; no `--agent` |
| Grilling, 2026-11-04 | Round 1 (Q1–Q5, model) and round 2 (Q1–Q5, agent) |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `model` first (T1), `agent` as a second ticket (T2) in this plan | `agent` in T1; separate plan | T1 shipped independently; user pulled #19 in after T1 | Grilling Q1; user, 2026-11-04 |
| D2 | `model` passed as `--model <m>`, inserted after the fixed flags and before the trailing prompt, for both CLIs | Codex `-c model=…`; `-m` | Issue asks for `--model`; prompt must stay last | Issue #17, `hc_tools.py:256` |
| D3 | Unset `model` → argv byte-identical to today | Default a model | "Unset keeps today's behaviour" | Issue #17 |
| D4 | Absent OK; non-string or blank → error; leading `-` → error; no allowlist; checked before `hc run` | Blank → default (`cli` precedent); allowlist | `task_id` precedent; leading `-` would parse as a flag; model names churn, CLI is the authority | Grilling Q2, hardening D4/D8 |
| D5 | Keep the 400-word cap; short property descriptions, trim existing text if needed | Raise the cap | Budget is a spec requirement | Grilling Q3, R56/R99 |
| D6 | Codex never applies a toml `model` (implementer or other agent); runs config.toml default unless `model` given | Apply toml model now | Out of scope; tracked in #20 | Grilling Q4 (both rounds) |
| D7 | Unknown model or unknown Claude agent surfaces after start (`exited`/`unreported` in `wait`); no pre-spawn probe | Probe the CLI first | Costly, CLI-specific; existing liveness flow already reports it | Grilling Q5; round 2 Q2 |
| D8 | No persistence of `model`/`agent`; resume unchanged | Store per worker | Resume reuses the live process | Plan D18, `hc_tools.py:364-394` |
| D9 | Reject `model` with surrounding or embedded whitespace/control characters (character-class check) | Pass verbatim; strip silently | Padded or control-char names only fail after start; still not an allowlist, so D4 holds | Ticket review, 2026-11-04 |
| D10 | Optional `agent`, default `implementer`; unset → argv byte-identical to today | Required; no default | Keeps today's behaviour | Round 2 Q1 |
| D11 | `agent` must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; Claude additionally allows `:` (plugin agents); Codex rejects anything else (name becomes `~/.codex/agents/<agent>.toml`) | Lowercase-only (`worker_name`); any string | Built-ins are mixed case (`Explore`, `Plan`); Codex path must not escape the agents dir | Round 2 Q1, live check |
| D12 | Codex: missing/unreadable `<agent>.toml` errors before `hc run` (existing read). Claude: no pre-start existence check | File check for Claude | Claude agents also come from project dirs, plugins and built-ins | Round 2 Q2 |
| D13 | Agents without the `report` tool are allowed; README and the `agent` property description say the agent needs `report` | Parse tool lists and reject | `tools:` absent means all tools; built-ins/plugins have no file to parse | Round 2 Q3 |
| D14 | T2 branches from main after #21 (T1) merges | Stack on T1 branch | Same lines; stacked branches break under squash merge | Round 2 Q5 |

## Scenarios

- **S1** WHEN `spawn` runs with `cli: "claude"` and `model: "haiku"`, THEN the program argv is `claude --agent implementer --permission-mode auto --model haiku <prompt>`, prompt last.
- **S2** WHEN `spawn` runs with `cli: "codex"` and `model: "gpt-5.5"`, THEN the program argv is `codex --no-daemon --model gpt-5.5 <prompt>`, prompt last, no permission-mode.
- **S3** WHEN `spawn` runs without `model` (either CLI), THEN the program argv is identical to today's.
- **S4** WHEN `model` is blank, whitespace-only or not a string, THEN `spawn` returns an error and no `hc run` happens.
- **S4a** WHEN `model` has leading/trailing whitespace or any whitespace/control character inside, THEN `spawn` returns an error and no `hc run` happens.
- **S5** WHEN `model` starts with `-`, THEN `spawn` returns an error and no `hc run` happens.
- **S6** WHEN a client lists tools, THEN `spawn`'s schema has an optional string `model` property (not required) and all tool descriptions total ≤400 words.
- **S7** WHEN `spawn` runs with `cli: "claude"` and `agent: "scene-reviewer"` (with or without `model`), THEN the program argv is `claude --agent scene-reviewer --permission-mode auto [--model <m>] <prompt>`, prompt last.
- **S8** WHEN `spawn` runs with `cli: "codex"` and `agent: "reviewer"`, THEN the prompt starts with `developer_instructions` from `~/.codex/agents/reviewer.toml` (not implementer's), followed by the task.
- **S9** WHEN `spawn` runs without `agent` (either CLI), THEN the program argv is identical to today's (`implementer`).
- **S10** WHEN `agent` is not a string, blank, or fails `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$` (e.g. `-x`, `../x`, `a/b`, `a.b`, 65 characters), THEN `spawn` returns an error and no `hc run` happens, for either CLI.
- **S10a** WHEN `agent` contains `:` (e.g. `plugin:reviewer`), THEN with `cli: "claude"` the argv carries `--agent plugin:reviewer`, and with `cli: "codex"` `spawn` returns an error and no `hc run` happens.
- **S11** WHEN `cli: "codex"` and `~/.codex/agents/<agent>.toml` is missing or lacks `developer_instructions`, THEN `spawn` returns an error naming that path and no `hc run` happens.
- **S12** WHEN a client lists tools, THEN `spawn`'s schema has an optional string `agent` property whose description says the agent needs the `report` tool, and all tool descriptions total ≤400 words.

## Out of scope

- Codex applying a toml `model`/`model_reasoning_effort` (#20).
- `model`/`agent` on `resume`, or as fields in status/list records (argv already shows them via `native.foreground.argv`).
- Model allowlists, pre-spawn model probing, Claude agent existence checks, parsing agent tool lists.

## Risks

- **Security:** argv list, no shell (`hc_tools.py:74-80`); leading-`-` rejection blocks flag injection (S5, S10); Codex agent name is charset-restricted so the toml path can't escape `~/.codex/agents/` (S10, S10a). Covered.
- **Performance:** n/a — one extra argv pair / one file read.
- **Migration:** n/a — no stored state; unset paths unchanged (S3, S9).
- **Rollback:** revert the PR; callers that pass `model`/`agent` fall back to them being ignored, as before (unknown args are not schema-rejected).

## Seams

- `tests/test_tools.py` `SpawnTests`: real MCP server over stdio with fake `hc` logging argv, fake `HOME` with `.codex/agents/*.toml` (S1–S3, S7–S9, S10a, S11).
- `tests/test_spawn_cleanup.py`: invalid input → no `run` (S4, S5, S10, S10a, S11).
- `tests/test_mcp_server.py`: tools/list schema and 400-word budget (S6, S12).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Optional `model` on `spawn`: schema, validation, `--model` in claude/codex argv, README note | S1–S6 | none | server/hc_tools.py, server/mcp_server.py, tests/test_tools.py, tests/test_spawn_cleanup.py, tests/test_mcp_server.py, README.md | agent | COMPLETE: PR #21 (83d1659, b6452ac); converge PASS; review no blocking; `python3 -B -m unittest discover -s tests` 361 OK; CI PASS |
| T2 | Optional `agent` on `spawn`: schema, validation, Claude `--agent <agent>`, Codex `<agent>.toml` instructions, README note | S7–S12 | T1 | server/hc_tools.py, server/mcp_server.py, tests/test_tools.py, tests/test_spawn_cleanup.py, tests/test_mcp_server.py, README.md | agent | COMPLETE: PR #23 (fb3b6fe, 40ec522); converge PASS; review no blocking; `python3 -B -m unittest discover -s tests` 370 OK; CI ubuntu+shellcheck PASS, macOS pending at merge (merged on user instruction) |
