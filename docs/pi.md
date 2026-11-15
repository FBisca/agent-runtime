# Pi

[Pi](https://github.com/badlogic/pi-mono) can lead and work. Unlike Claude Code and Codex, Pi ships no
PreToolUse hook, so the server authorizes a Pi session itself.

- [Pi as a lead](#pi-as-a-lead)
- [Pi rhythm: the extension](#pi-rhythm-the-extension)
- [Pi as a worker](#pi-as-a-worker)
- [Routing models with the orchestrator](#routing-models-with-the-orchestrator)
- [Local configuration](#local-configuration)

## Pi as a lead

A Pi session is identified by its own process: `pi` in the
process ancestry (Pi rewrites its process title to `pi`) or `PI_CODING_AGENT` in the environment as a
hint. The lead's generation is the Pi process's `<pid>:<lstart>`, so an MCP server that restarts keeps
the same team, and a Pi restart starts a new one. A Pi session without a visible Pi process, or one
whose environment claims two hosts at once, is refused: the runtime never substitutes Claude.

Pi has no PreToolUse hook of its own, so the server is the authority. A Pi lead may only read until
the operator writes `$XDG_CONFIG_HOME/agent-runtime/hosts.json` (default
`~/.config/agent-runtime/hosts.json`):

```json
{
  "hosts": {
    "pi": {
      "mutations": true,
      "backends": ["codex"],
      "paths": ["/Users/you/developer/checkout"]
    }
  }
}
```

`mutations` allows the mutating tools (`spawn`, `message`, `resume`, `stop`, `report`, and `task`
except its read-only listing). `backends` names the worker backends the lead may start (built-ins or
configured profiles); an empty list starts no worker, so no company-funded model is used until the
operator says so. `paths` limits where a worker may run; without it only the lead's own working
directory is allowed. A Pi lead must name its backend explicitly - the runtime never picks a provider
for it - and reaches only the workers of its own team. Register the server in Pi's own config
(`~/.pi/agent/mcp.json`):

```json
{
  "mcpServers": {
    "agent-runtime": {
      "command": "python3",
      "args": ["/absolute/path/to/agent-runtime/server/mcp_server.py"],
      "env": { "AGENT_RUNTIME_DISPATCH": "1" },
      "exposure": "direct"
    }
  }
}
```

`exposure: direct` declares the tools to the model (the default, `codemode`, keeps them reachable only
from codemode scripts). The server needs the durable dispatch store, so `AGENT_RUNTIME_DISPATCH=1` is
required, and the lead needs an `hc` terminal to start workers from.

## Pi rhythm: the extension

Pi has no Stop or PreToolUse hook, so the rhythm Claude Code and Codex get from theirs arrives in a Pi session
through the extension in `extensions/agent-runtime/`. It translates, and decides nothing: the hook entry decides
the stop, the driver writes the wakes, and the server refuses a mutating verb while the lead has undelivered
reports.

- **A turn is kept open while the team works.** At every turn end the extension runs the same entry the other
  hooks run — `agent-runtime-hook stop --cli pi --dialect json` — and turns a block into one more provider request
  carrying the hook's reason, word for word. An allowed stop adds nothing, so an errored or aborted response stays
  the hard exit it was. The block budget (three extra requests per user work period) and the `seen` fingerprint
  stay the runtime's, keyed by terminal and scope: switching host, or restarting Pi, in one terminal does not hand
  the session a fresh set of blocks.
- **A wake is pushed, not slept for.** The driver books one wake per escalation in the run's `wakes.jsonl`; the
  extension reads the file every two seconds and sends every line it has not pushed yet, in the order the driver
  booked them (one `advance()` round can book several), with a triggered turn, so an idle lead starts on it. The
  cursor is a position in the file, not its newest text, so a burst of wakes is not collapsed to one; a wake it
  already pushed — after a restart too — is pushed once, and an empty line pushes nothing.
- **Only the run's own lead receives them.** The driver runs in an hc terminal named after its plan, whose parent
  is the terminal that launched it; a run started in the lead's own terminal is that terminal. The extension
  pushes only the runs this session is the lead of, and it keeps one cursor per session in
  `$XDG_STATE_HOME/agent-runtime/pi-wakes.json` (`~/.local/state` by default), so a Pi worker — another session on
  the same machine — neither receives the lead's wakes nor spends its cursor. A run whose pane no longer shows the
  session as its lead is left to `await` and to the driver's own `hc send`.
- **A long sleep is refused.** A `bash` call that sleeps 60 seconds or more is refused, the reason naming
  `wait(all)`, because a sleep parks a turn only a wake can end; a sleep under a minute, and every other tool, is
  left alone. This is defence in depth: the server is the authority.

Register the extension in Pi's own settings (`~/.pi/agent/settings.json`). The installer prints this entry and
never writes the file:

```json
{
  "extensions": ["/absolute/path/to/agent-runtime/extensions/agent-runtime"]
}
```

Pi also discovers an extension linked into `~/.pi/agent/extensions/`, which a checkout install may prefer:

```sh
ln -s "$PWD/extensions/agent-runtime" ~/.pi/agent/extensions/agent-runtime
```

Two variables change where it looks. `AGENT_RUNTIME_HOOK` names the hook entry; without it the extension uses
this checkout's `bin/agent-runtime-hook`, then `agent-runtime-hook` on the `PATH`. `AGENT_RUNTIME_WAKES` narrows
the wake files it considers to a `:`-separated list; without it, every run under
`$XDG_STATE_HOME/orchestrate/<repo>/<slug>/wakes.jsonl` is considered. Naming a file does not make it the
reader's: a run's wakes go to the run's lead, and a file outside a run tree goes to a session whose own `hc`
terminal is a lead, so a worker that inherits the variable still receives nothing.

A Pi worker gets the same extension, and there the turn end is the worker's stop. The naming half of that answer
is the hook's, not the extension's: `hooks/stop_guard.py` is what tells a worker which assignment it still owes,
aiming `report complete` and `report blocked` as the two ways out, and `tests/test_hooks_stop_guard.py` is where that
wording is proved. A hook that refuses nothing — a lead without a team, or a worker before that guard exists —
is an allowed stop, and the extension adds nothing to it.

## Pi as a worker

The `pi` family is a built-in worker backend, next to `claude` and `codex`. A spawn that names it
launches `pi --approve --model <model> --thinking <effort> "<task>"`, with the task last; `effort` is the
runtime's own five levels (`low`, `medium`, `high`, `xhigh`, `max`) and Pi's extra levels (`off`,
`minimal`) are never sent. `--approve` is what keeps the worker from stopping at a trust prompt in a
folder Pi does not know: the worktree is a sibling of the repository, so the repository's trust entry does
not cover it, and nobody answers a prompt in a pane nobody is watching.

Pi has no role files and no `--agent` flag, so an explicitly selected role travels in the task:
the runtime reads the user's own Claude role file (`~/.claude/agents/<agent>.md`, front matter aside — set
`AGENT_RUNTIME_ROLE_DIR` to another directory) and puts the role's content in the task, under the role's
name and ahead of the task itself; the runtime states the report protocol there too, naming Pi's own tool
(`mcp__agent-runtime__report`). A role file that is missing, empty or unreadable is a refusal: nothing is
launched. Pi has no token accounting.

Provider scrubbing is family-aware: a `pi` spawn keeps `MINIMAX_API_KEY` and the Pi provider variables
(`PI_API_KEY`, `PI_PROVIDER`, `PI_MODEL`), and a `claude` or `codex` spawn loses all of them, so a
Claude or Codex worker never holds a MiniMax or Pi credential.

A Pi worker is a host the server authorizes itself (Pi ships no PreToolUse hook). Inside its own team
it may `report`, `message`, `wait`, `status`, `collect`, `list` and read a task list; it may not
`spawn`, `stop` or `resume`, and it reaches no worker outside its team. The authorised-directory
guarantee is unchanged: a Pi spawn outside the operator's `paths` is refused before anything starts.

## Routing models with the orchestrator

A Pi model is `provider/model`, so one run can mix providers. Pi resolves each provider's key itself
(`~/.pi/agent/auth.json`, or a Pi package such as `pi-ollama-cloud`), so the runtime needs no variable per
provider. The orchestrator's driver routes each class or step to a provider:

```sh
python3 -m agent_orchestrator run <plan> --cli pi \
  --route small=pi/minimax/MiniMax-M3.1-Flash@low \
  --route large=pi/ollama-cloud/kimi-k3.1-pro@high \
  --step reviewer=pi/ollama-cloud/glm-5.3@high
```

`--step` and `--route` beat `--routes-file`, which beats the repository's `.orchestrate/routes.json`, which beats
the user's profile for the run's backend: `~/.config/orchestrate/routes.json` for Claude, `routes-codex.json` for
`--cli codex` and `routes-pi.json` for `--cli pi`. The driver reads no model table: a new run with no route file,
or whose selected file is invalid, stops before any worker is spawned and names the file it expected, and
`--models-file` is refused. A Pi profile routes each class to a Pi model, for example:

```json
{
  "version": 1,
  "routes": [
    {"class": "small", "backend": "pi", "model": "minimax/MiniMax-M3.1-Flash", "effort": "low"},
    {"class": "medium", "backend": "pi", "model": "ollama-cloud/glm-5.3"},
    {"class": "large", "backend": "pi", "model": "ollama-cloud/kimi-k3.1-pro"}
  ]
}
```

A run that restarts keeps the routes its ledger recorded, whatever these files now say. An explicit argument that
differs from the record stops it before any worker is spawned or resumed, and a ledger with no complete route
record needs `--routes-file`, which records the complete routes before the run resumes. `--brain-cli` puts the
review gates on another backend: give each gate a `--step` route, or a medium route on that backend, or the run
stops before it spawns a worker.

Drives run in parallel per plan: two plans in one repository each run on their own models, each in its own
worktrees. A second `run` of a plan that is already being driven is refused, whatever `--state-dir` it names:
the lock is `<git-common-dir>/agent-orchestrator/<slug>.lock`, held for the run's life and released when the
process exits, so a crash leaves nothing to clean up.

## Local configuration

A working setup looks like this:

| What | Where | Value |
|---|---|---|
| The worker's tools | `~/.pi/agent/mcp.json` | the `agent-runtime` entry: `python3 <checkout>/server/mcp_server.py`, `AGENT_RUNTIME_DISPATCH=1`, `exposure: direct`, a 600 s timeout. The runtime writes this entry itself before it starts a Pi worker, so the path always points at the running runtime; the other servers in that file are left alone |
| The host policy | `~/.config/agent-runtime/hosts.json` | the `pi` host: `mutations` on, `backends` `["pi", "claude-minimax", "codex", "claude"]`, `paths` covering the checkouts under the developer's home |
| The rhythm | `~/.pi/agent/settings.json` | an `extensions` entry naming this checkout's `extensions/agent-runtime`, which is what keeps a lead's turn open, pushes a wake and refuses a long sleep (the installer prints it; it never writes the file) |

The `pi` backend in `backends` is what lets a Pi lead start a Pi worker; without it the spawn is
refused with the policy's reason.
