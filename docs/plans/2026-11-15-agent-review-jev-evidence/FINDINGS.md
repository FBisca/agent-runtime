# Probe findings: Jev (TypeSafe System One) as agent-review's screener

Date: probe run on the owner's TypeSafe credential. Not part of the repository.
Artifacts: `runs/*.json` beside this file (payloads and raw responses, no credentials).

## What was run

`POST https://api.typesafe.ai/v1/systemone`, `model: jev-latest`, `state: {base, head, diff}`,
`questions: {<bank id>: {type: "noul", instructions: <ask>, criteria: {true, false}}}` — the packaged
21-question bank read through `agent_review.questions.baseline()`.

| Run | Input | Result |
|---|---|---|
| tiny | `0bde508^..0bde508`, 2 questions | 200, 0.38s, input 8,690 tok |
| nocriteria | same, 1 question, no `criteria` | 200, 0.26s — `criteria` is optional, answer moved 0.90 → 0.83 |
| full | `7456440..72c4da9` (137,853 B) | **400 `max_tokens_exceeded`** |
| limits | same diff, truncated | 112,000 B → 34,023 tok accepted; 114,000 B → 400 |
| chunks | same diff, 28 files split in 2 (93,700 + 44,153 B) | 200, 21/21 answered each, 0.44s / 0.32s |
| repeat | `d2036c8^..d2036c8`, same payload twice | 19 of 21 answers moved, max delta 0.05 |
| clean control | `c239004^..c239004` | 200, 21/21 |

## Answers to the four open questions

1. **One probability is enough to carry an answer** — the wire returns `{"type":"noul","noul":p}` and
   nothing else. `value` and `confidence` (P1-D7) must be *derived* from `p` by a declared rule.
2. **The response names the served model: `model: "jev-1.14.0"`.** P1 S7 ("the model the provider
   resolved beside the configured alias") is implementable on the Jev path. Earlier audit finding B4
   ("unobtainable") is **wrong** — pi's SDK discards the field, the wire has it.
3. **The usable budget is ~34k input tokens, not the catalogued 64,000.** The known-bad diff does not
   fit in one request; chunking is a prerequisite, and the budget must be in tokens.
4. **`criteria` is optional but answer-sensitive** (0.90 with, 0.83 without). Leaving it out is legal
   and changes the judgment.

## Signal on the known-bad case

Ground truth: `agent-evals` case `orchestrator-silent-idle`, agent-judge **FAIL / 0.70**, three cited defects:

| Judged defect | Question it names | bad-min | clean | gap |
|---|---|---|---|---|
| transition invisible in events.jsonl | `engineering.external-legibility` | 0.67 | 0.61 | **+0.06** |
| no test for the S8 continuation | `contract.test-evidence` | 0.65 | 0.68 | -0.03 |
| idle answerer left working after a Lead decision | `engineering.survival-priority` | 0.68 | 0.76 | -0.08 |

Aggregate separation exists (mean bad-min 0.590 vs clean 0.698; 5 questions below 0.50 on the bad
change, 1 on the clean), but **it is not the judged defects that separate**. The largest gaps are
`engineering.hidden-state` (-0.30), `contract.requirement-traceability` (-0.27), `engineering.threshold`
(-0.26), `contract.error-handling` (-0.24), `engineering.elegance` (-0.24) — a "this change is big and
entangled" signal at least as much as a defect signal.

Two further observations:

- `engineering.type-contracts` scores 0.10–0.13 on **both** changes: a per-question constant, i.e. a
  bank item that fires on every case and discriminates nothing.
- No change scores high: the clean control's mean is 0.698. A fixed "passed above 0.8" bar would flag
  everything; a fixed 0.5 bar flags 5/21 on the bad case and 1/21 on the clean. Thresholds must be
  per-question and relative to a clean baseline, not absolute.

## Confounds, stated

- The 21 `criteria` pairs were authored in one sitting for this probe and matter (see run 2).
- `state` is the diff alone: no requirement text, no commit message, no test suite — D11 asks for
  requirements and existing evidence, which P0 does not supply.
- The diff was split by file in path order; a defect can sit in the chunk where the question is
  answered against the wrong context (min-across-chunks mitigates, and also lets confusion dominate).
- 21 compound questions over 90 KB may be answering "is this change generally good" 21 times.
- n = 1 bad case, n = 1 clean case. This is a probe, not the P4 benchmark (D56 wants 20–30).

## Consequence for the plans

- Requirements the spec lacks: System One transport + `TYPESAFE_API_KEY`; `criteria` on a question;
  probability native with a declared threshold and uncertain band; the served model in the report; the
  provider in the report; a token budget and timeout as configuration; and R4's coverage corrected —
  the report is reproducible in structure, **not** in judgments (±0.05, 19/21 move).
- P1-D7's `{value, confidence}` is satisfiable only by a declared derivation from `p`; P1-D5's
  `chunk.max_diff_chars` is the wrong unit for a 34k-token model; P4's S2 cannot freeze judgments.
- Before any of that is worth planning, the aggregate separation needs to hold on more cases, and the
  bank needs a calibration pass (one item is already a constant).
