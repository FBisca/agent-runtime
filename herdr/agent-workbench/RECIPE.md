# Agent workbench mod

One Herdr pane that shows a plan's ticket graph from the orchestrator's own loader and ledger. It runs only
`agent-orchestrator workbench plans|plan --json` (fixed argv, no shell, in the shown space's folder) and writes
nothing. Requirements: `docs/specs/agent-workbench.md` (R12 to R16).

## Where things live

| File | Holds | Runs under `node --test` |
|---|---|---|
| `model.js` | pure: reads an answer or its failure, rows, layers, chips, inspector, layout rule | yes |
| `data.js` | the fetch: one in flight, stale guard, flush, watch loop (`exec` is handed in) | yes, with a fake `exec` |
| `ui.js`, `views/` | draw what the model hands them, colours from `theme()` | no: they import Herdr |
| `main.js` | the `View`: follows the shown space, timers, the watcher | no |
| `test/` | `*.test.js`, one test per scenario named with its S-ID, and `fixtures.js` (answers as the CLI prints them) | |

The mod renders; it never recomputes. Layers, order, edges, verified and nulls are the orchestrator's. What the mod
decides is how an answer, or the lack of one, reads (`readPlans`, `notice`).

## Changing it

1. Write the test first in `test/` (name it `S<n> ...`), and see it fail.
2. Keep logic in `model.js` or `data.js`; a view only maps a model value to elements.
3. `uv run pytest tests/test_workbench_mod.py -m integration` runs the node tests from pytest. A missing `node` fails it.
4. `herdr-app check herdr/agent-workbench` loads the mod and renders it once. It does not lay out async states or
   narrow widths, so say what you saw with `herdr-app shot`, not that it passed.

## Rules

- Never call `writeTextFile`, a shell or any command but the two workbench verbs.
- An unknown value is shown as unknown (or its own text), never filled in; a failure is shown where it belongs
  (project or plan) with its way out; nothing is drawn when there is no data.
- Refresh: the state directory watcher marks the view dirty (flushed every `FLUSH_MS`), and a `FALLBACK_MS` timer
  fetches regardless. Never poll Herdr's live state: `render` already follows it.

## Try it

The mod is `place: "none"`: it appears only where a layout or `herdr-app do tab.new --mod agent-workbench` puts it.
