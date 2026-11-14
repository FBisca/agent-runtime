# Widened run: 12 cases, Jev vs gpt-6-luna (headless Codex), 2 repeats

Same input to both arms: the 21 packaged questions with the same true/false meanings, the same diff,
the same chunking (100 KB by file). Same output: one probability per question. 48 runs, 21/21 answered
every time. Artifacts: `runs/compare/*.json`.

luna ran through `codex exec -m gpt-6-luna` (headless, no OpenAI API key: the Decisions route needs a
key and the one in the zsh secrets is rejected with 401).

## Cases

Bad (7) — the change had a real check fail on it, or a later fix names its defect:

| case | ground truth |
|---|---|
| `429625b` | `rmtree` ran outside `plans.dir`, fixed by `109c98b` |
| `89bba2f` | flaky rule matched prior receipts by tree only, fixed by `3afe564` |
| `e9bd1ad` | duplicate check, a required command on the forbidden line, fixed by `f953773` |
| `7456440..72c4da9` | agent-judge FAIL/0.70 |
| `engine-gaps/t4r1` | acceptance evidence S8 failed on the change |
| `plan-layout/t4r1` | acceptance evidence S10 failed on the change |
| `stale-team/t1r1` | acceptance evidence S7 failed on the change |

Clean (5) — COMPLETE, no blocking finding ever: `c239004`, `74addfe`, `check-tiers/t2`,
`named-capture/t1`, `review-cli/t2`.

The last three come from the orchestrator's own run records (273 ticket heads, all resolvable, 130 with
blocking findings, 46 with a *verify-sourced* failing check). "Bad" is deterministic; "clean" means "no
reviewer raised a blocker", which is weak — a low score there may be a true finding nobody raised.

## Means per case

| case | kind | Jev | luna |
|---|---|---|---|
| `429625b-rmtree` | bad | 0.599 | 0.721 |
| `89bba2f-flaky` | bad | 0.630 | 0.845 |
| `e9bd1ad-checks` | bad | 0.647 | 0.839 |
| `silent-idle` | bad | 0.581 | 0.815 |
| `engine-gaps/t4r1` | bad | 0.645 | 0.831 |
| `plan-layout/t4r1` | bad | 0.599 | 0.749 |
| `stale-team/t1r1` | bad | 0.672 | 0.843 |
| `c239004-brief` | clean | 0.689 | 0.944 |
| `74addfe-rtk` | clean | 0.748 | 0.920 |
| `check-tiers/t2` | clean | 0.653 | 0.856 |
| `named-capture/t1` | clean | 0.645 | 0.939 |
| `review-cli/t2` | clean | 0.677 | 0.851 |

**Jev: bad 0.581–0.672, clean 0.645–0.748 — the ranges overlap** (`named-capture` 0.645 <
`stale-team` 0.672), so no mean threshold separates.
**luna: bad 0.721–0.845, clean 0.851–0.944 — separates by 0.006.** Razor thin, n = 12.

## The plan's rule (escalate when any critical/high question fails)

| arm | bar | missed bad | false on clean |
|---|---|---|---|
| Jev (11 watched) | 0.3–0.7 | 0 | **5/5 — every clean change, on `engineering.type-contracts`** |
| Jev (minus `type-contracts`) | 0.5 | 1/7 (`stale-team`) | 2/5 (`named-capture`, `review-cli`) |
| luna (11 watched) | 0.5 | 4/7 | 2/5 |
| luna (11 watched) | 0.6 | 1/7 | 3/5 |
| luna (11 watched) | 0.7 | 0/7 | 4/5 |

No bar separates for either arm. This is the finding: **the plan's escalation rule is worse than an
aggregate score**, because it amplifies single-question noise and single-question constants.

## Per-question discrimination (bad − clean, mean over cases)

| question | Jev gap | luna gap |
|---|---|---|
| `contract.error-handling` | −0.19 | −0.22 |
| `engineering.hidden-state` | −0.17 | −0.17 |
| `engineering.abstraction-levels` | −0.15 | −0.14 |
| `contract.requirement-traceability` | −0.13 | −0.03 |
| `engineering.survival-priority` | −0.11 | −0.19 |
| `engineering.type-contracts` | −0.07 (0.13 / 0.20: a constant) | −0.12 |
| `contract.missing-context` | **+0.01** | −0.18 |
| `contract.public-surface-compatibility` | **+0.08** | **+0.08** |

Nothing separates by more than 0.22 on a 0–1 scale, and two questions move the wrong way on both arms.

## Stability

| | mean \|Δ\| | p90 | max |
|---|---|---|---|
| Jev | 0.013 | 0.030 | 0.070 |
| luna | 0.066 | 0.150 | **0.700** (`engineering.threshold`, `plan-layout/t4r1`) |

A per-question gate threshold cannot sit on an instrument that moves 0.70 between two identical calls.

## Verdict

- **Jev as an instrument: yes.** 0.3–1.6 s, 0.013 repeat noise, names `jev-1.14.0`, fails loudly at its
  34k budget.
- **Jev as the gate with this bank: not demonstrated.** The 6-case result that looked like a 0.5–0.6
  separating band does not survive 12 cases.
- **luna is not better.** It separates on the mean by 0.006 and is 5× noisier per question.
- **The binding constraint is the instrument, not the model:** a 21-question compound bank judged from a
  diff alone. Every number above is consistent with that, and the earlier 6-case run was too small to see it.
