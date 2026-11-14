# A score's ladder bounds its answer; it does not snap it to a rung

## Why

`00b-agent-review-provider-contract` gave a score question its levels and made a judgment refuse a
level the ladder does not declare. The implementation reads a level as a *rung*: a five-level ladder
declares 0..4 and anything between them names no level, so 1.22 is refused.

The provider does not answer in rungs. The measured contract
(`docs/plans/2026-11-15-agent-review-jev-evidence/JEV.md`) records fractional scores on the same 0–4
axis — `hidden-state` 1.22, `error-handling` 0.29, 1.83 with richer criteria, per-question sd 0.02–0.07.
So the rule as shipped refuses every real score answer, and any configuration with a score question
ends `incomplete` rather than screened. The plan that shipped it pinned the refusal deliberately
(its brief: "0.5 is inside 0–1 and outside a five-level ladder"), because that plan's contract was
about the judgment's *shape*, not about the score axis. This plan corrects the axis.

## What changes

A score answer is bounded by the ladder, not snapped to it:

- **inside** — a finite number from 0 up to (number of declared levels − 1), fractional or not, is
  the provider's own answer and travels into the judgment unchanged;
- **outside** — anything below 0 or above the last rung is still refused, naming the question and the
  range the ladder allows, so the run ends `incomplete` instead of screened;
- a noul's probability and a choice's option are unchanged, and so is the confidence check.

The provider's answer stays native: the judgment's `value` is what the provider sent. Mapping a score
to a level's *label* is the ladder's own reading and the report's business, not a silent snap here.

## Decisions

- **01b-D1**: the ladder's bounds are `0..len(levels)-1`; inside that range the value is carried as
  given, fractional included. A score is a position on the ladder, not a rung index.
- **01b-D2**: no new judgment field. `value` stays the provider's number; adding a snapped `level`
  would reintroduce the reading this plan removes.
- **01b-D3**: the confidence rules from `00b` stand unchanged — required beside a score, inside 0..1.
- **01b-D4**: the promoted acceptance node that pinned the rung reading
  (`packages/agent-review/acceptance/screening/test_provider_contract.py`, S3's
  `a_score_between_the_declared_levels_is_refused`) is replaced by this plan's rule rather than kept
  beside it: the two cannot both hold, and the promoted copy becomes the package's own test at
  `00b`'s archive.

## Seams

- `agent_review.questions.Question.criteria` — a score's declared levels, already carried by `T2`.
- `agent_review.judgments._is_level` / `_levels` — the rung check this plan replaces.
- `packages/agent-review/acceptance/screening/test_provider_contract.py` — the promoted `00b` acceptance.
