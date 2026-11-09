# Install

- [Requirements](#requirements)
- [Claude Code](#claude-code)
- [Codex](#codex)
- [Pi](#pi)
- [Check the install](#check-the-install)

## Requirements

- Python 3.11+
- The [Herdr](https://herdr.dev/core/) `hc` CLI on `PATH` for spawning and driving worker panes
- Claude Code, Codex and/or Pi as the host

## Claude Code

From the public marketplace:

```
/plugin marketplace add FBisca/agent-runtime
/plugin install agent-runtime@agent-runtime
```

From a local clone, loaded in place so edits apply on the next session:

```sh
git clone https://github.com/FBisca/agent-runtime.git
```

```
/plugin marketplace add ./agent-runtime
/plugin install agent-runtime@agent-runtime
```

The plugin registers the MCP server (`.mcp.json`) and the hooks (`hooks/hooks.json`).

## Codex

Codex has no plugin marketplace, so it uses shims. `install.sh` links `agent-runtime`, `agent-runtime-hook`, `agent-runtime-mcp` and `agent-runtime-profile` into `~/.local/bin` (override with `BIN_DIR`) and prints a complete, ready-to-paste `~/.codex/config.toml` block. It is idempotent.

```sh
./install.sh
```

The block registers the MCP server with `AGENT_RUNTIME_DISPATCH=1`, a 600 s tool timeout and `approve` mode on the mutating tools (`spawn`, `message`, `resume`, `stop`, `report`, `task`), plus PreToolUse hooks for Bash and the runtime's own tools.

### Codex lead hooks

The [Stop guard and context reminders](architecture.md#hooks) also run on a Codex lead, as opt-in hooks.

`CODEX_LEAD_HOOKS=1 ./install.sh` reprints the whole Codex config (including `[mcp_servers.agent-runtime]`) plus the lead hooks; copy ONLY the `[[hooks.Stop]]`, `[[hooks.SessionStart]]` and `[[hooks.UserPromptSubmit]]` blocks into `~/.codex/config.toml` yourself, then trust them with `/hooks`. Codex runs a hook only once it is trusted, and an untrusted one raises a "Hooks need review" prompt. Trust is positional: a stale `[hooks.state."…config.toml:session_start:0:0"]` entry left by an old, retired hook shows as changed and must be re-trusted or deleted.

Then confirm a spawned Codex worker still starts its task, from inside an hc terminal:

```sh
AGENT_RUNTIME_LIVE_CODEX_HOOKS=1 python3 -B -m unittest discover -s tests -p test_live_acceptance.py -k spawned_codex_worker
```

It is skipped unless `~/.codex/config.toml` has the Stop and context hooks.

## Pi

Pi registers the server in its own config and needs a host policy before it may mutate anything. Both are on the [Pi](pi.md) page.

## Check the install

`agent-runtime resolve` shows which runtime a session resolves to; `status` and `list` inspect workers. To drive the tools from a script, see [Scripting with `agent-runtime call`](usage.md#scripting-with-agent-runtime-call).
