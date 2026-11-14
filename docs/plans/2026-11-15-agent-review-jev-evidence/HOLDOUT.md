# Hold-out: does the Score gate generalise? No.

13 changes that took no part in authoring the levels or fitting the thresholds: 3 bad (a real
acceptance check failed on that exact change) and 10 clean (COMPLETE, no blocking finding ever raised,
touches src/). Frozen rule from calibration: escalate if max severity >= 1.5 or confidence < 0.7.

| case | kind | max severity | confidence | frozen rule |
|---|---|---|---|---|
| `plan-amend/t4r2` | bad | 2.29 | 0.56 | escalate |
| `yaml-eec/t8r3` | bad | 2.18 | 0.41 | escalate |
| **`review-gate/t2r2`** | **bad** | **0.60** | **0.79** | **pass — MISSED** |
| `gaps/t3` | clean | 2.27 | 0.62 | escalate (false) |
| `gaps/t4` | clean | 1.88 | 0.58 | escalate (false) |
| `gaps/t1` | clean | 1.63 | 0.54 | escalate (false) |
| `label-evidence/t1` | clean | 1.45 | 0.59 | escalate (false) |
| `gaps/t2` | clean | 1.28 | 0.65 | escalate (false) |
| `check-tiers/t3` | clean | 1.19 | 0.65 | escalate (false) |
| `check-tiers/t1o1` | clean | 1.10 | 0.51 | escalate (false) |
| `workbench/t1` | clean | 0.83 | 0.67 | escalate (false) |
| `review-cli/t1` | clean | 0.58 | 0.73 | pass |
| `archive/t2` | clean | 0.56 | 0.66 | escalate (false) |

**1 of 3 bad missed, 9 of 10 clean escalated — the gate escalates 11 of 13 changes.**

Tuned on the hold-out itself (for reference, not for shipping), the best any metric achieves is
**0 missed / 8 of 10 false** (max severity, bar 0.59). `max over critical questions` and the
priority-weighted mean are worse: 10 of 10 false.

## Why it fails

- The ranges overlap almost completely: bad 0.60-2.29, clean 0.56-2.27. There is no bar.
- The missed bad change is **better on both axes than 8 of the 10 clean ones** (severity 0.60,
  confidence 0.79). Its defect is that a scenario's acceptance evidence was missing — a fact about the
  change that a diff cannot show. No question wording and no threshold reaches it.
- Severity is **not** a proxy for size or tangle: Spearman(max severity, log diff size) = -0.04,
  Spearman(max severity, changed files) = -0.43. So the instrument is not measuring "big change"; what it
  measures is unexplained. That is worse than a proxy — a proxy would at least be predictable.
- Confidence does not rescue it: the clean dense tickets (`gaps/*`, `check-tiers/*`) sit at 0.51-0.67,
  the same band as the bad ones (0.41-0.56).

## Verdict

The gate as specified (D01/D06/D09/D27: screen, escalate on flag or uncertainty) is **not supported** by
25 cases. The calibration result (0 missed / 1 false on 12 cases) was overfit: the same instrument, the
same thresholds, and 13 fresh cases turn it into 1 missed / 9 false.

The economics fail with it. The prior pilot put a reviewed task at about $2; screening costs about
$0.001. A gate that escalates 85% of changes saves ~15% of the deep-review budget, while missing a third
of the known defects.

The premise is not disproven; the *instrument* is wrong. Two consequences:

1. Principle questions measure the change's shape, and shape is not defect. The bank has to ask
   defect-class questions derived from the corpus we now have (10 bad changes with described defects:
   delete outside the configured root, match-prior-receipts by tree, a required command on the forbidden
   line, a state transition with no event, a fix with no test).
2. For the classes that are decidable from a diff, a deterministic check beats a model: deleting a path
   outside the repo root, writing into a protected path, adding a subprocess, weakening a permission
   check. No threshold, no false positives, no calibration. D06's "independent deterministic
   safeguards" is the part of the design the evidence supports.
