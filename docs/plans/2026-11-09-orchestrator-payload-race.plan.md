# Plan: A Report Waits For the File It Names, and the Agent That Sent It Is Told

Slug: `orchestrator-payload-race`. Living spec: `docs/specs/orchestrator-engine.md`. Status: **APPROVED**
(the owner approved it mid-run, after the engine lost a passing review to the race).

## Why

A worker's report wakes the driver; the file the report names is the payload. On 2026-11-09 a reviewer
reported at 13:38:35 and wrote its `review.json` — verdict **pass** — at 13:40:44. The driver read the
path in between, found nothing, and raised a request for the Lead. The file landed two minutes later,
and the reviewer's next report was answered with `matches no open gate`: the verdict was on disk and
no part of the run could read it. The unit was stuck, and the same race can hit every review gate.

## Sources

- `packages/agent-orchestrator/src/agent_orchestrator/driver.py` — `step_reviewed`, `step_reported`,
  `contract_error`, `note_agent` and the gate table.
- `docs/specs/orchestrator-engine.md` — R1–R15.
- The run that lost the review: `~/.local/state/orchestrate/test-suite/` (`t6`), and its file times.

## Decisions

| # | Choice | Rejected | Why | Status |
|---|---|---|---|---|
| D1 | The driver waits a bounded grace (5 s) for a payload a report announced. | Reading once and calling it missing. | A worker reports when its turn ends; a large file lands a moment later. | Requested |
| D2 | A payload still missing after the grace is the sending agent's news: it is told to write the file, and the gate waits. | Asking the Lead for every missing file. | The Lead cannot write the file; the agent that owes it can. | Requested |
| D3 | The warning is capped: two per file, then the Lead is asked, and the reason names the warnings. | Warning until the file appears. | An agent that never answers must not become a run that never ends. | Requested |
| D4 | An agent that is gone is not warned; the Lead is asked at once. | Spawning an agent to warn it. | Warning a dead pane buys nothing, and a fresh agent is not the sender. | Requested |
| D5 | The wait and the warning are one seam, `payload_waits` + `Driver.await_payload`, shared by the review and the report gates. | A copy in each gate. | The race is the same wherever a report names a file. | Requested |

## Scenarios

- **S1** WHEN a file lands inside the grace after the report that named it, THEN the driver reads it and nobody is warned.
- **S2** WHEN a file never lands and the agent that announced it is still there, THEN that agent is warned, once per report, at most twice, and the gate keeps waiting.
- **S3** WHEN a file never lands and no agent is left to write it, THEN the request goes straight to the Lead, naming that.

## Out of scope

- A retry of a unit whose file arrived after the request was raised. The run the race hit was finished by hand.
- The reviewer's prose. `review.md` is never read by the driver.

## Risks

- **Security:** none; the change reads paths the driver already names.
- **Performance:** a missing payload costs the grace (5 s, once per gate), which is the price of not losing a review.
- **Migration:** none; the ledger gains a `payload_warnings` map, written only when a warning is sent.
- **Rollback:** one commit; removing it restores "missing now means missing" for both gates.

## Seams

- `payload_waits(path, grace)` — the bounded wait, unit-tested with no repository.
- `Driver.await_payload(task_id, where, path)` — the gate behaviour, driven by the existing fake runtime.

## Tickets

| # | What it delivers | Satisfies | Blocked by | Paths | Size | Difficulty | Owner | Status |
|---|---|---|---|---|---|---|---|---|
| T1 | The two gates wait for their payload, warn the sending agent twice at most, and only then ask the Lead. | S1, S2, S3 | none | packages/agent-orchestrator/src/agent_orchestrator/driver.py, packages/agent-orchestrator/tests/test_gates.py, packages/agent-orchestrator/tests/test_payload.py, packages/agent-orchestrator/tests/test_wake.py, packages/agent-orchestrator/tests/test_decider.py, packages/agent-orchestrator/tests/fixtures.py, docs/specs/orchestrator-engine.md | regular | complex | agent | COMPLETE 2026-11-09 |
