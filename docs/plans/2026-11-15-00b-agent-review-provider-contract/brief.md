# Brief: The provider contract — screening through Jev

Slug: `00b-agent-review-provider-contract`. Contract: `plan.yaml`. Lane: **Full** (three tickets, risk-tagged work).
Archive: **promote** into `docs/specs/agent-review.md`, as P0 did, so each scenario becomes a requirement there.

## Why this phase exists

P0 built an OpenAI chat-completions adapter. The agreed concept is Jev-first (D01), and P0's S1 acceptance was
satisfied through a local bridge that is not in the repository: the shipped tool has never spoken to TypeSafe. Before
P1 can plan anything on top of screening, the tool must speak System One natively and say what it screened with.

Read `2026-11-15-agent-review-jev-evidence/JEV.md` for the measured protocol before touching `plan.yaml`.

## What changes

- **T1** (S1, S5): `packages/agent-review/` — the OpenAI chat adapter is replaced by a System One transport: the
  endpoint and `TYPESAFE_API_KEY`, `state` as an object, `questions` as an id → question map, the
  `TypeSafe-Danger-Zone-Long-Context: true` header when configured, and an oversized request that is `incomplete` with
  the provider's own reason rather than a truncated state.
- **T2** (S2): the bank carries what a Jev question needs — a score question's levels (required), a noul's optional
  criteria, a choice's options — and refuses a missing or misplaced one by path before any request is made. The
  fixture provider stays as the offline seam.
- **T3** (S3): a judgment carries the provider's native answer: `probability` for a noul, `score` and `confidence` for
  a score, `choice` and `probabilities` for a choice. A score outside the declared levels, or a confidence outside
  0–1, is refused naming the question.
- **T4** (S4, S6): the report says what produced it — provider endpoint, the served model beside the configured alias,
  the input limit in force, the long-context flag, the bank digest — and a refusal or an incomplete run keeps what it
  learned instead of discarding it.

## Decisions

- The source of truth for agreed behaviour is `docs/specs/agent-review.md` (D01–D57, D58 open); do not re-grill them.
  These decisions are this plan's own seams.
- **00b-D1** One bank serves one instrument. The chat adapter is deleted rather than generalised: the evidence is that
  a typed System One request is a different instrument (separate criteria, a confidence axis, no second number on a
  noul), and two transports would need two banks or a lossy rendering. `2026-11-15-agent-review-jev-evidence/JEV.md` §1.
- **00b-D2** The limits are configuration, not constants: 32k per question and 64k per request by default, 262k with
  the long-context header, and the report records which was in force. A request is never truncated silently.
- **00b-D3** Long context is opt-in per invocation. It is measurably more forgiving on a large state
  (`hidden-state` 1.22 against 1.72 chunked), so it is a caller's choice with a recorded flag, never a default.
- **00b-D4** The fixture provider answers in the typed shape, so every check stays offline and no test reaches the
  network.

## Out of scope

Repository-added questions, chunking, escalation, cost, and deep review are P1's and P2's. This phase changes the
transport, the bank's shape, the judgment's shape and the report's provenance; nothing about routing a change.

## Risks

- `external` (T1): the transport is the one place a real credential and a real charge are reachable. Every check uses
  the fixture; the live run is the human-verified S1 evidence in `plan.yaml`'s hand-off.
- `state` (T2, T3, T4): the bank digest, the served model and the native answer fields are what a frozen report is
  compared with later (D26), so each is additive and recorded, never inferred.

## Hand-off

1. Write `acceptance/test_provider_contract.py` first, red at base, one class per scenario with a control; `plan check`
   stays red until it exists.
2. Build T1, then T2, then T3, then T4 — they share `packages/agent-review/` and the bank's shape (T2) precedes the
   judgments that name its levels (T3).
3. The S1 live evidence replaces P0's bridge note: run the CLI against a real two-revision change and record the
   report naming `jev-1.14.0`, the provider endpoint and the limits.
