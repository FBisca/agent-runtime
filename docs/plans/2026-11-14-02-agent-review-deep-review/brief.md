# Brief: Risk-adaptive deep review

Slug: `02-agent-review-deep-review`. Contract: `plan.yaml`. Lane: **Full** (multiple tickets / risk-tagged work).

## What changes
- **T1** (S1, S2, S3): Reuse existing agent-runtime spawning with read-only harness-capability enforcement and composed specialized instructions.
- **T2** (S4, S5, S6): Produce structured finding/disposition contracts and failure-aware deep-review results.

## Decisions
- The source of truth for the agreed behavior is `docs/specs/agent-review.md`, whose decision register records D01–D57.
  Do not re-grill approved decisions.
- This phase keeps the *discard* archive until it is reviewed; when it is approved it promotes into
  `docs/specs/agent-review.md` as P0 does, so its scenarios become requirements there.
- The signal this phase consumes is `(value, confidence, threshold)` from `01-agent-review-smart-screening` (P1-D5); a
  raw probability is never a calibrated confidence, and the deterministic safeguards (`2026-11-15-00c-agent-review-safeguards`)
  run before it, so a safeguard hit escalates without a screening request.
- Acceptance is human-observable today. S3 (a harness that cannot enforce read-only must fail safely) is a security
  surface and must become `check:` evidence before this plan is approved; that acceptance file is the first red step.

## Out of scope
One deep-review agent per escalation. The agent decides when to stop within a global timeout. No fix loop, no live orchestrator integration, no code modifications, no test execution.

## Risks
- Verification uses concrete demonstrations and inspectable reports; no known test command has been run against the proposed package.
- Cost and quality assertions must use actual metrics, not assumptions. No model or harness behavior is presumed.

## Hand-off
1. Inspect the current agent-runtime checkout and run `plan check docs/plans/2026-11-14-02-agent-review-deep-review/plan.yaml`.
2. Resolve any schema/check warnings and record the results. Draft runnable black-box acceptance checks at the agreed CLI seam where feasible.
3. Seek plan approval before invoking `/orchestrate`; do not silently build the next phase.
