# Fair comparison: Jev (System One) vs Codex (gpt-6.1-sol), 6 historical cases, 2 repeats

Same input to both arms: the 21 packaged questions with the same true/false meanings, the same diff,
the same deterministic chunking (100 KB by file). Same output contract: one probability per question.
Artifacts: `runs/compare/*.json` (one file per case/arm/repeat, with every chunk).

`gpt-6.1-luna` could not be used: it is a classifier on OpenAI's Decisions API, which accepts only an
OpenAI API key; `~/.codex/auth.json`'s `OPENAI_API_KEY` is empty and ChatGPT tokens are rejected on
that route. The Codex CLI (`gpt-6.1-sol`, ChatGPT login) is reachable and was used instead — a chat
CLI, so a different instrument class from Jev.

## Cases

| case | kind | ground truth |
|---|---|---|
| `429625b` | bad | fixed by `109c98b`: `rmtree` ran outside `plans.dir` |
| `89bba2f` | bad | fixed by `3afe564`: flaky rule matched prior receipts by tree only |
| `e9bd1ad` | bad | fixed by `f953773`: duplicate check, a required command put on the forbidden line |
| `7456440..72c4da9` | bad | agent-judge FAIL/0.70: missing event, missing test, idle answerer left working |
| `c239004` | clean | no fix commit names it |
| `74addfe` | clean | no fix commit names it |

## Aggregate

| | Jev | Codex |
|---|---|---|
| answered | 21/21 every run | 21/21 every run |
| latency per case (all chunks) | 0.2–0.8 s | 9.5–24.2 s |
| repeat noise, mean \|Δ\| / max | 0.013 / 0.08 | 0.034 / 0.27 |
| mean probability, bad / clean | 0.613 / 0.722 | 0.745 / 0.904 |
| worst bad → best clean | 0.644 → 0.694 (gap 0.050) | 0.785 → 0.881 (gap 0.096) |
| separates 4 bad from 2 clean | yes, at a mean < 0.68 | yes, at a mean < 0.85 |

Both thresholds are **fitted on these six cases** and nothing else; with n = 6 that is a hint, not a
measurement. The fitted margins (0.050 vs 0.096) are the first quantitative reason to prefer Codex at
case level, against a 30–40× latency difference and 2.6× the per-question noise.

## Per-question separation (bad − clean, mean over cases)

Jev's largest: `engineering.hidden-state` −0.30, `engineering.abstraction-levels` −0.24,
`contract.requirement-traceability` −0.22, `engineering.threshold` −0.22,
`engineering.composition` −0.20, `contract.error-handling` −0.15.

Codex's largest: `contract.error-handling` −0.54, `engineering.survival-priority` −0.46,
`engineering.type-contracts` −0.37, `contract.missing-context` −0.31,
`engineering.abstraction-levels` −0.15, `contract.public-surface-compatibility` −0.15.

The arms lean on different questions: Codex on the critical contract questions, Jev on the engineering
"shapeliness" ones. Two questions are instruments of the other arm only:

- `engineering.type-contracts`: Jev 0.14 bad / 0.16 clean (a constant, discriminates nothing);
  Codex 0.29 / 0.66.
- `contract.missing-context`: Jev 0.67 / 0.56 (wrong direction); Codex 0.26 / 0.57 (its main
  discriminator).

## Attribution: did the named question go low on the change that caused the defect?

| case | question the fix names | Jev | Codex |
|---|---|---|---|
| `429625b` | `engineering.survival-priority` | 0.56 | 0.49 |
| `429625b` | `contract.error-handling` | 0.42 | 0.36 |
| `89bba2f` | `engineering.traceable-cause` | 0.69 | **0.94** |
| `89bba2f` | `engineering.hidden-state` | 0.40 | 0.61 |
| `e9bd1ad` | `contract.requirement-traceability` | 0.77 | **0.95** |
| `e9bd1ad` | `engineering.hidden-state` | 0.48 | 0.81 |
| `silent-idle` | `engineering.external-legibility` | 0.65 | **0.84** |
| `silent-idle` | `contract.test-evidence` | 0.68 | 0.68 |
| `silent-idle` | `engineering.survival-priority` | 0.67 | 0.45 |

One case attributes cleanly (the unguarded `rmtree`), and it does so on both arms. Three do not: the
flaky-by-tree defect scores 0.94 on "can behaviour be traced to its cause", the duplicate-check defect
0.95 on "does behaviour trace to a requirement", and the invisible-transition defect 0.84 on
"is internal state observable". A compound question about a principle does not localize a defect.

## What this says

1. The premise is not dead: four independently-known bad changes all sit below both clean controls on
   both instruments, at 21/21 coverage.
2. Neither instrument attributes reliably; the weak link is the question set and the input (diff only),
   not the classifier. That is P1's D09/D11/D42 territory, not a model choice.
3. Jev is the right first pass (0.2–0.8 s, stable to ±0.01); Codex is a plausible second opinion
   (wider case-level margin, leans on the critical questions) at 9–24 s. That two-stage shape is the
   router the register describes, and this run is its first evidence.
4. Before any threshold is written: per-question clean baselines are required. One question is dead on
   Jev, another is systematically low on Codex, and both would fire or never fire regardless of change.
5. `contract.test-evidence` scored 0.68 on the change whose judged defect was a missing test — a diff
   alone cannot show that a test is missing, only that none was added. Questions about absent things
   need state that says what should have been there.

## Correction: the metric is the gate, not attribution

The register's gate (D01, D06, D08, D27 + P1-D7) escalates when a critical/high judgment fails; it does
not need to name the defect — per-question association is D52's offline analysis. Re-scored on that rule
("escalate when any critical/high question is below the bar", 11 watched questions):

| arm | bar | missed bad | false on clean |
|---|---|---|---|
| Jev | 0.2–0.6 | 0 | **2 (both clean changes, on `engineering.type-contracts`)** |
| Codex | 0.5–0.6 | 0 | 0 |

`engineering.type-contracts` scores 0.10–0.18 on **every** change for Jev — a constant, so the gate
escalates everything and discriminates nothing. Codex does not have that constant.

With that one question removed from the watched set:

| arm | bar | missed bad | false on clean |
|---|---|---|---|
| Jev | 0.3 | 4 | 0 |
| Jev | 0.4 | 3 | 0 |
| **Jev** | **0.5–0.6** | **0** | **0** |
| Jev | 0.7 | 0 | 1 `c239004` |
| Codex | 0.4 | 2 | 0 |
| Codex | 0.5 | 1 | 0 |
| **Codex** | **0.6** | **0** | **0** |
| Codex | 0.7 | 0 | 1 `c239004` |

Jev's usable band after one bank fix is 0.5–0.6 (width 0.1); Codex's is ~0.6 alone. Jev is 30–40× faster
and 2.6× more stable. The gate's failure was a bank item, not the classifier.

`gpt-6-luna` (classifier, `$0.10/M` in, 922k ctx) is the right third arm and is not runnable: the
`OPENAI_API_KEY` in the zsh secrets is rejected (401). No other classifier provider is keyed here
(no OpenRouter, Cloudflare, AI Gateway, OpenCode, Copilot token).
