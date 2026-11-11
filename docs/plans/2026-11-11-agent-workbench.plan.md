# Plan: Agent Workbench for Herdr

Slug: `agent-workbench`. Living spec: `docs/specs/agent-workbench.md` (new, written by T1). Status: APPROVED 2026-11-11 by the plan's owner; `plan approve` at
base `675d626` (after yaml-eec T7 #145 and T8 #144), digest `dce361d1…`.

Contract: [`2026-11-11-agent-workbench.plan.yaml`](2026-11-11-agent-workbench.plan.yaml) (scenarios, tickets, evidence;
`agent-orchestrator plan check` passes). Oracle: [`2026-11-11-agent-workbench.oracle/`](2026-11-11-agent-workbench.oracle/),
red today for the right reasons (`workbench` is not a command; `plans list` names the demo plan `2026-11-10-demo`). This
file is the narrative and is not covered by the approval digest.

**Approval waits on yaml-eec.** `plan approve` runs on main after yaml-eec T7 (#145) and T8 (#144) merge, so the base
holds their `driver.py` changes and the ARCHIVED state; re-run the oracle red before approving.

## Why now

The Herdr Plans panel (herdr-app-mods, 412 lines of JS) parses `*.plan.md` tables itself, cannot see `*.plan.yaml`,
attributes agents to tickets by matching `t<N>` in terminal names, and is one of the four drifting plan readers
yaml-eec removes. yaml-eec has since put the plan model, the ledger, attempts, receipts and acceptance in the
orchestrator. Nothing shows them together: no command emits the ticket graph, the attempts with their worker, or why a
ticket is not verified. The source brief is the Agent Workbench handoff (2026-11-11).

## Sources

| Source | What it settles |
|---|---|
| Agent Workbench handoff (`~/Downloads/agent-workbench-herdr-handoff.md`, 2026-11-11) | Product principles, journeys A–E, rollout P0–P5, the 14 questions of §11. Its commands, fields and paths are proposals |
| `docs/plans/2026-11-10-yaml-eec.plan.{md,yaml}`, live ledger | T1–T6, T12 merged; T7 #145, T8 #144 open; T9–T11 not started. D1 ledger owns status, D2 digest, D8 discovery, D17 receipts bound to digest and tree, D20/T10 the panel reads `plans list --json` |
| `agent_orchestrator/plan.py` | plan/1 schema (`blocked_by`, not `depends_on`), strict loader with cycle and reference checks, `load`, `discover`, digest, approved snapshot |
| `agent_orchestrator/driver.py` | Ledger at `<state>/<repo-key>/<slug>.json`, tasks per attempt, `agents[name]`, `status --json`, `verify`, `plans list`; the address fallback at `driver.py:3504` |
| `agent_orchestrator/{events,evidence,runner,verify}.py` | `events.jsonl`, `attempts.jsonl` (schema 2, no assignment id), receipts, acceptance states |
| `agent_runtime/dispatch_store.py`, `dispatch_events.py` | 11 tables, no lifecycle timestamps, `messages.body` raw text; wakeups are hints, not a journal |
| `docs/specs/python-workspace-contracts.md` R194–R204 | Runtime stays dependency-free; no member opens another's store (R200) |
| `docs/specs/agent-eval.md`, `agent_eval/cli.py` | Cases local-only; only `capture` and `add-label`; reads the old unkeyed state layout |
| `docs/specs/installer-contract.md` | `install.sh --components`, R1–R9 |
| `~/.config/herdr-app/{AGENTS.md,herdr.d.ts,gpui-kit.d.ts}` (herdr-app 0.1.15) | Mod lifecycle, `exec`, `terminals()`, `run` commands, `PathBuilder`/`paint_path`, `herdr-app check` and `shot` and their limits |
| `~/.config/herdr-app/mods/plans*`, `installed.json`, `layouts/plans.json` | The existing panel is user-made (not shipped), parses Markdown, is wired into the active `plans` layout |
| Grill with Felippe, 2026-11-11 (Q1–Q16) | The decisions marked "grill" below |

## Current state (audit)

| Area | Label | Fact |
|---|---|---|
| Plan loader | exists | `plan.load` is importable from a sibling module; it runs git, returns the approved snapshot, refuses drift unless `verify=False` |
| Plan graph CLI | missing | `plans list` gives `{path, format, slug}`; `status --json` gives ticket id, state, reason, pr; nothing emits `blocked_by`, delivers, attempts or gates |
| `plans list` slug | defect | Taken from the file name (`2026-11-10-yaml-eec`), while the ledger is keyed by the `slug:` field |
| Attempts | exists | A ledger task per attempt (`t2`, `t2r1`, `t2d1`, `t2o1`, `t2p1`, `t2-review`) with assignment_id, runtime_id, times, gate, pr |
| Attempt → terminal | partial | `task.runtime_id` and `agents[name].address` hold the hc address the runtime returned, or the assignment id or name when it returned none: a reader cannot tell them apart today |
| Run id | missing | One run dir per repo-key and slug; a re-drive reuses it |
| Evidence | exists | Receipts bound to digest and tree, `ledger.unverified`, `verify` states PASS/FAIL/UNKNOWN/HUMAN_ACCEPTED/AWAITING_HUMAN |
| Runtime history | missing | No created/started/reported times in the store; history of unplanned work cannot be rebuilt |
| Eval links | missing | No read CLI; links by run slug through the old state layout |
| Herdr terminal identity | exists | `terminals()[].address` equals the hc address; remote addresses are `machine/tN`; the runtime records no machine |
| Drawn edges | uncertain | `PathBuilder` and `paint_path` exist in gpui-kit; positioning them inside a mod is unproven |
| Data size | measured | 109 state entries, 16 `events.jsonl`, largest plan 12 tickets: no cache is needed |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | yaml-eec stays untouched: its T10 still migrates the old panel; this plan retires that panel only after yaml-eec is archived (T8) | Absorb T10 and re-approve yaml-eec mid-run | One small throwaway PR is cheaper than re-approving an in-flight plan; yaml-eec S24 reads that panel | grill Q1, Q8 |
| D2 | Build a new mod in this repo at `herdr/agent-workbench/` and delete the herdr-app-mods panel | Evolve the herdr-app-mods `plans` mod | One repo holds the contract and its only view; a structured foundation instead of a grown single file | grill Q2 |
| D3 | Read-only `agent-orchestrator workbench plans` and `workbench plan` in a new `workbench.py`; per-query reads, no cache | An `agent-observability` package; a read-model SQLite | The orchestrator owns the loader and ledger; one consumer; data is small; R200 | grill Q3, Q14; handoff §4 |
| D4 | Scope is handoff P1–P3 on the local machine | P4 runtime journal, P5 evals and trends, remote links, run history now | No screen needs the missing runtime timestamps yet; eval has no read side; no run id exists | grill Q4 |
| D5 | Unplanned workers come live from Herdr `terminals()`; the runtime store is never read | Read `dispatch.sqlite3` | R200, and `messages.body` holds raw text | grill Q5 |
| D6 | A layered list (rows by topological layer, blocked-by chips) is the graph; a spike decides drawn edges | Draw the DAG now | `paint_path` positioning is unproven; the list also serves narrow panes | grill Q6 |
| D7 | This plan is plan/1 YAML with this narrative | A Markdown tickets table | yaml-eec precedent; `plan check` and `plan approve` exist | grill Q7 |
| D8 | Installed by `install.sh --components herdr`: symlink from a checkout, copy from a release, opt-in layout | Copy by hand; a layout switched automatically | Installer contract R1–R9; Herdr hot-reloads a symlinked mod; the handoff forbids layout changes | grill Q9 |
| D9 | All logic in Python (layers, order, state mapping, joins, nulls); the mod renders. Mod modules: `data.js`, `model.js` (pure, `node --test` from pytest), `views/`, `ui.js` | Layout and joins in JS | One tested implementation; the handoff keeps parsing and rules out of the mod | grill Q10 |
| D10 | The mod is `place: "none"`; the installer ships an opt-in `workbench` layout; the old mods and layout are removed by Felippe after acceptance | Auto-dock; agent deletes live config | The handoff forbids changing layouts; the deletion touches his live config | grill Q11 |
| D11 | Outcome = one integration oracle through the real driver (S21) + Felippe's visual check (S22) | A fixture-only JSON check; screenshots alone | The oracle reads ledgers the driver really wrote; `herdr-app check` does not lay out async states | grill Q12 |
| D12 | New code in `workbench.py`, only subparser registration in `driver.py`; the `plans list` slug fix lands in T1 | Wait for yaml-eec T11; leave the slug | Avoid conflicts with in-flight T7/T8; the workbench joins on the ledger key | grill Q13 |
| D13 | Refresh on `watchFolder` of the ledger and run dir plus a 30 s timer; one fetch in flight; never poll Herdr live state | A fast timer | Herdr re-renders on live changes; hidden mods keep their timers | grill Q15; handoff §8 |
| D14 | Markdown plans are listed with ledger state and `legacy: true`, no graph | Parse Markdown tickets again | A second parser is the drift yaml-eec removes; T11 removes Markdown | grill Q16 |
| D15 | An attempt's address is the runtime-returned address; the driver's fallback (assignment id or name) reads as null, and the driver stops writing the fallback as an address | Show the fallback; match terminals by name | The handoff forbids guessed joins; `driver.py:3504` writes the fallback today | gather (driver.py:3504) |
| D16 | Plan identity = `slug:` + repo-key; revision = approval digest; drift shown with both digests | A new plan id | yaml-eec D2 and D17 already define them | yaml-eec D2, D17 |

## Scenarios

Authoritative text is in the YAML. In brief:

- **S1** `plans list` names a YAML plan by its `slug:` field.
- **S2** `workbench plans` lists plans with state, counts, `schema_version`; Markdown is `legacy`.
- **S3** Not a repo, no plans and several plan dirs are distinct error kinds with the way out.
- **S4** `workbench plan` gives exactly `blocked_by` edges, topological layers, a stable byte-identical order.
- **S5** A cycle, duplicate, missing reference or malformed YAML is a scoped error, with no partial view and no write.
- **S6** Drift after approval shows the approved snapshot with both digests.
- **S7** Every attempt per ticket appears with kind, times, assignment and claimed; history is kept.
- **S8** No runtime address, or the fallback, gives a null address; the driver stops writing the fallback.
- **S9** Claimed complete with failed or unknown evidence, or merged unverified, is `verified: false`.
- **S10** Outcome states and item freshness are what `verify` says; acceptance is never inferred from tickets.
- **S11** The commands write nothing and print no bodies or env values; unmeasured values are null.
- **S12** The mod shows plans, a layered ticket list and one inspector.
- **S13** Command failures, scoped errors and no plans are shown in place with the way out.
- **S14** Narrow gives one column and a drill-in inspector.
- **S15** Refresh keeps one fetch in flight, drops stale answers, uses a fixed argv and writes nothing.
- **S16** Open is offered only for a live terminal at the attempt's address.
- **S17** A Runtime section shows unplanned live agents.
- **S18** `node --test` runs from pytest; a missing node fails.
- **S19** The `herdr` installer component behaves as above.
- **S20** The drawn-edge spike is recorded, with human acceptance.
- **S21** Outcome oracle (check).
- **S22** Felippe's visual check (human).
- **S23** The old panel is retired.

## UX

Full tab (opened from the `workbench` layout, or `herdr-app do tab.new --mod agent-workbench`). The values are
illustrative, not read from the ledger:

```text
agent-runtime ▾   yaml-eec ▾                                        RUNNING
YAML-native plans with Executable Evidence Contracts
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  9/12  · 2 in progress

LAYER 0   ● T1  plan/1 model, loader          ✓ verified   │ T7 · Integration mutation check
LAYER 1   ● T2  plans list --json   ← T1      ✓ verified   │ IN PROGRESS · not verified
          ● T3  ledger owns status  ← T1      ✓ verified   │ blocked by  T6 ✓
          ● T4  verify replaces …   ← T1      ✓ verified   │
LAYER 2   ● T5  evidence runner     ← T1 T3   ✓ verified   │ Attempts
          ● T12 archive brief       ← T3      ✓ verified   │  t7     worker  ACTIVE   t1sf  [Open]
LAYER 3   ● T6  ticket gates        ← T4 T5   ✓ verified   │  t7r1   fix     RESERVED  unknown
LAYER 4   ◉ T7  mutation check      ← T6      in progress  │ Gates
          ◉ T8  archive promotion   ← T6 T12  in progress  │  S25 ticket  FAIL  mutant survived
LAYER 5   ○ T9  skills docs         ← T2 T8   to do        │ Outcome  VERIFYING · S27 missing
…                                                          │
Runtime · 3 live agents not in a plan                    ▸
```

- Header: project (shown space), plan picker, one state. One progress bar and at most two counts.
- Center: the layered list. ← chips name the blockers; selecting a ticket highlights its blockers and dependents.
- Right: one inspector (plan, ticket or attempt), collapsible. Open appears only for a live terminal.
- Bottom: a collapsed Runtime row for unplanned live agents.
- Colours come from `theme()`: `done` for verified, `working` for in progress, `blocked` for attention, muted for to do. A glyph and text always carry the state too.

Narrow (side pane): the header collapses to plan and state, the list is one column (`◉ T7 mutation check · in progress`),
and selecting a row replaces the list with the inspector and a back row.

## View model (contract T1/T2 write into the living spec)

`workbench plan` prints `{schema_version: 1, slug, format, path, state, revision{digest, base, drift, authored_digest},
tickets[{id, delivers, blocked_by, layer, state, reason, pr, verified, attempts[{task_id, kind, status, started_at,
finished_at, assignment_id, address|null, claimed, latest}], gates[{scenario, phase, result, reason}]}],
outcome{state, items[{scenario, state, freshness, reason}]}}`. IDs are the plan's and the ledger's; nothing new is
minted. Unknown values are null. Errors print `{error: {kind, message, path?}}` on stderr with a non-zero exit.

## Out of scope

- Runtime event journal and lifecycle timestamps (handoff P4). This needs its own plan once a screen needs history.
- Evals, trends, cost and comparisons (P5). agent-eval needs a read side and the keyed state layout first.
- Remote-machine terminal links; run history and plan-revision comparison (no run id exists).
- A drawn DAG view. S20 decides whether it gets a plan.
- Any write from the UI: plan editing, decide, accept.
- yaml-eec T9–T11, which stay in their plan.

## Risks

- **Security:** The mod runs commands. It runs only `agent-orchestrator workbench …` with a fixed argv and no shell, in the space folder; plan text is data. The output carries no report or message bodies and no env values (S11, oracle).
- **Performance:** `plan.load` shells out to git per query; refresh is event-driven with one fetch in flight and a 30 s fallback. Data measured small (largest plan 12 tickets). No cache.
- **Migration:** No store or ledger migration. T2 changes what the driver writes for a missing runtime address. Old ledgers keep the fallback, and the reader maps it to null. Runtime SQLite is untouched.
- **Rollback:** Revert the PRs; uninstall the `herdr` component. Nothing else depends on the read commands. The old panel stays installed until T8, which runs only after acceptance and after yaml-eec is archived.
- **Concurrency with yaml-eec:** T7/T8 touch `driver.py`, so this plan is approved after they merge. T11 may stop discovering Markdown plans; the legacy rows then disappear, which is the intended end state.

## Seams

- `agent-orchestrator workbench … --json` as a subprocess (`cli` in `tests/fixtures.py`), driven through `AcceptanceFixture` with the fake runtime and gh for real ledgers.
- `workbench.py` pure functions (layers, ordering, address rule, freshness) as unit tests.
- `model.js` pure functions under `node --test`, run by `tests/test_workbench_mod.py`.
- `herdr-app check` and `herdr-app shot` for the mod (human evidence; `check` does not lay out async states).
- `install.sh` under `tests/installer/` with fake HOME.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | `workbench plans` and `workbench plan` graph, revision, drift, error kinds; `plans list` slug fix; living spec | S1–S6, S11 | none | `agent_orchestrator/workbench.py`, `driver.py`, `plan.py`, `tests/test_workbench.py`, `tests/test_discovery.py`, `docs/specs/agent-workbench.md` | regular | complex | agent | TO-DO |
| T2 | Attempts, the address rule and driver change, gates, outcome freshness; the outcome test | S7–S10, S21 | T1 | `workbench.py`, `driver.py`, `tests/test_workbench*.py`, `tests/test_agents.py`, spec | regular | complex | agent | TO-DO |
| T3 | Mod foundation: modules, plan list, layered list, inspector, empty and error states, narrow, refresh, node tests | S12–S15, S18 | T1 | `herdr/agent-workbench/`, `tests/test_workbench_mod.py`, spec | regular | complex | agent | TO-DO |
| T4 | Attempts and Open in the mod; Runtime section | S16, S17 | T2, T3 | `herdr/agent-workbench/`, `tests/test_workbench_mod.py`, spec | regular | complex | agent | TO-DO |
| T5 | `herdr` installer component and opt-in layout | S19 | T3 | `install.sh`, `scripts/`, `tests/installer/`, `herdr/layouts/`, installer spec | regular | simple | agent | TO-DO |
| T6 | Drawn-edge spike and its finding | S20 | T3 | `docs/plans/2026-11-11-agent-workbench.spike.md` | small | simple | agent | TO-DO |
| T7 | Visual acceptance in a real Herdr window | S22 | T4, T5 | `herdr/agent-workbench/` | | | felippe | TO-DO |
| T8 | Retire the old panel and layout; archive herdr-app-mods (after yaml-eec is archived) | S23 | T7 | herdr-app-mods `README.md` | | | felippe | TO-DO |

## Unknowns

- Whether `paint_path` can be positioned in a mod (T6 answers it).
- Whether hidden mods can tell they are hidden. If not, the 30 s fallback runs while hidden; that is cheap at measured sizes.
- yaml-eec T7 and T8 may add item states and receipt phases. The view model passes unknown states through as text rather than mapping them.
