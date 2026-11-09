# Plan: The Brief Names the Task Id The Report Must Carry

Slug: `orchestrator-report-task-id`. Living spec: `docs/specs/orchestrator-engine.md`. Status: **APPROVED**
(the driver refused a worker's report over it; the Lead answered it and the fix landed here).

## Why

A worker writes `report.json` with a `task_id`, and the driver refuses a report whose `task_id` is not
the unit's own. The unit's brief names the report's path but never the `task_id` the report must carry,
while its heading carries the *plan's* ticket id (`T2`). A worker that reads its brief writes `T2`; the
unit is `t2`; the driver asks the Lead to decide, and the Lead answers "rewrite the report with the id
the brief gives you" — a round per ticket, for a field the brief never named.

Seen in a real run: `report task_id 'T2' is not t2`, `report task_id 'T3' is not t3`.

## Sources

- `packages/agent-orchestrator/src/agent_orchestrator/driver.py` — `task_text`, the unit's brief; the
  retry and answer briefs already say `Report with task_id \`<id>\``.
- `packages/agent-orchestrator/src/agent_orchestrator/contracts.py` — `Report.task_id`, which the driver
  checks against the unit.
- `docs/specs/orchestrator-engine.md` — the requirements the engine holds.

## Decisions

| # | Choice | Rejected | Why | Status |
|---|---|---|---|---|
| D1 | The unit's brief names the `task_id` its report must carry, the way the retry and answer briefs already do. | Accepting the plan's ticket id as the task id. | The unit id is the driver's own; the plan's id is the plan's. Two names for one thing is what the worker guessed between. | Requested |
| D2 | The brief says which id, and the driver keeps refusing the wrong one. | The driver accepting any id for the unit. | The refusal is right; the brief was silent. | Requested |
| D3 | One line in the brief, not a new field in the packet. | Carrying the task id through the contract text. | The brief already carries the report's path; the id belongs beside it. | Requested |

## Scenarios

- **S1** WHEN a unit's brief is written, THEN it names the `task_id` its report must carry, so a worker
  that reads the brief writes the id the driver checks.

## Out of scope

- Renaming the plan's ticket ids. `T1` is the plan format's own; the unit id is the driver's.
- Accepting a report whose `task_id` is the plan's ticket id. The refusal stays.

## Risks

- **Security:** no change to what a worker may do; the brief names one more field.
- **Performance:** one more line in a brief.
- **Migration:** none; a brief is written per ticket.
- **Rollback:** one commit.

## Seams

- `Driver.task_text` — the unit's brief. The test reads the brief the run wrote.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The unit's brief names the `task_id` its report must carry, with a test that reads the brief a run wrote. | S1 | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_run.py, docs/specs/orchestrator-engine.md | small | simple | agent | TO-DO |
