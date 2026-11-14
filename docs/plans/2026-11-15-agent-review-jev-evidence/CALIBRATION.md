# Per-question calibration

Provisional. Reference set:

- noul: 5 clean changes — `74addfe-rtk`, `c239004-brief`, `check-tiers/t2`, `named-capture/t1`, `review-cli/t2`
- score: 15 clean changes — `74addfe-rtk`, `archive/t2`, `c239004-brief`, `check-tiers/t1o1`, `check-tiers/t2`, `check-tiers/t3`, `gaps/t1`, `gaps/t2`, `gaps/t3`, `gaps/t4`, `label-evidence/t1`, `named-capture/t1`, `review-cli/t1`, `review-cli/t2`, `workbench/t1`

## noul bank (0-1, higher = the change satisfies the question)

| question | clean μ | clean σ | bad μ | gap | verdict |
|---|---|---|---|---|---|
| `contract.error-handling` | 0.77 | 0.11 | 0.58 | -0.19 | keep |
| `engineering.hidden-state` | 0.69 | 0.12 | 0.52 | -0.17 | keep |
| `engineering.abstraction-levels` | 0.61 | 0.08 | 0.46 | -0.15 | keep |
| `contract.requirement-traceability` | 0.82 | 0.07 | 0.69 | -0.13 | keep |
| `engineering.survival-priority` | 0.79 | 0.04 | 0.68 | -0.11 | keep |
| `contract.test-evidence` | 0.77 | 0.05 | 0.66 | -0.10 | keep |
| `engineering.elegance` | 0.82 | 0.03 | 0.73 | -0.09 | weak |
| `engineering.new-concepts` | 0.78 | 0.04 | 0.70 | -0.08 | weak |
| `engineering.traceable-cause` | 0.83 | 0.03 | 0.75 | -0.08 | weak |
| `engineering.composition` | 0.71 | 0.11 | 0.64 | -0.07 | weak |
| `engineering.type-contracts` | 0.20 | 0.11 | 0.13 | -0.07 | dead |
| `engineering.threshold` | 0.48 | 0.25 | 0.42 | -0.06 | unstable |
| `engineering.hard-decisions` | 0.77 | 0.04 | 0.71 | -0.06 | weak |
| `engineering.understand-before-compressing` | 0.76 | 0.08 | 0.73 | -0.03 | no signal |
| `engineering.names-meaning` | 0.74 | 0.04 | 0.73 | -0.00 | no signal |
| `engineering.consequence-not-achievement` | 0.57 | 0.05 | 0.57 | -0.00 | no signal |
| `contract.missing-context` | 0.66 | 0.13 | 0.66 | +0.01 | no signal |
| `engineering.implicit-named` | 0.57 | 0.08 | 0.59 | +0.02 | no signal |
| `engineering.external-legibility` | 0.72 | 0.07 | 0.75 | +0.03 | no signal |
| `engineering.durable-intent` | 0.78 | 0.09 | 0.82 | +0.05 | no signal |
| `contract.public-surface-compatibility` | 0.52 | 0.23 | 0.60 | +0.08 | unstable |

## score bank (0-4 severity, higher = worse)

| question | clean μ | clean σ | bad μ | gap | confidence clean / bad | verdict |
|---|---|---|---|---|---|---|
| `engineering.hidden-state` | 0.92 | 0.71 | 1.65 | +0.72 | 0.46 / 0.24 | keep |
| `engineering.survival-priority` | 0.18 | 0.11 | 0.78 | +0.59 | 0.85 / 0.58 | keep |
| `contract.error-handling` | 0.23 | 0.17 | 0.67 | +0.44 | 0.81 / 0.49 | keep |
| `engineering.abstraction-levels` | 0.73 | 0.39 | 1.16 | +0.44 | 0.63 / 0.53 | keep |
| `contract.test-evidence` | 0.49 | 0.21 | 0.68 | +0.19 | 0.59 / 0.47 | no signal |
| `contract.requirement-traceability` | 0.55 | 0.39 | 0.54 | -0.01 | 0.57 / 0.55 | no signal |

**noul keeps 6 of 21**: `contract.requirement-traceability`, `contract.error-handling`, `contract.test-evidence`, `engineering.survival-priority`, `engineering.hidden-state`, `engineering.abstraction-levels`

**score keeps 4 of 6**: `contract.error-handling`, `engineering.abstraction-levels`, `engineering.hidden-state`, `engineering.survival-priority`
