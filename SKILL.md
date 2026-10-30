---
name: agent-runtime
description: Runtime contract for delegating to other agents: resolve the runtime, then load its contract and adapter. Use before spawning, messaging or waiting on other agents.
---

# Agent runtime

**Output:** the contract verbs and the adapter of the runtime you run in.

Before you spawn, message or wait on another agent, run `agent-runtime contract`. It prints the verbs, the gap rule and only the adapter for this runtime. Delegate through those verbs; never name `hc`, Herdmates or another runtime in the work you hand out.

When you need something the verbs do not cover, run `agent-runtime help`. It lists the runtime's capabilities, each marked in-contract yes or no. For a no, propose the change in chat in one line and ask before the raw command.

`agent-runtime resolve [--cli claude|codex] [--json]` (without `--cli` it detects the CLI from the nearest `codex` or `claude` ancestor process; if the walk fails, from `CODEX_THREAD_ID`/`CODEX_TOOL_CALL_ID` (codex) or `CLAUDECODE` (claude), then an hc terminal's foreground program, else claude) prints one line, `runtime=<herdmates|hc|native> role=<lead|worker|unknown> [parent=<id>] cli=<claude|codex>`, and always exits 0 (a failed `hc get` prints `runtime=hc role=unknown`; any other error prints `runtime=native role=unknown`).

- `HERDR_PANE_ID` set: `herdmates` for Claude (role `unknown`: teammate panes are not distinguishable yet), `native` for Codex.
- Else `HC_TERMINAL_ID` set: `hc`; role comes from `hc get` (parent null: lead, a plain id: worker). A failed, slow or malformed `hc get` gives `role=unknown`.
- Else `native`.

`agent-runtime status <name>` prints one JSON record (`name, runtime, role, parent, state, turn, native, next[]`); `agent-runtime list` prints the records of your children (hc). Each `next` row is `{why, do, verb, gap}`; `gap: true` rows are raw commands outside the contract.
