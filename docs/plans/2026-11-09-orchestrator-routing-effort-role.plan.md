# Plan: Effort Above `high`, the Role's Text, and a Brain per Step

Slug: `orchestrator-routing-effort-role`. Living spec: `docs/specs/orchestrator-engine.md` and
`docs/specs/agent-runtime.md`. Status: APPROVED 2026-11-09.

## Why now

The owner wants to launch the P4c plan with Pi workers on `minimax/MiniMax-M3.1-Flash` at effort
`max`, with the `implementer` role carried in full, and with each gate picking its own backend and
model. Three of those four are not expressible in the orchestrator today, and two Pi prerequisites
stop a worker silently.

- **Effort.** The runtime already speaks all five levels (`hc_tools.py`: `spawn_effort`). The
  orchestrator cannot produce the top two: the size/difficulty matrix only yields `low`, `medium`
  and `high`, and the one override that could name them (`--route`) cannot name the `pi` backend,
  because the route schema still refuses it with a stale rule ("pi is lead-only until worker launch
  is enabled" — pi worker launch shipped). The gates ignore the routes entirely: the reviewer's
  effort is the constant `high`, the gaps reviewer's `medium`.
- **The role's text.** Claude loads `~/.claude/agents/implementer.md` itself; Codex reads
  `~/.codex/agents/<agent>.toml` and injects the body as a preamble. Pi gets the literal line
  `Role: implementer` and nothing else — 5.1K of ownership rules, work protocol and verification
  rules missing.
- **The brain.** One backend and one model serve every gate. `gate_cli()` and `gate_model()` do not
  know which step they serve, so `--brain-cli codex` puts the reviewer on the codex *medium* model,
  never on the large one.
- **The worker stays interactive.** The runtime resumes a live pane (`hc send` into a running
  terminal) and the driver resumes the same pane for a fix round. A one-shot worker cannot be sent
  to, so every fix round would restart from zero context.
- **The two Pi prerequisites.** A Pi worker in an unvouched folder stops at the trust prompt, and
  without the `agent-runtime` entry in Pi's global `mcp.json` the worker finishes the work and never
  reports. Both are silent failures in a pane nobody is watching.

## Sources

| Source | What it settles |
|---|---|
| The session's handoff note (2026-11-09, kept in the owner's working copy) | The four seams, the files that carry the change, the tests that pin today's behaviour, the machine's traps. |
| The owner's note on the handoff (2026-11-09) | The fourth item (the interactive invariant) and the two Pi prerequisites (`--approve`, the runtime-owned MCP registration). |
| `docs/specs/agent-runtime.md` | R147–R149 (effort) and R160–R164 (role text, `instructions_file`): the runtime's spawn contract. |
| `docs/specs/orchestrator-engine.md` | R4 (the routing refusals) and R7: the engine's contract. |
| `packages/agent-orchestrator/src/agent_orchestrator/routes.py`, `driver.py` | The typed route schema, the matrix, the gates' spawn sites. |
| `packages/agent-runtime/src/agent_runtime/hc_tools.py` | The spawn contract: the role file per family, the effort flag, the argv. |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | The route schema accepts the `pi` backend; the stale "lead-only" refusal is gone. | Keeping the refusal until the schema is rewritten. | Pi worker launch already shipped (`tests/test_pi_worker.py`, the driver accepts `pi`); only the schema still says no. | handoff §1.2 |
| D2 | The matrix keeps its four cells. The effort above `high` comes from a route, not from a new size/difficulty cell. | Adding cells to the matrix. | The runs that exist today keep today's efforts; the route is the explicit lever, and a route already wins over the matrix. | handoff §2 (S1, S4) |
| D3 | A step route names a step's backend, model and effort. The steps are `implementer`, `reviewer`, `gaps` and `archive`. | One brain for every gate step (today). | The owner wants each gate to pick its own backend and model; the reviewer may sit on a different backend from the gaps reviewer. | owner note §3 |
| D4 | `--brain-cli` stays and names one backend for every gate step; a step route overrides it. | Removing `--brain-cli`. | The runs that exist today keep behaving exactly as they do. | handoff §1.4 |
| D5 | The role's text for a backend with no role file of its own comes from `~/.claude/agents/<agent>.md`, front matter stripped; `AGENT_RUNTIME_ROLE_DIR` moves the directory. | Injecting the role's name only (today). | The content is the discipline; the user's Claude roles already live there and the driver's default role is already `implementer`. | handoff §1.3 |
| D6 | `instructions_file` widens from "the codex family only" to "a backend with no role file"; it stays refused for Claude and stays exclusive with `agent`. | Allowing it for Claude too. | Claude loads the role itself (R162) and the two ways of naming a role stay exclusive (R163). | handoff §1.3 |
| D7 | A Pi worker is spawned with `--approve`. | A trust entry per worktree. | The worktree is a sibling of the repo, so the repo's trust entry does not cover it, and nobody answers a prompt in a pane nobody watches. | owner note §4 |
| D8 | The runtime owns Pi's MCP registration: before a Pi worker starts, it writes the `agent-runtime` entry into Pi's global `mcp.json`, pointing at the runtime's own absolute path. | Leaving the registration to the operator. | A missing entry means the worker never reports, and the absolute path moves with the checkout. | owner note §4 |
| D9 | Every worker stays interactive. A test fails when any backend is spawned one-shot. | A `--print` mode for the cheap workers. | The runtime resumes a live pane and the driver resumes the same pane for a fix round; a one-shot worker restarts from zero context. | owner note §4 |
| D10 | The step routes live in the route config's new optional `steps` list, and the command line gets `--step STEP=BACKEND/MODEL[@EFFORT]`. | A second config file. | One file describes one run: the classes it dispatches and the steps it gates. | handoff §1.4 |

