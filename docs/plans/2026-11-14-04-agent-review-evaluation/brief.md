# Brief: Frozen historical evaluation

> **2026-11-17:** plan/1 no longer carries scenario evidence: the `evidence:` blocks and the plan folder's
> `acceptance/` files were removed. Each ticket's own tests are its evidence: a scenario it satisfies needs a
> covering test the ticket changes, mapped in its report's `covers`. Read any evidence or acceptance-file
> guidance below as the tests the implementing ticket writes.

Slug: `04-agent-review-evaluation`. Contract: `plan.yaml`. Lane: **Full** (two tickets, risk-tagged, an evidence
review). Archive: **discard** until reviewed.

## Why this phase is rewritten

This was `human:`-only evidence with a waiver, and it trailed P1–P3. The Jev evaluation reversed that: on 25 real
changes the screening gate did **not** hold. Calibrated on 12 cases it looked good (0 missed, 1 false); on 13 held-out
cases the same frozen thresholds gave **1 missed and 9 of 10 clean changes escalated**, and the best any threshold
achieves on the hold-out is 0 missed with 8 of 10 clean escalated. The reference set and its artefacts are in `2026-11-15-agent-review-jev-evidence/`
(`JEV.md`, `CALIBRATION.md`, `HOLDOUT.md`).

A gate that escalates 85% of changes saves ~15% of the deep-review budget while missing a third of known defects. This
phase exists to say that out loud with numbers, on a frozen set, before anything ships.

## What changes

- **T1** (S1, S2): replay a frozen reference set through the existing CLI, report per case the decision, the kind and
  the judgments behind it, name the calibration split, and refuse to measure when the bank, thresholds or calibration
  moved after the split was fixed.
- **T2** (S3, S4): decide acceptance on the **escalation action** — a missed known-bad case fails; the clean-change
  escalation rate is reported against a declared ceiling; cost and latency are reported per case from the provider's
  usage and the price table (measured: $0.0017 per review, against the ~$2 prior pilot figure).

## Decisions

- The source of truth is `docs/specs/agent-review.md` (D01–D57, D58 open) plus the requirements P0, `00b` and P1
  promote; do not re-grill them.
- **P4-D1** The criterion is the action, not a question's value: a gate is judged by which changes it sends to deep
  review. Per-question values are diagnostic data (D52), never the pass criterion (D23, D25).
- **P4-D2** The split is fixed before any threshold is chosen, and the run records the digests it froze: bank,
  thresholds, calibration, price table (D26).
- **P4-D3** The reference set is grown before any threshold ships: a clean-change escalation rate measured on 5 or 15
  clean changes cannot support a claim. The plan carries the set it used and its size in the report.
- **P4-D4** Cost is a first-class result: a screening that costs what a deep review costs is a failure even at zero
  misses (D24).
- **P4-D5** An `always-deep-review` comparator is still out (D24): the numbers are compared against verified historical
  labels only.

## Out of scope

New review functionality, live shadow reviews, plan review, automatic tuning, production rollout, and any claim of
savings against an unmeasured always-deep-review baseline.

## Risks

- `state` (T1): the frozen digests are what make two runs comparable; a silent change to the bank must refuse, not
  re-measure.
- `external` (T2): the reference set is small and its clean labels mean "no reviewer raised a blocker", which is weaker
  than "verified clean" (D22). The plan reports the set and its provenance, and does not extrapolate prevalence (D57).

## Hand-off

1. `plan check` fails today on the missing `acceptance/test_agent_review_benchmark.py`; that file is the first red step.
2. Grow the reference set to 20–30 with verified-clean changes before freezing any threshold; `2026-11-15-agent-review-jev-evidence/calibration.json` is
   the shape the report should emit.
3. Report the hold-out result in the plan's own evidence, including the failure mode that motivated the rewrite.
