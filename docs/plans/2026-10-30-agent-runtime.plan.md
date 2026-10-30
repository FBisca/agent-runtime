# Plan: agent runtime contract

Slug: `agent-runtime`. Living spec: `docs/specs/agent-runtime.md`. Status: APPROVED 2026-10-30.

## Why now

Herdr.app (`hc`) nests child agents under their lead, mixes Claude and Codex workers, and keeps them visible. Claude teammates cannot do this here: split panes are unsupported in Ghostty, so teammates run in-process and never reach Herdr. Today `orchestrate` and `implementer` hard-code Herdmates and the `herdr` TUI, so agents in Herdr.app get the wrong instructions. A contract with per-runtime adapters, picked deterministically from the environment, lets the skills stay runtime-agnostic.

## Sources

| Source | What it settles |
|---|---|
| Session 2026-10-30 (this conversation), grilling Q1–Q14 | All decisions below marked "Q<n>" |
| Live test 2026-10-30: `hc run --bg` worker-a/worker-b, `cx-test` | hc children nest under the caller (`parent`), `hc send` works child→child with `<hc from=…>` tags, `wait --children all` returns, Codex status tracked |
| `hc --skill`, `hc schema` (hc 0.1.14) | Verbs, report pattern `hc send $HC_TERMINAL_ID`, Codex needs `--no-daemon`, first task belongs in `run`, `first_message.sent` |
| `hc get $HC_TERMINAL_ID` | `parent` null for a lead, set for a child; children get their own `HC_TERMINAL_ID`, no parent env var |
| code.claude.com/docs/en/agent-teams | Split panes unsupported in Ghostty; teammates lack nesting, Codex |
| code.claude.com/docs/en/hooks | SessionStart stdout → context; SubagentStart `additionalContext` fires for subagents and in-process teammates; PreToolUse deny |
| learn.chatgpt.com/docs/hooks (Codex) | SessionStart stdout → developer context; inline `[hooks]` in `config.toml`; changed hooks need `/hooks` trust |
| `agents/.agents/skills/orchestrate/` (SKILL.md, references/runtimes.md, scripts/orchestrate.py, tests/) | Runtime-specific text and the `herdr` subcommand to move; test command `python3 -B -m unittest discover -s tests` |
| `agents/.claude/agents/implementer.md`, `agents/.codex/agents/implementer.toml` | Herdmates/Herdr lines to replace |
| `install.sh`, `.stowrc`, `bin/bin/` | `bin/bin/*` lands on PATH as `~/bin/*` |
| `zsh/.zsh/aliases.zsh:78-84` | `claude()` routes through Herdmates only when `HERDR_PANE_ID` is set; unaffected |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | Shared contract of verbs: `spawn, message, report, wait, collect, stop, status, list`, plus `help` and a gap rule | Skills naming `hc`/Herdmates directly | Dependency inversion: skills depend on the contract, adapters on runtimes | Session, Q12′ |
| D2 | Adapters `hc`, `herdmates` (thin, today's runtimes.md text), `native` | Full Herdmates adapter; dropping Herdmates | TUI may be retired (`herdr-to-app`); keep it working, invest nothing | Q2 |
| D3 | Resolver precedence: `HERDR_PANE_ID` (Claude→herdmates, Codex→native) > `HC_TERMINAL_ID` (hc; role from `hc get` parent) > native | `HC_TERMINAL_ID` first | Innermost runtime wins; Herdmates is Claude-only | Q10, session |
| D4 | Startup hooks inject a one-line pointer; full contract loaded by `agent-runtime contract` only when delegating; `help` only on a gap | Inject full contract every session | ~30 tokens per session vs ~500; most sessions never delegate | Session |
| D5 | Gap rule: check `help`, propose the contract change in chat in one line, ask before the raw command | Silent raw calls; vault file | User wants to grow the contract deliberately | Session |
| D6 | `status`/`list` return `{name, runtime, role, parent, state, turn, native, next[]}`; `state` ∈ working, idle, blocked, done, exited, unknown | Runtime-specific readers in orchestrate | Orchestrate never names a runtime; `native` + `next` tell the agent what to do | Q12′ |
| D7 | Move `orchestrate.py herdr` and its 2 tests into the herdmates adapter; `inspect` reports `agent-runtime resolve` | Add an `hc` subcommand next to `herdr` | Same DI reason | Q14 |
| D8 | Codex hook inline in tracked `agents/.codex/config.toml` | `~/.codex/hooks.json` | That file is Herdr-managed and untracked | Q3 |
| D9 | SubagentStart hook: subagents and in-process teammates get "no runtime commands; report via return value or SendMessage" | Rely on prose | They inherit the lead's `HC_TERMINAL_ID`; `hc send` would type into the lead | Q7 |
| D10 | PreToolUse guard: subagents may run only read-only hc subcommands (get, ls, read, wait, templates, events, file, exec, schema, --help); `herdmates teammux-launch` denied inside any agent; leads unrestricted | Deny-list of run/send/kill only | Review found `hc keys t7 enter` could answer the lead's permission dialog; allow-list follows D9 | Q8, T4 review |
| D11 | hc spawn: Claude `claude --agent implementer "<task>"`; Codex `codex --no-daemon` with implementer.toml instructions in the first message | Templates first | Reuses existing definitions; templates can come later | Q9 |
| D12 | New skill `agents/.agents/skills/agent-runtime/` (contract, adapters, Python stdlib script, unittest); `bin/bin/agent-runtime` symlink | Script in `bin/` only | One home serves Claude and Codex through the shared skill dir | Q11 |
| D14 | All tickets land as commits on `feat/agent-runtime`, each gated; one PR at the end for Felippe to merge | PR and merge per ticket | Auto mode blocks unreviewed merges; one review is cheaper. Converge scope sees the whole branch, so scope is checked per ticket commit (`git show --stat`) | Session, orchestrate run |
| D15 | Routing (option D): `spawn` has kinds `lookup` (short read-only work; the CLI's own subagent tool in every runtime) and `worker` (implementation, long tasks, Codex, anything the user should watch; `hc run` under hc) | Every delegation an hc worker (C); helpers may act through hc (B) | Counsel 3/3: keeps lookups cheap with a structured result, hc where visibility pays | Counsel 2026-10-30 |
| D16 | If S27 fails (in-process teammates carry no `agent_id`), turn teammates off under hc: the hc adapter tells leads not to name subagents and `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is set to 0 | Accept unguarded teammates | The guard cannot tell them from the lead | Counsel 2026-10-30 |
| D17 | hc workers toward their parent: `send` (no `--force`) and reads allowed; `keys`, `interrupt`, `kill`, `send --force` denied. Toward their own children or siblings: unrestricted | Leave workers unrestricted | `send` is attributed and refused on a blocked lead; `keys` is anonymous and can answer the lead's permission dialog | Counsel 2026-10-30, session |
| D18 | Contract verb `resume(name, task)`: hc `hc send <name>` (then `wait`); Claude SendMessage to the returned agent ID; Codex `followup_task`; Herdmates SendMessage to the teammate. A fresh spawn with the same name is not resumption | Leave resume as a gap; fold into `message` | orchestrate's main mode is resuming the existing implementer; without a verb every resume asks the user | Felippe 2026-10-30 (gap rule) |
| D13 | Never commit the literal Context7 key; the config.toml ticket stages only its `[hooks]` hunk | Revert to `$CONTEXT7_API_KEY` | Var is not exported, so revert breaks Codex context7 | Q1, session |

## Scenarios

Resolver
- **S1** WHEN `HERDR_PANE_ID` is set and `--cli claude`, THEN `agent-runtime resolve` prints `runtime=herdmates`.
- **S2** WHEN `HERDR_PANE_ID` is set and `--cli codex`, THEN it prints `runtime=native`.
- **S3** WHEN only `HC_TERMINAL_ID` is set and `hc get` reports `parent: null`, THEN it prints `runtime=hc role=lead`.
- **S4** WHEN only `HC_TERMINAL_ID` is set and `hc get` reports `parent: tX`, THEN it prints `runtime=hc role=worker parent=tX`.
- **S5** WHEN neither variable is set, THEN it prints `runtime=native role=lead`.
- **S6** WHEN both variables are set, THEN `HERDR_PANE_ID` wins.
- **S7** WHEN `hc get` fails or exceeds 2 s, THEN it prints `runtime=hc role=unknown` and exits 0.

Contract and help
- **S8** WHEN `agent-runtime contract` runs, THEN it prints the verbs, the gap rule and only the resolved runtime's adapter.
- **S9** WHEN `agent-runtime help` runs, THEN it lists the runtime's capabilities, each marked in-contract yes or no.
- **S10** WHEN a capability is marked not in contract, THEN the printed gap rule says: propose the change in chat in one line, ask before the raw command.

Status
- **S11** WHEN `agent-runtime status <name>` runs under hc, THEN it prints JSON with `name, runtime, role, parent, state, turn, native, next` and `next` is non-empty.
- **S12** WHEN hc reports working, idle, done, blocked, exited/finished or anything else, THEN `state` is working, idle, done, blocked, exited or unknown, and a blocked worker's `next` includes a raw `hc keys` row marked as a gap.
- **S13** WHEN `status` runs under herdmates, THEN it maps `herdr pane get` output (the logic moved from `orchestrate.py herdr`) and the two moved tests pass.
- **S14** WHEN `status` runs under native, THEN `state` is unknown and `next` points to the return value or SendMessage.
- **S15** WHEN `agent-runtime list` runs under hc, THEN it returns one status per child of the caller.

Hooks
- **S16** WHEN a Claude session starts, THEN its context contains `agent-runtime: runtime=… role=… parent=… · run agent-runtime contract before delegating`.
- **S17** WHEN a Codex session starts after the hook is trusted, THEN its context contains the same line.
- **S18** WHEN a Claude subagent or in-process teammate starts, THEN its context says it is a subagent and must not run runtime commands.
- **S19** WHEN a subagent's Bash call runs `hc run`, `hc send` or `hc kill`, THEN the PreToolUse hook denies it with a reason.
- **S20** WHEN a Bash call inside an agent session runs `herdmates teammux-launch`, THEN it is denied.
- **S21** WHEN a lead (not a subagent) runs `hc send`, THEN it is allowed.

Consumers
- **S22** WHEN the bodies of orchestrate `SKILL.md`, `implementer.md` and `implementer.toml` are searched, THEN `hc`, `herdmates`, `herdr` appear only in a pointer to `agent-runtime`.
- **S23** WHEN `orchestrate.py inspect` runs, THEN it includes the `agent-runtime resolve` result, the `herdr` subcommand is gone, and the other 11 orchestrate tests pass.

Live
- **S24** WHEN the lead spawns a Claude implementer through the hc adapter, THEN the worker nests under the lead, does the task and reports with `hc send` to the lead.
- **S25** WHEN the lead spawns a Codex worker through the hc adapter, THEN it runs with `--no-daemon`, receives the task and reports to the lead.
- **S26** WHEN S24 and S25 run, THEN the Herdr.app sidebar shows both workers nested under the lead.
- **S27** WHEN an in-process teammate under hc runs `hc send <lead> x`, THEN the PreToolUse guard denies it (its call carries `agent_id`).

Routing and worker guard
- **S28** WHEN an hc worker runs `hc keys`, `hc interrupt`, `hc kill` or `hc send --force` aimed at its parent, THEN the PreToolUse guard denies it with a reason.
- **S29** WHEN an hc worker runs `hc send <parent> "<result>"`, or any hc command aimed at its own child or a sibling, THEN it is allowed.
- **S31** WHEN a Codex hc worker's Bash call runs `hc keys`, `hc interrupt`, `hc kill` or `hc send --force` aimed at its parent, THEN Codex's PreToolUse hook denies it.
- **S32** WHEN `agent-runtime` runs inside a Codex session without `--cli`, THEN it resolves as `cli=codex` (so Codex in the herdr TUI gets `runtime=native`).
- **S33** WHEN `agent-runtime contract` runs for any runtime, THEN it lists `resume(name, task)` with that runtime's resume operation, and `help` marks resume in-contract.
- **S30** WHEN `agent-runtime contract` runs for any runtime, THEN `spawn` lists kinds `lookup` and `worker`, `lookup` maps to that CLI's subagent tool, and the routing rule is printed.

## Out of scope

- Biweekly audit of tool updates, launchd job and vault task (deferred, Q4–Q6).
- hc templates (`~/.config/herdr-core/templates`).
- Fixing Herdmates hooks (`herdmates doctor` FAIL) or retiring the herdr TUI.
- Exporting `CONTEXT7_API_KEY` / fixing Codex context7 config.

## Risks

- **Security:** Codex PreToolUse payloads carry no subagent marker, so under Codex only the worker-to-parent rule (S31) is enforced; Codex subagents rely on the contract text. PreToolUse guard (S19–S21) blocks subagents typing into the lead; peer `<hc>` messages are treated as reports, not user instructions (contract text); the Context7 key is never staged (D13).
- **Codex sandbox:** inside Codex's sandbox `hc get` failed (resolve gave `role=unknown`), so a sandboxed Codex worker may not reach the hc socket to `report`. T8 checks this; mitigation is a Codex sandbox setting that allows the hc socket.
- **Performance:** resolver runs at every session start; `hc get` capped at 2 s and failures exit 0 (S7).
- **Migration:** `orchestrate.py herdr` removed; its tests move with the logic (S13, S23). Herdmates keeps working through its adapter.
- **Rollback:** revert the branch; hooks are separate entries in `settings.json` and `config.toml`; an untrusted Codex hook is skipped, not fatal.

## Seams

- `agent-runtime` CLI run as a subprocess with controlled env and a fake `hc`/`herdr` on `PATH` (unittest).
- Hook scripts fed JSON on stdin, asserting stdout/exit code (unittest).
- Existing orchestrate unittest suite.
- Live hc session for S24–S26.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | Skill skeleton, resolver `resolve` with precedence, role and timeout, PATH symlink, tests | S1–S7 | none | agents/.agents/skills/agent-runtime/, bin/bin/agent-runtime | agent | COMPLETE (aac8da5; 16 tests OK; converge PASS; review: no blocking) |
| T2 | Contract text, hc/herdmates/native adapters, `contract` and `help` commands, tests | S8–S10 | T1 | agents/.agents/skills/agent-runtime/ | agent | COMPLETE (90af113; 33 tests OK; converge PASS; review: no blocking) |
| T3 | `status` and `list` with normalized state, `native`, `next`; herdmates mapping and its 2 tests moved in | S11–S15 | T2 | agents/.agents/skills/agent-runtime/ | agent | COMPLETE (db70b0a; review no blocking; converge PASS) |
| T4 | Claude SessionStart pointer, SubagentStart notice, PreToolUse guard, hook tests | S16, S18–S21 | T1 | agents/.claude/settings.json, agents/.agents/skills/agent-runtime/ | agent | COMPLETE (d2adba1, 94de45a; converge PASS; review: no blocking) |
| T5 | Codex inline SessionStart hook in config.toml, staging only the `[hooks]` hunk | S17 | T1 | agents/.codex/config.toml | agent | COMPLETE (2c18281; converge PASS; review: no blocking) |
| T6 | Trust the Codex hook in `/hooks` and confirm the pointer line appears | S17 | T12 | none | Felippe | TO-DO |
| T7 | Rewrite orchestrate and implementer against the contract, delegating by the D15 routing rule; remove `herdr` subcommand; `inspect` uses resolve; README Agent Tooling | S22, S23 | T3 | agents/.agents/skills/orchestrate/, agents/.claude/agents/implementer.md, agents/.codex/agents/implementer.toml, README.md | agent | COMPLETE (73610f1; review no blocking; converge PASS) |
| T8 | Live run: Claude and Codex workers through the hc adapter; in-process teammate hits the guard (apply D16 if not); measure one hc worker vs one subagent on the same small task (tokens, seconds); confirm a sandboxed Codex worker can `hc send` its report; fix adapter text found wrong | S24, S25, S27 | T12 | agents/.agents/skills/agent-runtime/ | agent | TO-DO |
| T9 | Check the Herdr.app sidebar during T8 | S26 | T8 | none | Felippe | TO-DO |
| T10 | Guard hardening: subagent allow-list of read-only hc subcommands, options before the subcommand, shell keywords/wrappers (`then`, `{`, `timeout`, `xargs`, `-lc`), worker-thread allow test, notice text aligned with the contract | S19, S21 | T4 | agents/.agents/skills/agent-runtime/hooks/, agents/.agents/skills/agent-runtime/tests/test_hooks.py | agent | COMPLETE (5317749, 5623401; review blocking fixed; converge PASS) |
| T11 | Contract trim to ~500 words; trust-dialog keypress in-contract for spawn; hc `error` maps to `unknown` | S8, S9 | T2 | agents/.agents/skills/agent-runtime/references/, agents/.agents/skills/agent-runtime/adapters/, agents/.agents/skills/agent-runtime/tests/test_agent_runtime.py | agent | COMPLETE (9b09ed1; review no blocking; converge PASS) |
| T12 | Merge the PR, pull the live dotfiles checkout (resolve its uncommitted config.toml), restow `bin` so `~/bin/agent-runtime` exists | none | T7, T10, T11, T11b, T13, T14, T15, T16, T17, T18, T19 | none | Felippe | TO-DO |
| T11b | `spawn` kinds `lookup`/`worker` and the routing rule in contract, adapters and capabilities | S30 | T11 | agents/.agents/skills/agent-runtime/references/, agents/.agents/skills/agent-runtime/adapters/, agents/.agents/skills/agent-runtime/tests/test_agent_runtime.py | agent | COMPLETE (e7566c0; review no blocking; converge PASS) |
| T13 | Worker-to-parent guard in pre_tool_use: resolve the caller's parent only when a parent-sensitive hc command appears | S28, S29 | T10 | agents/.agents/skills/agent-runtime/hooks/, agents/.agents/skills/agent-runtime/tests/test_hooks.py | agent | COMPLETE (3183a9a, 5623401; review blocking fixed; converge PASS) |
| T14 | Wire pre_tool_use into Codex's PreToolUse (Bash) in config.toml, adapting to Codex's payload; worker-to-parent rule applies to Codex workers | S31 | T13 | agents/.codex/config.toml, agents/.agents/skills/agent-runtime/hooks/, agents/.agents/skills/agent-runtime/tests/test_hooks.py | agent | COMPLETE (395a209; review no blocking; converge PASS) |
| T15 | Codex auto-detection in resolve (env markers) used by contract/status/orchestrate; restore lost Codex/Claude guidance in the native adapter (agent_type implementer, fork_turns none, stale role definitions, resume by returned ID, no second worker to display one) | S32, S8 | T14 | agents/.agents/skills/agent-runtime/ | agent | COMPLETE (62e73f4, 2fff993; live: cli=codex in Codex, cli=claude here; converge PASS) |
| T16 | Implementer: one rule on starting agents; hc workers send the Return block with `report`. Orchestrate: resume wording, strict S23 test | S22, S23 | T7 | agents/.agents/skills/orchestrate/, agents/.claude/agents/implementer.md, agents/.codex/agents/implementer.toml | agent | COMPLETE (b8b3bc2; converge PASS) |
| T17 | Codex guard tests re-cover fail-open and literal-parent cases; drop the hardcoded agent-fleet path from the wiring test | S31, S16 | T14 | agents/.agents/skills/agent-runtime/tests/test_hooks.py | agent | COMPLETE (85b054c; converge PASS) |
| T18 | Gap fixes, hooks: subagent notice matches contract.md (read-only hc allowed; verbs not CLI commands); subagents get only `hc file read`; unknown `--opt value` before a worker's sensitive call target treated as unresolvable | S18, S19, S28 | T17 | agents/.agents/skills/agent-runtime/hooks/, agents/.agents/skills/agent-runtime/tests/test_hooks.py, agents/.agents/skills/agent-runtime/references/ | agent | COMPLETE (8e608c8; converge PASS; gap review follow-up) |
| T19 | Gap fixes, resolver: `exited`/`finished` in either hc field map to `exited`; remove dead loop; fix SKILL.md error sentence; hc.md names implementer.toml as the Codex instruction source; document `list` as hc-only | S12, S15 | T15 | agents/.agents/skills/agent-runtime/scripts/, agents/.agents/skills/agent-runtime/SKILL.md, agents/.agents/skills/agent-runtime/adapters/, agents/.agents/skills/agent-runtime/tests/test_agent_runtime.py | agent | COMPLETE (d7bee21, a085076; converge PASS; gap review follow-up) |
| T20 | `resume` verb in contract, adapters and capabilities (contract ≤600 words per runtime); orchestrate SKILL.md resumes through it | S33, S22 | T19 | agents/.agents/skills/agent-runtime/references/, agents/.agents/skills/agent-runtime/adapters/, agents/.agents/skills/agent-runtime/tests/test_agent_runtime.py, agents/.agents/skills/orchestrate/SKILL.md | agent | COMPLETE (d02782d; converge PASS; 81 + 14 tests OK) |
