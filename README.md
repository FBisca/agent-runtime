<div align="center">

# agent-runtime

**Let your coding agent delegate to other coding agents, as plain MCP tool calls.**

[![ci](https://github.com/FBisca/agent-runtime/actions/workflows/ci.yml/badge.svg)](https://github.com/FBisca/agent-runtime/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)

[Install](docs/install.md) · [Usage](docs/usage.md) · [Architecture](docs/architecture.md) · [Pi](docs/pi.md) · [Contributing](CONTRIBUTING.md)

</div>

![A Claude lead delegating one question to a Claude judge and a Codex judge](docs/assets/demo.gif)

<p align="center"><em>A Claude lead asks a Claude judge and a Codex judge the same question, then merges their verdicts (<a href="docs/assets/demo.mp4">full-quality MP4</a>).</em></p>

Works with any coding agent that speaks MCP; tested with Claude Code, Codex and Pi.

## Why

- **Mix agents.** Have Codex review what Claude wrote, ask both the same question, or hand the boring parts to a cheaper model.
- **Results, not screen-scraping.** Workers report back through a tool; the lead gets the report as a tool result, never by reading a terminal.
- **Durable.** Assignments, reports, messages and tasks live in a local SQLite store. Retries are safe, and a `/clear` or a server restart loses nothing.
- **Guarded.** Hooks and the server decide who may spawn, message or stop, and each worker launches with only its own provider's credentials.
- **No dependencies.** The runtime is Python standard library only.

## How it works

![agent-runtime architecture](docs/assets/architecture.png)

1. The **lead** (your coding agent) calls `spawn`. Each **worker** starts in its own terminal pane, run by [Herdr](https://herdr.dev/core/)'s `hc`.
2. The worker does the job and calls `report`.
3. The lead's `wait` returns the report as a tool result, so it never polls a pane.

More in [Architecture](docs/architecture.md).

## Quick start

You need Python 3.11+, Herdr's [`hc`](https://herdr.dev/core/) on your `PATH`, and a host agent.

**Claude Code**

```
/plugin marketplace add FBisca/agent-runtime
/plugin install agent-runtime@agent-runtime
```

**Codex**

```sh
git clone https://github.com/FBisca/agent-runtime.git
cd agent-runtime && ./install.sh   # prints the block to paste into ~/.codex/config.toml
```

**Pi:** see [Pi](docs/pi.md). `./install.sh --components runtime,pi` links the shims and prints the Pi
wiring; Pi itself comes from its [official installer](https://pi.dev/docs/latest/quickstart), which the
installer names when it cannot find `pi` and never runs.

The installer never writes, reads or prints a harness's configuration or credentials: it prints the
block to add, says when a config is already there (and leaves it), and `./install.sh uninstall
--components <list>` removes only the shims and release it made, keeping the rest.

Then ask in plain words, from inside an `hc` terminal:

| You say | Lead calls |
|---|---|
| "Ask Codex to review this diff" | `spawn(cli="codex", task=…)`, then `wait(target="all")` |
| "Have Claude and Codex both try this" | two `spawn`s, one `wait(target="all")` |
| "Tell the reviewer to also check tests" | `message(to="codex-review", text=…)` |
| "What is it doing?" | `status(name=…)` |

Full setup in [Install](docs/install.md), roles, profiles and scripting in [Usage](docs/usage.md).

## Tools

| Tool | What it does |
|---|---|
| `spawn` | Start a worker on a task, in its own pane |
| `wait` | Block until workers report, need attention or time out |
| `message` | Send text to a teammate or the lead |
| `resume` | Give an idle or finished worker another assignment |
| `report` | (worker) Send progress or the result to the lead |
| `task` | Shared team tasks with dependencies and atomic claims |
| `stop` | End a worker once its work is accepted |
| `status` | Diagnostic: a worker's state |
| `list` | Diagnostic: the workers you started |
| `collect` | Diagnostic: read a worker's screen when no report arrived |

## Documentation

| Page | What's in it |
|---|---|
| [Install](docs/install.md) | Requirements, Claude Code, Codex, Codex lead hooks |
| [Usage](docs/usage.md) | Worker roles, launch flags, CLI profiles for other providers, `agent-runtime call` |
| [Pi](docs/pi.md) | Pi as a lead and a worker, host policy, model routing |
| [Architecture](docs/architecture.md) | Components, hooks and the Stop guard, adding a host or a server |
| [Specs](docs/specs/) | The living contracts: every requirement, traced to a test |
| [Contributing](CONTRIBUTING.md) | How it is built, running the tests |

## License

[MIT](LICENSE)