## Scenarios

- **S1** WHEN a route names the backend `pi` and the effort `xhigh` or `max`, THEN the worker spawn carries
  that backend and that effort (`--thinking max` on pi, `--effort max` on claude, `-c
  model_reasoning_effort="max"` on codex).
- **S2** WHEN a route names a backend, a class, a model or an effort the driver cannot run, THEN the refusal
  names the value it named and the way out, and no worker is spawned.
- **S3** WHEN a step route names an effort, THEN that step's spawn carries it; and a run with no step route
  keeps today's efforts (the reviewer `high`, the gaps reviewer `medium`, the archive worker the matrix's
  `low`).
- **S4** WHEN no route names an effort, THEN the size/difficulty matrix still answers, exactly as it does
  today.
- **S5** WHEN a Pi worker is spawned with `--agent implementer`, THEN the prompt carries the role's content
  (not only its name), the task is still there, and the report protocol is still there.
- **S6** WHEN `instructions_file` names a file, THEN it applies to a backend with no role file of its own
  (pi, as codex already does), it is still refused for Claude, and it is still refused together with
  `agent`.
- **S7** WHEN the role's file is missing, empty or unreadable, THEN the spawn is refused by name and nothing
  is launched.
- **S8** WHEN two steps name different backends, THEN each step's spawn names its own backend, model and
  effort.
- **S9** WHEN `--brain-cli` names a backend, THEN every gate step runs on it; and a step route overrides it.
- **S10** WHEN a step route names an unknown step, backend, model or effort, THEN the run fails before any
  worker is spawned, naming the value.
- **S11** WHEN a worker is spawned on any backend, THEN the worker runs interactively: no `--print`, no `-p`,
  no `exec`, so the pane can be resumed.
- **S12** WHEN a Pi worker is spawned, THEN it is launched with `--approve`, so it never stops at a trust
  prompt.
- **S13** WHEN a Pi worker is spawned, THEN Pi's global `mcp.json` carries the `agent-runtime` server, with
  the runtime's own absolute path, dispatch on and exposure direct, and the other servers in that file
  survive.

## Out of scope

- Launching the P4c plan (`docs/plans/2026-11-07-selective-remote-installer.plan.md`). This plan
  makes it launchable; the launch is the owner's call.
- Changing the matrix's four cells, the class vocabulary or the model table's columns.
- A per-step *role*, or a per-step profile: the step names a backend, a model and an effort.
- Pi as a lead inside the engine (`pi-lead-adapter` is a separate plan).

## Risks

- **Security.** The role's text is the user's own `~/.claude/agents` file, injected into a worker's
  prompt: it is the same text Claude would have loaded. `AGENT_RUNTIME_ROLE_DIR` lets the operator
  point elsewhere; nothing else is read.
- **Security.** `--approve` trusts the project-local files of the worker's checkout for that run. The
  worker already runs with the operator's permissions, and the checkout is the operator's own; the
  alternative is a prompt nobody answers.
