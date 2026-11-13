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
| `ui.js`, `views/` | draw what the model hands them: state colours and neutrals from `theme()`, data colours from the fixed palette in `ui.js` | no: they import Herdr |
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

- The mod opens on home (`initialNav`, `homeView`), never on a plan picker, under the same header as a plan and a dashboard (the project, Expand and ↻). Home is the week's five cards from `workbench totals` (`cardsOf`: merged, first pass, escalations, tokens, ticket time; an unmeasured value reads `unknown`, tokens note how many tickets were measured, and no totals answer draws no cards), each carrying a small picture of its dashboard (`picture`: the week's columns, the first-pass ring, the escalation causes, measured and unmeasured tickets, the step medians; a card with no data has none, never a zero one); Needs me as one compact row per item with its glyph and action (`lead_pending` ⚑ Answer, `needs_you` ? Answer, `fix_plan` ⚠ Fix plan, `ready_to_drive` ▶ Drive, any other • Open); the active plans as cards in the order the command printed, each with its progress, how recently it moved (`recency`, read against `now`), its live agents, the count of the others folded into one line and its PRs to merge as `#n` chips that open GitHub only when `prLink` accepts them; one collapsed "N older" line. A plan card, a card or a Needs me action opens over home (`navigate`); ← returns home. The totals are fetched only while home or a dashboard shows; a plan open fetches the plan alone.
- A card's dashboard (`dashboardView(card, totals, layout, filter)`) is the headline (value, `▲ n% vs last week`, `▼`, or `first week measured` when the change is null or absent, and the week's sparkline), the charts the answer has a section for (no section, no chart, never a zero one), two columns when wide and one when narrow with shorter charts (`chartHeight`), and the rows behind them. Every scaled chart carries an `axis` of 0, half and a round top (1, 2 or 5 times a power of ten, `axisFor`) that covers its data; the ring and the heatmap have none. The mod computes none of it: the series, the histogram, p90, plan × cause and the change are the CLI's; the model only arranges them.
- A click on a day, a reason or a heatmap cell is a filter in the navigation (`navigate(nav, {type: "filter", filter})`): it keeps the rows the answer holds for it, dims the rest, the same click clears it and back clears it. It fetches nothing and writes nothing.
- Rows read for a person: a plan-level row names its unit (`alpha archive`), never `null`; a time reads against the window's end (`5m ago`, `3h ago`, then the day); a PR reads `#n`, and its chip opens only when `prLink` accepts the URL.
- Data colours are the mod's own fixed palette in `ui.js` (aqua, violet, magenta, green, in that order), over theme colours: the theme has no categorical slots, and its pastels failed the colour-vision checks on the dark surface. The model hands the views a slot (`stepLook`, `reasonLook`, a plan's rank), never a colour, and a reason or a step keeps its slot whatever order it arrives in. The state colours (`done`, `working`, `blocked`) colour states only, in no chart.
- A plan's workers on home come from the plan's own `agents` and join Herdr's terminals only by exact address, as a ticket's do.
- An open plan draws, from its own `workbench plan` answer alone (it fetches no totals), five cards (`planCards`: progress as done of total, first pass, wall time, escalations, tokens with how many tickets were measured, each with its picture; what the metrics do not carry reads `unknown`, never 0), a timeline (`planTimeline`: every ticket's phases on the plan's own clock with each merge marked when wide, each ticket's phase split when narrow) and its tickets as cards by layer (`cardLayers`, `ticketCard`: rounds or first pass, findings, time, tokens and model; the phase split; what it waits on; its live agents and the count of the others; its PR as `#n`). A ticket with no metrics shows no measures and no split. A card selects its ticket for the inspector, which sits beside the cards when wide (`planColumns` says how many cards and ticket cards fit the width it leaves) and is a drill-in when narrow. The inspector keeps its Measures section. The colours of the steps are the data palette's slots; the progress ring and a ticket's stripe are states and use the state colours.
- An open plan's header is a way back (←), the plan's name as the title and its whole goal: wrapped fully when wide, in at most three lines when narrow (`planHeader`), with the plan's wall time, first-pass rate and driver errors on one line. The repository's name is not shown. Each ticket names its agents by the ledger's name and role (`ticketWorkers`): `live` with Herdr's status and an Open at the exact address, `not live` when no live terminal has it, `unknown` with no address; a ticket card lists the live ones and counts the rest. There is no Runtime section.
- Never call `writeTextFile`, a shell or any command but the three workbench verbs. The Herdr commands it runs are
  `agent.focus` (Open) and the panel button's `layout.select`, `layout.show` and `layout.hide`; an attempt joins a terminal by exact address, never by name, and a remote space joins nothing. The one other call is the window's `open_url`, for a PR chip: only a URL `prLink` accepts (`https://github.com/<owner>/<repo>/pull/<n>`, nothing after it), checked again where it is called.
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
