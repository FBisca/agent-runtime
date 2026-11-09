# Usage

- [Asking for delegation](#asking-for-delegation)
- [The worker's role](#the-workers-role)
- [Launch flags](#launch-flags)
- [CLI profiles](#cli-profiles)
- [Scripting with `agent-runtime call`](#scripting-with-agent-runtime-call)

## Asking for delegation

Ask in plain words; the lead agent turns it into tool calls.

| You say | Lead calls |
|---|---|
| "Ask Codex to review this diff" | `spawn(kind="worker", cli="codex", name="codex-review", task=…, cwd=…)`, then `wait(target="all")` |
| "Have Claude and Codex both try this" | two `spawn`s (`cli="claude"`, `cli="codex"`), one `wait(target="all")` |
| "Tell the reviewer to also check tests" | `message(to="codex-review", text=…)` or `resume(name=…, task=…)` |
| "What is it doing?" | `status(name=…)`, then `collect` if it went quiet |

`cli` defaults to `claude`. Reports return as tool results; the lead never polls panes.

## The worker's role

All CLI families and named profiles default to no role and receive the worker protocol in their prompt. Select a role explicitly when needed:

| You say | Lead calls |
|---|---|
| "Run it as the implementer" | `spawn(…, agent="implementer")` — select the implementation role explicitly |
| "Run it as the reviewer" | `spawn(…, agent="reviewer")` — Codex reads `~/.codex/agents/reviewer.toml`, Claude `--agent reviewer` |
| "No role, the task says it all" | `spawn(…, agent="none")` — no role file, no preamble; the runtime states the worker protocol (report with `report(kind, assignment_id)` under structured dispatch or `report(text)` otherwise, the result block shape) in the prompt instead |
| "Use my role file" | `spawn(…, instructions_file="~/roles/researcher.md")` — Codex only; the file's text is the preamble. Not combinable with `agent` |

The role tomls stay in `~/.codex/agents/` either way: Codex reads them itself when it runs outside the runtime.

An agent without the `report` tool leaves the lead seeing the worker as unreported or stalled.

## Launch flags

`spawn` starts the CLI non-interactively with the task text:

- Claude: `--permission-mode auto`; Codex: `--no-daemon`; neither has a default role.
- An optional `model` adds `--model <model>` before the prompt.
- An optional `effort` (`low`, `medium`, `high`, `xhigh`, `max`) sets that task's reasoning effort as a launch flag, Claude `--effort <level>` and Codex `-c model_reasoning_effort="<level>"`, so it never changes the CLI's saved default.
- An optional `agent` selects a role: Claude `--agent <agent>`, Codex reads `~/.codex/agents/<agent>.toml`; `agent: "none"` runs the worker with no role at all (Claude drops `--agent`, Codex gets no preamble) and the runtime states the worker protocol in the prompt.
- `instructions_file` (Codex only) takes the role text from that file instead of a toml, and cannot be combined with `agent`.

Pi workers launch differently; see [Pi as a worker](pi.md#pi-as-a-worker).

## CLI profiles

Define local worker profiles in `$XDG_CONFIG_HOME/agent-runtime/profiles.json` (default `~/.config/agent-runtime/profiles.json`), or set `AGENT_RUNTIME_PROFILES` to another path. The repository ships no configured profiles. For example:

```json
{
  "profiles": {
    "custom-worker": {
      "cli": "claude",
      "env": {
        "ANTHROPIC_BASE_URL": "https://provider.example/api",
        "ANTHROPIC_AUTH_TOKEN": "$WORKER_API_KEY"
      }
    }
  }
}
```

Use `spawn(cli="custom-worker", …)`. Each `tools/list` request publishes the built-ins plus the configured names. `cli` selects a `claude` or `codex` family; `model` and `agent` keep that family's normal behavior. Built-in `claude` and `codex` launch under `env -u <NAME>…` for every provider variable below and every key any profile sets, so they run on the CLI's own login whatever the lead, pane or an earlier profile launch left in the environment; a malformed config still clears the provider variables.

### How a profile launches

Configured names run through the installed `bin/agent-runtime-profile <profile> <config-path> -- <program> <args...>` launcher. The server passes its resolved absolute config path, so launcher settings cannot select a different file. It clears `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` and every key any configured profile sets, then applies the selected profile's env and execs the CLI. Other login-session variables remain inherited.

`$NAME` references expand from the original launcher environment before scrubbing: the program environment `hc` gives the worker, inherited from the herdr core, not the shell calling spawn. Export referenced variables in the login startup file (`~/.zprofile` for zsh, not interactive-only `~/.zshrc`), then run `hc server restart` so the core picks up newly exported values; running programs keep running.

`ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` require references; literals remain allowed for nonsecret settings such as endpoints and models. Resolved credentials never enter the config, launch argv or temporary files. Exec errors name only the requested program, never a path containing an expanded value. A one-second startup window checks launcher diagnostics and terminal exits, removing failed workers. A launcher failing after this window is not caught by spawn and surfaces instead through `not_started` attention.

### Permissions on another provider

A `claude`-family profile may also set `permission_mode` and `settings`. Another provider (`ANTHROPIC_BASE_URL` elsewhere) gives no server-side auto mode review, so by default every action that needs the check is blocked. To keep auto mode, add `"CLAUDE_CODE_AUTO_MODE_SERVER": "0"` and `"CLAUDE_CODE_AUTO_MODE_MODEL": "<provider model id>"` to the profile's `env`: Claude Code then sends its own classifier requests to that model (needs Claude Code v2.1.302+; Anthropic does not list non-Claude classifier models as supported). Built-in workers never inherit these two variables.

Otherwise, set `"permission_mode": "acceptEdits"` (file edits run unasked; any command outside the allowlist still prompts and blocks the worker) or `"dontAsk"` (anything outside the allowlist is denied without a prompt, so the worker never blocks), and point `settings` at a Claude settings file holding the allowlist, for example `{"permissions": {"allow": ["Bash(cargo *)", "Bash(git *)"], "deny": ["Bash(git push --force*)"]}}`. `settings` must be an absolute or `~/` path to an existing file; the argv becomes `claude --agent <agent> --permission-mode <mode> --settings <path> [--model <m>] [--effort <level>] <prompt>`. Without these keys a profile launches with `--permission-mode auto`, like built-in `claude`. Codex profiles refuse both keys.

### Validation

Profiles cannot set any variable beginning with `HC_` or `HERDR_`, or the exact names `AGENT_RUNTIME_DISPATCH` and `PATH`. Reserved variables, literal credentials, unknown names and unsupported CLI families fail validation before `hc run`. Malformed config publishes only built-ins and returns an explicit error for configured spawns.

### Live check

An opt-in live check derives a provider `$NAME` reference from an existing local profile and runs eight launcher probes: every provider variable is seeded in the inherited environment, then tested both configured and omitted, checking expansion and scrubbing without printing values. It skips with a reason if no provider reference exists or its source is unavailable in the login session. Separately, it spawns the selected CLI, requires its report and checks `hc get` detection. Run inside an hc terminal:

```sh
AGENT_RUNTIME_LIVE_PROFILE=<name> python3 -B -m unittest discover -s tests -p test_live_acceptance.py -k test_S17_S18_S19
```

## Scripting with `agent-runtime call`

`agent-runtime call <tool> [--lead ID] [--json '{...}']` runs one MCP tool from a script in an hc terminal, printing its JSON result on stdout (a plain-text result is wrapped as `{"result": "..."}`; a tool error prints the MCP error result `{"content":[...],"isError":true}` and exits 1; exit 2 on usage, outside hc, a live-generation conflict, or an agent ancestor).

A script passing `--lead` leads its own team even when its hc terminal has a parent; the terminal address and `--lead` ID identify that team. Without `--lead`, a parented terminal resolves as a worker. One hc terminal has one live lead generation: `call` with another `--lead` exits 2, starting nothing, while the terminal's latest team has unfinished assignments or unacknowledged reports, so run a script driver in its own hc terminal.

The PreToolUse hook gates `agent-runtime call <tool>` in Bash like the MCP tool, reading the arguments as the CLI does (also when run as `agent_runtime.py` or through `python`): denied for subagents where the MCP tool is denied, asked on mutating tools, and on arguments it cannot read denied for subagents and asked otherwise.

The hook is early feedback; the boundary is in the CLI: `call` walks its process ancestors up to the hc terminal's root process (its pid from `hc get`) and exits 2, dispatching nothing, if any is a `claude` or `codex` executable (whatever shell form launched it), or if the process table cannot be read or the walk ends without reaching the terminal's root process (pid 1 first, as for an orphaned `nohup` or an `hc exec` daemon, refuses). So run `call` from a plain terminal or an hc pane, not from inside an agent session (`! agent-runtime call …` is refused).
