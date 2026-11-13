# Plan: a workbench worth opening

Slug: `workbench-value`. Contract: `2026-11-12-workbench-value.plan.yaml`. Lane: fast (see Delivery).

## Why now

The workbench lists 54 rows for this repository and the person cares about four. 13 rows are the Markdown twin of a
YAML plan, and 11 of those twins show a state the ledger contradicts (yaml-eec DRAFT beside ARCHIVED, plan-layout
DRAFT beside RUNNING). 3 of the 4 rows that fail to load are archived plans whose oracle was already promoted. It shows
no metric: 30 run directories hold 21,060 timestamped events, but `metrics.json` is written only when a run ends, and
nothing the workbench prints reads them. Nothing says what waits on the person. The side panel stops at 900 pixels.

## Sources

| Source | What it settles |
|---|---|
| The request and the grill (this session, Q1 to Q13) | The four changes; every decision below marked "grill" |
| `docs/specs/agent-workbench.md` | R2 and R12 (Markdown listed as legacy), R7 (writes nothing, no bodies, null not 0), R15 (two read verbs), R19 (panel button commands) |
| `docs/plans/2026-11-11-agent-workbench.plan.md` | D9 logic in Python, the mod renders; D14 Markdown listed legacy (reversed here for twins); run history and UI writes out of scope |
| `docs/plans/2026-11-12-plan-layout.plan.yaml` | Folder plans; it edits `plan.py`, `driver.py`, `workbench.py`; promote and discard delete the plan folder |
| `docs/plans/2026-11-12-worker-usage.plan.md` | Token usage reaches assignments and `attempts.jsonl`; until it lands every usage is unknown |
| `herdr/layouts/workbench.json`, `herdr-app commands` | The `main` and `panel` nodes; `layout.hide`, `layout.show`, `layout.resize {px|weight}`; `max: 900` caps a weight; `layout.set` cannot change a shipped layout's structure |
| A live probe (this session) | Hiding `main` and resizing the panel to weight 1 grows the workbench and the mod reflows; the cap left about 260 pixels empty |
| `~/.local/state/orchestrate/` (gather, read-only) | Event kinds and shapes, counts below |

