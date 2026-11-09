# Handoff — effort above `high`, the role's text, and a per-step brain

**State:** nothing is running, and the repository is clean. The session that wrote this handoff read
the P4c installer plan (`docs/plans/2026-11-07-selective-remote-installer.plan.md`) and its launch
mechanics, launched it once, and stopped it ~3 seconds in at the owner's request. The leftovers were
removed (worktree, branch, the plan file's Status cell, the run state). **The P4c run has not
started, and it cannot start the way the owner wants it to until the three items below land.**

**Why this handoff exists.** The owner wants the P4c plan launched with **Pi** workers on
**`minimax/MiniMax-M3.1-Flash`** at **effort `max`**, with the **`implementer` role carried in full**,
with **each gate picking its own backend and model**, and with **every worker interactive, so its
progress can be watched in Herdr**. The first three are not expressible in the orchestrator today; the
fourth is an invariant the machinery must keep and pin, so no later change can quietly make a worker
headless. This handoff states exactly where the machine stops, so the next agent starts from the seams
instead of from the search.

**Read this before touching anything.** Two of the three items are pinned by tests that assert the
*current* behaviour, and one of them (`routes.py`'s "pi is lead-only") is a **stale** rule that
contradicts code that already shipped.

---

## 1. The four items, and where each one stops

### 1.1 `xhigh` and `max` are unreachable

| Layer | Where | Today |
|---|---|---|
| The runtime's effort | `packages/agent-runtime/src/agent_runtime/hc_tools.py:292` (`spawn_effort`) | **already done**: `low`, `medium`, `high`, `xhigh`, `max`; claude `--effort`, codex `-c model_reasoning_effort="…"`, pi `--thinking`. Do not touch it. |
| The orchestrator's effort | `packages/agent-orchestrator/src/agent_orchestrator/driver.py:456` (`MATRIX`) | size × difficulty → (class, effort). It can only produce `low`, `medium`, `high`. |
| The route's effort | `driver.py:3654-3656` | `effort = route.effort or effort` — a route's effort already wins over the matrix, **for implementers only**. |
| The gates' effort | `driver.py:1895` (reviewer `high`), `driver.py:3494` (gaps `medium`), `driver.py:3349` (archive: the matrix → `low`) | **hard-coded**; a route's effort never reaches a gate. |
| The route schema's effort vocabulary | `packages/agent-orchestrator/src/agent_orchestrator/routes.py:30` | `EFFORTS = ("low", "medium", "high", "xhigh", "max")` — already lists `xhigh` and `max`. |

So `xhigh`/`max` are unreachable for two independent reasons: **the matrix cannot produce them**, and
**the only override that can (`--route`) cannot name `pi`**.

### 1.2 `pi` is refused by the routing schema, and that refusal is stale

| Where | What it says |
|---|---|
| `routes.py:26-29` | `BACKENDS = ("claude", "codex")  # the worker backends; pi is lead-only until worker launch is enabled` |
| `routes.py:81` | `backend: Literal["claude", "codex"]` — a typed route config cannot name pi |
| `routes.py:117-121` | a config naming pi is refused: *"names the backend 'pi', which cannot run a worker yet (pi is lead-only)"* |
| `routes.py:258-263` | `--route medium=pi/…@max` is refused with the same words |

**The rule is out of date.** Pi worker launch already landed:

- the runtime spawns pi — `hc_tools.py:483-489` (`["pi"] + model + [message]`);
- the driver accepts pi — `driver.py:155` (`backend must be codex, claude or pi`) and `--cli pi`
  (`driver.py:4118`);
- the runtime has a pi-worker suite — `tests/test_pi_worker.py`;
- the engine has a pi-backend suite — `packages/agent-orchestrator/tests/test_pi_backend.py`;
- pi's model table already answers — `packages/agent-orchestrator/src/agent_orchestrator/models.md`
  (`minimax/MiniMax-M3.1-Flash` for small, medium and large).

Only the routing schema still says no. **`packages/agent-orchestrator/tests/test_routes.py:46`
asserts the refusal mentions "lead-only" — that test moves with the change.**

Two things already work and must keep working:

- `--cli pi` with no route config resolves every class to `minimax/MiniMax-M3.1-Flash`
  (`driver.py:745-768`: no Pi column in the models file → the engine's own table answers);
- `pi auth check --provider minimax` → `ready`; `pi --list-models minimax` lists
  `MiniMax-M3.1-Flash` (1M context, thinking yes).

### 1.3 The role's **content** never reaches a Pi worker

| Backend | How the role arrives | Where |
|---|---|---|
| Claude | `--agent implementer`; Claude itself loads `~/.claude/agents/implementer.md` | `hc_tools.py:481` |
| Codex | the runtime reads `~/.codex/agents/<agent>.toml` → `developer_instructions` → preamble | `hc_tools.py:153-171`, `484-489` |
| Codex (explicit) | `instructions_file` → the file's content is the preamble | `hc_tools.py:327-349`; **codex-only** (`hc_tools.py:445`) |
| **Pi** | **the literal line `Role: implementer` and nothing else** | `hc_tools.py:462-470` |

`~/.claude/agents/implementer.md` is 5.1K and carries the real discipline: ownership rules, the work
protocol, the report contract, the verification rules. A Pi worker today gets **none of it** — the
runtime appends only the dispatch contract (`WORKER_PROTOCOL`, `hc_tools.py:175`).

**Target:** a backend with no role file of its own (pi, and anything after it) receives the role's
**content** as a preamble, the way Codex does. Open decision for the plan: where the text comes from.
`~/.claude/agents/<agent>.md` is the obvious default — the user's Claude roles already live there and
the driver's default role is already `implementer` (`driver.py:2727-2737`) — and `instructions_file`
must widen from "codex only" to "a backend with no role file", while staying refused for claude
(R162) and staying mutually exclusive with `agent` (R163).

### 1.4 The brain is one backend and one model for every gate

| Gate step | Spawn site | Backend | Model | Effort |
|---|---|---|---|---|
| implementer | `driver.py:3649-3695` | `--cli` | the class's route model | the matrix, or the route's |
| **reviewer** | `driver.py:1886-1896` | `gate_cli()` (`driver.py:2740`) | `gate_model("large")` (`driver.py:2767`) — **but with a brain named it returns the brain's _medium_ model** (`driver.py:2782`) | hard-coded `high` |
| **gaps reviewer** | `driver.py:3483-3496` | `gate_cli()` | `gate_model("medium")` → the brain's medium model | hard-coded `medium` |
| **archive worker** | `driver.py:3347-3400` | `--cli` (the worker backend) | the small class's route model | the matrix → `low` |

`gate_cli()` = `--brain-cli` or `--cli`. `gate_model()` = the brain's medium model when a brain is
named, else the class's route model. So `--brain-cli codex` puts the **reviewer** on the codex
*medium* model (`gpt-6.1-terra`), never on the large one — pinned by
`packages/agent-orchestrator/tests/test_pi_backend.py:51-57`.

**Target:** a **step** dimension. Each step (implementer, reviewer, gaps, archive) picks its own
backend, model and effort, from a route config and/or a command-line override. `--brain-cli` stays
as the shorthand that names one backend for every gate step, so the runs that exist today keep
behaving exactly as they do.

### 1.5 The worker is interactive, and that has to stay true

**The owner's requirement: a worker is not a headless batch job.** It runs a real session in a Herdr
pane the owner can attach to and read, and the owner watches the work happen there.

| Backend | The spawn today | Interactive? |
|---|---|---|
| Claude | `claude --agent implementer --permission-mode auto --model <m> --effort <e> <task>` (`hc_tools.py:481`) | yes — a TUI, no `-p` |
| Codex | `codex --no-daemon … <task>` (`hc_tools.py:486-489`) | yes — a TUI, no `exec` |
| Pi | `pi --model <m> --thinking <e> <task>` (`hc_tools.py:483-485`) | yes — a TUI, **no `--print`**, on purpose |

**Why the interactive mode is load-bearing, not an accident.** The runtime's `resume`
(`hc_tools.py:688-733`) sends a follow-up prompt into a **live** pane with `hc send <name> <prompt>`,
and the driver resumes the same pane for a fix round (`hand_round` → `hand_over`,
`driver.py:3013+`: *"the live pane is resumed, a gone pane (or a `fresh` round) gets a new worker"*).
A one-shot worker (`pi --print`, `claude -p`, `codex exec`) exits after its turn and can no longer be
sent to, so every fix round would start from zero context.

**Where the owner watches it.** The panes are named: `<slug>-t<n>` (implementer), `<slug>-t<n>-review`
(reviewer), `<slug>-gaps` (gaps reviewer), `<slug>-archive` (archive worker), and the Lead's own pane
is `orchestrate-<slug>`. `hc ls` lists them, `hc read <name>` prints a pane's transcript, and
`hc attach <name>` follows it live.

**The trap inside the feature:** an interactive worker that ends its turn **without reporting** leaves
a live-but-silent pane. The 30 s unreported window (`UNREPORTED_GRACE_SECONDS = 30`) then parks the
ticket on a person. "Interactive" and "reports" are two different things, and the driver needs both.

**Target:** a requirement that pins it — every spawn leaves a live, attachable pane on every backend;
**no** spawn path uses a headless or one-shot mode; a fix round resumes the same pane and inherits its
context. Anything that would swap a worker for a batch mode has to fail a test.

### 1.6 The Pi worker's two prerequisites

The interactive Pi worker only works if two things on the machine are in place. Both are cheap to make
the runtime own, so they should not be left to whoever happens to run the plan.

**a. Pi's trust prompt.** Pi will not load project-local files in a folder the owner has not vouched
for, and it asks the human. A worker cannot answer: the prompt sits in the pane, the work never starts,
and the runtime's startup window kills the pane (`not_started` attention → the ticket parks). A
worker's cwd is `~/developer/agent-runtime.worktrees/<slug>-t<n>` — a **sibling** of the repo, not
under it, so the repo's own trust entry does not cover it.

- Today: `~/.pi/agent/trust.json` carries `/Users/fbisca/developer/agent-runtime.worktrees: true`.
  Trust the **parent**, never each worktree.
- The durable fix: `pi --approve` (*"Trust project-local files for this run"*) in the spawn argv
  (`hc_tools.py:485`) — no trust file, no prompt, on any machine. `tests/test_pi_worker.py:47-48`
  pins the argv's first three elements and must move with it.

**b. The MCP registration.** Pi reads the global `~/.pi/agent/mcp.json` always, and a project-local
`.pi/mcp.json` **only in trusted projects**. The repo's tracked `.mcp.json` is *Claude Code's* format
(`${CLAUDE_PLUGIN_ROOT}`) — pi never reads it, and there is no `.pi/mcp.json` in the repo. So the
global file is the **only** source of the `report` tool for a Pi worker.

Lose it and the worker still has bash, read and write, finishes the task, and **never reports**: the
runtime hears nothing, the 30 s window flags it, and the ticket parks. No error explains why.

- Today: `pi mcp list` → `agent-runtime: connected, 10 tools (direct, global)`, pointing at
  `/Users/fbisca/developer/agent-runtime/server/mcp_server.py` with `AGENT_RUNTIME_DISPATCH=1`,
  `exposure: direct`, `timeout: 600`.
- Restoring it: `pi mcp add agent-runtime --env AGENT_RUNTIME_DISPATCH=1 -- python3 <abs path to
  server/mcp_server.py>` — but the CLI takes only `--env/--cwd/--url`, so `exposure` and `timeout` may
  have to be re-added to the JSON by hand.
- The fragility: that path is absolute into **this checkout**. Move or delete the checkout and the
  server cannot start, and every Pi worker goes silent in the same way. The installer's shim does not
  help — `install.sh:26` symlinks `~/.local/bin/agent-runtime-mcp` back into the checkout. The durable
  fix is for the runtime to own the registration (write or patch the Pi MCP entry at spawn time), so a
  worker can never start without a `report` tool.

---

## 2. The plan to write first

Per `AGENTS.md`: every behaviour change starts as a scenario in a plan, is written red first, and
lands with its requirement. **So the first thing the next agent does is write the plan**
(`/start-planning`), then implement it red-first. The scenarios below are a sketch, not the plan:
the ids, the wording and the ticket split are the next agent's.

**Item 1 — effort**
- S1: a route may name effort `xhigh` or `max`, and the worker spawn carries it (`--thinking max` on
  pi, `--effort max` on claude, `-c model_reasoning_effort="max"` on codex).
- S2: a route may name the backend `pi`; the "pi is lead-only" refusal is gone; a route that names an
  unknown backend still fails closed, naming the value and the way out.
- S3: a gate's effort comes from its route, not from a constant; a run with no route keeps today's
  efforts (reviewer `high`, gaps `medium`, archive `low`).
- S4: the matrix still answers when no route names an effort — the existing runs do not change.

**Item 2 — the role's text**
- S5: a pi worker with `--agent implementer` receives the role's content in its prompt; the task is
  still last and the report protocol is still there.
- S6: `instructions_file` works for a backend with no role file, is still refused for claude (R162)
  and is still mutually exclusive with `agent` (R163).
- S7: a role file that is missing, empty or unreadable is refused by name, and nothing is launched.

**Item 3 — the per-step brain**
- S8: each step's spawn names its own backend, model and effort; the reviewer can sit on a different
  backend from the gaps reviewer.
- S9: `--brain-cli` still names one backend for every gate step (backwards compatible); a step route
  overrides it.
- S10: a step route naming an unknown step, backend, model or effort fails the run **before** any
  worker is spawned, naming the value.

**Item 4 — the worker stays interactive**
- S11: every spawn, on every backend, leaves a **live, attachable** pane: it runs a session the owner
  can read with `hc read <name>` and follow with `hc attach <name>`.
- S12: no spawn path uses a headless or one-shot mode (`pi --print`, `claude -p`, `codex exec`); a fix
  round resumes the same pane and inherits its context.
- S13: a Pi worker starts without an interactive prompt and without a missing MCP entry: the spawn
  trusts its project files for that run, and the `report` tool is present in the worker whatever the
  machine's `~/.pi/agent/mcp.json` happens to say.

---

## 3. The files that carry the change

| File | What it owns |
|---|---|
| `packages/agent-orchestrator/src/agent_orchestrator/routes.py` | the typed route schema, `BACKENDS`, `EFFORTS`, the pi refusals, `override()`, `resolve()` — and the new **step** dimension |
| `packages/agent-orchestrator/src/agent_orchestrator/driver.py` | `MATRIX` (456), `class_models` (745), `gate_cli` (2740), `gate_model` (2767), `gate_label` (2784), the reviewer spawn (1886), the gaps spawn (3483), the archive dispatch (3347), `dispatch` (3649), the CLI (`--cli` 4118, `--brain-cli` 4120, `--route` 4164) |
| `packages/agent-runtime/src/agent_runtime/hc_tools.py` | `codex_instructions` (153), `WORKER_PROTOCOL` (175), `spawn_effort` (292), `spawn_agent` (309), `spawn_instructions` (327), the pi branch (462) and the per-family argv (480-489) — the place the role's text is injected for a backend with no role file |
| `packages/agent-orchestrator/src/agent_orchestrator/models.md` | the engine's own Pi model table — the fallback when the models file has no Pi column |

## 4. The tests that pin today's behaviour (they move with the change)

| Test | What it pins |
|---|---|
| `packages/agent-orchestrator/tests/test_routes.py:43-46` | the unsupported-backend refusal: **gemini stays refused**, **pi stops being refused** (the assertion names "lead-only" — it must change) |
| `packages/agent-orchestrator/tests/test_routes_driver.py` | the route config a run reads; the brain model out of a config; the legacy path's failure when the brain's column is absent |
| `packages/agent-orchestrator/tests/test_route_selection.py`, `test_route_selection_run.py` | the `--route` override and the route a run resolves |
| `packages/agent-orchestrator/tests/test_pi_backend.py` | the tier table — worker, reviewer, gaps, archive per backend; **the reference for the per-step behaviour** |
| `packages/agent-orchestrator/tests/test_gates.py` | the gates and their fix rounds |
| `packages/agent-orchestrator/tests/test_archive.py` | the archive worker and the gaps reviewer |
| `packages/agent-orchestrator/tests/test_route_selection_run.py:292,310` | a run with `--routes-file` **and** `--brain-cli` together |
| `tests/test_pi_worker.py` | the runtime's pi argv, the role line and the protocol |

## 5. The requirements the change lands with

- The runtime's spawn contract: `docs/specs/agent-runtime.md` — **R147–R149** (effort), **R160–R164**
  (role text and `instructions_file`). Extend those or add new ids; the ids are stable.
- The engine's contract: `docs/specs/orchestrator-engine.md` — **R1–R7**. The routing refusals are
  pinned there as **R4**; the per-step brain is new behaviour and needs its own requirement.
- The plan's scenarios live on as requirements with `(from <slug>/S<n>)` provenance.

---

## 6. Traps on this machine

- **Pi's trust prompt** blocks a worker in a directory Pi does not trust:
  `~/.pi/agent/trust.json` must carry the parent of `~/developer/agent-runtime.worktrees/`.
- **`~/.pi/agent/mcp.json`** must keep its `agent-runtime` entry (`server/mcp_server.py`,
  `AGENT_RUNTIME_DISPATCH=1`, `exposure: direct`). Without it a pi worker cannot call `report` and
  the runtime never sees it finish. It was restored once already.
- **A Pi worker is interactive, and that is the feature** — §1.5. The spawn is
  `pi --model … --thinking … <task>`; no `--print`, on purpose. Nothing may make a worker headless.
- **Pi's trust prompt blocks a headless worker** — §1.6a. `pi --approve` in the spawn argv removes
  the dependency on `~/.pi/agent/trust.json` entirely.
- **A Pi worker without the MCP entry never reports** — §1.6b. `pi mcp list` must say
  `agent-runtime: connected, 10 tools (direct, global)`.
- **The driver rewrites the plan file's Status cells** in place (`IN PROGRESS` → `COMPLETE <date> ·
  merged <url>`). A launched plan leaves the repo dirty; restore with `git checkout -- <plan>`.
- **The driver reads `Status:` more strictly than `converge` does.** `Status: **APPROVED**` reads as
  *missing* and the plan cannot be launched (one regex, one test — not fixed).
- **The driver does not strip backticks from a ticket's `Paths`**, so a backticked path reaches the
  worker with the backticks. Write the paths plain.
- **The budgets are measured debt**: ruff 40/45, ruff format 30/31, ty 277/285 — a change that pushes
  a count over its budget fails CI on purpose. The format budget sits one file below its limit.
- **The suite is load-flaky** in `tests/test_event_wait.py` and `tests/test_stop_failures.py`: run the
  failing test alone before believing it.
- **Converge reads a test's id from the docstring's first line**, and only the line after the `def`:
  keep the test signatures on one line.
- The general traps of this repository are in `docs/plans/2026-11-08-engine-suite-handoff.md` §5 —
  read that rather than rediscovering them.

## 7. The commands

```bash
cd ~/developer/agent-runtime
uv sync --frozen --all-packages
uv run pytest -n 4
uv run pytest -m integration -n 4
uv run ruff check . && uv run ruff format --check . && uv run ty check
shellcheck install.sh bin/*

# the engine's own suite
uv run pytest packages/agent-orchestrator/tests -n 4
```

## 8. The skills for the next session

- **`start-planning`** — write the plan first (sources → scenarios → tickets), stop for approval.
- **`tdd`** — red first; the scenarios above become the failing tests.
- **`converge`** — the gate that has to pass before the plan is archived.
- **`writing-for-agents`** — when the plan's own text is edited.

## 9. Where the work already started

A Pi agent is **already working this task** in the worktree
`~/developer/agent-runtime.worktrees/routing-effort-role`, on branch
`feat/orchestrator-routing-effort-role` (cut from `ecc5f62`), in the Herdr terminal **`t1kr`**
(pi, `(minimax) MiniMax-M3.1-Flash • max`). The handoff below was delivered to it through
`hc send` while it was working. The handoff file itself lives in the **main** checkout, not in that
worktree:

```text
/Users/fbisca/developer/agent-runtime/docs/plans/2026-11-09-orchestrator-routing-effort-role.handoff.md
```

Two things to know about that agent: it is on the model and the effort this handoff is about (so its
own runs already prove the route), and its worktree has no `.venv` of its own — `uv run pytest`
inside it reported *"No tests collected"*, which is an environment gap, not a red test.

## 10. What the last session did, and what it did not

**Did:** parsed the P4c plan and ran `check_plan` on it — it passes; the tickets are T1–T5 with a
**total** chain (T1 → T2 → T3 → T4 → T5), all `regular`/`complex`, `Owner: agent`, `Status: TO-DO`;
read the launch path (`driver.py:3800`), the routing, the gates and the spawn argv; launched the plan
once and stopped it at the owner's request.

**What that launch proved** (from the run's `events.jsonl`, since deleted): `route_source: legacy`,
class `medium`, model `sonnet`, effort `high`, `effort_source: matrix`; the worker spawned as
`claude --agent implementer --permission-mode auto --model sonnet --effort high`.

**Did not:** the P4c run has not started. The leftovers were removed — the plan file reverted, the
worktree `~/developer/agent-runtime.worktrees/selective-remote-installer-t1` removed, the branch
`feat/selective-remote-installer-t1` deleted, `~/.local/state/orchestrate/selective-remote-installer*`
deleted. **No pull request was opened and the repository is clean.**
