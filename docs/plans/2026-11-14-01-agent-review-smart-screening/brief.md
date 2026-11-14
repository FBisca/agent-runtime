# Brief: Smart configurable screening

Slug: `01-agent-review-smart-screening`. Contract: `plan.yaml`. Lane: **Full** (three tickets, risk-tagged work).
Archive: **promote** into `docs/specs/agent-review.md`, as P0 did, so each scenario becomes a requirement there.

## What changed since this brief was approved

The plan was approved against an OpenAI chat model and an applicability pre-pass. The evidence in `2026-11-15-agent-review-jev-evidence/` — 25 real
changes, ~120 requests — moved four things, and this brief replaces the old one:

1. **Fan-out replaces the applicability pre-pass.** Batching changes an answer by ≤ 0.19, so one request per chunk
   carries the whole bank and code filters. S2 (applicability, with D10's fallback) and P1-D2's `1 + len(chunks)` are
   gone; `usage.requests == len(chunks)`.
2. **The chunk budget is tokens.** The limit in force is 32k per question / 64k per request by default and 262k with
   long context (§`2026-11-15-agent-review-jev-evidence/JEV.md`); `chunk.max_diff_chars` was the wrong unit.
3. **Escalation is a threshold *or* a confidence.** A score never abstains — with an empty state it still answers — so
   the confidence is the only abstention signal there is. A judgment escalates when its value is below its calibrated
   threshold, or its confidence below that question's band.
4. **Thresholds are calibrated, not guessed.** `2026-11-15-agent-review-jev-evidence/CALIBRATION.md` keeps 6 of 21 noul questions and 4 of 6 score
   questions; a question whose clean mean never moves cannot carry a threshold. Weights, thresholds and calibration are
   declared beside the bank. (D07: the model never chooses them.)

The old scenarios S9–S13 (output-directory refusal, retained usage, standing report, choice options, whole-number
usage) moved to `00b-agent-review-provider-contract`, which owns the report and refusal contract they describe.

## What changes

- **T1** (S1, S2): `packages/agent-review/` — repository questions, their levels and their safeguard paths extend the
  mandatory baseline; weights, per-question thresholds and calibration are declared beside the bank; a sensitive path,
  either side of a rename included, recommends full review with a reason naming the pattern and its source.
- **T2** (S3, S4): deterministic file/hunk chunks inside the token budget in force, one request per chunk with the whole
  bank, total coverage of the changed regions, and a recorded coverage gap when the change is wider than the
  cross-file limit.
- **T3** (S5, S6): escalation signals from calibrated thresholds and per-question confidence bands with full
  provenance; cost from a declared price table naming the model the provider served, unknown when unlisted.

## Decisions

The source of truth is `docs/specs/agent-review.md` (D01–D57, D58 open) plus the requirements `00b` will promote; do not
re-grill them. Decisions here:

- **P1-D1** Repository configuration is additive and refused where it is ambiguous: a question that repeats a baseline
  id is refused by name (D05), a score question without levels is refused by path (00b's S2), and an empty additions
  list is legal because it asks the baseline alone.
- **P1-D2** A sensitive path match is decided by the tool from the changed paths and both sides of a rename, never by the
  model (D07, D13).
- **P1-D3** Chunks are built from the diff's hunks in path order inside the token budget; `coverage.changed_regions ==
  coverage.covered` holds on the completed path, because a hunk no chunk carries is a defect, not a gap (D41).
- **P1-D4** A cross-file question that cannot see a file it needs records a coverage gap and recommends full review
  instead of reading a clean screen as complete (D42, D43).
- **P1-D5** Escalation is `value below the question's threshold` or `confidence below the question's band`, both read
  from calibration; a raw probability is never treated as a calibrated confidence (D06, D08, D27, D48). Advisory
  uncertainty is recorded and never escalates alone.
- **P1-D6** Cost is computed from a declared table against the served model, written with its basis, and unknown rather
  than zero for an unlisted model (D24, D26; D58 stays open).

## Out of scope

No deep-review agent is spawned: P1 emits signals for P2 to consume, and the deterministic safeguards are their own plan
(`handoff-02`, `2026-11-15-00c-agent-review-safeguards`) that lands beside this one. No JSON-RPC, no benchmark, no plan
review, no live orchestrator integration.

## Risks

- `state` (T1, T3): the bank digest, the calibration and the price table are what a frozen report is compared with, so a
  wrong number must appear as a reason in the report, never as a silent default.
- `external` (T3): thresholds and bands come from calibration on a reference set that is still small; the plan freezes
  them with the table and reports the set it came from.
- No check reaches a real provider: the fixture answers in the typed shape 00b established.

## Hand-off

1. `plan check` fails today on the missing `acceptance/test_smart_screening_cli.py`; that file is the first red step.
   The old 767-line version encodes the previous contract (`{value, confidence}`, `chunk.max_diff_chars`,
   `1 + len(chunks)`) and must be rewritten, never patched.
2. Build T1, then T2, then T3 (they share `packages/agent-review/`; T3 reads what T1 declares).
3. Freeze the bank, the calibration and the price table before the first measured run (D26).
