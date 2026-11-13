# AGENTS.md: agent-orchestrator

The repo root's `AGENTS.md` applies here too. This file adds the rules for the driver's **narration**: the lines a
person reads in the driver's pane while a plan runs.

## Narration

The ledger and `events.jsonl` are the record; narration is what a person watching the pane needs to follow the run
and spot the line that asks for them. Every narration line goes through
`narrate.say(kind, step, text, task=...)` (`src/agent_orchestrator/narrate.py`), which owns the format, the colours,
the collapsing of repeated waits and the shortening of paths and PR links. `narrate.py` holds the vocabulary: the
`KINDS`, the `STEPS` and the ledger gate to step map (`step_of`). A new kind or step goes there first.

Write each line in this tone:

- **Plain words for what happened, then what happens next**: `worker workbench-value-t1 is gone (runtime: exited)`,
  `waiting for CI on #208`, `could not put #208 back to draft (...); trying again next round`.
- **Subject first, no task id in the text.** Pass `task=` (a task id such as `t5r3`, or a ticket id such as `T5`);
  the unit, step and attempt columns carry them.
- **Person words, not ledger words.** The pane says `review`, `fix`, `CI green`, `Lead decided`. Gate names,
  assignment states and raw API values (`converged`, `fix-pending`, `UNKNOWN`) stay in the ledger and the log.
- **The kind matches the stakes.** `ask` is for a line someone must act on, and only that; `fail` means something
  broke; `warn` is a problem the driver works around; polling is `wait`, so it collapses.
- **Short.** Hand `say` the whole error; it cuts the text, and the ledger keeps all of it. Name a run file or a pull
  request as it is (`say` makes `t5r3/report.json` and `#208` of them).

Machine-read output (a refusal's JSON, a `--json` view) stays on its own path and is never narrated.