Counts from the gather: 13 YAML plans, 41 Markdown (13 twins, 28 alone); 30 run directories, 28 with events; 146
failed CI polls are 28 distinct failures; 9,736 of 9,850 idle events are driver ticks; 202 escalations, 187 decisions;
16 error events, free text only; 830 deferred items with no stable id.

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D1 | One plan: list cleanup, a read-only Needs me, per-plan metrics and timeline, expand | The deferred backlog (dismiss, send to /start-planning) in this plan | The backlog needs a write path, an R7 amendment, stable item ids and a new allowed command; a plan of its own | grill Q1 |
| D2 | `workbench plans` drops a `.plan.md` whose stem has a `.plan.yaml`; a folder plan lists `plan.yaml` only | Filtering in the mod | Logic lives in Python (agent-workbench D9) | grill Q2 |
| D3 | Each entry carries `group`: active (RUNNING, DELIVERED, VERIFYING, APPROVED never driven, DRAFT, load error with no or non-final ledger) or older (ARCHIVED, ACCEPTED*, REJECTED, load error with a final ledger, Markdown alone) | A DRAFT age cutoff; listing load errors as active | No YAML DRAFT exists besides the one in progress; 3 of 4 load errors are archived plans | grill Q3, Q4 |
| D4 | `last_activity`: newest `at` in `events.jsonl`, else the plan file's mtime, else null; order active first, newest first, null last | Ledger `plan.history` | 44 of 56 ledgers have no plan state or history; every event has `at` | grill Q5 |
| D5 | Top-level `needs_me: [{plan, ticket, kind}]` for active plans: `needs_you`, `unverified`, `driver_gone`, `error`, `ready_to_approve`, `ready_to_drive`, `fix_plan`, `lead_pending`; no reason text | Idle events; printing escalation reasons | Idle is driver ticks; reasons quote reports and paths (R7) | grill Q6 |
| D6 | `driver_gone`: RUNNING, the last `start` event's pid raises `ProcessLookupError` on signal 0, no later `finish` | Event age alone | A crashed driver (deferred gaps a and e) is what the person must restart; this machine only | grill Q6 |
| D7 | `workbench plan --json` gains `run_dir` and `metrics` (null when the run directory does not exist), computed read-only from `events.jsonl` and `attempts.jsonl` | A new verb; running `metrics` (it writes `metrics.json`) | One fetch per plan; R7 | grill Q7 |
| D8 | Metrics: `wall_seconds` sums each drive from `start` to its `finish` or last event; phases from gate transitions (work: first RESERVED to `reported`; review: `reviewing` to `reviewed`; CI: `ready-for-ci` to `awaiting-merge`), an open phase has a null end and duration; `fix_rounds` counts the unit's `r<N>` tasks; `first_pass_rate` over tickets with events; `ci_failures` counts distinct task, PR and gate; `errors.by_gate` and `errors.last` cut to 120 characters; `tokens` null until worker-usage | File span for wall time; counting CI polls; classifying error text | The driver is sometimes down for an hour; polls repeat 5 to 10 times; error events carry free text only | grill Q8 |
| D9 | `metrics.timeline`: per ticket id, `[{phase, start, end}]` in phase order; the mod draws it at the wide layout | A separate timeline verb | Same data, one fetch | grill Q9 |
| D10 | Cross-run trends out of scope | Building them now | They need worker-usage | grill Q10 |
| D11 | Expand: `panelExpand(root, restorePx = 520)` in `model.js` returns null or `{expanded, commands}`; expanded means `main` hidden. Expand: hide `main`, resize panel weight 1. Collapse: show `main`, resize panel to the px the mod remembered. `panelToggle(root, restorePx)` closing while expanded shows `main` and restores the width first. The shipped panel drops `max`; the mod may run `layout.resize` | Hiding the sidebar; `layout.set` to lift the cap; a tab or overlay | The sidebar's agents list stays useful; a shipped layout refuses structural `layout.set` | grill Q11, probe |
| D12 | Home: Needs me on top, then the active plans, then one collapsed "N older" line | A separate Needs me tab | One screen | grill Q12 |
| D13 | Approved now, driven after `plan-layout` archives; T1 works over `discover()` output | Driving in parallel with plan-layout | Both edit `workbench.py`; folder plans must list right | grill Q13 |
| D14 | Scenario ids continue from S24 | Restarting at S1 | The spec and the mod's node tests already use S1 to S18 and S24 | spec coverage table |

## Out of scope

The deferred and gaps backlog with dismiss and "send to /start-planning" (next plan). Cross-run trends, cost and token
views (after worker-usage). Classifying driver error text. Any write from the workbench. Herdr notifications and link
handlers.

## Risks

- **Security:** new output reads events whose free text quotes reports and paths; only counts, kinds, ids, times and
  the 120-character driver error are printed (S28 asserts no escalation text). Covered.
- **Performance:** `workbench plans` now reads each active plan's `events.jsonl` (the largest is about 4 MB); it reads
  the last line for `last_activity` and full files only for `workbench plan`. Covered by T1 and T2 tests; a slow read
  shows as the mod's existing timeout state (R13).
- **Migration:** existing installs keep `max: 900` until the installer runs again (it copies the layout and keeps an
  edited copy aside). n/a beyond a reinstall.
- **Rollback:** every change is additive JSON fields, a mod view and one layout key; reverting the PRs restores R2,
  R12 and R19. Covered.

## Seams

- `agent-orchestrator workbench plans|plan --json`, in process through `WorkbenchFixture` (`test_workbench.py`), for
  S25 to S29 and S34.
- The mod's pure `model.js` under `node --test`, run from `tests/test_workbench_mod.py`, for S30 to S33.

## Delivery

Fast lane, as the person asked: five tickets, T1, T2 and T5 in parallel; one oracle file per seam; no Linear issues;
no oracle review; the drive's gate is the fast suite plus the workbench tests. The person authorized Claude, in this
session, to merge each of this plan's PRs once its checks are green.
