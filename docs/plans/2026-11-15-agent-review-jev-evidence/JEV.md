# Jev (TypeSafe System One), as measured on this machine

Everything below is measured, not read from the docs. ~120 requests across 25 real changes in this
repository, plus controls. Artifacts: `runs/`, `FINDINGS.md`, `COMPARISON-12.md`, `SCORES.md`,
`HOLDOUT.md`, `runs/understanding/`.

## 1. The protocol, as observed

`POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer $TYPESAFE_API_KEY`,
body `{model, state, questions}`, where `state` is a JSON object and `questions` maps id → question.

| type | request | response | notes |
|---|---|---|---|
| `noul` | `{type, instructions, criteria?}` | `{type:"noul", noul: P(true)}` | **criteria optional**; with criteria the answer moved 0.90 → 0.83 |
| `score` | `{type, instructions, criteria: [level…]}` | `{type:"score", score, confidence, probabilities: {level: p}}` | **criteria required** — 422 `missing ... score.criteria` without them |
| `choice` | `{type, instructions, criteria: {option: meaning}}` | `{type:"choice", choice, probabilities, confidence}` | extremely decisive: 0.99–1.00 confidence on all three test cases, including docs-only |

Other observed facts:

- The response names the served model: **`"model": "jev-1.14.0"`** (the catalog alias is `jev-latest`).
- Usage is `{input_tokens, output_tokens}`, ~20 output tokens per question.
- Input budget, default: **32k per question** (state + any one question) and 64k per request
  (state + all questions). The per-question limit binds first for one large state, which is why a
  112,000-byte diff was accepted at 34,023 tokens and 114,000 bytes was refused with
  `400 {"detail":{"error_type":"max_tokens_exceeded}}` — the state crossed 32k, not 64k.
- Input budget, long context: send **`TypeSafe-Danger-Zone-Long-Context: true`** and both limits become
  262k tokens. Measured: the 137,853-byte `silent-idle` diff (which fails without the header) passes in
  one request at 40,351 tokens in 0.53 s; an 800,000-byte state passes at 122,521 tokens.
- **Long context costs accuracy.** The same `silent-idle` change scored `hidden-state` 1.22 and
  `error-handling` 0.29 in one long request, against 1.72 and ~0.9 chunked at the default limits — it
  reads a big state more forgivingly. The docs say so too ("slower and less accurate ... turn it on only
  for requests that need it"). So the reason to chunk is accuracy, not capability.
- Cost: $0.042 per 1M input tokens — a 40k-token review is about **$0.0017**.
- Latency 0.2–0.9 s per request, independent of question count (21 questions on a 30k-token state: 0.3 s).
- Errors are precise: 400 `max_tokens_exceeded`, 422 with the JSON path of a missing field, 401 with
  the route's auth policy. No partial answers, no silent truncation.

## 2. What moves an answer

| factor | measured effect |
|---|---|
| **state** | the large one. A 3-line comment change scores 0.41 mean severity, an unguarded `rmtree` 1.33, and per question: `survival-priority` 0.01 → 2.42 |
| **question framing** | ~25% of the scale. The same principle asked as "does it satisfy" (noul 0.44) implies severity 2.25; asked as "how severely does it violate" (score) it answers 1.21. Both are stable to sd 0.02 |
| **criteria richness** | ~18% of the scale. Terse ladder ("none/minor/moderate/major/severe") → 1.12; the authored five levels → 1.83 |
| **question type** | Choice is decisive (confidence ~1.0); Score is graded; Noul hedges |
| **batching** | negligible. The same 6 questions alone vs in one request differ by ≤ 0.19 (5% of the scale). Fan-out is safe |
| **truncation** | small on the score (≤ 0.12), larger on confidence (`hidden-state` 0.22 → 0.15 on a truncated 110KB diff) |
| **more state** (commit message + changed files + 6KB of requirements) | severity goes **down** by 0.10–0.20, not up |

## 3. Reliability

- **Score noise: 1–2% of the scale.** Five identical requests: per-question sd 0.02–0.07 on 0–4, mean
  confidence sd 0.01. Compare luna's noul probabilities: max Δ 0.70 on a 0–1 scale between two calls.
- **Noul noise: ~1.3%** of the scale (mean |Δ| 0.013 over 25 cases).
- **Scores do not abstain.** With an empty state, the mean severity is still 0.98 and one question
  returns 3.51. Nouls hedge instead: empty state → P(true) 0.44.
- **Confidence is the abstention signal, and it works.** Known-bad `rmtree` 0.20, empty 0.60, blank
  0.79, benign 0.70. Across 25 changes it tracks "can this be judged", not size (Spearman with log diff
  size −0.03).
- **One question is pathological.** `contract.test-evidence` as a score returns 3.51 with no state,
  1.37 on an empty diff, 1.53 on a benign change, 0.82 on a real defect — it answers "I see no test",
  which a diff can never satisfy. It must not be a score question, and probably not a question.

## 4. What it can and cannot do for review

**Can:** judge a *bounded* question about the change in front of it, fast and stably; report how sure
it is; name the served model; refuse loudly at its budget; classify a change's kind (Choice) essentially
perfectly.

**Cannot:** catch a defect whose evidence is absent from the state — a test that was not added, an
acceptance check that was not updated. Measured: `review-gate/t2r2`, a change with a failing acceptance
check, scored severity 0.60 at confidence 0.79, i.e. *better on both axes than 8 of 10 clean changes*.

**And it is not a defect detector on principle questions.** 25 changes, 10 bad: the bad/clean
distributions overlap (0.60–2.29 vs 0.56–2.27). Calibrated on 12 cases it looked good (0 missed, 1
false); on 13 held-out cases the same frozen thresholds gave 1 missed and 9 false, and the best any
threshold achieves on the hold-out is 0 missed with 8 of 10 clean changes escalated.

## 5. Constraints this imposes on the design (for the plan updates)

1. A threshold is per **question type, framing and criteria set** — they move the number by 18–25% of
   the scale. It cannot be shared across models or framings; it belongs in configuration with the bank.
2. **Confidence gates the score.** A score without confidence is a number with no meaning: it never
   abstains, even on nothing.
3. **Scores need criteria**, and criteria are the instrument. They are content, versioned and frozen
   with the bank, not decoration.
4. **~34k tokens** is the budget: chunking is mandatory, and the unit must be tokens, not characters.
5. **Fan-out is safe** (Δ ≤ 0.19): one request carrying every question per chunk, filtered in code. No
   applicability pre-pass.
6. **Absent-evidence questions are out of scope for the screener** until the state carries evidence
   (P1's D11 work), and `test-evidence` must be rewritten or dropped.
7. **Defect-class questions, not principles.** The corpus now has 10 bad changes with described
   defects; the questions that discriminate at all are the narrow ones (`hidden-state` on three-plus
   booleans, `survival-priority` on failure modes), and even those overlap.
8. **Deterministic safeguards are the reliable half.** Deleting outside the configured root, writing
   into a protected path, a new subprocess, a weakened permission check: decidable in code, no
   threshold, no false positives. The model half of the gate is the part this evidence does not support.
