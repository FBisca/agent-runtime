# Brief: Protocol and reliability

Slug: `03-agent-review-protocol-reliability`. Contract: `plan.yaml`. Lane: **Full** (protocol + persistence integration).

## What changes
- **T1** (S1–S3): Implement optional JSON-RPC 2.0 over stdio with meaningful progress milestones, consistent CLI execution/verdict semantics, caller-selected review directories, optional JSONL events, and minimal lifecycle persistence with atomic final report.

## Decisions
- Follow the decision register in `docs/specs/agent-review.md` (D01–D57); do not re-grill approved decisions.
- This phase keeps the *discard* archive until it is reviewed; when it is approved it promotes into
  `docs/specs/agent-review.md` as P0 does, so its scenarios become requirements there.
- Use existing review engine; no second review implementation or live driver integration.
- Failures remain explicit and incomplete; no automatic retries or resume support.
- Acceptance is human-observable in this draft, but the seam it needs exists (P0's CLI acceptance and P1's
  contract-first file), so the first step of this phase is to turn S1-S3 into `check:` evidence rather than to wait
  for a seam. Only the persisted judgment shape changes: a judgment now carries a value and, where the provider gives
  one, a confidence.

## Out of scope
Historical benchmark curation/scoring (P4), live orchestrator integration, resumable execution, event processing infrastructure, plan-review support.

## Risks
- stdio framing, clean separation of protocol data from diagnostics, and partial-run durability need black-box tests.
- Do not claim the proposed interface was validated before implementation.

## Hand-off
1. Inspect the target checkout; run `plan check docs/plans/2026-11-14-03-agent-review-protocol-reliability/plan.yaml`.
2. Add executable acceptance checks when the CLI/RPC seam exists; record actual results.
3. Seek approval before implementing; do not start P4 implicitly.