- **Security.** The runtime writes Pi's global `mcp.json`. The write is narrow: the `agent-runtime`
  entry is replaced, every other server is preserved, and the file is written atomically.
- **Performance.** The role's text is read once per spawn and injected once: a 5K prompt on top of
  the task. No measurable cost against a model call.
- **Migration.** The route config's `steps` list is optional; a config without it behaves exactly as
  today. `--route`, `--brain-cli` and `--cli` keep their meaning.
- **Rollback.** One revert. The engine's defaults and the runtime's argv are unchanged for a run that
  names no step route and no Pi worker.

## Seams

- The runtime's spawn argv: `tests/test_tools.py` (`SpawnTests`) and `tests/test_pi_worker.py`
  (`PiSpawnTests`) — the argv and the prompt are the seam, and no model is called.
- The route schema: `packages/agent-orchestrator/tests/test_routes.py` and `test_route_selection.py`
  — the typed config and the command-line override.
- The driver's run: `packages/agent-orchestrator/tests/test_pi_backend.py` and
  `test_route_selection_run.py` — what each step's spawn names, end to end through the fake runtime.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The route schema accepts the `pi` backend and every effort the runtime speaks; the stale "lead-only" refusal is gone and an unknown value still fails closed by name. | S1, S2, S4 | none | packages/agent-orchestrator/src/agent_orchestrator/routes.py, packages/agent-orchestrator/tests/test_routes.py, packages/agent-orchestrator/tests/test_route_selection.py, packages/agent-orchestrator/tests/test_route_selection_run.py, packages/agent-orchestrator/tests/test_routes_driver.py | regular | simple | agent | COMPLETE 2026-11-09 · pull request https://github.com/FBisca/agent-runtime/pull/68 |
| T2 | A step route names a step's backend, model and effort, from the route config's `steps` list or from `--step`; a step route overrides `--brain-cli`, and an unknown step, backend, model or effort fails the run before any worker is spawned. | S8, S9, S10 | T1 | packages/agent-orchestrator/src/agent_orchestrator/routes.py, packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_routes.py, packages/agent-orchestrator/tests/test_route_selection.py, packages/agent-orchestrator/tests/test_route_selection_run.py, packages/agent-orchestrator/tests/test_routes_driver.py, packages/agent-orchestrator/tests/test_step_routes.py | regular | complex | agent | COMPLETE 2026-11-09 · pull request https://github.com/FBisca/agent-runtime/pull/68 |
| T3 | The gates' effort comes from the step route instead of a constant; a run with no step route keeps the reviewer's `high`, the gaps reviewer's `medium` and the archive worker's matrix `low`. | S3 | T2 | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_step_routes.py, packages/agent-orchestrator/tests/test_pi_backend.py, packages/agent-orchestrator/tests/test_gates.py, packages/agent-orchestrator/tests/test_archive.py | regular | simple | agent | COMPLETE 2026-11-09 · pull request https://github.com/FBisca/agent-runtime/pull/68 |
| T4 | A backend with no role file of its own receives the role's content as a preamble, the way Codex does; `instructions_file` widens to it, and a missing, empty or unreadable role file is refused by name with nothing launched. | S5, S6, S7 | none | packages/agent-runtime/src/agent_runtime/hc_tools.py, tests/test_tools.py, tests/test_pi_worker.py, tests/test_spawn_cleanup.py, README.md | regular | complex | agent | COMPLETE 2026-11-09 · pull request https://github.com/FBisca/agent-runtime/pull/68 |
| T5 | Every worker stays interactive, and a Pi worker is launched with `--approve` so it never stops at a trust prompt. | S11, S12 | T4 | packages/agent-runtime/src/agent_runtime/hc_tools.py, tests/test_tools.py, tests/test_pi_worker.py, README.md | small | simple | agent | COMPLETE 2026-11-09 · pull request https://github.com/FBisca/agent-runtime/pull/68 |
| T6 | The runtime owns Pi's MCP registration: before a Pi worker starts, Pi's global `mcp.json` carries the `agent-runtime` server with the runtime's own absolute path, dispatch on and exposure direct, and every other server survives. | S13 | T5 | packages/agent-runtime/src/agent_runtime/hc_tools.py, packages/agent-runtime/src/agent_runtime/pi_mcp.py, tests/test_pi_worker.py, README.md | regular | simple | agent | COMPLETE 2026-11-09 · pull request https://github.com/FBisca/agent-runtime/pull/68 |
