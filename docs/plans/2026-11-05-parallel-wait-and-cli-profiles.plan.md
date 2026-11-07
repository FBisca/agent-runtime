# Plan: parallel wait, live team ownership, configurable CLI profiles

Slug: `parallel-wait-and-cli-profiles`. Living spec: `docs/specs/agent-runtime.md`. Status: APPROVED 2026-11-05.

## Why now

Three things block the runtime from being the coordination point it claims to be.

1. `wait(target="all")` is a barrier, not an event wait: it returns only when every selected assignment is complete, needs attention, or the deadline hits (`server/dispatch_wait.py:87-91`). A fast worker's report sits in the store, `acked=0` and undelivered, until the slowest worker finishes — ten minutes in the live case ([#31](https://github.com/FBisca/agent-runtime/issues/31)). A lead whose turn length is the slowest worker's cannot orchestrate in parallel, and cannot react to a result that is already sitting there.
2. A CLI child started from the lead's own shell inherits `HC_TERMINAL_ID`, registers a newer team generation, and fences the live lead out of its own team with `stale team generation`. There is no recovery short of hand-editing the store ([#22](https://github.com/FBisca/agent-runtime/issues/22)).
3. `resume` returns success without proving the task was received; a worker idle at its pre-resume turn is invisible to every check until the 15-minute stall fires ([#13](https://github.com/FBisca/agent-runtime/issues/13)).

And the runtime names workers by executable, not by endpoint. `spawn(cli=…)` accepts `claude` and `codex` (`server/hc_tools.py:22`, `server/mcp_server.py:34`); the endpoint is whatever the login session carries (`hc --skill`: "Programs get the login session's environment"), so a lead running stock Claude, MiniMax-backed Claude Code and Codex side by side cannot say "run this one on MiniMax". The mechanism belongs in the runtime; the names belong to the user's machine.

## Sources

| Source | What it settles |
|---|---|
| The user's request, 2026-11-05 | The three areas: the wait contract, the bugs, the mixed-CLI question |
| The user's answers, grilling rounds 1–3, 2026-11-05 | Every decision below that is marked "user" |
| [Issue #31](https://github.com/FBisca/agent-runtime/issues/31) and its comment | `wait(all)` withholds a delivered report until the slowest worker ends; the fix direction is "return as soon as any report is undelivered" |
| [Issue #22](https://github.com/FBisca/agent-runtime/issues/22) | A CLI child inheriting `HC_TERMINAL_ID` supersedes the live lead's team; deleting the team row by hand was the only recovery |
| [Issue #13](https://github.com/FBisca/agent-runtime/issues/13) | `resume` reports success; the worker never receives the task; every `wait` times out |
| Codex audit "wait-audit", 2026-11-05 | The exact wait path, the R38 conflict, the requirement list to amend, the #22 root cause, the #13 detector |
| Codex audit "mixed-cli-audit", 2026-11-05 | The spawn argv, the env chain, `hc`'s env behaviour, the #20 confirmation |
| `hc --skill` (0.1.18) | "Programs get the login session's environment"; `hc run` takes argv or one shell line; `hc run` without a command is a login shell |
| `server/dispatch_wait.py:47-91` | The reason rules: `messages` is reserved for `target="any"`, `completed` needs no unfinished assignment |
| `server/dispatch_store.py:575-679` | `snapshot` freezes membership; `read` selects unacked messages, issues the receipt and writes `last_wait` in one transaction |
| `server/hc_tools.py:257-295` | The spawn argv; the env handed to `hc run`; `wait` returns before `bounded()` |
| `hooks/hook_lib.py:84-116` | "Undelivered" = unacked **and** not named by a receipt; "seen" = the last recorded reason ≠ timeout and the fingerprint unchanged |
| `server/mcp_server.py:29-70` | The published schema; the tool descriptions total 399 of the 400 words |
| `docs/specs/agent-runtime.md` R38, R39, R41, R42, R61, R65, R99, R109, R119, R120 | The contract being changed |
| `docs/plans/2026-11-04-lead-liveness-hooks.plan.md` D9, D18 | The last-wait row, the "seen" semantics, the accepted lost-final-response limitation |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | `wait(target="all")` returns on the first **undelivered** report (unacked and not named by a receipt), carrying the report, every selected assignment's state, `unfinished` and a receipt | Keep the barrier and steer leads to `target="any"`; add a `return_on` parameter | The barrier is the bug; a new parameter is vocabulary every caller must learn, and `any` is already routed to the parent-team inbox for a worker that leads children | user; issue #31; Codex wait-audit |
| D2 | The early return's reason is the existing `messages`; no new reason value | A new `progress` reason | One vocabulary; `target="any"` already returns `messages` | Codex wait-audit |
| D3 | "Undelivered" is the hook's definition: unacked **and** not named by any receipt | Any unacked message | A receipted-but-unacked message would be re-returned instantly on every retry, so the wait would spin on the same report | `hooks/hook_lib.py:84-89`; Codex wait-audit |
| D4 | A partial `messages` result does **not** write the team's `last_wait` row; only full batches (`completed`, `attention`, `failed`) do | Write the row and teach the hook per-assignment seen state; leave the row as-is | The lead's Stop guard reads "seen" from that row, so a partial result recorded as a delivery would let the lead end its turn with workers still running. Skipping the write keeps the guard's meaning and needs no schema and no hook change | user; `hooks/hook_lib.py:111-116`; `server/dispatch_store.py:675-678` |
| D5 | A report delivered in an earlier batch stays readable until its receipt is passed, so it may appear again with the same message ID | Ack on return; drop receipted reports from later reads | Ack-on-return loses the report when the host drops the result; dropping receipted reports breaks R36's replay after a cancellation or a lost response | R36; lead-liveness D3 |
| D6 | The wait result keeps the `MAX_RESPONSE_BYTES` envelope; the early return returns fewer messages than the barrier did, so the payload shrinks | Re-cap waits at `MAX_BASE_BYTES` | A wait result never carries an inbox piggyback (`server/hc_tools.py:770-773`), so the 96 KiB base cap — which exists so a base plus a piggyback fits the envelope — does not bind it. D22's wording stays ambiguous; this plan does not resolve it | Codex wait-audit; `server/hc_tools.py:770-773` |
| D7 | #22 is fixed on both sides: a CLI child whose generation descends from the terminal's owning CLI cannot claim the team, **and** a superseding generation whose CLI process is provably dead and whose team is empty no longer fences the incumbent | Lazy registration only; prevention without healing | Lazy registration fixes only a child that merely waits; healing is what recovers the store you hand-edited today | user; Codex wait-audit |
| D8 | A team counts as empty when it holds no workers, assignments, messages, receipts, callers, tasks or `last_wait` row | "No live workers" | A team with a message or a receipt still holds state the incumbent's run can collide with | Codex wait-audit |
| D9 | #13 is mitigated by a grace-based attention: `resume` records the dispatch time and the worker's turn, and `wait` raises `attention: "not_started"` when the worker is still idle at that turn, with no report for that assignment, past the grace | Positive delivery confirmation; both | hc gives no reliable ack for a swallowed input, so confirmation cannot prove the case; the mitigation surfaces the hang without ever resending the task | user; Codex wait-audit |
| D10 | The grace is a fixed 60 s from the resume | 30 s; a configurable value | The 30 s `unreported` grace covers a worker that never started at all; a resumed worker is mid-session and needs longer. A constant matches the existing style | Codex wait-audit |
| D11 | The profile names come from a local config file; the repository ships the loader, the format and the mechanism, and **no** profile names. `tools/list` publishes `cli`'s enum computed from that config when the schema is published, re-read per request, so a config written after the server started is picked up without a restart | A separate `profile` argument; a free-form `cli` with no published enum | Two knobs that can disagree; and a published enum keeps the client's own validation while staying extensible | user; Codex mixed-cli-audit; review, 2026-11-05 |
| D12 | The config lives at `$XDG_CONFIG_HOME/agent-runtime/profiles.json` (default `~/.config/agent-runtime/profiles.json`), overridden by `AGENT_RUNTIME_PROFILES=<path>` | Tracked in the dotfiles repo; an env var with no default | The same XDG convention the store already uses; the dotfiles repo can install a copy later without the runtime knowing | user |
| D13 | A configured profile's env is applied by a launcher that clears the provider variables it does not set (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`) before applying the profile | Inherit the lead's env; scrub only some spawns | The worker never inherited the lead's env in the first place — hc gives it the login session's — so the runtime can only control a worker's env through the command line | user; `hc --skill`; Codex mixed-cli-audit |
| D14 | Only a configured profile is launched through the launcher. The built-in `claude` and `codex` keep today's argv exactly and inherit the login session's environment | Wrap every spawn | The login session on this machine exports no provider variables, so a stock-claude worker already stops using MiniMax. Wrapping every spawn would rewrite R129–R142 and ~15 argv assertions for no observable gain | user; `hc --skill`; dotfiles `zsh/.zsh/aliases.zsh` |
| D15 | A profile's env values are `$NAME` references that the launcher expands from its own environment, so no token is ever in the config file, in the `hc run` argv, or on disk; a missing variable makes the spawn fail with the launcher's message. A credential variable (`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`) must be a reference — a literal value for it is rejected at validation. Non-credential values (an endpoint URL, a model name) may be literals | Literal values in the config; a free-form env map on `spawn` | The launcher runs under the login session, where the machine's secrets live; a literal would put a credential in a file and in a tool-call transcript. A literal endpoint URL is not a secret and must stay allowed | Codex mixed-cli-audit; review, 2026-11-05 |
| D16 | The wait description and the README are rewritten with the new contract; the tool descriptions stay within 400 words (399 are used today) | Raise the cap | R56/R99/R135 are spec requirements | `server/mcp_server.py:29-70` |
| D17 | One plan, three ticket groups: the wait contract, the two live bugs, the CLI profiles | Three plans | One approval, one place for the cross-area decisions (the early return against the Stop guard) | user |
| D18 | R38 is superseded by S1; R39, R41, R42, R61, R65, R99, R109 and R120 are amended by S1–S7; the new requirements are appended at the numbers the archive step assigns | Keep R38 and change the code against it | R38 states the opposite of the new contract in the same words the new requirement needs | Codex wait-audit |
| D19 | Implementer workers for T1–T4 run on the user's local MiniMax profile (`spawn(cli="minimax")`). T5 is bootstrapped by a Codex worker, because the profile mechanism cannot exist before it is built. The profile's `$MINIMAX_API_KEY` reference resolves from the **herdr core's** environment, not from the shell that runs the spawn: `hc` sets a program's environment on top of the core's, and the core captured its env when it started. So the token must be exported in the login path (`~/.zprofile`, not the interactive-only `~/.zshrc`), and the core must be restarted (`hc server restart`, programs keep running) for a new export to reach a worker | A stock `cli:"claude"` implementer for the rest; a PATH shim that rewrites `claude` for every claude worker | The user asked for MiniMax implementers. The bootstrap is the one exception the mechanism forces. The env model is hc's, not the runtime's: the launcher can only read what the program is given | user, 2026-11-05 |
| D20 | The review gate and the gaps gate dispatch Codex workers (`spawn(cli="codex")`). The converge gate is unchanged: it is a deterministic script, not a model | Keep the Opus reviewer the orchestrate skill names | The user asked for Codex on review and converge. Converge has no model in it, so only the two review gates change | user, 2026-11-05 |
| D21 | T5 executes first, then T6 (the local profile file), then the remaining tickets. T1 and T2 are blocked by T5 because both edit `server/hc_tools.py`, which T5 owns | Execute the tickets in numeric order | The implementers of the later tickets need the profile that T5's format and T6's file provide; without them the run falls back to a stock `claude` worker. T1 and T2 also share `server/hc_tools.py` and `server/mcp_server.py` with T5, so they branch after it merges | D19 |
| D22 | The config is JSON: `{"profiles": {"<name>": {"cli": "claude"\|"codex", "env": {"NAME": "value"}}}}`. A value of `$VAR` is a reference to a variable in the launcher's own environment. The launcher is `bin/agent-runtime-profile <profile> <config-path> -- <program> <args...>`: the server passes the config path it resolved, so the server and the launcher always read one file; the launcher clears the provider variables, applies the profile's env and `exec`s the program | TOML or YAML; a flat name→profile map; handing the values to the worker through the runtime's env; letting each side resolve the path for itself | JSON needs no dependency and the codebase parses JSON already; a flat map leaves room for global keys that are not profiles; the runtime's env does not reach the worker; two independent path resolutions can diverge when `AGENT_RUNTIME_PROFILES` or `XDG_CONFIG_HOME` differ between the server and the login session | Codex mixed-cli-audit; D15; gaps review, 2026-11-05 |
| D23 | A profile's env may not name a runtime control or identity variable: any key beginning with `HC_` or `HERDR_`, plus `AGENT_RUNTIME_DISPATCH` and `PATH`. The config is rejected at validation | A hand-picked list of variable names | A profile is provider configuration. It must not be able to rewrite the worker's identity, disable structured dispatch so reports stop following the registered assignment, or point the launcher at a different executable that would receive the credentials. The `hc` and `herdr` binaries read more control variables than any list can hold (`HC_SESSION`, `HC_SOCKET`, `HERDR_CLIENT_SOCKET_PATH`, `HERDR_BIN_PATH`, `HERDR_WORKSPACE_ID`, `HERDR_TAB_ID`, `HERDR_CONFIG_PATH`, `HERDR_STARTUP_CWD`, the `HERDR_PLUGIN_*` family), so the rule is by prefix | review, 2026-11-05; gaps review, 2026-11-05 |
| D24 | The launcher's diagnostics never echo an expanded value: an exec failure names the program, never a path or value built from one | Pass the raw `OSError` text through | The launcher's message reaches the terminal output and the spawn's tool result, so a path containing a secret would leak it into the transcript | review, 2026-11-05 |
| D25 | A missing `$NAME` reference makes the spawn fail with the launcher's message instead of reporting success for a launcher that is about to fail: after `hc run`, the runtime probes the terminal for a bounded startup window for the launcher's diagnostic, and treats a terminal that has already exited in that window as a failed launch. A launcher that fails after the window is not caught here; the durable net for a worker that never starts is the `not_started` attention (S12–S15) | An unbounded wait for readiness, which would break the prompt return that spawn promises; rely on the 30 s `unreported` attention alone | Spawn returns promptly, so readiness cannot be awaited; but the launcher fails within milliseconds of exec, and the window only has to outlast that | gaps review, 2026-11-05 |
| D26 | Implementer workers for T3 and T4 run on `MiniMax-M3.1-Flash`. T1 and T2 keep `MiniMax-M3`: they were dispatched before the switch and are mid-flight, so restarting them would discard their work. The model lives in the profile's env overlay, so a change applies to the next spawn with no `hc server restart` — only the `$NAME` references resolve from the core's environment. The profile still caps the worker's window at 512k (`CLAUDE_CODE_MAX_CONTEXT_TOKENS`) although the API serves 1M; that cap is the user's setting and this plan does not change it | Restart T1 and T2 on the new model; restart the herdr core on every model change | M3.1 Flash is agentic-tuned, about half the price and about three times the inference speed of M3 at the same 1M context, and the trial worker ran and reported cleanly (`MiniMax-M3.1-Flash [░░░░░░░░░░] 2% · 10k/512k`). The overlay is read per spawn, so the core restart is not needed; the core only holds the environment that `$NAME` references resolve against | user, 2026-11-05 |

| D27 | Use Codex implementers for remaining tickets and corrections | Wait for MiniMax quota | MiniMax implementers exhausted their token allowance | user, 2026-11-05 |

| D28 | Validate terminal root process and CLI ancestry before first dispatch writes, including pending fast reports; reject unverified or nested callers | Trust the first assignment ID holder | A child can inherit the assignment ID and win the first caller pin | T2 review and identity audit, 2026-11-05 |

## Scenarios

- **S1** WHEN `wait(target="all")` is pending and one selected assignment has an undelivered report while another selected assignment is still working, THEN the call returns `reason: "messages"` carrying that report, the state of every selected assignment, `unfinished` naming the still-open assignment IDs, and a receipt naming exactly the returned message IDs.
- **S2** WHEN a report was delivered in an earlier batch and its receipt was not passed, THEN that report stays readable and may appear again in a later batch with the same message ID.
- **S3** WHEN `wait(target="all")` returns `reason: "messages"` while work remains, THEN the team's `last_wait` row is unchanged, so the lead's Stop guard still blocks on the remaining working assignments.
- **S4** WHEN every selected assignment is complete, THEN `wait(target="all")` returns `reason: "completed"`, an empty `unfinished` list and the reports, and the `last_wait` row is written in the same transaction as the receipt.
- **S5** WHEN a selected assignment fails, blocks, needs a person or has stalled, THEN `wait` returns `reason: "attention"` or `"failed"` with every unfinished assignment, ahead of any pending report.
- **S6** WHEN no report is undelivered and no assignment has finished, THEN `wait` stays pending until the deadline and returns `reason: "timeout"`.
- **S7** WHEN a client lists tools, THEN the `wait` description says it returns as soon as a report is undelivered, that `unfinished` lists the work still open, and that the caller waits again for the rest; and all tool descriptions total ≤400 words.
- **S8** WHEN a runtime call arrives from a CLI process whose generation descends from the CLI that owns the terminal (a child that inherited `HC_TERMINAL_ID`), THEN the call registers no new team generation for that terminal and fails with an explicit error; the incumbent lead's team, assignments and workers are unchanged.
- **S9** WHEN the incumbent lead makes any runtime call after a child was refused, THEN the call is authorized against the incumbent's own team and its workers keep reporting to it.
- **S10** WHEN a newer team generation exists whose CLI process is provably dead and whose team holds no workers, assignments, messages, receipts, callers, tasks or `last_wait` row, THEN the incumbent's runtime calls are authorized against its own team again, with no manual store edit.
- **S11** WHEN a newer team generation exists and its CLI process is alive, or its team holds any of those rows, THEN the incumbent stays fenced with the existing `stale team generation` error.
- **S12** WHEN `resume` gives a worker a new assignment and the worker is still idle at its pre-resume turn, with no report for that assignment, for longer than the grace period, THEN `wait` returns `attention: "not_started"` for that assignment naming the resume, and the runtime never resends the task.
- **S13** WHEN the resumed worker advances its turn or reports within the grace period, THEN no attention is raised for that assignment.
- **S14** WHEN the worker is mid-turn at resume time, THEN the attention check runs from the resume, not from the worker's current turn.
- **S15** WHEN the grace period has not elapsed, THEN the assignment stays `working` with no attention.
- **S16** WHEN a profile config file declares profiles, THEN `tools/list` publishes spawn's `cli` enum as the built-ins `claude` and `codex` plus the configured names.
- **S17** WHEN `spawn(cli="<configured name>")` runs, THEN the worker is launched through the profile launcher with the built-in argv of that profile's CLI family and the prompt last.
- **S18** WHEN a configured profile's env names a variable (`$NAME`), THEN the launcher expands it from its own environment, so the value never appears in the config file, in the `hc run` argv, or on disk; and a missing variable makes the spawn fail with the launcher's message and no worker left running, when the launcher reports it within the spawn's startup window.
- **S19** WHEN a configured profile is applied, THEN the provider variables it does not set (`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL`) are absent from the worker's environment.
- **S20** WHEN `spawn(cli="<name not in the config>")` runs, THEN spawn returns an error naming the unknown profile and no `hc run` happens.
- **S21** WHEN no profile config file exists, THEN the published `cli` enum is exactly `["claude","codex"]` and a built-in spawn's argv is identical to today's.
- **S22** WHEN `AGENT_RUNTIME_PROFILES` is set, THEN the runtime reads that file instead of the default path and hands that same path to the launcher, so both sides read one file.
- **S23** WHEN a profile config file is malformed, THEN the runtime publishes the built-in enum and any spawn naming a configured profile fails with an explicit error before `hc run`.
- **S24** WHEN a configured profile names a CLI family the runtime does not know, THEN the spawn fails with an explicit error before `hc run`.
- **S25** WHEN a profile's env names a runtime control or identity variable (any `HC_*` or `HERDR_*` key, `AGENT_RUNTIME_DISPATCH` or `PATH`), THEN the config is rejected with an explicit error, no `hc run` happens, and no worker starts.
- **S26** WHEN the launcher cannot exec the program, THEN its diagnostic names the program and never an expanded value, so no secret reaches the terminal output or the tool result.

## Out of scope

- [#16](https://github.com/FBisca/agent-runtime/issues/16) (the lead ends its turn without waiting): the Stop guard that shipped in PR #18 already blocks it, within its 3-block budget and the backgrounded-wait exemption.
- [#20](https://github.com/FBisca/agent-runtime/issues/20) (Codex workers ignore `implementer.toml`'s model and reasoning effort): a separate spawn-contract change with its own R130/R131/R138 amendments.
- [#28](https://github.com/FBisca/agent-runtime/issues/28) (the Codex `/new` SessionStart source, and `pending` counting as working in the Stop predicate).
- [#29](https://github.com/FBisca/agent-runtime/issues/29) except the `wait` description, which T1 rewrites anyway: the README's "never write" wording, the `S21`→`R127` label and the duplicated store-path helper.
- Resolving D22's `MAX_BASE_BYTES` / `MAX_RESPONSE_BYTES` ambiguity (D6).
- Resume across a new CLI process (lead-liveness D7), the `more` pagination contract beyond what the early return changes, and provider variables outside the listed set.
- A profile that selects Codex's own configuration layers: the mechanism covers the environment overlay only.

## Risks

- **Security:** the profile's credentials live in the worker's environment, never in the config file, in the `hc run` argv or on disk (D15); the launcher clears the provider variables a profile does not set (D13); `hc run` is still exec'd without a shell and the task text is never shell-quoted. A profile name comes from a file the user owns. Covered.
- **Performance:** the wait returns once per report instead of once per barrier, so a chatty worker costs more MCP round-trips; the 0.5 s reconcile interval and the 0.2 s hc probe budget are unchanged, and the early return does fewer reads, not more. The launcher adds one exec per configured-profile spawn. Covered.
- **Migration:** the store schema is unchanged, so an older server keeps working against a new store. `last_wait` semantics change: a partial result is no longer recorded, so a lead that had already pulled a partial batch sees the Stop guard block again until the next full batch. The published `cli` enum grows only when a config file exists, and a client that cached the old enum is unaffected. Covered.
- **Rollback:** revert the PRs; a profile config file left on disk is inert for a reverted runtime, but a lead that names a configured profile then fails with an unsupported-`cli` error, so the file must be moved aside. Covered.

## Seams

- `tests/test_event_wait.py` `EventWaitTests`: a real MCP server over stdio, the real store and real IPC (S1–S6, S12–S15).
- `tests/test_dispatch.py` `DispatchTests`: spawn, report, resume and the generation rules over a fake `hc` (S8–S11, S12–S15).
- `tests/test_tools.py` `SpawnTests`: the fake `hc` that logs argv; extend it to log the environment overlay (S16–S21, S24).
- `tests/test_spawn_cleanup.py` `SpawnCleanupTests`: invalid input starts no terminal (S20, S23, S24).
- `tests/test_mcp_server.py` `ToolListTests`: the published schema and the 400-word budget (S7, S16, S21).
- `tests/test_hooks.py` `StopGuardTests` and `LeadContextTests`: the Stop guard and the context reminder (S3).
- `tests/test_live_acceptance.py`: the opt-in live path for the launcher's effect on the worker's environment and on `hc get`'s CLI detection (S17–S19).

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Owner | Status |
|---|---|---|---|---|---|---|
| T1 | `wait(all)` returns on the first undelivered report: the receipt-aware undelivered predicate in `Store.read`, the `messages` reason for `target="all"`, the `last_wait` write rule for partial batches, the `wait` description and the README | S1–S7 | T5 | server/dispatch_wait.py, server/dispatch_store.py, server/hc_tools.py, server/mcp_server.py, tests/test_event_wait.py, tests/test_dispatch.py, tests/test_budgets.py, tests/test_peer_dispatch.py, tests/test_mcp_server.py, tests/test_hooks.py, README.md | agent | COMPLETE |
| T2 | #22 prevention: a runtime call whose CLI generation descends from the terminal's owning CLI cannot register a new team generation; the incumbent keeps its team, assignments and workers | S8, S9 | T5 | server/agent_runtime.py, server/dispatch_store.py, server/hc_tools.py, hooks/hook_lib.py, tests/test_agent_runtime.py, tests/test_dispatch.py, tests/test_peer_dispatch.py, tests/test_hooks.py, tests/test_tools.py, tests/test_mcp_server.py | agent | IN PROGRESS |
| T3 | #22 healing: a superseding generation whose CLI process is provably dead and whose team is empty no longer fences the incumbent; the ownership rule is shared with the hooks | S10, S11 | T2 | server/agent_runtime.py, server/dispatch_store.py, hooks/hook_lib.py, tests/test_dispatch.py, tests/test_peer_dispatch.py, tests/test_hooks.py | agent | TO-DO |
| T4 | #13: `resume` records the dispatch time and the worker's turn; `wait` raises `attention: "not_started"` for a resumed assignment that never starts, and never resends the task | S12–S15 | T1 | server/hc_tools.py, server/dispatch_store.py, server/dispatch_wait.py, tests/test_dispatch.py, tests/test_event_wait.py | agent | IN PROGRESS |
| T5 | Configurable CLI profiles: the config loader, the dynamic `cli` enum in `tools/list`, the profile launcher that scrubs and applies the env, the profile validation before `hc run`, and the opt-in live test for the launcher's effect on the worker's environment | S16–S26 | none | server/profiles.py, server/hc_tools.py, server/mcp_server.py, bin/agent-runtime-profile, install.sh, tests/test_tools.py, tests/test_spawn_cleanup.py, tests/test_mcp_server.py, tests/test_live_acceptance.py, README.md | agent | COMPLETE |
| T6 | The first local profile file: `~/.config/agent-runtime/profiles.json` with the user's MiniMax profile, referencing `$MINIMAX_API_KEY` and holding no literal token | — | T5 | — | felippe | COMPLETE |
| T7 | Live end-to-end: a worker spawned on the configured profile starts, receives its task, reports, and stays identifiable to `status`/`collect`; a built-in worker is unaffected | S17–S19 | T5, T6 | — | felippe | TO-DO |
| T8 | The profile docs name where a `$NAME` reference resolves from (the program environment `hc` gives the worker, which is the herdr core's environment) and that a newly exported variable needs `hc server restart` before a worker sees it | — | T1 | README.md | agent | COMPLETE |

| T9 | Restore the worker qualifier on assignment_id documentation | — | T1 | server/mcp_server.py | agent | COMPLETE |
| T10 | Restore coverage that stopping a worker wakes an already pending wait | — | T1 | tests/test_peer_dispatch.py | agent | COMPLETE |
| T11 | Qualify the early-report wait description as applying to target=all | — | T1 | server/mcp_server.py | agent | COMPLETE |

| T12 | Verify stop notification wakes a pending wait independently of reconciliation polling | — | T10 | tests/test_peer_dispatch.py | agent | TO-DO |

| T13 | Exercise resume attention through real MCP and fake-hc dispatch seams | S12–S15 | T4 | tests/test_event_wait.py, tests/test_dispatch.py | agent | TO-DO |

## Gate log

| Ticket | Gate | Command | Result |
|---|---|---|---|
| T5 | tests | `python3 -B -m unittest discover -s tests` (worktree `t5-profiles`, branch `feat/cli-profiles`) | PASS — 404 tests, 4 opt-in skips, OK |
| T5 | lint | `shellcheck install.sh bin/*` | PASS |
| T5 | lint | `git diff --check main...HEAD` | PASS |
| T5 | converge | `converge.py <plan> --ticket T5 --base main --repo <worktree>` | PASS — coverage, tagged, scope; spec SKIPPED without `--final` |
| T5 | review | Codex worker `t5-review-2`, fresh context, corrected branch | PASS — no blocking findings |
| T5 | gaps | Codex worker `t5-gaps-2`, advisory | 5 partial; all 5 fixed in the hardening round |
| T5 | hardening | Codex worker `t5-impl`, third round: prefix rule, startup window, three test strengthenings | commit `d4281e0`; 405 tests OK, shellcheck PASS, converge PASS |
| T5 | PR | `gh pr create` → PR #33 on `github.com/FBisca/agent-runtime` | CI PASS (shellcheck + 6 test jobs); merged by the user as `dcfb0db` |
| T6 | local file | `~/.config/agent-runtime/profiles.json` | COMPLETE — mode 0600, profile `minimax`, 10 env keys, `ANTHROPIC_AUTH_TOKEN` is a `$MINIMAX_API_KEY` reference |

| T1 | converge, review, PR | converge.py --ticket T1; independent review; PR #37 | PASS; CI PASS, merged |
| T8–T11 | converge, review, PR | converge per ticket; review; PR #38 | PASS; CI PASS, merged |
