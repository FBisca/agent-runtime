# Agent workbench mod

One Herdr pane that opens on home (the week's cards, what needs the person, the active plans) and shows a plan's ticket
graph from the orchestrator's own loader and ledger. It runs only
`agent-orchestrator workbench plans|plan|totals --json` (fixed argv, no shell, in the shown space's folder) and writes
nothing. Requirements: `docs/specs/agent-workbench.md`.

## Where things live

| File | Holds | Runs under `node --test` |
|---|---|---|
| `model.js` | pure: reads an answer or its failure, rows, layers, chips, inspector, layout rule | yes |
| `data.js` | the fetch: one in flight, stale guard, flush, watch loop (`exec` is handed in) | yes, with a fake `exec` |
| `ui.js`, `views/` | draw what the model hands them, colours from `theme()` | no: they import Herdr |
| `main.js` | the `View`: follows the shown space, timers, the watcher | no |
| `test/` | `*.test.js`, one test per scenario named with its S-ID, and `fixtures.js` (answers as the CLI prints them) | |
| `acceptance/` | the promoted plan acceptance tests (`*.test.js`); the pytest runner runs them beside `test/` | yes |

The mod renders; it never recomputes. Layers, order, edges, verified and nulls are the orchestrator's. What the mod
decides is how an answer, or the lack of one, reads (`readPlans`, `notice`).

## Changing it

1. Write the test first in `test/` (name it `S<n> ...`), and see it fail.
2. Keep logic in `model.js` or `data.js`; a view only maps a model value to elements.
3. `uv run pytest tests/test_workbench_mod.py -m integration` runs the node tests from pytest. A missing `node` fails it.
4. `herdr-app check herdr/agent-workbench` loads the mod and renders it once. It does not lay out async states or
   narrow widths, so say what you saw with `herdr-app shot`, not that it passed.

## Rules

- The mod opens on home (`initialNav`, `homeView`), never on a plan picker: the week's five cards from `workbench totals` (`cardsOf`: merged, first pass, escalations, tokens, ticket time; an unmeasured value reads `unknown`, tokens note how many tickets were measured, and no totals answer draws no cards), Needs me as one row per plan holding its items (each with its way to act), the active plans in the order the command printed with their workers, the PRs awaiting merge, one collapsed "N older" line. A plan row, a card or a PR row opens over home (`navigate`); ← returns home. The totals are fetched only while home or a dashboard shows; a plan open fetches the plan alone.
- A plan's workers on home come from the plan's own `agents` and join Herdr's terminals only by exact address, as a ticket's do.
- A selected plan with `metrics` shows wall time, first-pass rate and driver errors in its header, a Measures section in the inspector and, at wide widths, a timeline (`timelineOf`). What the orchestrator did not measure reads `unknown`, never 0.
- An open plan's header is a way back (←), the plan's name as the title and its whole goal: wrapped fully when wide, in at most three lines when narrow (`planHeader`). The repository's name is not shown. Each ticket row names its agents by the ledger's name and role (`ticketWorkers`): `live` with Herdr's status and an Open at the exact address, `not live` when no live terminal has it, `unknown` with no address. There is no Runtime section.
- Never call `writeTextFile`, a shell or any command but the three workbench verbs. The Herdr commands it runs are
  `agent.focus` (Open) and the panel button's `layout.select`, `layout.show` and `layout.hide`; an attempt joins a terminal by exact address, never by name, and a remote space joins nothing.
- An unknown value is shown as unknown (or its own text), never filled in; a failure is shown where it belongs
  (project or plan) with its way out; nothing is drawn when there is no data.
- Refresh: the state directory watcher marks the view dirty (flushed every `FLUSH_MS`), and a `FALLBACK_MS` timer
  fetches regardless. Never poll Herdr's live state: `render` already follows it.

## The tab bar button

Herdr's tabs mod draws Files and Changes only, so the workbench layout puts this mod beside it with props
`{ toggle: true }`: that instance draws the Workbench button (`views/toggle.js`, logic in `panelToggle` and
`panelsWith`) and fetches nothing.

## Try it

The mod is `place: "none"`: it appears only where a layout or `herdr-app do tab.new --mod agent-workbench` puts it.
