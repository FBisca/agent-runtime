# Plan: a silent idle is not a finished agent

Contract: [`2026-11-10-orchestrator-silent-idle.plan.yaml`](2026-11-10-orchestrator-silent-idle.plan.yaml) (scenarios,
tickets, evidence; validated by `agent-orchestrator plan check`). Oracles:
[`2026-11-10-orchestrator-silent-idle.oracle/`](2026-11-10-orchestrator-silent-idle.oracle/). This file is the
narrative: why, sources, decisions, risks. It is not covered by the approval digest.

Slug: `orchestrator-silent-idle`. Living spec: `docs/specs/orchestrator-engine.md`. Status: ARCHIVED 2026-11-10.

## Why now

Issue #94: on run `investor-alts-access` (2026-11-10) the driver escalated `review.json: cannot read (No such file or
directory)` twice on one unit while the reviewer was still working. The reviewer had ended its turn to wait on a queued
integration spec run; Claude Code reports that as `idle`. `check_ended` turns any `idle`/`done` into `exited`, the
reviewer branch marks the gate `reviewed`, and the gate reads a file not yet written. On main the #74 payload path now
resumes the reviewer twice first, with a message that falsely says its report reached the driver, and the resume
replaces its assignment id, so the reviewer's real `complete` later matches no gate. Workers and answerers share the
same misreading: a worker idle on a background command is asked about at once.

## Sources

| Source | What it settles |
|---|---|
| [Issue #94](https://github.com/FBisca/agent-runtime/issues/94) | Symptom, cause chain, three candidate fixes, Lead workaround |
| `packages/agent-orchestrator/src/agent_orchestrator/driver.py` @ `759dfc3` | `check_ended` ~1434 (idle/done → exited); `on_agent_attention` ~1459–1520 (reviewer exited → reviewed; worker exited → request unless `report_landed`); `receive` ~1419 (reviewer complete → reviewed); `read_payload`/`warn_sender` ~1826–1925 (5 s grace, 2 warnings, assignment rewrite ~1894); `idle_ticks` ~1306–1379 (timed, two unchanged ticks → `idle` request); `review_text` ~2196–2245 |
| `packages/agent-runtime/src/agent_runtime/dispatch_wait.py:19–120` | The runtime cannot tell "turn ended, background job pending" from done; failure states are distinct from turn-ended |
| `docs/specs/orchestrator-engine.md` R15, R18 | Awaiting-merge is never idle; payload race: bounded grace, warn the sender twice, a gone sender goes to the Lead |
| `docs/plans/2026-11-09-orchestrator-payload-race.plan.md`, `2026-11-09-orchestrator-lead-alerts.plan.md`, commit `00775c4` (#83) | The payload-race design this plan must keep; the idle tick leaves a runtime-working agent alone |
| Tests: `test_wake.py:111`, `test_gates.py:51,393,409`, `test_pr.py:377–430`, `test_run.py:427` | The GateFixture seam; `test_wake.py:111` encodes the behaviour #94 objects to |
| Run logs `~/.local/state/orchestrate/*/events.jsonl` (gatherer, read-only) | 8 `contract-error where=review` across 13 runs (6 "No such file"); 5 `payload-warning where=review`. The incident's drive was probably older than #74 (unverified) |
| `AGENTS.md` | Fix the class; red first; classify tests by behaviour |
| Grill, delegated to Codex `gpt-6.1-sol` (2026-11-10) | Q0–Q11, recorded as D0–D11 |

## Decisions

| # | Choice | Rejected | Why | Source |
|---|---|---|---|---|
| D0 | The driver reinterprets the states it already gets | A runtime API for pending background jobs | Turn-end and failure are already distinct; no new signal needed for this bug | `dispatch_wait.py:19,36`; Grill Q0 |
| D1 | A live reviewer advances only on its matching `complete`; idle/done keeps it `reviewing`, even with a valid file on disk | Option 1 (short grace or liveness check), option 2 (warn on idle), accepting the file alone | An ended turn does not establish a finished review; a warning resume replaces the assignment and loses the real report | `driver.py:1419,1434,1518`; Grill Q1 |
| D2 | A silent agent is bounded by the existing monotonic `--idle-minutes` watchdog (default 10; baseline + two unchanged ticks); no new knob | Loop-iteration counts; a new grace flag | Loop iterations burn in seconds; the watchdog already exists and is timed | `driver.py:1302–1341`; Grill Q2 |
| D3 | Fix the class now: workers and answerers get the same idle classification; the worker `report_landed` shortcut stays | Reviewer only with follow-up issues; removing the shortcut | Same misreading; the shortcut is a supported recovery | `AGENTS.md`; `driver.py:1503`; Grill Q3 |
| D4 | Real failures stay immediate: `failed`/exited/gone agents, `unreported`/`error`, `blocked`/`decision`, restart orphans. The deferral lives where observations are classified, not in `on_agent_attention("exited")` | Deferring every `exited` | A dead agent cannot finish its background job | `dispatch_wait.py:37`; `driver.py:4100`; `test_run.py:427`; Grill Q4 |
| D5 | Recovery text becomes neutral ("The driver cannot read <path>: <reason>. Write or repair that file, then report complete under this assignment."); a genuine recovery resume still issues a new assignment; a silent idle issues none | Keeping the false "your report reached the driver"; accepting superseded assignment ids | Fixes the lost `complete` without weakening assignment ownership | `driver.py:1876,1894`; Grill Q5 |
| D6 | The reviewer brief says background waits are allowed, ending a turn does not finish the review, and the order is: jobs finish → review file → `complete` with the brief's assignment id | Driver-only change; banning background jobs | Matches how the harness behaves; keeps the report protocol | `driver.py:2230–2241`; Grill Q6 |
| D7 | A reviewer `complete` after the gate went `requested` stays Lead-owned; no replay | Auto-closing the request; reusing the worker held-message replay | Different ownership and replay contract | `driver.py:1726,1737`; Grill Q7 |
| D8 | The Lead's request is `event: idle`, one decision wake, naming the agent, the expected file, the inactivity and `decide <plan> <unit> retry \| answer \| skip` | A missing-file contract error; a new request kind | The Lead sees what is actually true and the way out | `driver.py:1330,1613`; Grill Q8 |
| D9 | Rewrite `test_wake.py:111` and the worker immediate-idle tests (`test_wake.py:100`, `test_decider.py:107`, `test_report_timing.py:65`) to the new contract; keep `test_report_timing.py:52` and every R18, dead-sender, orphan and awaiting-merge regression; timed behaviour through `Driver.clock`, no sleeps | Keeping the two-resume assertion | The old test encodes the defect | Grill Q9 |
| D10 | Outcome S1 and S4, backed by a new GateFixture oracle (`baseline: fail`), risk `state, concurrency`, no waiver | Selecting existing tests (they assert the opposite); human evidence | An executable regression at the existing seam; the red was reproduced at base | `test_gates.py:51`; yaml-eec oracle ladder; Grill Q10 |
| D11 | One regular/complex ticket (~450–650 lines), exact file fences | Separate classifier and regression tickets; reviewer patch plus role follow-ups | The shared classification and its role regressions must land together | Grill Q11 |

## Out of scope

- Runtime telemetry for background jobs, heavy-job queue integration.
- Changing idle thresholds or what goes into the idle signature.
- Accepting superseded assignment ids in general.
- Replaying a reviewer's late `complete` after the Lead was asked (D7).
- Restarting or patching drives already running an older driver.

## Risks

- **Security:** covered. Assignment ownership stays exact; file, schema and commit validation unchanged; no new paths or privileges.
- **Performance:** covered. A silent idle no longer enters the 5 s payload wait; no new status probe per observation.
- **Migration:** n/a. No ledger or schema change; a unit already `requested` needs the usual Lead decision; in-memory idle clocks reset on restart as today.
- **Rollback:** covered. Revert driver, brief and tests together; no data to migrate. The promoted oracle stays as the regression.
- **Residual:** a background job longer than the idle horizon (~20–30 min at default) still reaches the Lead, now as an honest `idle` request.

## Seams

- `GateFixture` (`packages/agent-orchestrator/tests/test_gates.py:51`) with the in-process fake runtime: scripted `wait` steps, `assignment(name, "working", "idle")`, `calls("resume")`, `events(...)`, `request()`.
- `Driver.clock` for timed idle ticks (`test_pr.py:418` pattern); `--idle-minutes 0` for tick-per-loop tests.

## Baseline of this plan's oracle (run by hand, 2026-11-10, at `759dfc3`)

`test_reviewer_idle_outcome.py.oracle`, placed at `packages/agent-orchestrator/tests/`, run twice:

| Test | Base result |
|---|---|
| `test_a_reviewer_idle_on_a_background_job_completes_when_it_reports` (S1) | FAIL on assertion: the idle reviewer was resumed |
| `test_a_reviewer_that_never_reports_is_asked_about_by_the_idle_watchdog` (S4) | FAIL on assertion: the idle reviewer was resumed |
| `test_control_a_reviewer_that_reports_at_once_completes_the_unit` | PASS |
| `test_control_a_dead_reviewer_without_a_review_goes_straight_to_the_lead` | PASS |

A file-without-report case (S2) was dropped from the oracle: at base the driver never finishes that run, and the hung
thread leaks into the next test. S2 is covered by T1's own tests.
